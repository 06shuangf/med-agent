# -*- coding: utf-8 -*-
"""诊断推理 Agent(旗舰):ReAct 工具循环 + CoT/Self-Refine/ToT 可切换推理模式。

这是 JD#1(核心算法:任务规划/诊疗路径推理/自主决策/反思纠错)与
JD#4(ReAct/CoT/Self-Refine/ToT 落地)的主实现。
复用 FinResearchAgent 的执行链(工具循环/观测压缩/预算熔断/trace),
叠加医疗合规门禁(比金融更严:红线全 block 级 + 急症就医引导强制)。
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import MedAgentConfig
from ..knowledge.rules import EMERGENCY_PATTERNS, MEDICAL_COMPLIANCE_RULES
from ..runtime.llm import LLMClient
from ..runtime.trace import Trace
from ..tools.registry import ToolRegistry
from .prompts import DIAGNOSIS_SYSTEM, REASONING_METHOD_SUFFIX


class MedicalBudgetExceeded(RuntimeError):
    pass


class DiagnosisAgent:
    """诊断推理 Agent。reasoning: react(默认)/cot/self_refine/tot。"""

    def __init__(self, cfg: MedAgentConfig, registry: ToolRegistry, knowledge):
        self.cfg = cfg
        self.registry = registry
        self.knowledge = knowledge
        self.llm = LLMClient(cfg)
        self.reviewer = LLMClient(cfg, model=cfg.reviewer_model)

    # ------------------------------------------------------------------
    def run(self, task: str, trace: Trace | None = None,
            reasoning: str = "react") -> tuple[str, Trace]:
        trace = trace or Trace(task)
        # 急症预检:症状文本命中红旗 → 系统层强制置顶就医指令
        emergency_hits = [p for p in EMERGENCY_PATTERNS if p in task]
        if reasoning not in REASONING_METHOD_SUFFIX:
            reasoning = "react"
        system = (DIAGNOSIS_SYSTEM.format(guidelines=self.knowledge.format_context(task))
                  + REASONING_METHOD_SUFFIX[reasoning])
        if emergency_hits:
            system += ("\n## 系统预检(最高优先级)\n用户描述命中急症信号: "
                       + ", ".join(emergency_hits)
                       + "。回答开头必须立即给出'立即拨打120或前往急诊'指令。")
        trace.add("system", thought=f"模式={reasoning} 急症预检={'命中:' + ','.join(emergency_hits) if emergency_hits else '无'} "
                                     f"RAG命中={len(self.knowledge.search(task))} 卡")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]

        final_text = ""
        reflect_done = 0
        for step_i in range(self.cfg.max_steps):
            self._check_budget(trace)
            result = self.llm.chat(messages, tools=self.registry.specs())
            trace.add_usage(result.usage.as_dict())

            if result.tool_calls:
                messages.append({"role": "assistant",
                                 "content": result.content or None,
                                 "tool_calls": [self._raw_tc(tc) for tc in result.tool_calls]})
                import concurrent.futures as cf
                tcs = result.tool_calls
                if self.cfg.max_steps and len(tcs) > 1:
                    with cf.ThreadPoolExecutor(max_workers=min(4, len(tcs))) as ex:
                        obs_list = list(ex.map(self._exec_one, tcs))
                else:
                    obs_list = [self._exec_one(tc) for tc in tcs]
                for tc, obs in zip(tcs, obs_list):
                    compact = self._record(tc, obs, trace)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": compact})
                continue

            candidate = result.content.strip()
            # Self-Refine 模式:一次自我审查修订
            if reasoning == "self_refine" and reflect_done < self.cfg.max_reflect_loops:
                reflect_done += 1
                refined = self._self_refine(task, candidate, trace)
                if refined:
                    candidate = refined
            # Review(所有模式):独立审阅
            review = self._review(task, candidate, trace)
            if review["verdict"] == "pass":
                gate = self.compliance_gate(candidate, emergency=bool(emergency_hits))
                trace.gate = gate
                if gate["blocked"]:
                    trace.finish("blocked", gate["revised"])
                    return trace.final_answer, trace
                trace.finish("passed", gate["revised"])
                return trace.final_answer, trace
            if step_i >= self.cfg.max_steps - 1:
                break
            messages.append({"role": "user",
                             "content": "【审阅意见】上一稿存在以下问题,请修正后重答:\n"
                                        + review["issues_text"] + "\n如需补充数据请继续调用工具。"})
            trace.add("replan", thought="Review 未通过: " + review["issues_text"][:160])

        if not final_text:
            final_text = candidate or "任务未能在预算内完成,已收集数据见 trace。"
        gate = self.compliance_gate(final_text, emergency=bool(emergency_hits))
        trace.gate = gate
        trace.finish("failed" if gate["blocked"] else "passed", gate["revised"] or final_text)
        return trace.final_answer, trace

    # ------------------------------------------------------------------
    # Self-Refine:自我审查 + 修订(一步)
    def _self_refine(self, task: str, draft: str, trace: Trace) -> str | None:
        prompt = ("你是临床思维审阅官。审查下面的鉴别分析草稿,找出:证据不足/过度解读/"
                  "漏掉致命病因/表述违反合规(确定诊断、处方剂量)四类问题。"
                  "若无问题输出 ORIGINAL;有问题则直接输出修订后的完整版本(不要解释)。\n\n"
                  f"【任务】{task}\n\n【草稿】\n{draft[:6000]}")
        try:
            r = self.reviewer.chat([{"role": "user", "content": prompt}])
            trace.add_usage(r.usage.as_dict())
            out = r.content.strip()
            if out and out != "ORIGINAL" and len(out) > len(draft) * 0.4:
                trace.add("self_refine", thought=f"草稿 {len(draft)} 字 → 修订 {len(out)} 字")
                return out
            trace.add("self_refine", thought="审查通过,保留原稿")
        except Exception as e:
            trace.add("self_refine", error=f"Self-Refine 故障(降级原稿): {e}")
        return None

    # ------------------------------------------------------------------
    # 独立 Review
    def _review(self, task: str, answer: str, trace: Trace) -> dict:
        obs_digest = "\n".join(
            f"- [{s.tool}] {s.result if isinstance(s.result, str) else json.dumps(s.result, ensure_ascii=False)[:160]}"
            for s in trace.steps if s.type == "observation")[:1600]
        prompt = ("你是医疗输出质量审阅官,按四维审查:1)证据性:结论是否有症状/检验/指南依据;"
                  "2)合规:无确定诊断、无处方剂量、急症有就医引导;3)结构:五段齐全"
                  "(就医建议/鉴别分析/数据解读/补充信息/声明);4)完整性:真正回答了任务。\n"
                  "输出 JSON:{\"verdict\":\"pass\"|\"fail\",\"issues\":[...]}\n\n"
                  f"【任务】{task}\n\n【工具观测摘要】\n{obs_digest}\n\n【待审】\n{answer[:5000]}")
        try:
            r = self.reviewer.chat([{"role": "user", "content": prompt}])
            trace.add_usage(r.usage.as_dict())
            m = re.search(r"\{.*\}", r.content, re.DOTALL)
            data = json.loads(m.group(0)) if m else {"verdict": "pass", "issues": []}
        except Exception as e:
            data = {"verdict": "pass", "issues": []}
            trace.add("review", error=f"reviewer 故障(降级放行): {e}")
        issues = [str(i) for i in data.get("issues", [])][:5]
        trace.add("review", thought=f"verdict={data.get('verdict')} issues={len(issues)}",
                  result={"issues": issues})
        return {"verdict": data.get("verdict", "pass"), "issues": issues,
                "issues_text": "\n".join(f"- {i}" for i in issues) or "(无)"}

    # ------------------------------------------------------------------
    # 医疗合规门禁(硬规则,最后防线)
    def compliance_gate(self, answer: str, emergency: bool = False) -> dict:
        violations: list[dict] = []
        for rule in MEDICAL_COMPLIANCE_RULES:
            if rule.get("pattern"):
                hit = None
                for m in re.finditer(rule["pattern"], answer):
                    ws = max(0, m.start() - rule.get("negation_window", 0))
                    prefix = answer[ws:m.start()]
                    if any(neg in prefix for neg in rule.get("negations", [])):
                        continue
                    # 后文驳斥检查:'不用去医院是不行的/不可以' —— 否定词出现在
                    # 命中段右侧,同样属于驳斥表述而非劝阻就医
                    suffix = answer[m.end():m.end() + rule.get("negation_window", 0)]
                    if any(neg in suffix for neg in rule.get("suffix_negations", [])):
                        continue
                    hit = m.group(0)
                    break
                if hit:
                    violations.append({"id": rule["id"], "name": rule["name"],
                                       "severity": rule["severity"], "hit": hit,
                                       "msg": rule["message"]})
            if rule.get("require") == "emergency_escalation" and emergency:
                if not re.search(r"(立即.{0,6}(就医|拨打|120|急诊)|急诊.{0,4}就诊|马上去医院)", answer):
                    violations.append({"id": "MR-003", "name": "急症缺就医引导",
                                       "severity": "block", "hit": "",
                                       "msg": rule["message"]})
            if rule.get("require") == "medical_disclaimer" and "声明" not in answer and "不构成医疗建议" not in answer:
                violations.append({"id": "MR-004", "name": "缺免责声明",
                                   "severity": "block", "hit": "",
                                   "msg": rule["message"]})
            if rule.get("require") == "citation_note" and "[MG-" not in answer:
                violations.append({"id": "MR-006", "name": "缺指南引用",
                                   "severity": "warn", "hit": "",
                                   "msg": rule["message"]})
        blocked = any(v["severity"] == "block" for v in violations)
        revised = answer
        if blocked:
            revised = ("该回答未通过医疗合规门禁,已拦截。违规项: "
                       + "; ".join(f"{v['id']} {v['name']}" for v in violations if v["severity"] == "block")
                       + "\n\n如遇急症请立即拨打 120。本系统不替代执业医师面诊。")
        return {"blocked": blocked, "violations": violations, "revised": revised}

    # ------------------------------------------------------------------
    # 工具执行(复用 FinResearchAgent 的压缩/容错模式)
    def _exec_one(self, tc: dict) -> Any:
        import time
        t0 = time.time()
        try:
            fn = self.registry.get(tc["name"])
            obs = fn(**tc["arguments"]) if isinstance(tc["arguments"], dict) else {"error": "参数必须是对象"}
        except TypeError as e:
            obs = {"error": f"参数不匹配: {e}", "hint": "检查参数名与类型"}
        except Exception as e:
            obs = {"error": str(e)}
        lat = round((time.time() - t0) * 1000, 1)
        if isinstance(obs, dict):
            obs["_latency_ms"] = lat
        else:
            obs = {"_raw": obs, "_latency_ms": lat}
        return obs

    def _record(self, tc: dict, obs: Any, trace: Trace) -> str:
        lat = obs.pop("_latency_ms", 0.0) if isinstance(obs, dict) else 0.0
        err = str(obs.get("error", "")) if isinstance(obs, dict) else ""
        compact = json.dumps(obs, ensure_ascii=False)[:1500]
        trace.add("action", tool=tc["name"], arguments=tc["arguments"], result=obs,
                  error=err, latency_ms=lat)
        trace.add("observation", result=compact)
        return compact

    def _check_budget(self, trace: Trace) -> None:
        if trace.usage.get("total_tokens", 0) > self.cfg.max_total_tokens:
            raise MedicalBudgetExceeded("token 预算熔断")

    @staticmethod
    def _raw_tc(tc: dict) -> dict:
        return {"id": tc["id"], "type": "function",
                "function": {"name": tc["name"],
                             "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
