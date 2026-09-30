# -*- coding: utf-8 -*-
"""问诊 Agent:多轮对话状态维护 + 长短记忆管理(JD#2)。

短记忆:会话内 N 轮对话原文(窗口截断)。
长记忆:跨会话的"患者档案"(按 patient_id 文件持久化),新会话自动注入。
急症中断:命中红旗征 → 立即中断采集,优先就医指令。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from ..config import MedAgentConfig
from ..knowledge.rules import EMERGENCY_PATTERNS
from ..runtime.llm import LLMClient
from ..runtime.trace import Trace
from .prompts import CONSULTATION_SYSTEM


class ConsultationAgent:
    def __init__(self, cfg: MedAgentConfig, knowledge, patient_id: str = "default",
                 database=None):
        self.cfg = cfg
        self.knowledge = knowledge
        self.llm = LLMClient(cfg)
        self.patient_id = patient_id
        self.memory_dir = os.path.join(cfg.workspace_dir, "patients")
        os.makedirs(self.memory_dir, exist_ok=True)
        self.history: list[dict[str, str]] = []   # 短记忆
        self.collected: dict[str, str] = {}       # 已采集信息槽
        # 结构化数据库(可选注入;不传则回落文件记忆)
        self.db = database
        self.consult_id = None
        if self.db is not None:
            try:
                profile = {"chronic": [], "note": "档案由问诊创建"}
                self.db.upsert_patient(patient_id, profile)
                self.consult_id = self.db.start_consultation(patient_id)
            except Exception:
                self.db = None  # DB 故障降级为文件记忆,不阻塞问诊

    # ------------------------------------------------------------------
    # 槽位抽取(确定性 + LLM 混合:关键词抽明显槽,遗漏由 LLM 下一轮补)
    SLOTS = ["主诉", "持续时间", "伴随症状", "既往史", "用药史", "过敏史"]

    def _extract_slots(self, text: str) -> None:
        for slot, pat in [
            ("持续时间", r"(持续|已经有|约)\s*(\d+\s*[天日月年小时]+)"),
            ("过敏史", r"(过敏|青霉素过敏|药物过敏)"),
            ("既往史", r"(既往|有\s*\S+\s*病史|高血压史|糖尿病史)"),
            ("用药史", r"(服用|在吃|用了)\s*\S+"),
        ]:
            m = re.search(pat, text)
            if m and slot not in self.collected:
                self.collected[slot] = m.group(0)[:60]

    def _emergency_check(self, text: str) -> list[str]:
        return [p for p in EMERGENCY_PATTERNS if p in text]

    # ------------------------------------------------------------------
    def chat(self, user_message: str) -> str:
        self._extract_slots(user_message)
        emergency = self._emergency_check(user_message)
        if emergency:
            reply = ("⚠️ 您描述的情况属于急症信号,请立即拨打 120 或前往急诊科!"
                     "\n就医时请告知:症状开始时间、当前用药、过敏史。\n\n本对话不构成医疗建议。")
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": reply})
            return reply

        # 组装上下文:短记忆窗口 + 长记忆档案 + 已采集槽位
        short = self.history[-self.cfg.max_history_turns:]
        long_mem = self._load_long_memory()
        system = CONSULTATION_SYSTEM.format(
            collected=json.dumps(self.collected, ensure_ascii=False) if self.collected else "(暂无)",
            history=("\n".join(f"[{m['role']}] {m['content'][:100]}" for m in short[-6:]) or "(首轮)"),
        )
        if long_mem:
            system += "\n## 患者档案(长记忆,注意时效)\n" + "\n".join(long_mem)

        messages = [{"role": "system", "content": system}] + short + \
                   [{"role": "user", "content": user_message}]
        try:
            r = self.llm.chat(messages)
            reply = r.content.strip()
        except Exception as e:
            reply = f"(系统繁忙,请稍后重试或直接就医。{e})"

        # 若本轮用户消息含主诉且尚未记录 → 沉淀主诉槽
        if "主诉" not in self.collected and len(user_message) > 6:
            self.collected["主诉"] = user_message[:80]
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": reply})
        # 结构化库:消息级落库(问诊全程可回放)
        if self.db is not None and self.consult_id is not None:
            try:
                self.db.log_message(self.consult_id, "user", user_message)
                self.db.log_message(self.consult_id, "assistant", reply)
            except Exception:
                pass
        return reply

    # ------------------------------------------------------------------
    # 长记忆:双通道 —— SQLite 结构化库优先,文件回落
    def save_session(self) -> str:
        summary = " | ".join(f"{k}:{v}" for k, v in list(self.collected.items())[:4])
        if self.db is not None:
            try:
                if self.consult_id is not None:
                    self.db.finish_consultation(self.consult_id, len(self.history),
                                                self.collected, summary)
                # 档案合并:采集到的既往史/过敏史写回患者 profile
                profile_update = {k: v for k, v in self.collected.items()
                                  if k in ("既往史", "过敏史", "用药史")}
                if profile_update:
                    self.db.upsert_patient(self.patient_id, profile_update)
                return summary
            except Exception:
                pass  # DB 失败回落文件
        # 文件通道
        path = self._path()
        data = self._load_file()
        entry = {
            "id": f"m{int(time.time()*1000)%10**10:010d}",
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "collected": self.collected,
            "turns": len(self.history),
            "last_summary": summary,
        }
        data.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return summary

    def _load_long_memory(self, limit: int | None = None) -> list[str]:
        limit = limit or self.cfg.max_long_memory
        lines: list[str] = []
        if self.db is not None:
            try:
                hist = self.db.patient_history(self.patient_id, limit)
                for c in hist.get("consultations", []):
                    if c.get("summary"):
                        lines.append(f"- [{c['started_at']}] 问诊: {c['summary']}")
                for m in hist.get("medications", []):
                    lines.append(f"- [{m['created_at']}] 用药: {m['drug']} {m.get('note','')}")
                for r in hist.get("records", []):
                    lines.append(f"- [{r['created_at']}] 病历: {r['source_text'][:60]}")
                if lines:
                    return lines[-limit:]
            except Exception:
                pass  # DB 失败回落文件
        data = self._load_file()[-limit:]
        return [f"- [{d['ts']}] {d.get('last_summary', '')}" for d in data]

    def _path(self) -> str:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", self.patient_id)
        return os.path.join(self.memory_dir, f"{safe}.json")

    def _load_file(self) -> list[dict]:
        if not os.path.exists(self._path()):
            return []
        try:
            with open(self._path(), encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
