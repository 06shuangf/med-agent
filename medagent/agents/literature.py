# -*- coding: utf-8 -*-
"""文献指南 Agent:医疗 RAG 问答(检索增强 + 反幻觉三道闸门)。

复用 rag-knowledge-base 的设计:信号门控拒答 / 提示词硬约束+强制引用 /
引用后校验过滤伪造。检索层为混合检索(实体加权 BM25)。
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import MedAgentConfig
from ..runtime.llm import LLMClient
from ..runtime.trace import Trace
from .prompts import LITERATURE_SYSTEM


class LiteratureAgent:
    def __init__(self, cfg: MedAgentConfig, knowledge, database=None):
        self.cfg = cfg
        self.knowledge = knowledge
        self.llm = LLMClient(cfg)
        self.db = database  # 可选:数据库向量检索通道(不传则单引擎)

    def _hybrid_search(self, question: str) -> tuple[list[dict], list[dict]]:
        """双引擎检索:内存混合检索(实体加权 BM25)+ DB 向量余弦。"""
        mem_hits = self.knowledge.search(question, top_k=self.cfg.rag_top_k)
        db_hits = []
        if self.db is not None:
            try:
                db_hits = self.db.vector_search(question, top_k=self.cfg.rag_top_k)
            except Exception:
                db_hits = []
        return mem_hits, db_hits

    def _rrf_fuse(self, mem_hits: list[dict], db_hits: list[dict],
                  k: int = 4, rrf_k: int = 60) -> tuple[list[dict], float]:
        """RRF 融合两路排名(只用排名,对分数分布免疫;同 rag-knowledge-base 设计)。"""
        scores: dict[str, float] = {}
        cards: dict[str, dict] = {}
        for rank, h in enumerate(mem_hits):
            cid = h["card"]["id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
            cards[cid] = h["card"]
        for rank, h in enumerate(db_hits):
            cid = h["card"]["id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
            cards[cid] = h["card"]
        fused = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        return ([{"card": cards[cid], "score": round(s, 4), "source": "rrf_fused"}
                 for cid, s in fused],
                fused[0][1] if fused else 0.0)

    def run(self, question: str, trace: Trace | None = None) -> tuple[str, Trace]:
        trace = trace or Trace(question)
        # 闸门1:检索信号门控 —— 双引擎一致性与实体覆盖联合判定
        mem_hits, db_hits = self._hybrid_search(question)
        if mem_hits or db_hits:
            hits, rrf_top = self._rrf_fuse(mem_hits, db_hits, k=self.cfg.rag_top_k)
        else:
            hits, rrf_top = [], 0.0
        # 信号判定:(a)DB 向量通道与内存通道首位一致(双引擎共识),或
        # (b)问题实体出现在命中卡片的 entities 中(强相关证据)。
        # 单靠 BM25 分数会被"问题里零散词撞上高频医学词"骗过(如年份类知识库外问题)。
        top_mem = mem_hits[0]["card"]["id"] if mem_hits else None
        top_db = db_hits[0]["card"]["id"] if db_hits else None
        consensus = top_mem is not None and top_mem == top_db
        q_lower = question.lower()
        entity_hit = any(ent.lower() in q_lower
                         for h in mem_hits[:2] for ent in h["card"].get("entities", []))
        # 双引擎共识仍可能同错(零散词撞上高频医学词),追加内容锚点校验:
        # 命中卡片正文必须包含问题的"内容词"(长于1字的非停用中文词/英文词根),
        # 否则视为词面噪声,不算有效信号。
        import re as _re
        content_words = [w for w in _re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", question)]
        _vague = {"什么", "怎么", "如何", "哪些", "多少", "可以", "应该", "问题", "情况",
                  "医疗", "适应证", "建议", "多少小时"}
        anchor_ok = False
        if content_words:
            for h in mem_hits[:2]:
                content = h["card"]["content"]
                for w in content_words:
                    if w in _vague or w.isdigit():
                        continue
                    if w in content:
                        anchor_ok = True
                        break
                if anchor_ok:
                    break
        has_signal = (consensus and anchor_ok) or entity_hit
        cited_pool = {h["card"]["id"] for h in hits} | {h["card"]["id"] for h in mem_hits}
        trace.add("retrieval", tool="hybrid_search",
                  result={"mem": [(h["card"]["id"], h["score"]) for h in mem_hits],
                          "db": [(h["card"]["id"], h["score"]) for h in db_hits],
                          "fused": [(h["card"]["id"], h["score"]) for h in hits],
                          "consensus": consensus, "entity_hit": entity_hit,
                          "anchor_ok": anchor_ok, "has_signal": has_signal})
        if not has_signal:
            answer = ("知识库中无足够信息回答该问题。建议查阅最新临床指南原文或咨询专科医生。\n\n"
                      "### 声明\n本回答不构成医疗建议。")
            trace.add("abstain", thought="信号门控触发(双引擎均无有效命中),拒绝回答")
            trace.finish("abstained", answer)
            return answer, trace

        context = "\n".join(f"[{h['card']['id']}] {h['card']['content']}" for h in hits)
        messages = [
            {"role": "system", "content": LITERATURE_SYSTEM.format(guidelines=context[:3000])},
            {"role": "user", "content": question},
        ]
        try:
            r = self.llm.chat(messages)
            trace.add_usage(r.usage.as_dict())
        except Exception as e:
            trace.finish("failed", f"LLM 调用失败: {e}")
            return trace.final_answer, trace
        answer = r.content.strip()

        # 闸门3:引用校验 —— 答案中引用 id 必须 ∈ 本次召回集合
        # 注意口径统一:提取时去掉 [ ] 括号,与召回池的裸 id 直接比较
        cited_in_answer = set(re.findall(r"\[?(MG-[A-Za-z]+-\d+)\]?", answer))
        cited_pool = {cid for cid in cited_pool}
        forged = {f"[{c}]" for c in (cited_in_answer - cited_pool)}
        if forged:
            for f in forged:
                answer = answer.replace(f, "(引用无效已移除)")
            trace.add("citation_check", error=f"过滤伪造引用: {sorted(forged)}")
        valid_cited = cited_in_answer & cited_pool
        if not valid_cited and "[MG-" not in answer:
            # 答案缺引用:先重试一次"补挂引用"(把召回卡片明确列出要求标注),
            # 而不是直接丢弃正确答案 —— 拒答是最后手段
            retry_system = LITERATURE_SYSTEM.format(guidelines=context[:3000]) + (
                "\n\n【格式强制】你的上一个回答没有标注任何 [MG-xx-xxx] 引用。"
                "请重新输出同一内容,并在每个事实后标注来源卡片 id:"
                + ", ".join(sorted(cited_pool)) + "。缺少引用将被拒收。")
            try:
                r2 = self.llm.chat([{"role": "system", "content": retry_system},
                                    {"role": "user", "content": question}])
                trace.add_usage(r2.usage.as_dict())
                answer2 = r2.content.strip()
                cited2 = set(re.findall(r"\[?(MG-[A-Za-z]+-\d+)\]?", answer2)) & cited_pool
                if cited2:
                    answer = answer2
                    valid_cited = cited2
                    trace.add("citation_retry", thought="补挂引用成功")
                else:
                    trace.add("citation_retry", error="重试仍无引用")
            except Exception as e:
                trace.add("citation_retry", error=f"重试失败: {e}")
        if not valid_cited:
            # 答案没有任何有效引用 → 视为无据,降级为拒答
            answer = ("检索证据不足,本问题在当前知识库覆盖范围外。"
                      "建议查阅最新指南或咨询专科医生。\n\n### 声明\n不构成医疗建议。")
            trace.add("abstain", thought="无有效引用,降级拒答")
            trace.finish("abstained", answer)
            return answer, trace

        if "### 声明" not in answer and "不构成医疗建议" not in answer:
            answer += "\n\n### 声明\n本回答仅供医学信息参考,不构成医疗建议。"
        trace.add("citation_check", thought=f"有效引用 {sorted(valid_cited)}")
        trace.finish("passed", answer)
        return answer, trace
