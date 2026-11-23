# -*- coding: utf-8 -*-
"""T1 主指标的 bootstrap 95% 置信区间（协议 §三要求「所有主指标给 1,000 次 bootstrap 的 95% CI」）。

做法：先对每个查询算出 0/1 正确性与 reciprocal rank，再按查询重采样 1000 次
（百分位法取 2.5% / 97.5%）。不重跑检索，因此很快；但注意 CI 只反映**查询抽样**方差，
不反映语料或标注方差——论文中须写明这一点。

指标：
- 款级 / 条级 P@5、MRR（B0、B1，conduct 与 full 两设定）
- 款级 / 条级 flip 准确率（Y、N 两类）
- 条件 flip（B0 条级已命中的翻转题上）

用法: python build/tools/eval_ci.py [--n 1000] [--seed 2026]
输出: build/out/query_ci.md
"""
import csv
import glob
import io
import os
import random
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
sys.path.insert(0, BUILD)
sys.path.insert(0, HERE)
import common as C
import s4_reason
import eval_queries as E

OUT = os.path.join(BUILD, "out", "query_ci.md")
B0, B1 = E.B0, E.B1
MODES = ("conduct", "full")


def load():
    rows = []
    for p in sorted(glob.glob(os.path.join(C.PROJ_DIR, "corpus", "*查询集*.csv"))):
        for r in csv.DictReader(io.open(p, encoding="utf-8-sig")):
            r["_batch"] = os.path.basename(p).split("_")[-1].replace(".csv", "")
            rows.append(r)
    return rows


def per_query(con, rows, mode):
    """返回 dict：每个指标 -> 逐查询取值列表（0/1 或 rr）。"""
    out = {}
    for key, as_of, tag in (("b0_prov_ids", B0, "B0"), ("b1_prov_ids", B1, "B1")):
        for level in ("para", "article"):
            ok, rr = [], []
            for r in rows:
                gold = E.parse_ids(r[key])
                got = s4_reason.find_law(con, E.qstr(r, mode), as_of, k=5,
                                         basin_hint=E.qhint(r, mode))
                ids = [g["prov_id"] for g in got]
                if level == "article":
                    gk = {E.to_article(x) for x in gold}
                    rank = next((i + 1 for i, pid in enumerate(ids)
                                 if E.to_article(pid) in gk), None) if gold else None
                else:
                    rank = next((i + 1 for i, pid in enumerate(ids) if pid in gold), None) if gold else None
                ok.append(1 if (rank and rank <= 5) else 0)
                rr.append(1.0 / rank if rank else 0.0)
            out["%s_%s_p5" % (tag, level)] = ok
            out["%s_%s_mrr" % (tag, level)] = rr

    for level in ("para", "article"):
        for cls in ("Y", "N"):
            vals = []
            for r in rows:
                if r["flip"] != cls:
                    continue
                g0, g1 = E.parse_ids(r["b0_prov_ids"]), E.parse_ids(r["b1_prov_ids"])
                got = s4_reason.find_law(con, E.qstr(r, mode), B1, k=5, basin_hint=E.qhint(r, mode))
                top1 = got[0]["prov_id"] if got else None
                if level == "article":
                    g0k, g1k = {E.to_article(x) for x in g0}, {E.to_article(x) for x in g1}
                    t1 = E.to_article(top1) if top1 else None
                    good = t1 is not None and ((cls == "Y" and t1 in g1k and t1 not in g0k)
                                               or (cls == "N" and t1 in g0k))
                else:
                    good = top1 is not None and ((cls == "Y" and top1 in g1 and top1 not in g0)
                                                 or (cls == "N" and top1 in g0))
                vals.append(1 if good else 0)
            out["flip_%s_%s" % (level, cls)] = vals

    # 条件 flip：B0 条级已命中者
    cond = []
    for r in rows:
        if r["flip"] != "Y":
            continue
        g0, g1 = E.parse_ids(r["b0_prov_ids"]), E.parse_ids(r["b1_prov_ids"])
        if not g0:
            continue
        g0k = {E.to_article(x) for x in g0}
        hit0 = any(E.to_article(g["prov_id"]) in g0k
                   for g in s4_reason.find_law(con, E.qstr(r, mode), B0, k=5,
                                               basin_hint=E.qhint(r, mode)))
        if not hit0:
            continue
        g1k = {E.to_article(x) for x in g1}
        top = s4_reason.find_law(con, E.qstr(r, mode), B1, k=1, basin_hint=E.qhint(r, mode))
        cond.append(1 if (top and E.to_article(top[0]["prov_id"]) in g1k) else 0)
    out["cond_flip"] = cond
    return out


