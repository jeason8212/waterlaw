# -*- coding: utf-8 -*-
"""生成第二标注者用的**空白双标注表单**。

从三批查询集 CSV 中提取查询输入（query_id / category / watershed / region /
regulated_conduct），**剥离全部金标列**（duty_bearer 也剥离——义务承担者
直接指向主依据条文的主体，属答案泄漏）。

输出 corpus/12_双标注表单_空白.csv，供标注者 B 独立填写。
用法：python tools/make_annotation_form.py
"""
import csv
import glob
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
PROJ = os.path.dirname(BUILD)
OUT = os.path.join(PROJ, "corpus", "12_双标注表单_空白.csv")

FIELDS = ["query_id", "category", "watershed", "region", "regulated_conduct",
          "b0_prov_ids", "b0_basis", "b1_prov_ids", "b1_basis",
          "flip", "answer_type", "verify_note"]


def main():
    rows = []
    for p in sorted(glob.glob(os.path.join(PROJ, "corpus", "*查询集*.csv"))):
        if os.path.basename(p).startswith("12_"):
            continue
        for r in csv.DictReader(io.open(p, encoding="utf-8-sig")):
            rows.append({
                "query_id": r["query_id"],
                "category": r["category"],
                "watershed": r["watershed"],
                "region": r["region"],
                "regulated_conduct": r["regulated_conduct"],
            })
    rows.sort(key=lambda x: x["query_id"])
    assert len(rows) == 60, "查询数 %d != 60" % len(rows)

    with io.open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, **{k: "" for k in
                                ("b0_prov_ids", "b0_basis", "b1_prov_ids",
                                 "b1_basis", "flip", "answer_type", "verify_note")}})
    print("[写盘] %s（%d 查询，金标列已全部剥离）" % (OUT, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
