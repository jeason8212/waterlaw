# -*- coding: utf-8 -*-
"""跨代概念词典（SYN）与 R-d 罚则降权的全量回归评测。

四个设定（其余全部相同）：
- base  ：rules="abc", syn=False   —— 冻结主系统 B8（基准，数字须与 query_eval.md 一致）
- syn   ：rules="abc", syn=True    —— B8+SYN（仅词典）
- rd    ：rules="abcd", syn=False  —— B8+RD（仅罚则降权）
- synrd ：rules="abcd", syn=True   —— B8+SYN+RD

输出 build/out/synonym_report.md：总体指标对照 + 逐查询变化（修复/回归/不变）。

报告纪律：SYN 与 RD 均为 **post-hoc 组件**（2026-11-16 测试集错误分析 Q22/Q43/Q58
之后构造），动机、词典法律依据、回归清单必须全部披露；本文件数字不得并入冻结主表，
只作扩展行（B8+SYN / B8+SYN+RD）报告。

用法: python tools/eval_synonym.py
"""
import io
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import s4_reason
import common as C
from tools.eval_queries import load, qstr, qhint, parse_ids, to_article

OUT = os.path.join(C.OUT_DIR, "synonym_report.md")
B0, B1 = "2026-06-30", "2026-11-06"
MODES = ("conduct", "full")
CONFIGS = {
    "base": dict(rules="abc", syn=False),
    "syn": dict(rules="abc", syn=True),
    "rd": dict(rules="abcd", syn=False),
    "synrd": dict(rules="abcd", syn=True),
}


def eval_config(con, rows, mode, rules, syn):
    """单设定全指标 + 逐查询（B0/B1 rank、top1）。"""
    det = {}
    p5, rr = {"B0": 0, "B1": 0}, {"B0": 0.0, "B1": 0.0}
    for asof, key in ((B0, "b0_prov_ids"), (B1, "b1_prov_ids")):
        tlab = "B0" if asof == B0 else "B1"
        for r in rows:
            gold = parse_ids(r[key])
            got = s4_reason.find_law(con, qstr(r, mode), asof, k=5,
                                     basin_hint=qhint(r, mode), rules=rules, syn=syn)
            ids = [g["prov_id"] for g in got]
            rank = next((i + 1 for i, pid in enumerate(ids) if pid in gold), None) if gold else None
            if rank:
                p5[tlab] += 1
                rr[tlab] += 1.0 / rank
            det.setdefault(r["query_id"], {})[tlab] = dict(
                rank=rank, top1=ids[0] if ids else None, gold=gold)
    n = max(len(rows), 1)
    # flip（款级）
    fy = fn = 0
    ny = nn = 0
    for r in rows:
        g0, g1 = parse_ids(r["b0_prov_ids"]), parse_ids(r["b1_prov_ids"])
        d = det[r["query_id"]]["B1"]
        top1 = d["top1"]
        if r["flip"] == "Y":
            ny += 1
            fy += 1 if (top1 and top1 in g1 and top1 not in g0) else 0
        else:
            nn += 1
            fn += 1 if (top1 and top1 in g0) else 0
    return dict(p5={k: v / n for k, v in p5.items()}, rr={k: v / n for k, v in rr.items()},
                fy=fy, ny=ny, fn=fn, nn=nn, det=det, n=n)


