# -*- coding: utf-8 -*-
"""MedAgent CLI 入口。

用法:
  python -m medagent.cli "任务" [--mode react|cot|self_refine|tot] [--trace]
  python -m medagent.cli --ask "问题"                # 文献指南 Agent(RAG)
  python -m medagent.cli --record "病历文本"          # 病历分析 Agent
  python -m medagent.cli --consult                    # 问诊多轮对话(输入 q 退出)
  python -m medagent.cli --eval [--cases T-M01,T-E01] # 评测集
  python -m medagent.cli --bench [--modes cot,react]  # 推理算法对比实验
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from .config import load_config
from .knowledge.kb import KnowledgeEngine, KNOWLEDGE_BASE_DEFAULT
from .tools.registry import build_registry
from .tools import medical_tools
from .agents.diagnosis import DiagnosisAgent
from .agents.literature import LiteratureAgent
from .agents.record_analyst import MedicalRecordAgent
from .agents.consultation import ConsultationAgent
from .agents.coordinator import CoordinatorAgent
from .runtime.trace import Trace


def make_knowledge(cfg):
    return KnowledgeEngine(cards=KNOWLEDGE_BASE_DEFAULT, top_k=cfg.rag_top_k)


def make_db(cfg=None, index: bool = True):
    """SQLite 医学数据库(向量库/患者库/轨迹库)。失败返回 None,各 Agent 降级。"""
    from .runtime.database import MedicalDatabase
    from .knowledge.guidelines import MEDICAL_KB
    cfg = cfg or load_config()
    try:
        db = MedicalDatabase(os.path.join(cfg.workspace_dir, "medagent.db"))
        if index and db.stats()["cards"] < len(MEDICAL_KB):
            db.index_cards(MEDICAL_KB)
        return db
    except Exception:
        return None


def make_diagnosis(cfg=None):
    cfg = cfg or load_config()
    knowledge = make_knowledge(cfg)
    medical_tools.bind_guideline_engine(knowledge)
    return DiagnosisAgent(cfg, build_registry(), knowledge)


def make_literature(cfg=None):
    cfg = cfg or load_config()
    return LiteratureAgent(cfg, make_knowledge(cfg), database=make_db(cfg))


def make_record(cfg=None):
    cfg = cfg or load_config()
    return MedicalRecordAgent(cfg, make_knowledge(cfg))


def make_coordinator(cfg=None):
    cfg = cfg or load_config()
    knowledge = make_knowledge(cfg)
    medical_tools.bind_guideline_engine(knowledge)
    return CoordinatorAgent(cfg, knowledge, build_registry())


# ------------------------------------------------------------------
def cmd_run(task: str, mode: str, show_trace: bool) -> None:
    agent = make_diagnosis()
    answer, trace = agent.run(task, reasoning=mode)
    print("=" * 72)
    print(answer)
    print("=" * 72)
    if show_trace:
        print(trace.pretty())
    os.makedirs("workspace/traces", exist_ok=True)
    trace.save(f"workspace/traces/diag_{trace.started_ms:.0f}.json")


def cmd_ask(question: str) -> None:
    answer, trace = make_literature().run(question)
    print(answer)
    os.makedirs("workspace/traces", exist_ok=True)
    trace.save(f"workspace/traces/lit_{trace.started_ms:.0f}.json")


def cmd_record(text: str) -> None:
    answer, trace = make_record().run(text)
    print(answer)
    os.makedirs("workspace/traces", exist_ok=True)
    trace.save(f"workspace/traces/rec_{trace.started_ms:.0f}.json")


def cmd_consult(patient_id: str) -> None:
    cfg = load_config()
    agent = ConsultationAgent(cfg, make_knowledge(cfg), patient_id, database=make_db(cfg))
    print("问诊助手已启动(输入 q 退出,输入 save 归档本次会谈到患者档案)")
    while True:
        try:
            user = input("\n患者> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user.lower() == "q":
            break
        if user.lower() == "save":
            summary = agent.save_session()
            print(f"[已归档患者档案 {patient_id}: {summary}]")
            continue
        print("\n助手> " + agent.chat(user))
    print("\n会话结束。")


def cmd_eval(cases: str | None) -> None:
    from .runtime.evaluation import run_medical_suite, MEDICAL_TEST_SUITE
    suite = MEDICAL_TEST_SUITE
    if cases:
        ids = set(cases.split(","))
        suite = [c for c in MEDICAL_TEST_SUITE if c["id"] in ids]
    coord = make_coordinator()
    lit = make_literature()
    rec = make_record()
    agents = {"coordinator": coord.run, "literature": lit.run,
              "record_analyst": rec.run}
    print(f"评测集: {len(suite)} 用例")
    report = run_medical_suite(lambda case: agents[case["agent"]](case["task"]),
                               suite, trace_dir="workspace/traces")
    print("\n---- 指标 ----")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=1))
    print("\n---- 质量门禁 ----")
    for k, v in report["gate"]["details"].items():
        mark = {True: "✓", False: "✗", None: "-"}[v["pass"]]
        print(f"  {mark} {k}: {v['value']} (要求 {v.get('threshold')}) {v['desc']}")
    if report["badcases"]:
        print("\n---- badcase 分析 ----")
        for b in report["badcases"]:
            print(f"  [{b['id']}] {b['type']}/{b['agent']} 失败项: {b['metrics'].get('badcase_reason')}")
    print(f"\n门禁总判定: {'通过 ✅' if report['gate']['overall'] else '未通过 ❌'}")


def cmd_bench(modes: str | None) -> None:
    from .reasoning.benchmark import run_comparison, REASONING_MODES
    ms = modes.split(",") if modes else REASONING_MODES
    print(f"推理算法对比: 模式 {ms}")
    report = run_comparison(lambda mode: make_diagnosis(), modes=ms,
                            trace_dir="workspace/traces/bench")
    print("\n---- 汇总 ----")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))


def cmd_badcase() -> None:
    """轨迹库 badcase 报表(JD#7):失败轨迹 + 用量汇总。"""
    db = make_db(index=False)
    if db is None:
        print("数据库不可用")
        return
    print("---- 数据库统计 ----")
    print(json.dumps(db.stats(), ensure_ascii=False))
    print("\n---- 用量汇总 ----")
    print(json.dumps(db.usage_summary(), ensure_ascii=False))
    print("\n---- badcase(非 passed 轨迹) ----")
    cases = db.badcase_report()
    if not cases:
        print("(无)")
    for b in cases:
        print(f"  #{b['trace_id']} [{b['agent']}/{b['status']}] {b['steps']}步 "
              f"{b['tokens']}tok {b['duration_s']}s | {b['task'][:50]}")
    db.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="medagent")
    ap.add_argument("task", nargs="?")
    ap.add_argument("--mode", default="react", choices=["react", "cot", "self_refine", "tot"])
    ap.add_argument("--ask", help="文献指南 RAG 问答")
    ap.add_argument("--record", help="病历文本解析")
    ap.add_argument("--consult", action="store_true", help="问诊多轮对话")
    ap.add_argument("--patient", default="default", help="问诊患者档案 id")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--badcase", action="store_true", help="轨迹库 badcase 报表")
    ap.add_argument("--cases")
    ap.add_argument("--modes")
    ap.add_argument("--trace", action="store_true")
    args = ap.parse_args()

    if args.eval:
        cmd_eval(args.cases)
    elif args.bench:
        cmd_bench(args.modes)
    elif args.badcase:
        cmd_badcase()
    elif args.ask:
        cmd_ask(args.ask)
    elif args.record:
        cmd_record(args.record)
    elif args.consult:
        cmd_consult(args.patient)
    elif args.task:
        cmd_run(args.task, args.mode, args.trace)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
