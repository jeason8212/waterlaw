# -*- coding: utf-8 -*-
"""三条可废止规则（R-a 流域范围 / R-b LEX_SPECIALIS 优先 / R-c 援引穿透）的消融与归因。

为什么单独一个脚本：`run_baselines.py` 的 P@5 只问"金标在不在 top-5"，而这三条规则
**只重排已召回的候选**，本来就不改变 top-5 的成员构成 —— 用 P@5 评价它们会得出"没用"的
错误结论。它们真正影响的是 **top-1**（也就是 flip 判定的对象）和 MRR。所以这里按
「top-1 变动 × 变动是否正确」逐条归因。

输出：
  build/out/ablation_rules.md   论文 B7 消融节的证据（留一规则 + 逐查询归因 + 病例）

用法: python build/baselines/analyze_ablation.py
"""
import os
import sqlite3
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
TOOLS = os.path.join(BUILD, "tools")
for _p in (HERE, BUILD, TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common as C
import s4_reason
import eval_queries as E
import bs_core as B

OUT = os.path.join(BUILD, "out", "ablation_rules.md")
B0, B1 = E.B0, E.B1
RULES = [("R-a", "bc", "流域范围（空间效力）"),
         ("R-b", "ac", "LEX_SPECIALIS 特别法优先"),
         ("R-c", "ab", "援引穿透（通道条→规范本体）")]


def top(con, q, as_of, hint, rules, k=5):
    return s4_reason.find_law(con, q, as_of, k=k, basin_hint=hint, rules=rules)


def ok(gold, prov, level):
    if not gold or not prov:
        return None
    if level == "article":
        return E.to_article(prov) in {E.to_article(g) for g in gold}
    return prov in gold


def main():
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = B.load_queries()
    L = ["# 三条可废止规则的消融与归因（B7 证据）", "",
         "语料 %d 款；B0 池 %d、B1 池 %d。判定时点与金标同 `eval_queries`。"
         % (len(B.Corpus(con).ids), len(B.Corpus(con).pool(B0)), len(B.Corpus(con).pool(B1))), "",
         "## 一、留一规则：top-1 与 MRR 的净变化（full 设定）", "",
         "| 设定 | B0 MRR | B1 MRR | B0 top-1 正确 | B1 top-1 正确 | 合计正确（108 题次） |",
         "|---|---|---|---|---|---|"]

    settings = [("B8 完整（abc）", "abc")] + [("−%s %s" % (r[0], r[2]), r[1]) for r in RULES]
    cache = {}
    fire = {}                    # 规则 -> 在 top-5 中触发的槽位数
    for label, rules in settings:
        mrr = {B0: 0.0, B1: 0.0}
        cnt = {B0: 0, B1: 0}
        n = {B0: 0, B1: 0}
        for r in rows:
            for as_of, key in ((B0, "b0_prov_ids"), (B1, "b1_prov_ids")):
                gold = E.parse_ids(r[key])
                if not gold:
                    continue
                got = top(con, E.qstr(r, "full"), as_of, E.qhint(r, "full"), rules)
                ids = [g["prov_id"] for g in got]
                cache[(label, r["query_id"], as_of)] = ids
                if label.startswith("B8"):
                    for g in got:
                        for tag in ("R-a", "R-b", "R-c"):
                            if tag in (g.get("priority_note") or ""):
                                fire[tag] = fire.get(tag, 0) + 1
                n[as_of] += 1
                rk = next((i + 1 for i, p in enumerate(ids)
                           if ok(gold, p, "article")), None)
                mrr[as_of] += 1.0 / rk if rk else 0.0
                cnt[as_of] += 1 if rk == 1 else 0
        L.append("| %s | %.3f | %.3f | %d/%d | %d/%d | %d/%d |"
                 % (label, mrr[B0] / max(n[B0], 1), mrr[B1] / max(n[B1], 1),
                    cnt[B0], n[B0], cnt[B1], n[B1], cnt[B0] + cnt[B1], n[B0] + n[B1]))
    L += ["", "> 表内分母只算**有金标**的题次（B0 48 题、B1 60 题；B0 有 12 题金标为 `(none)`，"
          "不进 MRR 与 top-1 口径），与 `eval_queries.py` 以 60 为分母的 P@5 不是同一口径，勿混用。",
          "> 触发频次（B8，full 设定，600 个 top-5 槽位）："
          + "、".join("%s %d 次" % (k, fire.get(k, 0)) for k in ("R-a", "R-b", "R-c"))
          + "。触发 ≠ 决胜：只有改变了 top-1 才影响翻转判定。", ""]

    # ---------------- 逐查询归因
    L += ["", "## 二、逐查询归因：去掉该规则后 top-1 是否变、变得对不对", "",
          "只列 top-1 发生变化的题次；`增益` = 完整方法对、去规则后错，`损害` = 反之。", ""]
    summary = []
    for tag, keep, desc in RULES:
        gain, harm, neutral = [], [], []
        for r in rows:
            for as_of, key in ((B0, "b0_prov_ids"), (B1, "b1_prov_ids")):
                gold = E.parse_ids(r[key])
                if not gold:
                    continue
                full = cache[("B8 完整（abc）", r["query_id"], as_of)]
                abl = cache[("−%s %s" % (tag, desc), r["query_id"], as_of)]
                if full[:1] == abl[:1]:
                    continue
                f_ok = ok(gold, full[0] if full else None, "article")
                a_ok = ok(gold, abl[0] if abl else None, "article")
                rec = (r["query_id"], as_of, abl[0] if abl else "-", full[0] if full else "-",
                       ";".join(gold), f_ok, a_ok)
                if f_ok and not a_ok:
                    gain.append(rec)
                elif a_ok and not f_ok:
                    harm.append(rec)
                else:
                    neutral.append(rec)
        churn = len(gain) + len(harm) + len(neutral)
        summary.append((tag, desc, churn, len(gain), len(harm)))
        L += ["### %s %s —— top-1 变动 %d 题次（增益 %d / 损害 %d / 中性 %d）"
              % (tag, desc, churn, len(gain), len(harm), len(neutral)), ""]
        if gain or harm or neutral:
            L += ["| 查询 | 时点 | 去规则后 top-1 | 完整方法 top-1 | 金标 | 判定 |", "|---|---|---|---|---|---|"]
            for rec in gain:
                L.append("| %s | %s | %s | **%s** | %s | 增益 |"
                         % (rec[0], rec[1], rec[2], rec[3], rec[4]))
            for rec in harm:
                L.append("| %s | %s | %s | %s | %s | 损害 |"
                         % (rec[0], rec[1], rec[2], rec[3], rec[4]))
            for rec in neutral:
                tag_n = "中性（两条皆对）" if (rec[5] and rec[6]) else "中性（两条皆错）"
                L.append("| %s | %s | %s | %s | %s | %s |"
                         % (rec[0], rec[1], rec[2], rec[3], rec[4], tag_n))
        else:
            L.append("_该规则在本样本上不改变任何 top-1。_")
        L.append("")

    L += ["## 三、结论口径（写论文时照此表述）", "",
          "| 规则 | top-1 变动 | 增益 | 损害 | 净 |", "|---|---|---|---|---|"]
    for tag, desc, churn, g, h in summary:
        L.append("| %s %s | %d | %d | %d | %+d |" % (tag, desc, churn, g, h, g - h))
    L += ["",
          "**必须诚实写进正文的三点**：",
          "1. 三条规则**不改变 top-5 召回**（P@5 与消融版完全相同），它们只重排已召回的候选；"
          "因此不能用 P@5 证明其必要性，只能用 top-1 / MRR / flip 准确率。",
          "2. 本样本上每条规则的影响都很小（见上表），单条规则的增益不构成统计证据；"
          "结论只能表述为「在某些可指认的案件上纠正了排序位移」，不能表述为「显著提升」。",
          "3. 若某规则出现「损害 > 增益」，必须在正文点明并给出反例，而不是删掉该行——"
          "这是可废止规则（defeasible）的固有代价，也是本文与硬编码优先级方案的区别。", ""]

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    for tag, desc, churn, g, h in summary:
        sys.stdout.write("%s churn=%d gain=%d harm=%d\n" % (tag, churn, g, h))
    sys.stdout.write("报告：%s\n" % OUT)


if __name__ == "__main__":
    main()