def boot(vals, n, rng):
    """百分位 bootstrap：返回 (point, lo, hi)。"""
    if not vals:
        return float("nan"), float("nan"), float("nan")
    k = len(vals)
    point = sum(vals) / float(k)
    stats = []
    for _ in range(n):
        s = sum(vals[rng.randrange(k)] for _ in range(k)) / float(k)
        stats.append(s)
    stats.sort()
    lo = stats[int(0.025 * n)]
    hi = stats[min(int(0.975 * n), n - 1)]
    return point, lo, hi


LABEL = {
    "B0_para_p5": "B0 款级 P@5", "B1_para_p5": "B1 款级 P@5",
    "B0_article_p5": "B0 条级 P@5", "B1_article_p5": "B1 条级 P@5",
    "B0_para_mrr": "B0 款级 MRR", "B1_para_mrr": "B1 款级 MRR",
    "B0_article_mrr": "B0 条级 MRR", "B1_article_mrr": "B1 条级 MRR",
    "flip_para_Y": "款级 flip=Y 准确率", "flip_para_N": "款级 flip=N 准确率",
    "flip_article_Y": "条级 flip=Y 准确率", "flip_article_N": "条级 flip=N 准确率",
    "cond_flip": "条件 flip=Y（B0 已命中）",
}
ORDER = ["B0_para_p5", "B1_para_p5", "B0_article_p5", "B1_article_p5",
         "B0_para_mrr", "B1_para_mrr", "B0_article_mrr", "B1_article_mrr",
         "flip_para_Y", "flip_para_N", "flip_article_Y", "flip_article_N", "cond_flip"]


def main():
    n = 1000
    seed = 2026
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])

    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = load()
    res = {m: per_query(con, rows, m) for m in MODES}

    L = ["# T1 主指标的 bootstrap 95% 置信区间", "",
         "共 **%d** 个查询；重采样 **%d** 次（seed=%d，百分位法 2.5%%/97.5%%），"
         "按查询重采样。" % (len(rows), n, seed), "",
         "> CI 只反映**查询抽样**方差；不覆盖语料选择、标注分歧、参数选择三项方差。"
         "论文须在 Limitations 中写明这一口径（协议 §三 的既定要求）。", ""]

    for m in MODES:
        L += ["## %s" % ("conduct（纯事由）" if m == "conduct" else "full（流域＋事由）"), "",
              "| 指标 | 点估计 | 95% CI | n（分母） |", "|---|---|---|---|"]
        rng = random.Random(seed)
        for key in ORDER:
            vals = res[m][key]
            p, lo, hi = boot(vals, n, rng)
            fmt = "%.3f" if "mrr" in key else "%.1f%%"
            if "mrr" in key:
                cell = (fmt % p, "[%.3f, %.3f]" % (lo, hi))
            else:
                cell = (fmt % (p * 100), "[%.1f%%, %.1f%%]" % (lo * 100, hi * 100))
            L.append("| %s | %s | %s | %d |" % (LABEL[key], cell[0], cell[1], len(vals)))
        L.append("")

    # 与 2026-11-09 仲裁前基线的对照（仅点估计）
    L += ["## 附：与仲裁前基线的点估计对照", "",
          "| 指标 | 仲裁前（2026-11-09，30 翻转） | 仲裁后（2026-11-16，28 翻转） |",
          "|---|---|---|",
          "| conduct 款级 P@5 B0 / B1 | 71.7% / 95.0% | 78.3% / 96.7% |",
          "| full 款级 P@5 B0 / B1 | 71.7% / 95.0% | 78.3% / 96.7% |",
          "| conduct 款级 MRR B0 / B1 | 0.616 / 0.805 | 0.689 / 0.849 |",
          "| full 款级 MRR B0 / B1 | 0.616 / 0.825 | 0.700 / 0.869 |",
          "",
          "> 排序代码在这一轮**零改动**：差异全部来自金标纠错（10 条实质改动）。"
          "这是论文可直接引用的一个论点——**金标质量即评测上限**。", ""]

    io.open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
    print("\n".join(L[:40]))
    print("\n[写盘] %s" % OUT)


if __name__ == "__main__":
    main()
