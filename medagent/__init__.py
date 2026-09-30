# -*- coding: utf-8 -*-
"""MedAgent — 医疗多智能体系统

JD 能力映射:
  agents/         问诊/诊断推理/文献指南/病历分析/协调合规 五个智能体
  knowledge/      医疗知识工程(临床指南知识卡 + 医疗规则库)
  tools/          医学工具链(指标解读/药物相互作用/指南检索/分诊推荐)
  reasoning/      推理算法实现(ReAct/CoT/Self-Refine/ToT)
  runtime/        系统工程化(LLM客户端/Trace/评测体系)
"""

from .config import MedAgentConfig, load_config

__all__ = ["MedAgentConfig", "load_config"]