def main():
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = load()
    res = {cfg: {m: eval_config(con, rows, m, **kw) for m in MODES}
           for cfg, kw in CONFIGS.items()}

    # 冻结自检：base 数字须与 query_eval.md 的冻结基线一致
    ok = True
    frozen = {("conduct", "B0"): 0.783, ("conduct", "B1"): 0.967,
              ("full", "B0"): 0.783, ("full", "B1"): 0.967}
    for (m, t), v in frozen.items():
        if abs(res["base"][m]["p5"][t] - v) > 0.002:
            ok = False
            print("[FAIL] base %s %s P@5=%.3f 期望 %.3f" % (m, t, res["base"][m]["p5"][t], v))

    L = ["# 跨代概念词典（SYN）× 罚则降权（R-d）回归评测", "",
         "> 自动生成，请勿手改。重跑：`python build/tools/eval_synonym.py`", "",
         "**报告纪律**：SYN（corpus/18 跨代概念对照词典，5 条对照）与 R-d（罚则降权，"
         "规范型查询门控）均为 **post-hoc 组件**——2026-11-16 在测试集错误分析"
         "（Q22/Q43/Q58：法典编纂概念替换导致的词汇漂移 / 罚则复述触发要件）之后构造。"
         "每条词典对照均有独立于测试集的法律依据（旧法条文 ↔ 法典条文逐字对照），"
         "但按 paper/03 报告纪律第 5 条，只能以扩展行（B8+SYN / B8+SYN+RD）报告，"
         "冻结主表 B8 数字不变。", "",
         "动机病例：", "",
         "- Q22（突发水污染事故→突发生态环境事件，P-01）：B1 金标 L-001#117-1 原先"
         "**不在 top-5**（词簇整体不可见）；",
         "- Q43（水污染损害赔偿纠纷调解→侵害他人合法权益调解，P-05）：B1 金标 "
         "L-001#1077-1 原先不在 top-5（「赔偿责任」词簇全部落空）；",
         "- Q58（罚则复述触发要件）：B1 top-1 是 L-001#1109-2（第329条的罚则款），"
         "靠「设置/限制」等偶然词压过行为规范款。", "",
         "冻结自检（base 与 query_eval.md 一致）：**%s**" % ("PASS" if ok else "FAIL"), ""]

    for m in MODES:
        L += ["## 设定 %s" % m, "",
              "| 系统 | P@5 B0 | P@5 B1 | MRR B0 | MRR B1 | flip=Y | flip=N |",
              "|---|---|---|---|---|---|---|"]
        for cfg in CONFIGS:
            r = res[cfg][m]
            L.append("| %s | %.1f%% | %.1f%% | %.3f | %.3f | %d/%d | %d/%d |" % (
                cfg, r["p5"]["B0"] * 100, r["p5"]["B1"] * 100,
                r["rr"]["B0"], r["rr"]["B1"], r["fy"], r["ny"], r["fn"], r["nn"]))
        L.append("")

    # 逐查询变化（相对 base，款级 rank）
    L += ["## 逐查询变化（synrd 相对 base；款级 rank，—=不在 top-5）", "",
          "只列出有变化的查询；未列出的查询四个设定位次完全相同。", ""]
    for m in MODES:
        base_det = res["base"][m]["det"]
        syn_det = res["synrd"][m]["det"]
        L += ["### %s" % m, "",
              "| 查询 | base B0 rank | synrd B0 rank | base B1 rank | synrd B1 rank | "
              "base B1 top-1 | synrd B1 top-1 |", "|---|---|---|---|---|---|---|"]
        nchg = 0
        for qid in sorted(base_det, key=lambda x: int(x[1:])):
            b, s = base_det[qid], syn_det[qid]
            if b["B0"]["rank"] == s["B0"]["rank"] and b["B1"]["rank"] == s["B1"]["rank"] \
               and b["B1"]["top1"] == s["B1"]["top1"]:
                continue
            nchg += 1
            L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                qid, b["B0"]["rank"] or "—", s["B0"]["rank"] or "—",
                b["B1"]["rank"] or "—", s["B1"]["rank"] or "—",
                b["B1"]["top1"] or "—", s["B1"]["top1"] or "—"))
        if not nchg:
            L.append("| （无变化） | | | | | | |")
        L.append("")

    # SYN 与 RD 的分别归因（动机查询）
    L += ["## 分量归因（动机查询，conduct 设定，B1 款级 rank）", "",
          "| 查询 | base | +SYN | +RD | +SYN+RD | 金标 |", "|---|---|---|---|---|---|"]
    for qid in ("Q22", "Q43", "Q58"):
        row = [qid]
        for cfg in ("base", "syn", "rd", "synrd"):
            row.append(str(res[cfg]["conduct"]["det"][qid]["B1"]["rank"] or "—"))
        row.append(" / ".join(res["base"]["conduct"]["det"][qid]["B1"]["gold"]))
        L.append("| %s |" % " | ".join(row))
    L += ["", "> Q22 残余：top-1 为 L-001#121-2（企业侧应急物资与处置条款，同样含"
          "「突发生态环境事件」）——词典解决了**可见性**（金标从 top-5 外升入），"
          "剩余是两条同概念法典条款之间的细粒度排序，属语义相关度问题而非词汇漂移，"
          "如实报告不强行调参。", ""]

    io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    for m in MODES:
        r = res["synrd"][m]
        print("[%s] base P@5 %.1f%%/%.1f%% -> synrd %.1f%%/%.1f%%  flipY %d/%d flipN %d/%d" % (
            m, res["base"][m]["p5"]["B0"] * 100, res["base"][m]["p5"]["B1"] * 100,
            r["p5"]["B0"] * 100, r["p5"]["B1"] * 100, r["fy"], r["ny"], r["fn"], r["nn"]))
    print("[自检] %s" % ("PASS" if ok else "FAIL"))
    print("[写盘] %s" % OUT)


if __name__ == "__main__":
    main()
