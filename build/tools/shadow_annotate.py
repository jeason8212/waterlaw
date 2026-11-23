# -*- coding: utf-8 -*-
"""影子标注器（算法第二意见）——**冒烟与分歧热图专用，不是人类标注者**。

定位（见 paper/04_标注协议与IAA方案.md §6）：
1. 与被评测系统 v0.2 实现路径不同：SQL LIKE 关键词召回 + 类别关键词表 +
   独立实现的位阶/特别法权重（非 bigram+IDF）；
2. 用途：① 验证 kappa_iaa.py 管线端到端可跑；② 产出高分歧查询热图，
   作为真人标注者 B 的优先复核清单；
3. 其与 gold 的一致性**不得写入论文 IAA**。

判定规则刻意按编码手册（协议 §二）实现一版“机器可执行子集”：
- 规则 1：时点有效性（SQL 侧过滤 valid_from/valid_to）；
- 规则 2.1：特别法加分（长江/黄河法对同流域查询 +0.25）；
- 规则 2.3：B1 侧法典加分（+0.10，法典接管推定）；
- 规则 2.4：并列依据取 top-3；
- 规则 4：flip = 主依据不同或 B0 空。

输出：corpus/13_影子标注_算法B.csv（与金标同 schema）。
用法：python tools/shadow_annotate.py
"""
import csv
import glob
import io
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
PROJ = os.path.dirname(BUILD)
sys.path.insert(0, BUILD)
import common as C

OUT = os.path.join(PROJ, "corpus", "13_影子标注_算法B.csv")
B0, B1 = "2026-06-30", "2026-11-06"

# 六类关键词表（影子标注器唯一的知识来源；与 v0.2 的术语表无关）
CAT_KW = {
    "跨省界": ["跨省", "省界", "跨行政", "行政区域间", "行政区"],
    "岸线": ["岸线", "河湖管理", "堤防", "河道管理", "护堤"],
    "取水": ["取水", "生态流量", "水量调度", "水资源费", "生态用水", "用水总量"],
    "采砂": ["采砂", "砂石", "采掘"],
    "生态补偿": ["补偿"],
    "排污口": ["排污口"],
}

# 位阶权重（与 v0.2 HIER_W 数值不同，实现独立）
DOC_RANK = {"L-001": 1.00, "L-002": 1.20, "L-003": 1.20, "L-004": 1.05,
            "L-017": 1.05, "A-009": 0.95}
BASIN_DOC = {"长江": "L-002", "黄河": "L-003"}


def load_queries():
    rows = []
    for p in sorted(glob.glob(os.path.join(PROJ, "corpus", "*查询集*.csv"))):
        if os.path.basename(p).startswith(("12_", "13_")):
            continue
        for r in csv.DictReader(io.open(p, encoding="utf-8-sig")):
            rows.append(r)
    rows.sort(key=lambda x: x["query_id"])
    return rows


def kw_cov(text, kws):
    """类别关键词覆盖率（命中数/总数），非绝对次数——防长条文霸榜。"""
    if not kws:
        return 0.0
    return sum(1 for k in kws if k in text) / float(len(kws))


def conduct_bigrams(conduct):
    """事由字符 bigram（中文短语无空格，词级切分无效）。"""
    t = re.sub(r"[的与和及、，。；\s]", "", conduct)
    return [t[i:i + 2] for i in range(len(t) - 1)] if len(t) >= 2 else []


def bigram_cov(text, bg):
    if not bg:
        return 0.0
    return sum(1 for g in bg if g in text) / float(len(bg))


def candidates(con, kws, terms, as_of, basin_doc):
    """返回该时点有效、按影子打分排序的 (prov_id, score, hit) 列表。"""
    sql = """
        SELECT p.prov_id, p.text, p.doc_id FROM provision p
        JOIN doc d ON d.doc_id = p.doc_id
        WHERE (d.valid_from IS NULL OR d.valid_from <= ?)
          AND (d.valid_to   IS NULL OR ? <= d.valid_to)
    """
    out = []
    for pid, text, doc in con.execute(sql, (as_of, as_of)):
        c_cov = kw_cov(text, kws)
        b_cov = bigram_cov(text, terms)
        if c_cov == 0 and b_cov == 0:
            continue
        s = (0.7 * c_cov + 0.6 * b_cov + 0.05) * DOC_RANK.get(doc, 1.0)
        if doc == "L-001":
            s *= 1.15 if as_of == B1 else 1.0     # 规则 2.3：法典接管推定
        if basin_doc and doc == basin_doc:
            s *= 1.25                              # 规则 2.1：特别法加分
        out.append((pid, round(s, 3), c_cov + b_cov))
    out.sort(key=lambda x: (-x[1], -x[2], x[0]))
    return out


def main():
    con = sqlite3.connect(C.DB_PATH)
    queries = load_queries()
    assert len(queries) == 60

    out_rows = []
    for r in queries:
        cat = r["category"]
        kws = []
        for key, v in CAT_KW.items():
            if key in cat:
                kws = v
                break
        terms = conduct_bigrams(r["regulated_conduct"])
        ws = r["watershed"].strip()
        basin_doc = BASIN_DOC.get(ws)

        def pick(c):
            """top-3 且分数不低于最高分 0.75 倍（减少低分并列多计）。"""
            if not c:
                return []
            cut = c[0][1] * 0.75
            return [x[0] for x in c[:3] if x[1] >= cut]

        c0 = candidates(con, kws, terms, B0, basin_doc)
        c1 = candidates(con, kws, terms, B1, basin_doc)
        b0, b1 = pick(c0), pick(c1)

        if not b0 and not b1:
            flip, note = "", "影子标注器：两时点均无关键词命中"
        elif not b0:
            flip = "Y"
            note = "B0 无命中"
        elif b0[0] != (b1[0] if b1 else None):
            flip = "Y"
            note = "主依据迁移：%s → %s" % (b0[0], b1[0] if b1 else "(空)")
        else:
            flip = "N"
            note = "主依据延续 %s" % b0[0]
        at = "empty" if (not b0 and not b1) else ("single" if len(b1 or b0) == 1 else "multi")

        out_rows.append({
            "query_id": r["query_id"], "category": cat, "watershed": ws,
            "region": r["region"], "regulated_conduct": r["regulated_conduct"],
            "b0_prov_ids": ";".join(b0) or "(none)",
            "b0_basis": "影子标注器 top-3（分数 %s）" % ";".join(str(x[1]) for x in c0[:3]),
            "b1_prov_ids": ";".join(b1) or "(none)",
            "b1_basis": "影子标注器 top-3（分数 %s）" % ";".join(str(x[1]) for x in c1[:3]),
            "flip": flip, "flip_reason": note if flip == "Y" else "",
            "answer_type": at, "verify_note": note,
        })

    with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print("[写盘] %s（60 查询）" % OUT)
    n_flip = sum(1 for r in out_rows if r["flip"] == "Y")
    print("影子 flip=Y：%d / 60（gold 为 30）" % n_flip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
