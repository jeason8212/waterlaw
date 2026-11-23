# -*- coding: utf-8 -*-
"""答案类别判定（abstention）评测 —— v1 条文级 / v2 行为级，含逐项消融与参数网格。

三个层次的结果分开报：
1. **v1（条文级）**：`answer_class.py`，比较整条文本的覆盖率。抓 5/12。
2. **v2（行为级）**：`conduct_index.py`，比较可诉规范句（含枚举项继承）、加时间/空间效力与
   前身检验。抓 10/12，48 个非空查询零误判。
3. **消融**：把 v2 的三个图谱构件逐个拿掉，看各值多少——这是"KG 有什么用"的可指认回答。

留出口径：
- B0（12 个 none / 48 个有金标）含正例，参数在这一侧定，属 in-sample，必须标注。
- B1 真值没有 none，误弃权必须为 0；但 B1 侧 later 集合为空（语料里没有 valid_from > 2026-11-06
  的文件），判据结构上无法触发——**这一侧因此不构成有效的泛化检验**，如实写进报告，不许拿来
  当"留出验证"卖。

用法: python build/tools/eval_answer_class.py
输出: build/out/answer_class.md
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
for _p in (BUILD, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common as C          # noqa: E402
import s4_reason            # noqa: E402
import eval_queries as E    # noqa: E402
import answer_class as AC   # noqa: E402
import conduct_index as CI  # noqa: E402

OUT = os.path.join(BUILD, "out", "answer_class.md")
B0, B1 = E.B0, E.B1


def load():
    return E.load()


# ---------- 两种判据 ----------
def v1_flags(rows, con, cov):
    """条文级：返回 {qid: {as_of: (cls, signals)}}。"""
    out = {}
    for r in rows:
        out[r["query_id"]] = {}
        for as_of in (B0, B1):
            s = AC.signals(con, E.qstr(r, "full"), as_of, cov)
            cls = "none" if (s["gap"] >= AC.DELTA and s["cov_later"] >= AC.KAPPA) else "provision"
            out[r["query_id"]][as_of] = (cls, s)
    return out


def v2_flags(rows, idx, delta=CI.DELTA, kappa=CI.KAPPA, theta=CI.THETA):
    """行为级：返回 {qid: {as_of: (cls, signals)}}。basin_hint 用查询自带的流域。"""
    out = {}
    for r in rows:
        out[r["query_id"]] = {}
        for as_of in (B0, B1):
            cls, s = idx.decide(E.qstr(r, "full"), as_of, basin_hint=E.qhint(r, "full"),
                                delta=delta, kappa=kappa, theta=theta)
            out[r["query_id"]][as_of] = (cls, s)
    return out


def score(rows, flags, as_of, retrieval):
    """检索一次，判类一次，给出 (tp, fp, fn, tn, p5, acp5)。"""
    tp = fp = fn = tn = 0
    hit_n = ac_n = 0
    detail = {}
    for r in rows:
        gold = E.parse_ids(r["b0_prov_ids" if as_of == B0 else "b1_prov_ids"])
        cls = flags[r["query_id"]][as_of][0]
        ids = [] if cls == "none" else retrieval(r, as_of)
        hit = bool(gold) and bool(ids) and any(
            E.to_article(p) in {E.to_article(g) for g in gold} for p in ids)
        if gold:
            tn += cls == "provision"
            fp += cls == "none"
            hit_n += hit
            ac_n += hit
        else:
            tp += cls == "none"
            fn += cls == "provision"
            ac_n += cls == "none"
        detail[r["query_id"]] = dict(cls=cls, hit=hit, ids=ids[:2], gold=gold)
    n = max(len(rows), 1)
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, n=len(rows),
                p5=hit_n / n, acp5=ac_n / n, detail=detail)


def main():
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = load()

    def retrieve(r, as_of):
        return [x["prov_id"] for x in s4_reason.find_law(
            con, E.qstr(r, "full"), as_of, k=5, basin_hint=E.qhint(r, "full"))]

    cov = AC._scorer(con)
    idx = CI.index(con)
    f1 = v1_flags(rows, con, cov)
    f2 = v2_flags(rows, idx)
    s1_0, s1_1 = score(rows, f1, B0, retrieve), score(rows, f1, B1, retrieve)
    s2_0, s2_1 = score(rows, f2, B0, retrieve), score(rows, f2, B1, retrieve)
    n_empty = s2_0["tp"] + s2_0["fn"]

    # ---------- 消融 ----------
    abl = []
    idx_nb = CI.index(con, basin_gate=False)
    idx_ni = CI.index(con, inherit=False)
    abl.append(("v2 − 流域效力过滤（长江法可用于淮河查询）", v2_flags(rows, idx_nb)))
    abl.append(("v2 − 枚举项继承（declarative 清单项不入索引）", v2_flags(rows, idx_ni)))
    abl.append(("v2 − 前身检验（theta=1.01，即只比落差）",
                v2_flags(rows, idx, theta=1.01)))

    L = ["# 答案类别判定（abstention）评测：条文级 vs 行为级", "",
         "> 生成：`python build/tools/eval_answer_class.py`｜v2 参数 delta=%.2f（落差下限）、"
         "kappa=%.2f（后法侧最低覆盖）、theta=%.2f（前身相似度上限）" % (CI.DELTA, CI.KAPPA, CI.THETA),
         "> v1 见 `build/answer_class.py`（条文全文比较）；v2 见 `build/conduct_index.py`（可诉规范句比较，"
         "conduct 节点已物化进 `kg.db`，%d 条）。" % idx.N, "",
         "## 一、判定结果", "",
         "| 判据 | 时点 | 空金标 | 判对 | 误判 | 漏判 | 准确率 |",
         "|---|---|---|---|---|---|---|"]
    for name, s0, s1 in (("v1 条文级", s1_0, s1_1), ("**v2 行为级**", s2_0, s2_1)):
        for tag, s in ((B0, s0), (B1, s1)):
            L.append("| %s | %s%s | %d | %d | %d | %d | %d/%d = %.1f%% |"
                     % (name, tag, "（in-sample）" if tag == B0 else "",
                        s["tp"] + s["fn"], s["tp"], s["fp"], s["fn"],
                        s["tp"] + s["tn"], s["n"], 100.0 * (s["tp"] + s["tn"]) / s["n"]))
    L += ["", "B1 的「误判 0」这一格**不能当作泛化证据**：语料里没有任何 valid_from > %s 的文件，"
          "later 集合为空，判据在 B1 结构上不可能触发。真正的负例检验是 B0 侧那 48 个非空金标查询"
          "（误判 %d 个）。这一点必须在正文里写清，否则就是把一个空检验当成了留出验证。" % (B1, s2_0["fp"]), ""]

    L += ["## 二、指标增益（full 设定，60 查询）", "",
          "| 时点 | 系统 | P@5 | AC-P@5 | 增量 |", "|---|---|---|---|---|"]
    for tag, s, base in ((B0, s2_0, s2_0["p5"]), (B1, s2_1, s2_1["p5"])):
        L.append("| %s | v1 条文级 + 弃权层 | %.1f%% | %.1f%% | %+.1f |"
                 % (tag, 100 * s1_0["p5"] if tag == B0 else 100 * s1_1["p5"],
                    100 * (s1_0["acp5"] if tag == B0 else s1_1["acp5"]),
                    100 * ((s1_0["acp5"] if tag == B0 else s1_1["acp5"])
                           - (s1_0["p5"] if tag == B0 else s1_1["p5"]))))
        L.append("| %s | **v2 行为级 + 弃权层** | %.1f%% | **%.1f%%** | **%+.1f** |"
                 % (tag, 100 * base, 100 * s["acp5"], 100 * (s["acp5"] - base)))
    L += ["", "AC-P@5 的上限是 P@5 + 20 个百分点（12 个空答案查询各值 1 分，12/60 = 20pp）。"
          "v2 拿到 %+.1f 个百分点，即回收可获得空间的 **%.0f%%**；v1 只回收 %.0f%%。"
          % (100 * (s2_0["acp5"] - s2_0["p5"]),
             100 * (s2_0["acp5"] - s2_0["p5"]) / 0.20,
             100 * (s1_0["acp5"] - s1_0["p5"]) / 0.20), ""]

    # ---------- 消融 ----------
    empty_gold = {r["query_id"]: not E.parse_ids(r["b0_prov_ids"]) for r in rows}
    L += ["## 三、逐项消融：三个图谱构件各值多少", "",
          "| 设定 | B0 召回 | B0 误判 | 丢失的查询 | 新出现的误判 |", "|---|---|---|---|---|"]
    caught2 = {q for q, v in f2.items() if v[B0][0] == "none"}
    abl_res = []
    for label, fb in abl:
        got = {q for q, v in fb.items() if v[B0][0] == "none"}
        lost = sorted(caught2 - got)
        new_fp = sorted(q for q in got if not empty_gold[q])
        abl_res.append((label, got, lost, new_fp))
        L.append("| %s | %d/%d | %d | %s | %s |"
                 % (label, sum(1 for q in got if empty_gold[q]), n_empty, len(new_fp),
                    "、".join(lost) if lost else "—",
                    "、".join(new_fp) if new_fp else "—"))
    L += ["",
          "三行分别说明三件事：",
          "",
          "1. **流域效力过滤**：拿掉后丢失 %d 例（%s）——淮河查询被长江法/黄河法的平行条文"
          "「补上了依据」，落差消失。这不是精度调优，是空间效力：长江保护法第2条、黄河保护法第2条"
          "各自限定流域，淮河的事由它们管不着。检索基线里这层信息只能靠降权近似（R-a），"
          "判类时则必须是硬约束。附带一个结构事实：丢失的 5 例**全部是淮河查询**——"
          "淮河是语料里唯一没有流域专门法的流域，空间效力过滤只会在这一侧改变结论。"
          % (len(abl_res[0][2]), "、".join(abl_res[0][2]) or "—"),
          "2. **枚举项继承**：拿掉后丢失 %d 例（%s）——法典第730条的采砂禁令写在「（二）」项里，"
          "该句在 norm 表里是 declarative（不计入职责矩阵），但它的可诉性来自引导句的「禁止」。"
          "不继承，索引里就没有这条禁令，Q31 的「后法已有明确依据」这一半就塌了。"
          % (len(abl_res[1][2]), "、".join(abl_res[1][2]) or "—"),
          "3. **前身检验**：拿掉后新增 %d 个假弃权（%s）——法典把旧法条文重述一遍时，"
          "落差同样很大（写得更规整，覆盖率自然更高）。前身检验问的是「这条后法行为在旧法里找不找得到"
          "对应的一条」，找到就不许弃权。没有它，任何「重述型」查询都会被误判成制度空白。"
          % (len(abl_res[2][3]), "、".join(abl_res[2][3]) or "—"),
          "",
          "**这三个构件都不是检索特征，而是图谱字段**：doc.valid_from/to（时间效力）、"
          "doc→Watershed（空间效力）、norm.deontic（规范模态）。换句话说，"
          "答案类别判定吃的是图谱的结构，不是文本相似度。", ""]

    # ---------- 参数网格 ----------
    S = {q: f2[q][B0][1] for q in f2}
    pos = [q for q in S if empty_gold[q]]
    neg = [q for q in S if not empty_gold[q]]

    def fires(s, d, k, t):
        return s["gap"] >= d and s["cov_later"] >= k and s["sim"] < t

    feasible = []
    for d in [x / 100.0 for x in range(10, 39)]:
        for k in (0.40, 0.42, 0.44, 0.45):
            for t in [x / 100.0 for x in range(18, 42)]:
                tp = [q for q in pos if fires(S[q], d, k, t)]
                fp = [q for q in neg if fires(S[q], d, k, t)]
                if len(tp) == 10 and not fp:
                    feasible.append((d, k, t))
    ds = [f[0] for f in feasible] or [0]
    ts = [f[2] for f in feasible] or [0]
    ks = sorted({f[1] for f in feasible}) or [0]

    L += ["## 四、参数网格：可行域有多大", "",
          "在 delta ∈ [0.10, 0.38]（步长 0.01）× kappa ∈ {0.40, 0.42, 0.44, 0.45} × "
          "theta ∈ [0.18, 0.41]（步长 0.01）上穷举，要求「召回 10/12 且误判 0/48」：", "",
          "| 项 | 值 |", "|---|---|",
          "| 满足条件的设定数 | **%d / %d** |" % (len(feasible), 29 * 4 * 24),
          "| delta 可行区间 | [%.2f, %.2f] |" % (min(ds), max(ds)),
          "| kappa 可行取值 | %s |" % "、".join("%.2f" % x for x in ks),
          "| theta 可行区间 | [%.2f, %.2f] |" % (min(ts), max(ts)),
          "| 选定 | delta=%.2f、kappa=%.2f、theta=%.2f |" % (CI.DELTA, CI.KAPPA, CI.THETA),
          "",
          "可行域跨 delta 近 0.2、theta 近 0.2，说明结论不是刀锋上的巧合；"
          "但同时要承认：这 12 个正例本身就在眼前，网格只能证明**稳健性**，不能证明**无偏**。"
          "最小边距只有 0.018（Q37 的 later 覆盖率 0.468 对 kappa=0.45），这是全表最紧的一处，须如实报告。", ""]

    # ---------- 逐条 ----------
    L += ["## 五、12 个空答案查询逐条（B0，v2 信号）", "",
          "| 查询 | 事由 | cov(t) | cov(后法) | 落差 | 前身 | 判定 | 后法依据 |",
          "|---|---|---|---|---|---|---|---|"]
    by_q = {r["query_id"]: r for r in rows}
    for q in sorted(pos, key=lambda q: -S[q]["gap"]):
        s = S[q]
        mark = "none ✅" if f2[q][B0][0] == "none" else "漏判 ❌"
        L.append("| %s | %s | %.3f | %.3f | %+.3f | %.3f | %s | %s |"
                 % (q, by_q[q]["regulated_conduct"][:20], s["cov_asof"], s["cov_later"],
                    s["gap"], s["sim"], mark, s["prov_later"] or "—"))
    L += ["", "紧邻的四个负例（表格外，用于看边界）：", "",
          "| 查询 | cov(t) | cov(后法) | 落差 | 前身 | 为什么没弃权 |", "|---|---|---|---|---|---|"]
    near = sorted(neg, key=lambda q: -S[q]["gap"])[:4]
    for q in near:
        s = S[q]
        why = []
        if s["gap"] < CI.DELTA:
            why.append("落差不足")
        if s["cov_later"] < CI.KAPPA:
            why.append("后法覆盖不足")
        if s["sim"] >= CI.THETA:
            why.append("前身相似 %.3f ≥ %.2f（旧法已有对应行为）" % (s["sim"], CI.THETA))
        L.append("| %s | %.3f | %.3f | %+.3f | %.3f | %s |"
                 % (q, s["cov_asof"], s["cov_later"], s["gap"], s["sim"], "；".join(why)))
    L += [""]

    # ---------- 残留 ----------
    missed = sorted(q for q in pos if f2[q][B0][0] != "none")
    L += ["## 六、残留 %d 例：为什么调参解决不了" % len(missed), ""]
    for q in missed:
        s = S[q]
        L.append("- **%s**（%s）：cov(t)=%.3f、cov(后法)=%.3f、落差 %+.3f、前身 %.3f。"
                 % (q, by_q[q]["regulated_conduct"][:26], s["cov_asof"], s["cov_later"],
                    s["gap"], s["sim"]))
    L += ["",
          "两例的共同点是**词汇未漂移、制度已位移**：",
          "- Q42 的旧法（水污染防治法第31条「跨行政区域水污染纠纷协商解决」）确实建了一套跨区域协调制度，"
          "只是不是法典第21条的「四统一」；前身相似度 0.479，判据据此认为「旧法已有」——按字面它是对的。",
          "- Q23 更极端：旧法第25条逐字含「建立监测数据共享机制」，落差 0.000。差别在制度位置"
          "（旧法放在监测网络制度里，法典升格为独立的共享平台义务），字符 bigram 表达不了。",
          "",
          "这与 Q22/Q43/Q58 的「词汇漂移」（同一个制度、换了一套词）正好相反，是表示层的另一个方向。"
          "两条合起来构成对「检索式表示」的完整批评：**它既漏掉换词的同义，也漏掉同词的易位。**", ""]

    # ---------- 结论 ----------
    L += ["## 七、结论与写法", "",
          "1. **可写**：行为级判据在 12 个空答案查询上抓到 %d 个、48 个非空查询零误判；"
          "AC-P@5 由 %.1f%% 提到 %.1f%%。逐项消融给出三个图谱构件各自的贡献（流域过滤→Q48、"
          "枚举继承→Q31、前身检验→挡住 4 个假弃权）。"
          % (s2_0["tp"], 100 * s2_0["p5"], 100 * s2_0["acp5"]),
          "2. **不可写**：不能说「解决了空答案问题」，也不能说「B1 留出验证通过」——B1 侧判据无法触发，"
          "那个 0 是空的。",
          "3. **论文定位**：答案类别是独立于检索的任务；条文级表示抓不到，是因为它把「谈这个题目」与「规范这个行为」混为一谈；行为级表示抓得住，靠的是图谱里本来就有的三个字段。"
          "这条同时回答了「KG 相对纯检索的增量在哪」——不在排序分上，在**能否表达「该不该给答案」**。",
          "4. **样本量**：12 个正例，任何 F1 都不具统计意义；报告召回与误判数，不报 F1。", ""]

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")

    sys.stdout.write("v1: TP=%d FP=%d | v2: TP=%d FP=%d（B0）| AC-P@5 %.1f%% -> %.1f%%\n"
                     % (s1_0["tp"], s1_0["fp"], s2_0["tp"], s2_0["fp"],
                        100 * s2_0["p5"], 100 * s2_0["acp5"]))
    sys.stdout.write("可行设定 %d 组；报告：%s\n" % (len(feasible), OUT))


if __name__ == "__main__":
    main()
