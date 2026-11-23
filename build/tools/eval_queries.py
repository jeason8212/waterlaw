# -*- coding: utf-8 -*-
"""查询集检索评测：双输入设定（conduct / full）× 双粒度（款级 / 条级）。

输入设定（实验设计的既定要求，分离「元信息贡献」与「表示层贡献」）：
- conduct：查询串 = regulated_conduct，不含流域/区域元信息（最难设定，诚实下界）；
- full   ：查询串 = watershed + 「流域」+ regulated_conduct（完整五元组里的流域字段）。
  两设定的差值 = 流域元信息对 R-a/R-b/R-c 三条可废止规则的贡献。

用法: python tools/eval_queries.py
"""
import csv
import glob
import io
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import s4_reason
import common as C

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "query_eval.md")
B0, B1 = "2026-06-30", "2026-11-06"
MODES = ("conduct", "full")


def load():
    rows = []
    for p in sorted(glob.glob(os.path.join(C.PROJ_DIR, "corpus", "*查询集*.csv"))):
        for r in csv.DictReader(io.open(p, encoding="utf-8-sig")):
            r["_batch"] = os.path.basename(p).split("_")[-1].replace(".csv", "")
            rows.append(r)
    return rows


def qstr(r, mode):
    """查询串：两种设定都用纯 regulated_conduct。full 模式的流域信息不进文本，
    经 basin_hint 独立通道传给 find_law（拼接进文本会稀释事由词的判别力）。"""
    return r["regulated_conduct"]


def qhint(r, mode):
    """流域元信息（仅 full 模式提供）。"""
    if mode == "full":
        return (r.get("watershed") or "").strip() or None
    return None


def parse_ids(s):
    return [x.strip() for x in (s or "").split(";") if x.strip() and x.strip() != "(none)"]


def to_article(prov_id):
    """款级 prov_id -> 条级键（L-001#317-3 -> L-001#317）。
    分条级与款级两级评测：条级衡量「找对法条」，款级衡量「找对款」。"""
    if "#" not in prov_id:
        return prov_id
    doc, rest = prov_id.split("#", 1)
    no = rest.split("-")[0]
    return "%s#%s" % (doc, no)


def doc_valid(con, doc_id, as_of):
    """文件在该时点是否有效（废止法引用率必须按时点判定，不能按文档号硬编码）。"""
    r = con.execute("SELECT valid_from,valid_to FROM doc WHERE doc_id=?", (doc_id,)).fetchone()
    if not r:
        return False
    vf, vt = r[0] or "", r[1] or ""
    return (not vf or as_of >= vf) and (not vt or as_of <= vt)


def eval_at(con, rows, as_of, key, level="para", mode="conduct"):
    hits5, rr, repealed, detail = 0, 0.0, 0, []
    for r in rows:
        gold = parse_ids(r[key])
        got = s4_reason.find_law(con, qstr(r, mode), as_of, k=5, basin_hint=qhint(r, mode))
        ids = [g["prov_id"] for g in got]
        if level == "article":
            gold_k = {to_article(g) for g in gold}
            rank = next((i + 1 for i, pid in enumerate(ids) if to_article(pid) in gold_k), None) if gold else None
        else:
            rank = next((i + 1 for i, pid in enumerate(ids) if pid in gold), None) if gold else None
        ok = rank is not None and rank <= 5
        hits5 += 1 if ok else 0
        rr += 1.0 / rank if rank else 0.0
        bad = sum(1 for g in got if not doc_valid(con, g["doc_id"], as_of))
        repealed += 1 if bad else 0
        detail.append(dict(qid=r["query_id"], batch=r["_batch"], cat=r["category"],
                           q=r["regulated_conduct"], gold=gold, rank=rank, ids=ids[:3]))
    n = max(len(rows), 1)
    return dict(p5=hits5 / n, mrr=rr / n, rep=repealed / n, detail=detail, n=len(rows))


