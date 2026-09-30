# -*- coding: utf-8 -*-
"""金融知识工程(二):领域知识库 + RAG 检索。

知识库 = 结构化的"金融常识卡片"(Schema 体系),每张卡片带标签与实体。
检索 = 混合召回:关键词/实体匹配 + BM25 风格词频得分,纯标准库实现、零依赖。

这里的"知识工程"思路:
  1. 把专家经验拆成原子化知识卡片(定义、方法、口径、风险)
  2. 用 Schema 约束卡片结构(topic/ entities/ tags/ content)
  3. 检索时按实体命中率 + 词频相关性排序,取 top-k 注入 system prompt
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from typing import Any

from .guidelines import MEDICAL_KB as KNOWLEDGE_BASE_DEFAULT

# ----------------------------------------------------------------------
# 知识卡片 Schema:
#   id        唯一编号
#   topic     主题域 valuation/ ratio/ risk/ industry/ method ...
#   entities  覆盖的实体(股票代码、行业、指标名)
#   tags      检索标签
#   content   知识正文(专家经验的原子化表达)
# ----------------------------------------------------------------------
KNOWLEDGE_BASE: list[dict[str, Any]] = [
    # ---- 估值方法 ----
    {
        "id": "KB-V-001", "topic": "valuation", "entities": ["PE", "市盈率"],
        "tags": ["估值", "市盈率", "相对估值"],
        "content": ("市盈率 PE = 股价 / 每股收益,适用于盈利稳定的主板成熟企业。"
                    "判断高低必须与行业均值、公司历史分位比较,跨行业直接比较 PE 无意义。"
                    "亏损股 PE 为负,应改用 PB 或 PS 估值。"),
    },
    {
        "id": "KB-V-002", "topic": "valuation", "entities": ["PB", "市净率"],
        "tags": ["估值", "市净率", "银行", "重资产"],
        "content": ("市净率 PB = 股价 / 每股净资产,适用于银行、券商、重资产制造业。"
                    "A 股银行股 PB 长期低于 1 属常态;PB 显著低于 1 且 ROE 持续为负,"
                    "需警惕资产减值风险而非‘便宜’。"),
    },
    {
        "id": "KB-V-003", "topic": "valuation", "entities": ["DCF", "自由现金流"],
        "tags": ["估值", "DCF", "绝对估值"],
        "content": ("DCF 将未来自由现金流折现求和,是对成长股/科技股更本质的估值方法,"
                    "但对增长率 g 与折现率 r 极度敏感:g 或 r 变动 1pp,结果可能变动 20%+。"
                    "结论必须给区间(乐观/中性/悲观三档假设),禁止给单点值。"),
    },
    {
        "id": "KB-V-004", "topic": "valuation", "entities": ["PS", "市销率"],
        "tags": ["估值", "市销率", "亏损企业"],
        "content": ("市销率 PS = 市值 / 营业收入,适用于尚未盈利的高增长企业(如创新药、SaaS)。"
                    "PS 估值需结合毛利率与收入增速:同样 PS=10,毛利率 80% 与 20% 的公司质量天差地别。"),
    },
    # ---- 财务分析 ----
    {
        "id": "KB-F-001", "topic": "ratio", "entities": ["ROE", "净资产收益率"],
        "tags": ["财务分析", "ROE", "杜邦分析"],
        "content": ("ROE = 净利率 × 总资产周转率 × 权益乘数(杜邦分解)。"
                    "高 ROE 要看驱动结构:靠净利率(产品力)优于靠周转(运营效率),"
                    "靠权益乘数(加杠杆)质量最差。连续 3 年 ROE>15% 且负债率 <60% 是优质信号。"),
    },
    {
        "id": "KB-F-002", "topic": "ratio", "entities": ["毛利率", "净利率"],
        "tags": ["财务分析", "盈利能力"],
        "content": ("毛利率反映产品竞争力与定价权。毛利率同比大幅波动(>5pp)需查明原因:"
                    "是产品提价、成本下降(好),还是会计口径变更、渠道压货(差)。"
                    "毛利率远高于同行且现金流持续为负,是财务造假的高危组合。"),
    },
    {
        "id": "KB-F-003", "topic": "ratio", "entities": ["资产负债率", "流动比率"],
        "tags": ["财务分析", "偿债能力", "风险"],
        "content": ("资产负债率行业差异极大:银行 ~92% 属正常,制造业 >70% 即需警惕。"
                    "流动比率 <1 且经营现金流为负,意味着短期偿债压力真实存在。"
                    "有息负债/总资产持续攀升且高于同行均值 15pp 以上,列为重点风险。"),
    },
    {
        "id": "KB-F-004", "topic": "ratio", "entities": ["经营现金流", "净利润"],
        "tags": ["财务分析", "现金流", "收入质量"],
        "content": ("经营现金流净额 / 净利润 连续多年 <0.7,说明利润‘纸面化’(应收账款堆积或确认激进)。"
                    "现金流与利润长期背离是收入造假的核心预警信号,优先级高于任何利润表指标。"),
    },
    # ---- 风险识别 ----
    {
        "id": "KB-R-001", "topic": "risk", "entities": ["商誉"],
        "tags": ["风险", "商誉减值", "并购"],
        "content": ("商誉/净资产 >30% 为高危:一旦被并购资产业绩变脸,减值将直接击穿利润。"
                    "关注被并购方业绩承诺到期年度 —— 承诺期结束后的第一年是减值高发期。"),
    },
    {
        "id": "KB-R-002", "topic": "risk", "entities": ["质押", "大股东"],
        "tags": ["风险", "股权质押"],
        "content": ("大股东股权质押比例 >50% 视为流动性紧张信号,>70% 属高危"
                    "(平仓风险可能引发控制权变更,股价负反馈)。"),
    },
    {
        "id": "KB-R-003", "topic": "risk", "entities": ["客户集中度"],
        "tags": ["风险", "大客户依赖"],
        "content": ("第一大客户收入占比 >50% 时,客户流失或砍价直接威胁生存,"
                    "常见于消费电子产业链(果链)与 Tier-2 供应商,估值应给依赖性折价。"),
    },
    # ---- 行业框架 ----
    {
        "id": "KB-I-001", "topic": "industry", "entities": ["银行业"],
        "tags": ["行业", "银行", "周期"],
        "content": ("银行股分析核心:净息差(定价能力)、不良率(资产质量)、拨备覆盖率(利润蓄水池)。"
                    "看银行不看 PE 看股息率与 PB-ROE 匹配度;经济下行期不良暴露滞后 1-2 年。"),
    },
    {
        "id": "KB-I-002", "topic": "industry", "entities": ["白酒", "消费"],
        "tags": ["行业", "消费", "白酒"],
        "content": ("高端白酒的定价权来自品牌与稀缺性,核心跟踪指标:批价(飞天茅台散瓶批价是板块锚)、"
                    "合同负债(经销商打款意愿,是收入的先行指标)、库存周期。"
                    "批价倒挂(市场价 < 出厂价)是行业景气见顶的强烈信号。"),
    },
    {
        "id": "KB-I-003", "topic": "industry", "entities": ["新能源", "光伏", "锂电"],
        "tags": ["行业", "新能源", "产能周期"],
        "content": ("光伏与锂电是典型产能周期行业:技术迭代 + 产能扩张 → 供给过剩 → 价格战 → 全行业亏损 → 出清。"
                    "分析要点:硅料/碳酸锂价格位置、行业开工率、头部企业现金储备能否熬过出清期。"
                    "周期底部特征 = 龙头开始亏损 + 落后产能永久退出。"),
    },
    # ---- 方法论 ----
    {
        "id": "KB-M-001", "topic": "method", "entities": ["研究流程"],
        "tags": ["方法论", "研究框架"],
        "content": ("个股基本面研究标准流程:① 商业模式与行业格局 → ② 财务质量(盈利/现金流/杠杆)"
                    " → ③ 成长驱动 → ④ 估值水平 → ⑤ 风险清单。任何一环缺失,结论可信度都要降级。"),
    },
    {
        "id": "KB-M-002", "topic": "method", "entities": ["风险提示"],
        "tags": ["方法论", "合规", "风险提示"],
        "content": ("涉及个股的研究输出必须附风险提示:数据时点声明 + 假设敏感性声明 + 不构成投资建议。"
                    "这是合规底线,缺一不可。"),
    },
]

_ZH_EN_PUNCT = str.maketrans({"(": " ( ", ")": " ) ", ",": " , ", "，": " , ",
                              "。": " 。 ", "的": " 的 ", "与": " 与 ", "和": " 和 "})
_STOPWORDS = set("的 与 和 在 对 从 被 是 于 by the a an of to for and or".split())


def _tokenize(text: str) -> list[str]:
    text = text.lower().translate(_ZH_EN_PUNCT)
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    return [t for t in tokens if t not in _STOPWORDS]


class KnowledgeEngine:
    """零依赖混合检索:实体命中 + BM25 词频相关度。"""

    def __init__(self, cards: list[dict] | None = None, top_k: int = 4):
        self.cards = cards if cards is not None else KNOWLEDGE_BASE
        self.top_k = top_k
        # 预建索引
        self._doc_tokens = [ _tokenize(c["content"] + " " + "".join(c["tags"])
                                       + " " + " ".join(c["entities"]))
                             for c in self.cards ]
        self._df: Counter = Counter()
        for toks in self._doc_tokens:
            self._df.update(set(toks))
        self._n = len(self.cards)
        self._avgdl = sum(len(t) for t in self._doc_tokens) / max(1, self._n)

    # ---- 检索 ----
    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        k = top_k or self.top_k
        # 查询重写:剥离指令性前缀("请/帮我/查询/分析一下"),保留实体与主题词,
        # 显著提高对自然语言任务的命中率
        cleaned = re.sub(r"^(请|帮我|麻烦|麻烦你|帮我)?\s*(查询|查一下|看看|分析|分析一下|研究|研究一下|评估|了解一下|检索)\s*",
                         "", query.strip())
        cleaned = re.sub(r"[??。!,，]", " ", cleaned)
        q = cleaned if len(cleaned) >= 4 else query
        q_tokens = set(_tokenize(q))
        if not q_tokens:
            return []
        scored: list[tuple[float, dict]] = []
        for card, toks in zip(self.cards, self._doc_tokens):
            tf = Counter(toks)
            score = 0.0
            # 实体精确命中加权(最强的相关性信号)
            for ent in card["entities"]:
                if ent.lower() in q.lower():
                    score += 3.0
            # BM25
            dl = len(toks)
            for t in q_tokens:
                if t not in tf:
                    continue
                idf = math.log(1 + (self._n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                score += idf * (tf[t] * 2.2) / (tf[t] + 1.2 * (0.25 + 0.75 * dl / self._avgdl))
            if score > 0.2:
                scored.append((score, card))
        scored.sort(key=lambda x: -x[0])
        return [{"card": c, "score": round(s, 3)} for s, c in scored[:k]]

    def format_context(self, query: str, top_k: int | None = None) -> str:
        hits = self.search(query, top_k)
        if not hits:
            return "(无相关领域知识)"
        parts = [f"[{h['card']['id']}] ({h['card']['topic']}, score={h['score']}) "
                 f"{h['card']['content']}" for h in hits]
        return "\n".join(parts)

    def stats(self) -> dict:
        topics: Counter = Counter(c["topic"] for c in self.cards)
        return {"cards": self._n, "topics": dict(topics)}

    # ---- 持久化(支持外部扩展知识库) ----
    @classmethod
    def load_from(cls, path: str, top_k: int = 4) -> "KnowledgeEngine":
        with open(path, encoding="utf-8") as f:
            cards = json.load(f)
        return cls(cards=cards, top_k=top_k)

    def dump_to(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.cards, f, ensure_ascii=False, indent=2)
