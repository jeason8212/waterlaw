# -*- coding: utf-8 -*-
"""标注者间一致性（IAA）计算：Cohen's kappa + 集合型一致性 + bootstrap CI。

支持**分组报告**（预标注设计）：
  - group=B（空白独立标注）：干净的 IAA，**主报告**
  - group=A（预标注待审核）：锚定条件下的 IAA
  - 两者之差 = 预标注的锚定效应量

用法：
    python tools/kappa_iaa.py                          # 影子冒烟（13_算法B）
    python tools/kappa_iaa.py --b corpus/15_预标注表单_已填.csv   # 正式（含分组）

输出：out/iaa_report.md
"""
import argparse
import csv
import glob
import io
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
PROJ = os.path.dirname(BUILD)
OUT = os.path.join(BUILD, "out", "iaa_report.md")
DEFAULT_B = os.path.join(PROJ, "corpus", "13_影子标注_算法B.csv")
N_BOOT = 1000


def parse_ids(field):
    """解析条款编号串，兼容中英文分隔符。

    标注者在 Excel 里填写时，中文输入法会打出全角分号/逗号（；，、）。只按 ASCII 分号
    切分会把 "L-002#28-2；L-002#91-1" 整串当成一个编号，导致集合比较全部失配、J 值
    被误报为 0——这是会直接污染 IAA 数字的静默错误。
    """
    import re as _re
    field = (field or "").strip()
    if not field or field in ("(none)", "none", "-"):
        return []
    parts = _re.split(r"[;；,，、]", field)
    return [x.strip() for x in parts if x.strip() and x.strip() not in ("(none)", "none", "-")]


def read_csv_rows(path):
    """读 CSV，自动识别编码。

    标注者 B 的表格很可能被 Excel/WPS 打开过——它们会把 UTF-8(BOM) 的 CSV 另存为
    本地编码（中文 Windows = GBK/GB18030）。硬按 utf-8 读会直接 UnicodeDecodeError，
    让整个 IAA 流程卡在"文件读不进来"这种与统计无关的问题上。
    """
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        # 若按 utf-8 解出的首行里出现 U+FFFD 或明显乱码，继续试下一个
        first = text.split("\n", 1)[0]
        if "query_id" in first:
            return list(csv.DictReader(io.StringIO(text)))
    raise SystemExit("[错误] 无法识别文件编码：%s（请另存为 UTF-8 CSV 后重试）" % path)


def load_a():
    rows = {}
    for p in sorted(glob.glob(os.path.join(PROJ, "corpus", "*查询集*.csv"))):
        b = os.path.basename(p)
        if b.startswith(("12_", "13_", "15_")):
            continue
        for r in read_csv_rows(p):
            rows[r["query_id"]] = r
    return rows


def load_b(path):
    rows = {}
    for r in read_csv_rows(path):
        rows[r["query_id"]] = r
    return rows


def cohen_kappa(pairs):
    n = len(pairs)
    if n == 0:
        return None, None, None, 0
    labels = set(x for p in pairs for x in p)
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    po = sum(1 for a, b in pairs if a == b) / float(n)
    pe = sum(ca[l] * cb[l] for l in labels) / float(n * n)
    if pe >= 1.0:
        return (1.0 if po >= 1.0 else 0.0), po, pe, n
    return (po - pe) / (1.0 - pe), po, pe, n


def jaccard(sa, sb):
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(set(sa) & set(sb)) / float(len(set(sa) | set(sb)))


def micro_prf(pairs_of_sets):
    tp = sum(len(set(g) & set(s)) for g, s in pairs_of_sets)
    g_tot = sum(len(set(g)) for g, s in pairs_of_sets)
    s_tot = sum(len(set(s)) for g, s in pairs_of_sets)
    p = tp / float(s_tot) if s_tot else (1.0 if g_tot == 0 else 0.0)
    r = tp / float(g_tot) if g_tot else (1.0 if s_tot == 0 else 0.0)
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f, tp, g_tot, s_tot


