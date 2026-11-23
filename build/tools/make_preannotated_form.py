# -*- coding: utf-8 -*-
"""生成**预标注（model-assisted）双标注表单**：一半预标注、一半空白。

设计（见 paper/04_标注协议与IAA方案.md §六）：
- 60 查询按类别分层随机分成两组各 30：
  * **组 A（预标注）**：填入现有金标答案 + 竞争候选（检索器 top-10 中不在金标里的前 3 条），
    标注者只需"接受 / 修改 / 换选"，成本约 5 分钟/条；
  * **组 B（空白）**：完全空白，标注者从零独立标注，成本约 20 分钟/条。
- 组 B 的 κ = **干净的 IAA**（可写入论文）；组 A 的 κ = **锚定条件下的 IAA**；
  两组之差 = 预标注的锚定效应量（这是本设计的额外产出，不是缺陷）。
- 分层随机：六类各 10 条，每类随机 5 条进 A 组、5 条进 B 组（seed 固定可复现）。

输出：corpus/15_预标注表单_待审核.csv
用法：python tools/make_preannotated_form.py
"""
import csv
import glob
import io
import os
import random
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
PROJ = os.path.dirname(BUILD)
sys.path.insert(0, BUILD)
import common as C
import s4_reason

OUT = os.path.join(PROJ, "corpus", "15_预标注表单_待审核.csv")
B0, B1 = "2026-06-30", "2026-11-06"
SEED = 2026

FIELDS = ["query_id", "group", "category", "watershed", "region",
          "regulated_conduct", "b0_prov_ids", "b0_basis", "b1_prov_ids",
          "b1_basis", "flip", "answer_type", "verify_note",
          "alt_candidates", "review_action", "review_note"]


def load_all():
    rows = []
    for p in sorted(glob.glob(os.path.join(PROJ, "corpus", "*查询集*.csv"))):
        if os.path.basename(p).startswith(("12_", "13_", "15_")):
            continue
        for r in csv.DictReader(io.open(p, encoding="utf-8-sig")):
            rows.append(r)
    rows.sort(key=lambda x: x["query_id"])
    assert len(rows) == 60, len(rows)
    return rows


def split_by_category(rows):
    """六类各 10 条，每类随机 5 条进 A 组。"""
    rnd = random.Random(SEED)
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    group = {}
    for cat, items in by_cat.items():
        assert len(items) == 10, (cat, len(items))
        idx = list(range(len(items)))
        rnd.shuffle(idx)
        for i, it in enumerate(items):
            group[it["query_id"]] = "A" if i in idx[:5] else "B"
    return group


def parse_ids(field):
    field = (field or "").strip()
    if not field or field in ("(none)", "none", "-"):
        return []
    return [x.strip() for x in field.split(";") if x.strip()]


def article(pid):
    if "#" not in pid:
        return pid
    d, rest = pid.split("#", 1)
    return d + "#" + rest.split("-")[0]


def snippet(con, pid, query=""):
    """条文摘要：围绕查询词定位取窗口，而不是死取开头。

    取开头会把关键词落在条文中后段的候选（如 L-017#26-1 的"负责监测…省界水体"
    在第 60 字之后）截成无关片段，审核者无从判断。故先定位查询 bigram 的首个命中
    位置，以其为锚取 ±窗口；找不到命中时退回开头。
    """
    r = con.execute("SELECT text FROM provision WHERE prov_id=?", (pid,)).fetchone()
    if not r:
        return ""
    t = (r[0] or "").replace("\n", " ").strip()
    q = re.sub(r"[的与和及、，。；\s]", "", query or "")
    hit = -1
    for i in range(len(q) - 1):
        j = t.find(q[i:i + 2])
        if j >= 0:
            hit = j
            break
    if hit < 0:
        return t[:40] + ("…" if len(t) > 40 else "")
    lo = max(0, hit - 10)
    hi = min(len(t), hit + 34)
    return ("…" if lo > 0 else "") + t[lo:hi] + ("…" if hi < len(t) else "")


def alts(con, q, as_of, gold_ids, hint):
    """检索器 top-10 中排除金标（条级）后的前 3 条竞争候选。

    候选池取 top-10 而非 top-5：实测检索器对"监测/水"等通用 bigram 的噪声命中
    （罚则列举、防渗条款）常排在专门条款前面，池太小会把真正有价值的竞争者
    （如 Q01 B0 的 L-017#26-1，排第 3）挤出候选列。
    """
    gold_art = {article(x) for x in gold_ids}
    out = []
    try:
        got = s4_reason.find_law(con, q, as_of, k=10, basin_hint=hint)
    except Exception:
        return out
    for g in got:
        pid = g["prov_id"]
        if article(pid) in gold_art:
            continue
        out.append("%s(%.2f) %s" % (pid, g["score"], snippet(con, pid, q)))
        if len(out) >= 3:
            break
    return out


def main():
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row       # find_law 内部用 dict(row)，必须是 Row
    rows = load_all()
    group = split_by_category(rows)

    out_rows = []
    for r in rows:
        g = group[r["query_id"]]
        base = {k: r[k] for k in ("query_id", "category", "watershed",
                                  "region", "regulated_conduct")}
        if g == "A":
            b0, b1 = parse_ids(r["b0_prov_ids"]), parse_ids(r["b1_prov_ids"])
            hint = r["watershed"].strip() or None
            alt = []
            if not b0:
                alt += ["B0 侧：金标判空（本语料库无依据）"]
            a0 = alts(con, r["regulated_conduct"], B0, b0, hint)
            a1 = alts(con, r["regulated_conduct"], B1, b1, hint)
            if a0:
                alt.append("B0 其他候选：" + "；".join(a0))
            if a1:
                alt.append("B1 其他候选：" + "；".join(a1))
            out_rows.append({**base,
                             "group": "A",
                             "b0_prov_ids": r["b0_prov_ids"],
                             "b0_basis": r["b0_basis"],
                             "b1_prov_ids": r["b1_prov_ids"],
                             "b1_basis": r["b1_basis"],
                             "flip": r["flip"],
                             "answer_type": r["answer_type"],
                             "verify_note": r["verify_note"],
                             "alt_candidates": " ｜ ".join(alt),
                             "review_action": "", "review_note": ""})
        else:
            out_rows.append({**base, "group": "B",
                             **{k: "" for k in FIELDS[6:]}})
    out_rows.sort(key=lambda x: x["query_id"])

    with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out_rows)

    na = sum(1 for r in out_rows if r["group"] == "A")
    print("[写盘] %s" % OUT)
    print("组 A（预标注待审核）：%d 条；组 B（空白独立标注）：%d 条" % (na, len(out_rows) - na))
    from collections import Counter
    print("组 A 类别分布：", dict(Counter(r["category"] for r in out_rows if r["group"] == "A")))
    print("组 B 类别分布：", dict(Counter(r["category"] for r in out_rows if r["group"] == "B")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
