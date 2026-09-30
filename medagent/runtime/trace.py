# -*- coding: utf-8 -*-
"""可观测性:结构化执行轨迹(Trace)。

每一次 Agent 运行生成一条 Trace,记录:
  - 每一步的 thought / tool_call / tool_result / error
  - LLM 用量(token / 调用次数)
  - 延迟(每步 & 端到端)
  - Review 结论与最终门禁判定

Trace 是调试、评测、回归测试的共同数据底座 —— 问题定位不靠猜,靠轨迹回放。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


def _now_ms() -> float:
    return round(time.time() * 1000, 1)


@dataclass
class Step:
    index: int
    type: str                 # plan / action / observation / review / final / error / system
    thought: str = ""
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        if isinstance(d.get("result"), (dict, list)):
            s = json.dumps(d["result"], ensure_ascii=False)
            d["result_preview"] = s[:400]
        return d


class Trace:
    """一次 Agent 运行的完整轨迹。"""

    def __init__(self, task: str):
        self.task = task
        self.started_ms = _now_ms()
        self.start = time.time()
        self.steps: list[Step] = []
        self.usage: dict[str, int] = {}
        self.gate: dict[str, Any] = {}       # 质量门禁结论
        self.status: str = "running"         # running / passed / blocked / failed
        self.final_answer: str = ""

    # ---- 记录 ----
    def add(self, type_: str, **kw) -> Step:
        step = Step(index=len(self.steps), type=type_, **kw)
        self.steps.append(step)
        return step

    def add_usage(self, usage: dict[str, int]) -> None:
        for k, v in usage.items():
            self.usage[k] = self.usage.get(k, 0) + v

    def finish(self, status: str, final_answer: str = "") -> None:
        self.status = status
        self.final_answer = final_answer
        self.duration_s = round(time.time() - self.start, 2)

    # ---- 输出 ----
    @property
    def duration_ms(self) -> float:
        return round((time.time() - self.start) * 1000, 1)

    def tool_calls(self) -> list[Step]:
        return [s for s in self.steps if s.type == "action"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "duration_s": getattr(self, "duration_s", None) or round(time.time() - self.start, 2),
            "steps": [s.as_dict() for s in self.steps],
            "usage": self.usage,
            "gate": self.gate,
            "final_answer": self.final_answer,
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.as_dict(), f, ensure_ascii=False, indent=2)

    def pretty(self, max_result: int = 160) -> str:
        lines = [f"Trace: {self.task[:80]}", f"status={self.status} steps={len(self.steps)}"]
        for s in self.steps:
            head = f"  [{s.index:02d}] {s.type:11s}"
            if s.type == "action":
                lines.append(f"{head} {s.tool}({json.dumps(s.arguments, ensure_ascii=False)[:120]})")
                if s.error:
                    lines.append(f"         ↳ error: {s.error[:120]}")
                elif s.result is not None:
                    r = json.dumps(s.result, ensure_ascii=False) if not isinstance(s.result, str) else s.result
                    lines.append(f"         ↳ {r[:max_result]}")
            elif s.thought:
                lines.append(f"{head} {s.thought[:160]}")
            elif s.error:
                lines.append(f"{head} ERROR {s.error[:160]}")
        u = self.usage
        lines.append(f"  usage: {u.get('total_tokens', 0)} tokens, {u.get('llm_calls', 0)} llm calls, "
                     f"{getattr(self, 'duration_s', round(time.time() - self.start, 2))}s")
        return "\n".join(lines)