def boot_ci(pairs, stat_fn, n_boot=N_BOOT, seed=13):
    if not pairs:
        return None, None
    random.seed(seed)
    n = len(pairs)
    stats = []
    for _ in range(n_boot):
        sample = [pairs[random.randrange(n)] for _ in range(n)]
        v = stat_fn(sample)
        if v is not None:
            stats.append(v)
    if not stats:
        return None, None
    stats.sort()
    return stats[int(0.025 * len(stats))], stats[int(0.975 * len(stats))]


def to_article(pid):
    if "#" not in pid:
        return pid
    d, rest = pid.split("#", 1)
    return d + "#" + rest.split("-")[0]


def compute(A, B, qs):
    """对给定 query_id 子集算全部指标。"""
    res = {"n": len(qs)}
    fp = [(A[q]["flip"], B[q].get("flip", "")) for q in qs]
    fp = [(a, b) for a, b in fp if a and b]
    res["k_flip"], res["po"], res["pe"], res["n_flip"] = cohen_kappa(fp)
    res["flip_pairs"] = fp
    res["k_flip_ci"] = boot_ci(fp, lambda prs: cohen_kappa(prs)[0])
    if fp:
        res["flip_boot"] = fp

    ap = [(A[q]["answer_type"], B[q].get("answer_type", "")) for q in qs]
    ap = [(a, b) for a, b in ap if a and b]
    res["k_at"], res["po_at"], res["pe_at"], res["n_at"] = cohen_kappa(ap)

    jac, pairs_sets, pairs_art = [], [], []
    dis = []
    for q in qs:
        for side in ("b0", "b1"):
            ga = parse_ids(A[q][side + "_prov_ids"])
            gb = parse_ids(B[q].get(side + "_prov_ids", ""))
            j = jaccard(ga, gb)
            jac.append(j)
            pairs_sets.append((ga, gb))
            pairs_art.append(([to_article(x) for x in ga], [to_article(x) for x in gb]))
            if j < 1.0:
                dis.append((q, side, j, ga, gb))
    res["mean_j"] = sum(jac) / len(jac) if jac else 0.0
    res["mean_j_ci"] = boot_ci([(x, x) for x in jac],
                               lambda prs: sum(x[0] for x in prs) / len(prs))
    res["m_p"], res["m_r"], res["m_f"], res["tp"], res["gtot"], res["stot"] = micro_prf(pairs_sets)
    res["a_p"], res["a_r"], res["a_f"], _, _, _ = micro_prf(pairs_art)
    res["dis"] = dis
    return res


