# -*- coding: utf-8 -*-
"""协调合规 Agent:多 Agent 编排 + 冲突消解 + 合规终审(JD#6)。

编排策略(路由规则,程序确定性执行,LLM 只做内容层工作):
  - 诊断/鉴别/症状类  → DiagnosisAgent
  - 指南/标准/原理类问题 → LiteratureAgent
  - 病历文本解析类     → MedicalRecordAgent
  多路命中 → 并行执行 → 冲突消解(证据强度仲裁) → 聚合 → 合规终审。
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import MedAgentConfig
from ..runtime.llm import LLMClient
from ..runtime.trace import Trace
from .prompts import COORDINATOR_SYSTEM
from .diagnosis import DiagnosisAgent
from .literature import LiteratureAgent
from .record_analyst import MedicalRecordAgent

ROUTE_RULES: list[dict[str, Any]] = [
    {"agent": "record_analyst", "patterns": ["病历", "出院小结", "门诊病历", "入院记录"],
     "desc": "病历文本解析"},
    {"agent": "literature", "patterns": ["指南", "标准是什么", "诊断标准", "如何鉴别", "评分",
                                          "时间窗", "原则", "适应证", "禁忌"],
     "desc": "文献指南问答"},
    {"agent": "diagnosis", "patterns": ["症状", "怎么办", "可能是什么", "鉴别", "不舒服", "疼",
                                         "发热", "咳嗽", "头痛", "胸痛", "乏力", "化验", "检查结果"],
     "desc": "诊断推理分析"},
]


class CoordinatorAgent:
    def __init__(self, cfg: MedAgentConfig, knowledge, registry):
        self.cfg = cfg
        self.knowledge = knowledge
        self.diagnosis = DiagnosisAgent(cfg, registry, knowledge)
        self.literature = LiteratureAgent(cfg, knowledge)
        self.record = MedicalRecordAgent(cfg, knowledge)
        self.llm = LLMClient(cfg, model=cfg.reviewer_model)

    # ------------------------------------------------------------------
    def route(self, task: str) -> list[dict[str, str]]:
        hits: list[dict[str, str]] = []
        for rule in ROUTE_RULES:
            matched = [p for p in rule["patterns"] if p in task]
            if matched:
                hits.append({"agent": rule["agent"], "desc": rule["desc"],
                             "matched": ",".join(matched[:3])})
        if not hits:  # 默认走诊断推理
            hits = [{"agent": "diagnosis", "desc": "默认", "matched": ""}]
        return hits[:2]  # 最多并行两个子 Agent,控制成本

    def run(self, task: str, trace: Trace | None = None) -> tuple[str, Trace]:
        trace = trace or Trace(task)
        routes = self.route(task)
        trace.add("routing", result=routes)

        # 并行执行命中的子 Agent
        import concurrent.futures as cf
        runners = {"diagnosis": (self.diagnosis.run, task),
                   "literature": (self.literature.run, task),
                   "record_analyst": (self.record.run, task)}
        selected = [(r["agent"], runners[r["agent"]]) for r in routes if r["agent"] in runners]
        results_map: dict[str, tuple[str, Any]] = {}
        if len(selected) == 1:
            agent_name, (fn, arg) = selected[0]
            ans, tr = fn(arg)
            results_map[agent_name] = (ans, tr)
        else:
            with cf.ThreadPoolExecutor(max_workers=len(selected)) as ex:
                futu = {ex.submit(fn, arg): agent_name for agent_name, (fn, arg) in selected}
                for fut in cf.as_completed(futu):
                    agent_name = futu[fut]
                    try:
                        ans, tr = fut.result()
                        results_map[agent_name] = (ans, tr)
                    except Exception as e:
                        results_map[agent_name] = (f"子 Agent 执行失败: {e}", None)

        results: dict[str, str] = {}
        for agent_name, (ans, tr) in results_map.items():
            results[agent_name] = ans
            if tr is not None:
                trace.add("sub_agent", tool=agent_name, thought=f"status={tr.status}",
                          result={"answer_head": ans[:200],
                                  "usage": tr.usage if hasattr(tr, "usage") else {}})
                if hasattr(tr, "usage") and tr.usage:
                    trace.add_usage(tr.usage)

        # 单路结果 → 直接终审输出;多路 → 冲突消解与聚合
        if len(results_map) == 1:
            agent_name, (ans, _tr) = next(iter(results_map.items()))
            final = self._finalize(task, ans, agent_name, trace)
            return final, trace

        results = {name: ans for name, (ans, _tr) in results_map.items()}
        merged = self._resolve_and_merge(task, results, trace)
        final = self._finalize(task, merged, "coordinator", trace)
        return final, trace

    # ------------------------------------------------------------------
    # 冲突消解 + 聚合(LLM 层:基于证据强度的仲裁,规则提示词约束)
    def _resolve_and_merge(self, task: str, results: dict[str, str], trace: Trace) -> str:
        evidence = {name: len(re.findall(r"\[MG-[A-Za-z]+-\d+\]", ans))
                    for name, ans in results.items()}
        prompt = (COORDINATOR_SYSTEM
                  + f"\n\n## 证据强度计量(程序统计的指南引用数)\n"
                  + json.dumps(evidence, ensure_ascii=False)
                  + "\n(仲裁规则:有 [MG-] 引用的结论优先于无引用;检验数据支持优先于纯症状推测;"
                    "分歧无法消解时如实呈现两方与各自依据,不强行统一)\n\n"
                  + f"【用户任务】{task}\n\n"
                  + "\n\n".join(f"【{name} 的输出】\n{ans[:3000]}"
                                 for name, ans in results.items()))
        try:
            r = self.llm.chat([{"role": "user", "content": prompt}])
            trace.add("arbitration", thought=f"证据强度 {evidence}",
                      result={"merged_len": len(r.content)})
            return r.content.strip()
        except Exception as e:
            trace.add("arbitration", error=f"仲裁失败(降级拼接): {e}")
            return "\n\n---\n\n".join(f"【{k}】\n{v}" for k, v in results.items())

    # ------------------------------------------------------------------
    # 合规终审(复用诊断 Agent 的硬门禁 + 急症检测)
    def _finalize(self, task: str, answer: str, source: str, trace: Trace) -> str:
        from ..knowledge.rules import EMERGENCY_PATTERNS
        emergency = any(p in task for p in EMERGENCY_PATTERNS)
        gate = self.diagnosis.compliance_gate(answer, emergency=emergency)
        trace.gate = gate
        if gate["blocked"]:
            trace.finish("blocked", gate["revised"])
        else:
            trace.finish("passed", answer)
        return trace.final_answer
