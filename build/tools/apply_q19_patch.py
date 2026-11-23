# -*- coding: utf-8 -*-
"""修正批次 1 的 Q19 金标：补入水法第 34 条第 1 款（并行冗余规范）。

问题：Q19 原金标 B0 仅引水污染防治法第 64 条、B1 仅引法典第 318 条，两侧都漏计
水法第 34 条第 1 款——“在饮用水水源保护区内，禁止设置排污口”，与两部条款逐字相同的
并行冗余规范，且水法在 B0/B1 均有效。缺漏的后果：系统在 B1 返回水法第 34 条第 1 款
（完全正确的现行条文）会被误判为错误，评测低估召回。

修正原则：
- 金标两侧补入 L-004#34-1，翻转判定维持 Y——翻转判据是“污染防治领域主依据”（特别法）
  由水污染防治法第 64 条迁入法典第 318 条，冗余孪生条款不承担主依据地位；
- verify_note 声明：B1 返回法典第 318 条或水法第 34 条第 1 款均计正确，
  返回已废止的水污染防治法第 64 条方计错误；
- 与批次 3 的 Q59（L-017#32-2→法典#274-2，无冗余孪生条款）构成最小对照对。
"""
import csv
import io
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(PROJ, "corpus", "09_查询集_批次1.csv")
BAK = SRC + ".bak_q19"


def main():
    shutil.copyfile(SRC, BAK)
    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8-sig")))
    header = list(rows[0].keys())
    patched = 0
    for r in rows:
        if r["query_id"] != "Q19":
            continue
        r["b0_prov_ids"] = "L-017#64-1;L-004#34-1"
        r["b0_basis"] = ("水污染防治法第64条：在饮用水水源保护区内，禁止设置排污口；"
                         "水法第34条第1款为逐字相同的并行冗余规范（在 B0/B1 均有效）")
        r["b1_prov_ids"] = "L-001#318-1;L-004#34-1"
        r["b1_basis"] = ("法典第318条：在饮用水水源保护区内，禁止设置排污口；"
                         "水法第34条第1款并行规范保留，B1 时点仍有效")
        r["flip"] = "Y"
        r["flip_reason"] = ("污染防治领域主依据由水污染防治法第64条（已废止）迁入法典第318条；"
                            "水法第34条第1款为逐字相同的并行冗余规范，在 B0/B1 均有效，"
                            "但不承担污染防治领域主依据地位")
        r["verify_note"] = ("“同规则异条号+冗余孪生”样本：B1 引用已废止的水污染防治法第64条即计错；"
                            "返回法典第318条或并行有效的水法第34条第1款均计正确。"
                            "与批次3 Q59（有毒有害水污染物排污口监测，无冗余孪生条款）"
                            "构成“有无冗余孪生”最小对照对，可分离条号迁移与规范冗余两种效应")
        patched += 1
    assert patched == 1, "应恰好修正 1 行，实际 %d" % patched
    with io.open(SRC, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    print("[修正] Q19 金标已补入 L-004#34-1 冗余孪生规范（备份：%s）" % BAK)


if __name__ == "__main__":
    main()
