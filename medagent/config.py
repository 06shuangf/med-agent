# -*- coding: utf-8 -*-
"""医疗 Agent 系统全局配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class MedAgentConfig:
    # ---- LM ----
    api_base: str = "https://token.pixdance.top/v1"
    api_key_env: str = "PIXDANCE_CHAT_KEY"
    model: str = "glm-5.3"            # 主模型
    debug_model: str = "glm-5.3-flash"  # 开发调试用便宜模型
    reviewer_model: str = "glm-5.3"   # 审阅/仲裁模型
    temperature: float | None = None  # None=不传(兼容只支持默认值的模型)

    # ---- ReAct 循环 ----
    max_steps: int = 8
    max_retries_per_tool: int = 1
    max_reflect_loops: int = 1        # Self-Refine 轮数
    tot_branches: int = 3             # ToT 分支宽度
    tot_depth: int = 2                # ToT 探索深度

    # ---- 记忆 ----
    max_history_turns: int = 12       # 问诊短记忆窗口
    max_long_memory: int = 5          # 病历长记忆召回条数

    # ---- RAG ----
    rag_top_k: int = 4

    # ---- 成本门禁 ----
    max_total_tokens: int = 80000

    # ---- 路径 ----
    workspace_dir: str = "workspace"

    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"环境变量 {self.api_key_env} 未设置")
        return key


def load_config(**overrides) -> MedAgentConfig:
    cfg = MedAgentConfig()
    for k, v in overrides.items():
        if not hasattr(cfg, k):
            raise ValueError(f"未知配置项: {k}")
        setattr(cfg, k, v)
    return cfg
