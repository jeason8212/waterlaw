# -*- coding: utf-8 -*-
"""答案类别判定（abstention）的 **v1 条文级判据**：在给定时点，该事由**在本语料里有没有**规范依据。

> ⚠️ 现状（2026-11-16）：默认判据已换成 `conduct_index.py` 的 **v2 行为级**版本
> （`apply_abstention(engine="conduct")`）。v2 在同一批查询上从 5/12 提到 10/12、误判仍为 0。
> 本文件保留 v1 作为**消融对照**与判据演化记录，别把它当最新结果引用。

为什么单列一个模块：T1 的 60 个查询里有 12 个在 B0（法典施行前）金标就是 `(none)`——考的不是
"找对哪一条"，而是"该不该给答案"。当前排序器对这类查询一律返回 5 条条文，P@5 里全记成错，
但那是**答案类别误差**，不是检索误差（`paper/05` §7.6 已分解过）。

## 机制（不是相似度阈值，而是时间溯源）

一条查询在时点 t 的答案之所以是 `(none)`，法律上有两种成因：
  (i) **制度尚未设立**——该事由的规范依据由后来施行的法典创设（如"自然湿地擅自采砂""入河排污口
      排查整治"），t 时点的语料里根本没有对应制度；
  (ii) **本语料未覆盖**——该事由从来不由这六部法调整（如"毁林开垦"属森林法）。

本模块只判 (i)，判据是**时间溯源**：

    gap = cov(将来才施行的文件) − cov(t 时点有效文件)

若 later 的法典对同一事由的覆盖远高于 t 时点有效法，且法典确实给出了明确依据（cov_later ≥ κ），
说明"制度是法典新设的"，t 时点应回 `(none)`。这条判据有清晰的法理解释，且**不需要看金标**：
它比较的是"同一查询在两个时点的最佳匹配"，而不是"匹配分够不够高"。

为什么不用绝对阈值：实测绝对覆盖率无法区分——空答案查询的 cov 从 0.114 到 0.647 连续分布，
非空答案查询最低只有 0.126（Q27），单一阈值必然两头误判。见 `build/out/answer_class.md` 的分布表。

## 已知边界（必须写进论文）

12 个空答案查询里只有 5 个是 (i) 型，其余 7 个是 (ii) 型或"字面覆盖但规范不覆盖"（Q24 岸线强度
控制、Q48 生态水量调度：旧法有同主题条文，缺的是具体行为义务）。**任何基于文本相似度的判据都
找不出这 7 个**，要解决得靠"事由→首次规范依据"的索引（数据工程），不是换阈值。本文如实报告这一点。
"""
import math
import sqlite3
import sys

import os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import common as C
import s4_reason

# 判据参数（由法理与分布形态确定，非网格搜索最优值；敏感性见 eval_answer_class.py 的 δ 扫描）
DELTA = 0.25        # 时间溯源落差下限：later 比 as-of 高出 25 个百分点的加权覆盖率
KAPPA = 0.45        # later 侧须达到的最低覆盖：确保"后来确实有明确依据"，而非泛泛相关


def _scorer(con):
    """复用排序器的 bigram 倒排与 IDF，保证与 find_law 同一特征口径。"""
    items, df, N = s4_reason._index(con)
    terms = s4_reason.load_terms()

    def cov(query, doc_filter):
        qbg = s4_reason.expand(query, terms)
        if not qbg:
            return 0.0
        w = {g: math.log(1.0 + N / (1.0 + df.get(g, 0))) for g in qbg}
        tot = sum(w.values()) or 1.0
        best = 0.0
        for pid, did, bg in items:
            if did not in doc_filter:
                continue
            c = sum(wt for g, wt in w.items() if g in bg) / tot
            if c > best:
                best = c
        return best

    return cov


def _docs(con):
    return {r[0]: (r[1] or "", r[2] or "") for r in
            con.execute("SELECT doc_id, valid_from, valid_to FROM doc")}


def signals(con, query, as_of, cov=None):
    """返回该查询在 as_of 的答案类别信号。

    cov_asof   ：该时点有效文件中的最佳加权覆盖率
    cov_later  ：**尚未施行**文件（valid_from > as_of）中的最佳覆盖率；无此类文件时为 0
    coc_later  ：同类文件中的"已废止"文件覆盖率（仅诊断用，不参与判定）
    """
    cov = cov or _scorer(con)
    docs = _docs(con)
    asof_ids = {d for d, (vf, vt) in docs.items()
                if (not vf or as_of >= vf) and (not vt or as_of <= vt)}
    later_ids = {d for d, (vf, vt) in docs.items() if vf and as_of < vf}
    past_ids = {d for d, (vf, vt) in docs.items() if vt and as_of > vt}
    ca = cov(query, asof_ids)
    cl = cov(query, later_ids) if later_ids else 0.0
    cp = cov(query, past_ids) if past_ids else 0.0
    return {"cov_asof": round(ca, 4), "cov_later": round(cl, 4), "cov_past": round(cp, 4),
            "gap": round(cl - ca, 4), "n_later_docs": len(later_ids)}


def decide(con, query, as_of, cov=None, delta=DELTA, kappa=KAPPA):
    """返回 (answer_class, evidence)。answer_class ∈ {"provision", "none"}。"""
    s = signals(con, query, as_of, cov)
    if s["gap"] >= delta and s["cov_later"] >= kappa:
        return "none", s
    return "provision", s


def apply_abstention(con, query, as_of, k=5, basin_hint=None, rules="abc",
                     engine="conduct", syn=False):
    """接在排序器前面的一层：判 `none` 就返回空列表，否则照常检索。

    engine="conduct"（默认）：v2 行为级判据（`conduct_index.py`）——比较可诉规范句，并用
        doc 的时间/空间效力与前身检验把关。当前成绩见 `build/out/answer_class.md`。
    engine="provision"：v1 条文级判据（本模块 `decide`），保留作消融对照。
    syn：透传给 s4_reason.find_law 的跨代概念词典开关（post-hoc 扩展 B8+S 用），
        弃权判据本身不使用词典。
    """
    if engine == "conduct":
        import conduct_index as CI
        cls, ev = CI.index(con).decide(query, as_of, basin_hint=basin_hint)
    else:
        cls, ev = decide(con, query, as_of)
    if cls == "none":
        return [], ev
    return [x["prov_id"] for x in
            s4_reason.find_law(con, query, as_of, k=k, basin_hint=basin_hint,
                               rules=rules, syn=syn)], ev


if __name__ == "__main__":
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    for q, d in (("在自然湿地擅自采砂", "2026-06-30"), ("河湖岸线开发利用强度控制", "2026-06-30"),
                 ("在自然湿地擅自采砂", "2026-11-06")):
        cls, ev = decide(con, q, d)
        sys.stdout.write("%s @ %s → %s %s\n" % (q, d, cls, ev))
