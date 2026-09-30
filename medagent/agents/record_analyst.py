# -*- coding: utf-8 -*-
"""病历分析 Agent:结构化解析 + 完整度校验 + 一致性检查(JD#7 病历解析完整度)。"""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import MedAgentConfig
from ..runtime.llm import LLMClient
from ..runtime.trace import Trace
from .prompts import MEDICAL_RECORD_SYSTEM

REQUIRED_FIELDS = ["主诉", "现病史", "既往史", "过敏史", "用药"]


class MedicalRecordAgent:
    def __init__(self, cfg: MedAgentConfig, knowledge):
        self.cfg = cfg
        self.knowledge = knowledge
        self.llm = LLMClient(cfg)

    def run(self, record_text: str, trace: Trace | None = None) -> tuple[str, Trace]:
        trace = trace or Trace("病历分析")
        try:
            r = self.llm.chat([
                {"role": "system", "content": MEDICAL_RECORD_SYSTEM},
                {"role": "user", "content": f"【病历文本】\n{record_text[:8000]}"},
            ])
            trace.add_usage(r.usage.as_dict())
        except Exception as e:
            trace.finish("failed", f"LLM 调用失败: {e}")
            return trace.final_answer, trace
        answer = r.content.strip()

        # 确定性完整度校验(不依赖 LLM):必备字段在病历原文中的粗检
        missing_in_source = [f for f in REQUIRED_FIELDS if f not in record_text]
        trace.add("completeness_check",
                  result={"source_missing_fields": missing_in_source})
        if missing_in_source:
            note = ("\n\n### 系统校验补充(确定性检查)\n病历原文未出现字段: "
                    + "、".join(missing_in_source)
                    + ";解析结果基于现有文本,缺失项已在上方校验中列出。")
            if "系统校验补充" not in answer:
                answer += note
        if "### 声明" not in answer:
            answer += "\n\n### 声明\n解析仅供临床工作流参考,不构成医疗建议。"
        trace.finish("passed", answer)
        return answer, trace