def flip_acc(con, rows, level="para", mode="conduct"):
    """翻转正确率：Y 类要求 B1 top1 落在 B1 金标且不同于 B0 金标；N 类要求仍在 B0 金标内。"""
    rows_y = [r for r in rows if r["flip"] == "Y"]
    rows_n = [r for r in rows if r["flip"] == "N"]
    ok_y, det_y = 0, []
    for r in rows_y:
        g0, g1 = parse_ids(r["b0_prov_ids"]), parse_ids(r["b1_prov_ids"])
        ids = [g["prov_id"] for g in s4_reason.find_law(con, qstr(r, mode), B1, k=5, basin_hint=qhint(r, mode))]
        top1 = ids[0] if ids else None
        if level == "article":
            g0k, g1k = {to_article(x) for x in g0}, {to_article(x) for x in g1}
            t1 = to_article(top1) if top1 else None
            good = t1 is not None and t1 in g1k and t1 not in g0k
        else:
            good = top1 is not None and top1 in g1 and top1 not in g0
        ok_y += 1 if good else 0
        det_y.append((r["query_id"], r["_batch"], top1, g0, g1, good))
    ok_n, det_n = 0, []
    for r in rows_n:
        g0 = parse_ids(r["b0_prov_ids"])
        ids = [g["prov_id"] for g in s4_reason.find_law(con, qstr(r, mode), B1, k=5, basin_hint=qhint(r, mode))]
        top1 = ids[0] if ids else None
        if level == "article":
            g0k = {to_article(x) for x in g0}
            t1 = to_article(top1) if top1 else None
            good = t1 is not None and t1 in g0k
        else:
            good = top1 is not None and top1 in g0
        ok_n += 1 if good else 0
        det_n.append((r["query_id"], r["_batch"], top1, g0, good))
    return (ok_y, len(rows_y), det_y), (ok_n, len(rows_n), det_n)


def cond_flip(con, rows, mode):
    """条件翻转准确率：只在「B0 条级已命中」的翻转题上算净效应。"""
    ok = n = 0
    for r in rows:
        if r["flip"] != "Y":
            continue
        g0, g1 = parse_ids(r["b0_prov_ids"]), parse_ids(r["b1_prov_ids"])
        if not g0:
            continue
        g0k = {to_article(x) for x in g0}
        hit0 = any(to_article(g["prov_id"]) in g0k
                   for g in s4_reason.find_law(con, qstr(r, mode), B0, k=5, basin_hint=qhint(r, mode)))
        if not hit0:
            continue
        n += 1
        g1k = {to_article(x) for x in g1}
        top = s4_reason.find_law(con, qstr(r, mode), B1, k=1, basin_hint=qhint(r, mode))
        if top and to_article(top[0]["prov_id"]) in g1k:
            ok += 1
    return ok, n


def run_mode(con, rows, mode):
    e0 = eval_at(con, rows, B0, "b0_prov_ids", mode=mode)
    e1 = eval_at(con, rows, B1, "b1_prov_ids", mode=mode)
    a0 = eval_at(con, rows, B0, "b0_prov_ids", level="article", mode=mode)
    a1 = eval_at(con, rows, B1, "b1_prov_ids", level="article", mode=mode)
    (y_ok, y_n, det_y), (n_ok, n_n, det_n) = flip_acc(con, rows, mode=mode)
    (ya_ok, ya_n, _), (na_ok, na_n, _) = flip_acc(con, rows, level="article", mode=mode)
    c_ok, c_n = cond_flip(con, rows, mode)
    return dict(e0=e0, e1=e1, a0=a0, a1=a1, y=(y_ok, y_n), n=(n_ok, n_n),
                ya=(ya_ok, ya_n), na=(na_ok, na_n), cond=(c_ok, c_n),
                det_y=det_y, det_n=det_n)


MODE_LABEL = {"conduct": "conduct（纯事由，最难设定）", "full": "full（流域＋事由）"}