def block(L, r, tag):
    if r["n"] == 0:
        return
    L += ["### %s（n = %d）" % (tag, r["n"]), "",
          "- flip κ = **%.3f**（95%% CI [%.3f, %.3f]，po = %.3f，pe = %.3f）"
          % (r["k_flip"] or 0, (r["k_flip_ci"][0] or 0), (r["k_flip_ci"][1] or 0),
             r["po"] or 0, r["pe"] or 0),
          "- answer_type κ = %.3f" % (r["k_at"] or 0),
          "- 款级平均 Jaccard = %.3f（微平均 P = %.3f，R = %.3f，F1 = %.3f）"
          % (r["mean_j"], r["m_p"], r["m_r"], r["m_f"]),
          "- 条级 P = %.3f，R = %.3f（同条不同款视为一致）" % (r["a_p"], r["a_r"]), ""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--b", default=DEFAULT_B, help="标注者 B 的 CSV 路径")
    args = ap.parse_args()

    A, B = load_a(), load_b(args.b)
    common = sorted(set(A) & set(B))
    only_a, only_b = set(A) - set(B), set(B) - set(A)
    if only_a or only_b:
        print("[警告] query_id 不一致：仅A=%s 仅B=%s" % (sorted(only_a), sorted(only_b)))

    shadow = "13_" in os.path.basename(args.b)
    has_group = any((B[q].get("group") or "").strip() for q in common)

    allr = compute(A, B, common)
    gA = [q for q in common if (B[q].get("group") or "").strip().upper() == "A"]
    gB = [q for q in common if (B[q].get("group") or "").strip().upper() == "B"]
    rA = compute(A, B, gA) if gA else None
    rB = compute(A, B, gB) if gB else None

    L = ["# IAA 报告（标注者间一致性）", "",
         "- 标注者 A：金标（三批查询集合并，%d 查询）" % len(A),
         "- 标注者 B：`%s`（%d 查询）" % (os.path.relpath(args.b, PROJ), len(B)),
         "- 共同查询：%d%s" % (len(common),
                          "（分组：A 组 %d / B 组 %d）" % (len(gA), len(gB)) if has_group else ""),
         "- **报告性质：%s**" % ("影子冒烟（算法第二意见，不得写入论文 IAA）" if shadow else
                            ("预标注双标注（组 B 为干净 IAA，组 A 为锚定条件）" if has_group
                             else "人-人 IAA（可写入论文）")),
         "", "## 1. 总体", ""]
    block(L, allr, "全部查询")

    L += ["## 1b. flip 混淆矩阵（全部）", "", "| A\\B | Y | N |", "|---|---|---|"]
    for la in ("Y", "N"):
        L.append("| %s | %s |" % (la, " | ".join(
            str(sum(1 for a, b in allr["flip_pairs"] if a == la and b == lb))
            for lb in ("Y", "N"))))
    L.append("")

    if has_group:
        L += ["## 2. 分组（预标注设计）", "",
              "> 组 B 是**干净的 IAA**（可写入论文）；组 A 是**锚定条件下**的一致性。"
              "两者之差即预标注的锚定效应量。", ""]
        if rB:
            block(L, rB, "组 B｜空白独立标注（主报告）")
        if rA:
            block(L, rA, "组 A｜预标注待审核（锚定条件）")
        if rA and rB:
            L += ["**锚定效应**：组 A 的 flip κ 比组 B %+.3f，Jaccard %+.3f。"
                  "正向差值说明预标注确实拉高了一致性——论文中须如实报告这一数字，"
                  "并以此说明组 B 的数字才是可外推的 IAA。"
                  % ((rA["k_flip"] or 0) - (rB["k_flip"] or 0),
                     rA["mean_j"] - rB["mean_j"]), ""]

    L += ["## 3. 分歧清单（Jaccard < 1，共 %d 处）" % len(allr["dis"]), ""]
    hi = [d for d in allr["dis"] if d[2] == 0.0]
    L += ["**优先复核（Jaccard = 0，共 %d 处）**" % len(hi), ""]
    L += ["| 查询 | 时点 | Jaccard | gold | 标注B |", "|---|---|---|---|---|"]
    for q, side, j, ga, gb in sorted(allr["dis"], key=lambda x: x[2])[:80]:
        L.append("| %s | %s | %.2f | %s | %s |" % (
            q, side, j, ";".join(ga) or "(none)",
            (";".join(gb[:5]) + ("…" if len(gb) > 5 else "")) if gb else "(none)"))
    L += ["", "## 4. 解读", "",
          "- κ < 0.40：编码手册须修订后重标；0.40–0.60：补校准例后重标分歧项；≥ 0.60：进仲裁。",
          "- 集合型诊断：微平均 R 低 = B 漏计（手册检索指引不足）；P 低 = B 多计（并列依据收得过宽）。",
          "- 空答案查询双方一致判空时 Jaccard 计 1.0；单方判空计 0.0。", ""]

    io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("总体：κ(flip)=%.3f  Jaccard=%.3f  分歧 %d 处（J=0: %d）"
          % (allr["k_flip"] or 0, allr["mean_j"], len(allr["dis"]), len(hi)))
    if has_group and rA and rB:
        print("组B(干净): κ=%.3f J=%.3f | 组A(锚定): κ=%.3f J=%.3f | 锚定效应 Δκ=%+.3f"
              % (rB["k_flip"] or 0, rB["mean_j"], rA["k_flip"] or 0, rA["mean_j"],
                 (rA["k_flip"] or 0) - (rB["k_flip"] or 0)))
    print("[写盘] %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
