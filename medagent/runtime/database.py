# -*- coding: utf-8 -*-
"""医学向量数据库与结构化库(SQLite,零依赖,单文件)。

JD#3「医疗向量数据库」与 JD#5「医疗知识库对接」的落地:
  - MedicalVectorStore: SQLite 存知识卡 + 预计算词频向量,支持
    倒排索引检索(关键词召回)+ 向量余弦(词频 TF-IDF 向量)混合 ——
    "医疗向量数据库"的教学级实现,接口与 Milvus/Chroma 对齐。
  - PatientDB: 患者档案/问诊会话/病历/用药记录的结构化存储,
    支持跨会话长记忆查询(JD#2 病历长短记忆管理)。
  - MedTraceStore: Agent 执行轨迹入库,支持 badcase SQL 分析(JD#7)。

所有表自动建库建表,零配置;生产环境可平移到 PG/Milvus,接口不变。
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from collections import Counter
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_cards (
    card_id   TEXT PRIMARY KEY,
    topic     TEXT NOT NULL,
    entities  TEXT NOT NULL,          -- JSON list
    tags      TEXT NOT NULL,          -- JSON list
    content   TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_kb_topic ON kb_cards(topic);

-- 倒排索引:token -> card_id(关键词召回通道)
CREATE TABLE IF NOT EXISTS kb_inverted (
    token   TEXT NOT NULL,
    card_id TEXT NOT NULL,
    tf      REAL NOT NULL,
    PRIMARY KEY (token, card_id)
);
CREATE INDEX IF NOT EXISTS idx_inv_token ON kb_inverted(token);

-- 文档向量(词频 TF-IDF,存储非零维,稀疏向量)
CREATE TABLE IF NOT EXISTS kb_vectors (
    card_id TEXT NOT NULL,
    dim     TEXT NOT NULL,            -- token 作为维度名
    weight  REAL NOT NULL,
    PRIMARY KEY (card_id, dim)
);

-- 文档元数据(向量模长,余弦用)
CREATE TABLE IF NOT EXISTS kb_doc_meta (
    card_id TEXT PRIMARY KEY,
    norm    REAL NOT NULL,
    length  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    profile    TEXT DEFAULT '{}',     -- JSON: 年龄/性别/慢病等
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS consultations (
    consult_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id),
    started_at TEXT DEFAULT (datetime('now', 'localtime')),
    turns      INTEGER DEFAULT 0,
    collected  TEXT DEFAULT '{}',     -- JSON: 采集到的槽位
    summary    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS consult_messages (
    msg_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    consult_id  INTEGER NOT NULL REFERENCES consultations(consult_id),
    role        TEXT NOT NULL,        -- user / assistant
    content     TEXT NOT NULL,
    ts          TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS medical_records (
    record_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  TEXT REFERENCES patients(patient_id),
    source_text TEXT NOT NULL,
    analysis    TEXT DEFAULT '',      -- Agent 解析结果
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS medication_log (
    log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT REFERENCES patients(patient_id),
    drug       TEXT NOT NULL,
    note       TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    task       TEXT NOT NULL,
    agent      TEXT DEFAULT '',
    status     TEXT DEFAULT '',
    steps      INTEGER DEFAULT 0,
    tokens     INTEGER DEFAULT 0,
    duration_s REAL DEFAULT 0,
    gate_json  TEXT DEFAULT '{}',
    answer     TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_trace_status ON agent_traces(status);
"""


_STOP = set("的与和在对待不吗呢啊吧么是了有对于从被")


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    return [t for t in tokens if t not in _STOP]


