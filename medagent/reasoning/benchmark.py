# -*- coding: utf-8 -*-
"""推理算法对比实验:CoT vs ReAct vs Self-Refine vs ToT(JD#4/#8)。

同一批医疗任务分别用四种推理模式跑诊断 Agent,对比:
  - 关键结论正确性(鉴别假设覆盖关键病因/急症识别/就医引导)
  - 指南引用率(证据化程度)
  - token 成本与延迟
方法论复用 mini-dev-team 的基准纪律:任务固定、判分标准统一、如实报告方差。
"""
from __future__ import annotations

import json
import re
from typing import Any

# 对比任务集(覆盖:急症识别/慢性病标准/鉴别方法/用药安全/分诊)
BENCHMARK_TASKS: list[dict[str, Any]] = [
    {
        "id": "B-01", "task": "62岁男性,突发压榨性胸骨后疼痛40分钟,伴大汗,向左臂放射。可能是什么问题?该怎么办?",
        "must_include_any": ["120", "急诊", "立即就医"],           # 急症识别
        "should_include_any": ["冠", "心梗", "心肌", "ACS"],
        "emergency": True,
    },
    {
        "id": "B-02", "task": "体检发现血压 156/98 mmHg,不同日测了三次都差不多。这是高血压吗?严重吗?",
        "should_include_any": ["140", "1级", "一级", "分级"],
    },
    {
        "id": "B-03", "task": "空腹血糖 7.4 mmol/L,糖化血红蛋白 6.9%。能确定是糖尿病吗?",
        "should_include_any": ["7.0", "6.5", "标准"],
    },
    {
        "id": "B-04", "task": "长期吃华法林,现在医生开了克拉霉素,两药能一起用吗?",
        "should_include_any": ["相互作用", "INR", "出血", "避免"],
    },
    {
        "id": "B-05", "task": "反复头痛半年,双侧太阳穴胀痛,工作紧张时加重。可能是什么原因?该挂什么科?",
        "should_include_any": ["紧张", "偏头痛", "神经内科"],
    },
    {
        "id": "B-06", "task": "孩子2岁,发热38.9℃一天,精神还可以,能吃退烧药吗?",
        "should_include_any": ["对乙酰氨基酚", "布洛芬", "退热"],
    },
    {
        "id": "B-07", "task": "老人76岁,长期服用阿司匹林,现在关节痛想自己买布洛芬吃,可以吗?",
        "should_include_any": ["相互作用", "出血", "错开", "对乙酰氨基酚", "咨询"],
    },
    {
        "id": "B-08", "task": "总感到情绪低落、睡不着、对什么都没兴趣两个月了,是哪里出了问题?",
        "should_include_any": ["抑郁", "精神", "心理"],
        "should_ask_any": ["自杀", "就医", "专科"],
    },
]

REASONING_MODES = ["cot", "react", "self_refine", "tot"]


def judge_answer(task: dict, answer: str) -> dict:
    """确定性判分:必含关键词 / 应含关键词 / 急症就医引导 / 指南引用数。"""
    checks: dict[str, Any] = {}
    if "must_include_any" in task:
        checks["must_hit"] = any(k in answer for k in task["must_include_any"])
    if "should_include_any" in task:
        checks["should_hit"] = any(k in answer for k in task["should_include_any"])
    if "should_ask_any" in task:
        checks["should_ask"] = any(k in answer for k in task["should_ask_any"])
    if task.get("emergency"):
        checks["escalation"] = bool(re.search(r"(立即.{0,6}(就医|拨打|120|急诊)|急诊.{0,4}就诊)", answer))
    checks["citations"] = len(set(re.findall(r"\[MG-[A-Za-z]+-\d+\]", answer)))
    passed = all(v for k, v in checks.items() if isinstance(v, bool))
    return {"passed": passed, "checks": checks}


def run_comparison(agent_factory, tasks: list[dict] | None = None,
                   modes: list[str] | None = None, trace_dir: str | None = None) -> dict:
    """agent_factory(mode) -> DiagnosisAgent;逐任务×逐模式运行并判分。"""
    import os
    tasks = tasks or BENCHMARK_TASKS
    modes = modes or REASONING_MODES
    rows: list[dict] = []
    for task in tasks:
        for mode in modes:
            try:
                agent = agent_factory(mode)
                from ..runtime.trace import Trace
                tr = Trace(task["task"])
                _, tr = agent.run(task["task"], tr, reasoning=mode)
                verdict = judge_answer(task, tr.final_answer)
                row = {"task": task["id"], "mode": mode,
                       "passed": verdict["passed"] and tr.status == "passed",
                       "status": tr.status,
                       "tokens": tr.usage.get("total_tokens", 0),
                       "llm_calls": tr.usage.get("llm_calls", 0),
                       "duration_s": getattr(tr, "duration_s", None),
                       "citations": verdict["checks"].get("citations", 0)}
                if trace_dir and tr.status in ("passed", "blocked"):
                    os.makedirs(trace_dir, exist_ok=True)
                    tr.save(os.path.join(trace_dir, f"{task['id']}_{mode}.json"))
            except Exception as e:
                row = {"task": task["id"], "mode": mode, "passed": False,
                       "status": "error", "error": str(e)[:160],
                       "tokens": 0, "llm_calls": 0, "citations": 0}
            rows.append(row)
            print(f"  [{task['id']}] {mode:12s} {'PASS' if row['passed'] else 'FAIL'} "
                  f"tokens={row.get('tokens', 0)}")
    # 汇总
    summary: dict[str, dict] = {}
    for mode in modes:
        mrows = [r for r in rows if r["mode"] == mode]
        n = len(mrows)
        summary[mode] = {
            "pass_rate": round(sum(r["passed"] for r in mrows) / n, 2) if n else None,
            "avg_tokens": int(sum(r.get("tokens", 0) for r in mrows) / n) if n else 0,
            "avg_citations": round(sum(r.get("citations", 0) for r in mrows) / n, 1) if n else 0,
        }
    return {"rows": rows, "summary": summary}
