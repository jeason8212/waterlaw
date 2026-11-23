# -*- coding: utf-8 -*-
"""查询集结构与“时点一致性”校验（支持多批次）。

扫描 corpus/ 下所有 `*查询集*.csv`，逐行检查：
1. 所有 prov_id 在库中存在；
2. B0（2026-06-30）侧引用的条文，其所属文件在该日有效（valid_from <= B0 <= valid_to）；
3. B1（2026-11-06）侧引用的条文，其所属文件在该日有效（不得引用已废止法）；
4. flip 标记与答案变化一致：Y 要求主依据（首条）不同，或 B0 为空；N 要求主依据相同且 B0 ⊆ B1；
5. flip=Y 必须给出 flip_reason；
6. **B0 为空的查询必须在 verify_note 中声明语料边界**——否则“从无到有”型翻转会被
   误读为“法律上无依据”，这是此类研究最常见的攻击点；
7. query_id 全局唯一（跨批次不重复）；
8. 统计各类别分布、翻转比例，输出 out/query_audit.md。

退出码：0 通过，1 存在错误。
"""
import csv
import glob
import io
import os
import sqlite3
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
PROJ = os.path.dirname(BUILD)
sys.path.insert(0, BUILD)
import common as C

B0, B1 = "2026-06-30", "2026-11-06"
OUT = os.path.join(BUILD, "out", "query_audit.md")


def valid_at(con, prov_id, date):
    """条文所属文件在 date 是否有效。"""
    row = con.execute("""
        SELECT d.valid_from, d.valid_to FROM provision p
        JOIN doc d ON d.doc_id = p.doc_id WHERE p.prov_id = ?
    """, (prov_id,)).fetchone()
    if not row:
        return None
    vf, vt = row[0] or "", row[1] or ""
    if vf and date < vf:
        return False
    if vt and date > vt:
        return False
    return True


def parse_ids(field):
    field = (field or "").strip()
    if not field or field in ("(none)", "none", "-"):
        return []
    return [x.strip() for x in field.split(";") if x.strip()]


def load():
    paths = sorted(glob.glob(os.path.join(PROJ, "corpus", "*查询集*.csv")))
    rows, src = [], {}
    for p in paths:
        for r in csv.DictReader(io.open(p, encoding="utf-8-sig")):
            r["_src"] = os.path.basename(p)
            rows.append(r)
            src[r["query_id"]] = os.path.basename(p)
    return paths, rows


def main():
    con = sqlite3.connect(C.DB_PATH)
    paths, rows = load()
    errs, notes = [], []

    seen = Counter(r["query_id"] for r in rows)
    for qid, n in seen.items():
        if n > 1:
            errs.append("%s: query_id 跨批次重复（出现 %d 次）" % (qid, n))

    for r in rows:
        qid = r["query_id"]
        b0, b1 = parse_ids(r["b0_prov_ids"]), parse_ids(r["b1_prov_ids"])

        for pid in set(b0 + b1):
            if valid_at(con, pid, B0) is None:
                errs.append("%s: 条文不存在 %s" % (qid, pid))
        for pid in b0:
            if valid_at(con, pid, B0) is False:
                errs.append("%s: B0 侧引用了 2026-06-30 尚无效/已失效的条文 %s" % (qid, pid))
        for pid in b1:
            if valid_at(con, pid, B1) is False:
                errs.append("%s: B1 侧引用了 2026-11-06 已失效的条文 %s" % (qid, pid))

        is_flip = r["flip"] == "Y"
        if is_flip:
            if not r["flip_reason"].strip():
                errs.append("%s: flip=Y 但未写翻转理由" % qid)
            if b0 and b1 and b0[0] == b1[0]:
                errs.append("%s: flip=Y 但主依据未变（%s），翻转不成立——仅依据集合扩大应判 N"
                            % (qid, b0[0]))
            if not b0 and not b1:
                errs.append("%s: 两侧均为空" % qid)
        else:
            if b0 and b1 and b0[0] != b1[0]:
                errs.append("%s: flip=N 但主依据不同（%s vs %s）" % (qid, b0[0], b1[0]))
            if b0 and not set(b0).issubset(set(b1)):
                errs.append("%s: flip=N 但 B0 依据未被 B1 覆盖" % qid)

        if not b0:
            note = r["verify_note"]
            if "语料边界" not in note:
                errs.append("%s: B0 为空但 verify_note 未声明语料边界"
                            "（“无依据”须限定为本语料库范围）" % qid)
            notes.append("%s: B0 侧为空——评测时须单列“应返回无依据”，并在论文中声明语料边界"
                         % qid)

    # 统计
    cats = Counter(r["category"] for r in rows)
    flips = sum(1 for r in rows if r["flip"] == "Y")
    empty_b0 = sum(1 for r in rows if not parse_ids(r["b0_prov_ids"]))
    multi = sum(1 for r in rows if r["answer_type"] == "multi")
    neg_ctrl = sum(1 for r in rows if "负对照" in r["verify_note"])

    lines = ["# 查询集校验报告（全批次）", "",
             "- 文件：%s" % "、".join("`corpus/%s`" % os.path.basename(p) for p in paths),
             "- 基线：B0 = %s（法典前），B1 = %s（法典后）" % (B0, B1),
             "- 查询数：**%d**；翻转 **%d**（%.0f%%）；不翻转 **%d**；B0 空答案 %d；多依据 %d；负对照 %d"
             % (len(rows), flips, 100.0 * flips / max(len(rows), 1), len(rows) - flips,
                empty_b0, multi, neg_ctrl), "",
             "## 类别分布", "", "| 类别 | 合计 | 翻转 | 不翻转 |", "|---|---|---|---|"]
    for c in cats:
        f = sum(1 for r in rows if r["category"] == c and r["flip"] == "Y")
        lines.append("| %s | %d | %d | %d |" % (c, cats[c], f, cats[c] - f))
    lines += ["", "## 逐条时点一致性", "",
              "| 查询 | 批次 | B0 依据 | B1 依据 | 翻转 | 判定 |", "|---|---|---|---|---|---|"]
    for r in rows:
        bad = any(e.startswith(r["query_id"] + ":") for e in errs)
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            r["query_id"], r["_src"].split("_")[-1].replace(".csv", ""),
            r["b0_prov_ids"], r["b1_prov_ids"], r["flip"], "❌" if bad else "✅"))
    if notes:
        lines += ["", "## 提示", ""] + ["- " + n for n in notes]
    lines += ["", "## 结论", "",
              "**通过**：所有引用条文存在且在其所标注的时点有效；翻转标记与答案变化一致；"
              "B0 空答案均声明语料边界。" if not errs
              else "**%d 处错误**：\n" % len(errs) + "\n".join("- " + e for e in errs)]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[-6:]))
    print("\n[写盘] %s" % OUT)
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