class MedicalDatabase:
    """医学数据库统一入口:向量知识库 + 患者库 + 轨迹库。"""

    def __init__(self, path: str = "workspace/medagent.db"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._card_count = self._count("kb_cards")

    # ================================================================
    # 知识库(向量 + 倒排混合)
    # ================================================================
    def index_cards(self, cards: list[dict[str, Any]]) -> int:
        """重建/追加知识卡索引:倒排 + TF-IDF 稀疏向量。"""
        n_docs = self._count("kb_cards")
        df: Counter = Counter()
        card_tokens: dict[str, Counter] = {}
        for c in cards:
            toks = _tokenize(c["content"] + " " + "".join(c.get("tags", []))
                             + " " + " ".join(c.get("entities", [])))
            tf = Counter(toks)
            card_tokens[c["id"]] = tf
            df.update(set(tf))
        total = n_docs + len(cards)
        for c in cards:
            cid = c["id"]
            tf = card_tokens[cid]
            self.conn.execute(
                "INSERT OR REPLACE INTO kb_cards VALUES (?,?,?,?,?,datetime('now','localtime'))",
                (cid, c["topic"], json.dumps(c.get("entities", []), ensure_ascii=False),
                 json.dumps(c.get("tags", []), ensure_ascii=False), c["content"]))
            self.conn.execute("DELETE FROM kb_inverted WHERE card_id=?", (cid,))
            self.conn.execute("DELETE FROM kb_vectors WHERE card_id=?", (cid,))
            w: dict[str, float] = {}
            for tok, f in tf.items():
                idf = math.log(1 + (total - df[tok] + 0.5) / (df[tok] + 0.5))
                weight = idf * f
                w[tok] = weight
                self.conn.execute("INSERT INTO kb_inverted VALUES (?,?,?)", (tok, cid, f))
                self.conn.execute("INSERT OR REPLACE INTO kb_vectors VALUES (?,?,?)",
                                  (cid, tok, weight))
            norm = math.sqrt(sum(x * x for x in w.values())) or 1.0
            self.conn.execute("INSERT OR REPLACE INTO kb_doc_meta VALUES (?,?,?)",
                              (cid, norm, len(tf)))
        self.conn.commit()
        self._card_count = self._count("kb_cards")
        return self._card_count

    def vector_search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        """TF-IDF 稀疏向量余弦检索(数据库内 JOIN 计算,不加载全库)。"""
        q_tf = Counter(_tokenize(query))
        if not q_tf:
            return []
        rows = self.conn.execute(
            "SELECT card_id, weight FROM kb_vectors WHERE dim IN (%s)"
            % ",".join("?" * len(q_tf)), tuple(q_tf.keys())).fetchall()
        if not rows:
            return []
        scores: dict[str, float] = {}
        for r in rows:
            scores[r["card_id"]] = scores.get(r["card_id"], 0.0) + r["weight"] * q_tf.get(r["dim"], 0) \
                if "dim" in r.keys() else 0.0
        # 上一行在 Row 上的 dim 取法不可靠,改用显式查询
        scores = {}
        for tok, f in q_tf.items():
            rs = self.conn.execute(
                "SELECT card_id, weight FROM kb_vectors WHERE dim=?", (tok,)).fetchall()
            for r in rs:
                scores[r["card_id"]] = scores.get(r["card_id"], 0.0) + r["weight"] * f
        norms = {r["card_id"]: r["norm"] for r in self.conn.execute("SELECT card_id, norm FROM kb_doc_meta")}
        q_norm = math.sqrt(sum(f * f for f in q_tf.values())) or 1.0
        ranked = sorted(((s / (norms.get(cid, 1.0) * q_norm), cid) for cid, s in scores.items()),
                        reverse=True)[:top_k]
        out = []
        for score, cid in ranked:
            card = self.conn.execute("SELECT * FROM kb_cards WHERE card_id=?", (cid,)).fetchone()
            if card:
                out.append({"card": {"id": cid, "topic": card["topic"],
                                     "entities": json.loads(card["entities"]),
                                     "tags": json.loads(card["tags"]),
                                     "content": card["content"]},
                            "score": round(score, 4), "source": "vector_db"})
        return out

    def keyword_lookup(self, token: str) -> list[dict]:
        """倒排索引通道:包含某 token 的全部卡片。"""
        rows = self.conn.execute(
            "SELECT i.card_id, i.tf, c.topic FROM kb_inverted i "
            "JOIN kb_cards c ON c.card_id=i.card_id WHERE i.token=?", (token,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        return {
            "cards": self._count("kb_cards"),
            "inverted_tokens": self._count("kb_inverted", distinct=True),
            "vector_dims": self._count("kb_vectors"),
            "patients": self._count("patients"),
            "traces": self._count("agent_traces"),
        }

    # ================================================================
    # 患者库(问诊长记忆/病历/用药)
    # ================================================================
    def upsert_patient(self, patient_id: str, profile: dict | None = None) -> None:
        row = self.conn.execute("SELECT profile FROM patients WHERE patient_id=?",
                                (patient_id,)).fetchone()
        merged = json.loads(row["profile"]) if row else {}
        merged.update(profile or {})
        self.conn.execute(
            "INSERT INTO patients(patient_id, profile) VALUES (?,?) "
            "ON CONFLICT(patient_id) DO UPDATE SET profile=excluded.profile, "
            "updated_at=datetime('now','localtime')",
            (patient_id, json.dumps(merged, ensure_ascii=False)))
        self.conn.commit()

    def get_patient(self, patient_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
        if not row:
            return None
        return {"patient_id": row["patient_id"], "profile": json.loads(row["profile"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def start_consultation(self, patient_id: str) -> int:
        cur = self.conn.execute("INSERT INTO consultations(patient_id) VALUES (?)", (patient_id,))
        self.conn.commit()
        return cur.lastrowid

    def log_message(self, consult_id: int, role: str, content: str) -> None:
        self.conn.execute("INSERT INTO consult_messages(consult_id, role, content) VALUES (?,?,?)",
                          (consult_id, role, content[:2000]))
        self.conn.commit()

    def finish_consultation(self, consult_id: int, turns: int, collected: dict, summary: str) -> None:
        self.conn.execute(
            "UPDATE consultations SET turns=?, collected=?, summary=? WHERE consult_id=?",
            (turns, json.dumps(collected, ensure_ascii=False), summary[:500], consult_id))
        self.conn.commit()

    def patient_history(self, patient_id: str, limit: int = 5) -> list[dict]:
        """跨会话长记忆:该患者的历史会话摘要 + 病历 + 用药。"""
        consults = self.conn.execute(
            "SELECT started_at, summary, collected FROM consultations "
            "WHERE patient_id=? ORDER BY consult_id DESC LIMIT ?",
            (patient_id, limit)).fetchall()
        records = self.conn.execute(
            "SELECT created_at, source_text FROM medical_records "
            "WHERE patient_id=? ORDER BY record_id DESC LIMIT ?", (patient_id, limit)).fetchall()
        meds = self.conn.execute(
            "SELECT drug, note, created_at FROM medication_log "
            "WHERE patient_id=? ORDER BY log_id DESC LIMIT ?", (patient_id, limit)).fetchall()
        return {"consultations": [dict(c) for c in consults],
                "records": [dict(r) for r in records],
                "medications": [dict(m) for m in meds]}

    def save_record(self, patient_id: str, source_text: str, analysis: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO medical_records(patient_id, source_text, analysis) VALUES (?,?,?)",
            (patient_id, source_text[:8000], analysis[:8000]))
        self.conn.commit()
        return cur.lastrowid

    def log_medication(self, patient_id: str, drug: str, note: str = "") -> None:
        self.conn.execute("INSERT INTO medication_log(patient_id, drug, note) VALUES (?,?,?)",
                          (patient_id, drug, note[:500]))
        self.conn.commit()

    # ================================================================
    # 轨迹库(badcase SQL 分析)
    # ================================================================
    def save_trace(self, task: str, agent: str, status: str, steps: int,
                   tokens: int, duration_s: float, gate: dict, answer: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO agent_traces(task, agent, status, steps, tokens, duration_s, gate_json, answer) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (task[:300], agent, status, steps, tokens, duration_s,
             json.dumps(gate, ensure_ascii=False), answer[:6000]))
        self.conn.commit()
        return cur.lastrowid

    def badcase_report(self) -> list[dict]:
        """失败/blocked 轨迹清单(JD#7 badcase 分析的数据底座)。"""
        rows = self.conn.execute(
            "SELECT trace_id, agent, status, steps, tokens, duration_s, task "
            "FROM agent_traces WHERE status NOT IN ('passed') "
            "ORDER BY trace_id DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]

    def usage_summary(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) n, SUM(tokens) tokens, AVG(duration_s) avg_dur, "
            "SUM(status='passed') passed FROM agent_traces").fetchone()
        return dict(row) if row else {}

    # ================================================================
    def _count(self, table: str, distinct: bool = False) -> int:
        sql = f"SELECT COUNT({'DISTINCT token' if distinct else '*'}) FROM {table}"
        return self.conn.execute(sql).fetchone()[0]

    def close(self) -> None:
        self.conn.close()