def main():
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = load()
    res = {m: run_mode(con, rows, m) for m in MODES}

    L = ["# 查询集检索基线（规则 v0 + 三条可废止规则，全批次）", "",
         "排序 = bigram-IDF 覆盖率 × 位阶权重 × R-a 流域降权 / R-b 特别法优先 / R-c 援引穿透"
         "（规则定义见 `s4_reason.find_law` docstring）。Top-5，命中任一金标条文即算对。",
         "共 **%d** 个查询（批次：%s）。两种输入设定：" % (
             len(rows), "、".join(sorted({r["_batch"] for r in rows}))),
         "- **conduct**：查询串 = `regulated_conduct`，不含流域元信息（最难设定，诚实下界）；",
         "- **full**：查询串仍为 `regulated_conduct`，但流域字段经 `basin_hint` 参数"
         "独立传入（元信息不进文本，避免稀释事由词判别力）。"
         "两设定的差值 = 流域元信息对可废止规则的贡献。", ""]

    for m in MODES:
        r = res[m]
        e0, e1, a0, a1 = r["e0"], r["e1"], r["a0"], r["a1"]
        L += ["## 设定：%s" % MODE_LABEL[m], "",
              "### 总体（款级 vs 条级）", "",
              "款级 = 必须命中金标那一条款；条级 = 命中同一条法条即可（款号不计）。", "",
              "| 时点 | 款级 P@5 | 条级 P@5 | 款级 MRR | 条级 MRR | 废止法引用率 |",
              "|---|---|---|---|---|---|",
              "| B0 = %s | %.1f%% | %.1f%% | %.3f | %.3f | %.0f%% |"
              % (B0, e0["p5"] * 100, a0["p5"] * 100, e0["mrr"], a0["mrr"], e0["rep"] * 100),
              "| B1 = %s | %.1f%% | %.1f%% | %.3f | %.3f | %.0f%% |"
              % (B1, e1["p5"] * 100, a1["p5"] * 100, e1["mrr"], a1["mrr"], e1["rep"] * 100),
              "", "### 翻转正确率（版本感知的真实检验）", "",
              "| 口径 | 正确 | 合计 | 准确率 | 判据 |", "|---|---|---|---|---|",
              "| 款级 flip=Y | %d | %d | %.0f%% | B1 top-1 落在 B1 金标且不是 B0 金标 |"
              % (r["y"][0], r["y"][1], 100.0 * r["y"][0] / max(r["y"][1], 1)),
              "| 款级 flip=N | %d | %d | %.0f%% | B1 top-1 仍在 B0 金标内（不漂移） |"
              % (r["n"][0], r["n"][1], 100.0 * r["n"][0] / max(r["n"][1], 1)),
              "| 条级 flip=Y | %d | %d | %.0f%% | 同上，条级匹配 |"
              % (r["ya"][0], r["ya"][1], 100.0 * r["ya"][0] / max(r["ya"][1], 1)),
              "| 条级 flip=N | %d | %d | %.0f%% | 同上，条级匹配 |"
              % (r["na"][0], r["na"][1], 100.0 * r["na"][0] / max(r["na"][1], 1)),
              "| **条件 flip=Y** | %d | %d | %.0f%% | **仅在 B0 条级已命中的题上算净效应** |"
              % (r["cond"][0], r["cond"][1], 100.0 * r["cond"][0] / max(r["cond"][1], 1)),
              "",
              "> 条件口径的意义：无条件口径把「本来就检索不到」和「换时点后找不到新答案」"
              "混在一起，会低估版本感知能力。条件口径回答的是——**能找对的题，"
              "换时点后是否还能找对**。", ""]

    L += ["## 两设定对照（元信息贡献）", "",
          "| 指标 | conduct | full | 差值 |", "|---|---|---|---|"]
    for name, get in [("款级 P@5 B0", lambda r: r["e0"]["p5"]),
                      ("款级 P@5 B1", lambda r: r["e1"]["p5"]),
                      ("款级 MRR B0", lambda r: r["e0"]["mrr"]),
                      ("款级 MRR B1", lambda r: r["e1"]["mrr"]),
                      ("条级 flip=Y", lambda r: r["ya"][0] / max(r["ya"][1], 1)),
                      ("条级 flip=N", lambda r: r["na"][0] / max(r["na"][1], 1))]:
        cv, fv = get(res["conduct"]), get(res["full"])
        fmt = "%.1f%%" if "P@5" in name else "%.3f" if "MRR" in name else "%.0f%%"
        L.append("| %s | %s | %s | %s |" % (name, fmt % cv, fmt % fv, fmt % (fv - cv)))
    L += ["", "> 差值解读：full 与 conduct 的差距不是「系统更聪明」，而是流域元信息激活了"
          "R-a/R-b/R-c 的门控条件。conduct 设定下这些规则大多被按可废止逻辑关闭——"
          "不知道管辖事实就不用规则，这正是设计意图。", ""]

    for m in MODES:
        r = res[m]
        L += ["## 翻转逐条（%s，flip=Y，条级判定）" % m, "",
              "| 查询 | 批次 | B1 实际 top-1 | B0 金标 | B1 金标 | 判定 |", "|---|---|---|---|---|---|"]
        for qid, b, top1, g0, g1, good in r["det_y"]:
            g0k = {to_article(x) for x in g0}
            g1k = {to_article(x) for x in g1}
            t1 = to_article(top1) if top1 else None
            ga = t1 is not None and t1 in g1k and t1 not in g0k
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                qid, b, top1 or "—", " / ".join(g0) or "(空)", " / ".join(g1), "✅" if ga else "❌"))
        L += ["", "## 负对照（%s，flip=N，检验是否过度反应）" % m, "",
              "| 查询 | 批次 | B1 实际 top-1 | B0 金标 | 条级判定 |", "|---|---|---|---|---|"]
        for qid, b, top1, g0, good in r["det_n"]:
            g0k = {to_article(x) for x in g0}
            t1 = to_article(top1) if top1 else None
            ga = t1 is not None and t1 in g0k
            n_ctrl = next((rr for rr in rows if rr["query_id"] == qid and "负对照" in rr["verify_note"]), None)
            L.append("| %s%s | %s | %s | %s | %s |" % (
                qid, "（负对照）" if n_ctrl else "", b, top1 or "—", " / ".join(g0) or "(空)",
                "✅" if ga else "❌"))
        L.append("")

    L += ["## B1 逐条命中明细（full 设定，款级）", "",
          "| 查询 | 事由 | 金标 | 款级位次 | 条级位次 | Top-3 实际返回 |", "|---|---|---|---|---|---|"]
    art_rank = {d["qid"]: d["rank"] for d in res["full"]["a1"]["detail"]}
    for d in res["full"]["e1"]["detail"]:
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            d["qid"], d["q"][:24], " / ".join(d["gold"]) or "(空)", d["rank"] or "—",
            art_rank.get(d["qid"]) or "—", " / ".join(d["ids"])))

    L += ["", "## 读法", "",
          "- conduct 设定的数字是**诚实下界**：查询串不含流域信息时，R-a/R-b/R-c 多数被"
          "门控关闭（按可废止逻辑「不知道管辖事实就不用规则」），这正是设计意图；",
          "- full − conduct 差值 = 流域元信息的边际贡献，论文里应单独成一列，"
          "以分离「信息量」与「表示层」的贡献；",
          "- 条级显著高于款级，差值即「找对法条但找错款」的比例——支撑论文的**表示粒度**主张；",
          "- 比 P@5 更重要的是 **flip accuracy**：能找到相关条文却分不清新旧，是表示层缺陷；",
          "- 负对照（Q20/Q25/Q34/Q36/Q40）若 ❌，说明系统因 B1 新增援引/同构边而错误改判，"
          "这是论文必须报告的假阳性类型；",
          "- 废止法引用率若为 0，是**表示层**的贡献，与检索质量无关。"]
    io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")

    for m in MODES:
        r = res[m]
        print("[%s] P@5 款级 %.1f%%/%.1f%%  条级 %.1f%%/%.1f%%  MRR %.3f/%.3f  flip(Y条级 %d/%d N条级 %d/%d)"
              % (m, r["e0"]["p5"] * 100, r["e1"]["p5"] * 100, r["a0"]["p5"] * 100, r["a1"]["p5"] * 100,
                 r["e0"]["mrr"], r["e1"]["mrr"], r["ya"][0], r["ya"][1], r["na"][0], r["na"][1]))
    print("[写盘] %s" % OUT)


if __name__ == "__main__":
    main()
