# -*- coding: utf-8 -*-
"""公共层：路径、中文数字、CSV 读取、SQLite schema。仅依赖标准库。"""
import csv
import io
import os
import re
import sqlite3

BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(BUILD_DIR)
CORPUS_DIR = os.path.join(PROJ_DIR, "corpus")
RAW_DIR = os.path.join(BUILD_DIR, "data", "raw")
OUT_DIR = os.path.join(BUILD_DIR, "out")
DB_PATH = os.path.join(OUT_DIR, "kg.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS doc;
CREATE TABLE doc (
  doc_id      TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  doc_type    TEXT,
  hierarchy   TEXT,
  issuer      TEXT,
  valid_from  TEXT,          -- ISO 日期；NULL = 无明确起点
  valid_to    TEXT,          -- NULL = 截至观察时点仍有效
  status      TEXT,
  source      TEXT
);

DROP TABLE IF EXISTS provision;
CREATE TABLE provision (
  prov_id     TEXT PRIMARY KEY,   -- L-001#21-2
  doc_id      TEXT NOT NULL,
  article_no  INTEGER,
  article_cn  TEXT,
  para_no     INTEGER DEFAULT 1,
  heading     TEXT,
  text        TEXT NOT NULL,
  is_proviso  INTEGER DEFAULT 0,  -- 含"但是/但"的但书款
  FOREIGN KEY (doc_id) REFERENCES doc(doc_id)
);

DROP TABLE IF EXISTS norm;
CREATE TABLE norm (
  norm_id        TEXT PRIMARY KEY,  -- L-001#21-1-N1
  prov_id        TEXT NOT NULL,
  doc_id         TEXT NOT NULL,
  sent_no        INTEGER,
  text           TEXT NOT NULL,
  deontic        TEXT,              -- obligation|prohibition|power|advocacy|definition|referral|declarative|n/a
  modality       TEXT,
  subject_text   TEXT,
  action_text    TEXT,
  object_text    TEXT,
  condition_text TEXT,
  is_negative    INTEGER DEFAULT 0, -- 元条款（目的/适用范围/生效/冲突规则）不建 Norm
  counts_as_duty INTEGER DEFAULT 1, -- 倡导/定义/援引不计入职责矩阵
  extractor      TEXT,
  confidence     REAL,
  FOREIGN KEY (prov_id) REFERENCES provision(prov_id)
);

DROP TABLE IF EXISTS entity;
CREATE TABLE entity (
  ent_id  TEXT PRIMARY KEY,
  etype   TEXT NOT NULL,      -- Agency|Institution|CoordDimension|Watershed|Activity
  name    TEXT NOT NULL,
  UNIQUE (etype, name)
);

DROP TABLE IF EXISTS edge;
CREATE TABLE edge (
  src_id     TEXT NOT NULL,
  rel        TEXT NOT NULL,
  dst_id     TEXT NOT NULL,
  prov_id    TEXT,
  evidence   TEXT,
  confidence REAL,
  PRIMARY KEY (src_id, rel, dst_id, prov_id)
);

DROP TABLE IF EXISTS issue;
CREATE TABLE issue (
  code     TEXT,       -- TIME_INCONSISTENT|DANGLING_REFERRAL|ORPHAN_NORM|DUTY_GAP
  severity TEXT,       -- ERROR|WARN|INFO
  message  TEXT,
  ref      TEXT
);

CREATE INDEX idx_norm_doc ON norm(doc_id);
CREATE INDEX idx_norm_deontic ON norm(deontic);
CREATE INDEX idx_prov_doc ON provision(doc_id);
CREATE INDEX idx_edge_rel ON edge(rel);
"""

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn2int(s: str) -> int:
    """'二十一' -> 21, '一百零八' -> 108"""
    total, cur = 0, 0
    for ch in s:
        if ch in CN_DIGITS:
            cur = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            total += (cur or 1) * CN_UNITS[ch]
            cur = 0
        else:
            raise ValueError("非中文数字: %r" % s)
    return total + cur


def read_csv(name: str):
    path = os.path.join(CORPUS_DIR, name)
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def connect(fresh: bool = True) -> sqlite3.Connection:
    os.makedirs(OUT_DIR, exist_ok=True)
    if fresh and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    if fresh:
        con.executescript(SCHEMA)
    return con


def d_minus_one(iso: str) -> str:
    """'2026-08-15' -> '2026-08-14'（废止日的前一日为效力终点）"""
    import datetime
    d = datetime.date.fromisoformat(iso)
    return (d - datetime.timedelta(days=1)).isoformat()


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def norm_space(s: str) -> str:
    return re.sub(r"\s+", "", s or "")
