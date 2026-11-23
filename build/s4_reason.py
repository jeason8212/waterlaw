# -*- coding: utf-8 -*-
"""S4 推理：三类查询 + 一项审计，全部在 SQLite 上做，不需要图数据库。

R1 找法(as_of)   —— 时点相关：同一事实在不同 as_of 下返回不同依据；
                    若依据条文本身是援引条款，则沿 REFERS_TO 追一跳，并校验被援引文件在该时点是否有效
                    （这是本课题的核心指标：repealed-citation rate）。
R2 定责(as_of)   —— 主体 × 职责形式矩阵，只统计 counts_as_duty=1 的规范，倡导/定义/援引不计入。
R3 识缺(as_of)   —— 协同维度覆盖度：某维度只有单一法律依据、无配套规定的，标为"单点依赖"。
R4 援引审计      —— 全库 REFERS_TO 边按 as_of 校验，输出悬空援引清单。

用法: python s4_reason.py [--as-of 2026-11-06]
"""
import argparse
import math
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

HIER_W = {"法律": 1.0, "行政法规": 0.8, "地方性法规": 0.7, "部门规章": 0.6,
          "司法解释": 0.6, "地方共同立法（学理待定）": 0.55}
DEFAULT_ASOF = "2026-11-06"


def load_terms():
    terms = []
    for r in C.read_csv("06_术语与实体词表.csv"):
        keys = [r["term_cn"]] + [a for a in r["aliases"].split("、") if a]
        terms.append((r["term_cn"], keys))
    return terms


def docs_at(con, as_of):
    """该时点有效的文件集合（valid_from <= as_of <= valid_to）。"""
    ok = set()
    for r in con.execute("SELECT doc_id, valid_from, valid_to FROM doc"):
        vf, vt = r[1] or "", r[2] or ""
        if (not vf or as_of >= vf) and (not vt or as_of <= vt):
            ok.add(r[0])
    return ok


def doc_meta(con):
    """doc_id -> 文件元数据（含效力区间，供 follow_referral 判断援引目标在 as_of 是否有效）。"""
    return {r[0]: {"doc_id": r[0], "name": r[1], "hierarchy": r[2],
                   "valid_from": r[3] or "", "valid_to": r[4]}
            for r in con.execute(
                "SELECT doc_id, name, hierarchy, valid_from, valid_to FROM doc")}


# 中文无空格，按词切分需分词器（本项目不引入第三方依赖）。字符二元组（bigram）在中文上
# 覆盖率高且无需词典：'跨省饮用水水源保护区' -> {跨省,省饮,饮用,用水,水水,水源,源保,保护,护区}
# 原先按空格/标点切分，整串中文查询只能整串匹配，导致 text.count(整串) 近乎为 0，
# 排序实际由术语表别名与条文长度主导 —— 这是 v0 评测 P@5 异常低的真正原因。
_CJK_STOP = set("的在与和及或称等或其中其他有关本条")


def bigrams(text):
    if not text:
        return set()
    t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text)
    return {t[k:k + 2] for k in range(len(t) - 1)
            if not (t[k] in _CJK_STOP and t[k + 1] in _CJK_STOP)}


_IDX_CACHE = {}

# R-d 罚则模式：法律责任条款的典型句式（违反本法规定…责令…处…罚款/没收/吊销/强制拆除）。
# 注意这是**款级**判定：同一法条的行为规范款与罚则款分开判。
_SAN_PAT = re.compile(r"违反本法规定|责令|强制拆除|没收|吊销|处[^，。；]{0,16}罚款")

# R-d 查询侧门控：查询串带违法/责任语义时**不**降权罚则——此时罚则就是正答
# （金标中的罚则条款 Q06/Q09/Q47 的查询串全部带此类标记；Q13 无标记、
# 其罚则金标降权后仍在 top-5 内，实测见 build/out/synonym_report.md）。
_QUERY_VIOL = ("违法", "违反", "超过", "逾期", "法律责任", "处罚",
               "罚款", "吊销", "没收", "侵占", "未依法", "未实施")


def _index(con, syn=False):
    """一次性建 bigram 倒排所需的 df 统计；键用 (DB 路径, syn)，进程内复用。

    syn=True 时启用跨代概念对照词典（build/synmap.py）的条文侧 bigram 扩充：
    条文包含某对照一侧全部术语 → 补另一侧 bigram。冻结主系统 B8 用 syn=False。
    items 元组：(prov_id, doc_id, bigram集, 是否罚则款)。
    """
    key = (C.DB_PATH, syn)
    if key in _IDX_CACHE:
        return _IDX_CACHE[key]
    extra = None
    if syn:
        import synmap
        extra = synmap.provision_extra(con)
    items, df = [], Counter()
    for p in con.execute("SELECT prov_id, doc_id, text FROM provision"):
        pid, did, txt = p[0], p[1], p[2]
        base = bigrams(txt)
        # df 只按**原始** bigram 统计：IDF 反映语料真实词频分布，词典扩充
        # 只作为匹配时的等价关系，不得改变全库权重（否则牵一发动全身）。
        for g in base:
            df[g] += 1
        bg = base | extra[pid] if (extra and pid in extra) else base
        items.append((pid, did, bg, bool(_SAN_PAT.search(txt or ""))))
    N = len(items) or 1
    _IDX_CACHE[key] = (items, df, N)
    return _IDX_CACHE[key]


def expand(query, terms):
    """查询词袋 = query 的 bigram ∪ 术语表命中词的全部别名（及其 bigram）。"""
    qbg = bigrams(query)
    for canon, keys in terms:
        if any(k and k in query for k in keys):
            for k in keys:
                qbg |= bigrams(k)
    return qbg


def _basins(meta):
    """流域专属法识别（v1，基于文件名）：名称含"长江"→长江流域、"黄河"→黄河流域。
    空间效力依据：长江保护法第2条、黄河保护法第2条（各自限定流域适用范围）。
    法典/水法/水污染防治法/补偿条例为全国普遍适用，不进此表。"""
    b = {}
    for did, d in meta.items():
        n = d.get("name", "")
        if "长江" in n:
            b[did] = "长江"
        elif "黄河" in n:
            b[did] = "黄河"
    return b


_BASIN_WORDS = ("长江", "黄河", "淮河", "海河", "辽河", "松花江", "珠江", "太湖")


def _query_basin(query):
    """查询串中的流域指称。None = 通用查询（未指明流域，如"河湖岸线开发利用强度"）。
    命中多个流域词（跨流域查询）也视为 None，交由 R-a 轻罚而非强罚。"""
    hits = [w for w in _BASIN_WORDS if w in query]
    return hits[0] if len(hits) == 1 else None


def _special_pairs(con):
    """(特别法, 一般法) 文件对，来自图谱 LEX_SPECIALIS 边（special → general）。"""
    return set(con.execute("SELECT src_id, dst_id FROM edge WHERE rel='LEX_SPECIALIS'"))


def find_law(con, query, as_of, k=5, basin_hint=None, rules="abc", syn=False):
    """BM25-lite：query 的 IDF 加权 bigram 覆盖率 × 位阶权重 × 可废止调整规则。

    rules：启用哪几条可废止规则，字母串（'a'/'b'/'c'/'d'），默认全开 "abc"。
           传 "" 即为消融设定 B7（去掉全部规则，仅保留覆盖率×位阶权重）。
           'd' = R-d 罚则降权（post-hoc，见下），默认**不开**——冻结主系统 B8
           的行为与历史评测完全一致。
    syn  ：True 启用跨代概念对照词典（build/synmap.py，corpus/18）的条文侧
           bigram 扩充。默认 False（冻结）。与 rules 独立，便于分别归因。
           **SYN 与 R-d 均为 post-hoc 组件**（2026-11-16 测试集错误分析后加入），
           报告时只能以 B8+SYN / B8+SYN+RD 变体单独呈现。

    覆盖率的分母是 query 侧的权重和，与条文长度无关，因此不再系统性偏向长条文
    （原实现 score = Σ count(词)×len(词)，长条文天然占优）。

    调整规则（均为默认优先，非硬编码顺序；每条命中会在结果的 priority_note 里留痕）：
    R-a 流域范围降权：长江/黄河保护法只适用于各自流域（各自第2条）。
        查询指明流域而候选属另一流域 → ×0.70；查询未指明流域而候选为流域专属 → ×0.85。
        典型病例：淮河采砂查询的 top-1 曾是黄河法第69条（Q32）。
    R-b 特别法优先（LEX_SPECIALIS）：查询流域与特别法流域一致、且该特别法在图谱中
        指向的一般法也有候选命中 → 特别法 ×1.15。仅"同池竞争"时触发，特别法覆盖率
        远低于一般法时不强行翻转（可废止：范围/事项不重叠时特别法优先不适用）。
        典型病例：黄河排污口查询的 top-1 曾被法典第275条挤掉黄河法第76条（Q40）。
    R-c 援引穿透：候选池中的援引条（如法典第876条"依照《长江保护法》的规定…"）
        沿 REFERS_TO 边把自身 50% 分数传导给被援引文件的候选。法条明确指向
        "依照某法规定执行"时，被指法才是规范本体，援引条只是通道。
        典型病例：长江岸线查询的 top-1 曾是法典第876条而非它指向的长江法第26条（Q05）。
    R-d 罚则降权（post-hoc，rules 含 'd' 时启用）：查询串为规范型（无违法/责任
        语义标记）时，罚则款（_SAN_PAT 命中）×0.80。法理：同一违法要件在行为规范款
        与法律责任款中被复述，找法任务的答案是第一性义务条款，罚则是违反后的
        第二性后果；查询本身在问违法后果（如"…的法律责任"）时不降——此时罚则
        就是正答。典型病例：Q58 top-1 曾是法典第1109条（第329条的罚则款）。
    """
    ok = docs_at(con, as_of)
    meta = doc_meta(con)
    items, df, N = _index(con, syn)
    qbg = expand(query, load_terms())
    if not qbg:
        return []
    w = {g: math.log(1.0 + N / (1.0 + df.get(g, 0))) for g in qbg}
    tot = sum(w.values()) or 1.0
    rows = []
    for pid, did, bg, san in items:
        if did not in ok:
            continue
        cov = sum(wt for g, wt in w.items() if g in bg) / tot
        if cov <= 0.02:
            continue
        d = meta[did]
        rows.append({"prov_id": pid, "doc": d["name"], "doc_id": did,
                     "hier": d["hierarchy"], "text": "",
                     "score": round(cov * HIER_W.get(d["hierarchy"], 0.5), 4),
                     "cov": round(cov, 4),
                     "norm": [], "priority_note": "", "san": san})

    # ---- 两条可废止调整规则（详见 find_law docstring）----
    # basin_hint：流域元信息走独立通道，不进文本——拼接进查询串会改变 bigram
    # 权重分布（"流域"等通用词稀释事由词的判别力），实测使 B1 P@5 下降 12.5 个百分点。
    qb = basin_hint or _query_basin(query)
    basin = _basins(meta)
    pairs = _special_pairs(con)
    pool_docs = {r["doc_id"] for r in rows}
    # 邻近性条件的前提：每个一般法文件在候选池中的最佳原始分
    best_raw = {}
    for r in rows:
        best_raw[r["doc_id"]] = max(best_raw.get(r["doc_id"], 0.0), r["score"])
    for r in rows:
        db = basin.get(r["doc_id"])
        if "a" in rules and db and qb and db != qb:
            r["score"] = round(r["score"] * 0.70, 4)
            r["priority_note"] = "R-a 流域范围外降权"
        elif "a" in rules and db and not qb:
            r["score"] = round(r["score"] * 0.85, 4)
            r["priority_note"] = "R-a 流域专属条款（查询未指明流域）"
        if "b" in rules and db and qb == db:
            tgts = [g for sp, g in pairs if sp == r["doc_id"]]
            # 邻近性条件：特别法候选的原始分须达到其一般法对手最佳分的 80%，
            # 才视为"同事项竞争"——否则特别法优先不适用（可废止）。
            # 病例（full 模式实测）：跨省水源保护区查询（金标=法典第317条），
            # 无条件 ×1.15 会把仅沾边的长江法候选顶上去。
            gen_best = max([best_raw.get(g, 0.0) for g in tgts if g in pool_docs], default=0.0)
            if gen_best and r["score"] >= 0.80 * gen_best:
                r["score"] = round(r["score"] * 1.15, 4)
                r["priority_note"] = (r["priority_note"] + "；" if r["priority_note"] else "") \
                    + "R-b LEX_SPECIALIS 优先（与一般法候选同事项竞争）"

    # R-c 援引穿透：援引条把 50% 分数传导给被援引文件的候选（norm_id -> prov_id 映射）。
    # 门控：被援引文件若为流域专属法，仅在查询处于其流域内时才传导——
    # 法典第876条对长江法的援引只在长江流域事项中成立，通用查询不得借道（Q24 病例）。
    by_prov = {r["prov_id"]: r for r in rows}
    refs = con.execute("SELECT src_id, dst_id FROM edge WHERE rel='REFERS_TO'") if "c" in rules else []
    for src_norm, dst_doc in refs:
        prov = src_norm[: src_norm.rfind("-")]
        sr = by_prov.get(prov)
        if not sr or sr["score"] <= 0:
            continue
        dst_basin = basin.get(dst_doc)
        if dst_basin and qb != dst_basin:
            continue
        for r in rows:
            if r["doc_id"] == dst_doc:
                r["score"] = round(r["score"] + 0.5 * sr["score"], 4)
                r["priority_note"] = (r["priority_note"] + "；" if r["priority_note"] else "") \
                    + "R-c 援引穿透（来自 %s）" % prov

    # R-d 罚则降权（post-hoc）：规范型查询（无违法/责任标记）时罚则款 ×0.80。
    # 门控方向的选择依据：金标中的 4 个罚则条款（Q06/Q09/Q13/Q47），其查询串
    # 除 Q13 外全部自带违法/责任语义标记——查询在问违法后果时罚则就是正答。
    if "d" in rules and not any(v in query for v in _QUERY_VIOL):
        for r in rows:
            if r["san"]:
                r["score"] = round(r["score"] * 0.80, 4)
                r["priority_note"] = (r["priority_note"] + "；" if r["priority_note"] else "") \
                    + "R-d 罚则降权（规范型查询）"
    rows.sort(key=lambda x: -x["score"])
    for r in rows[:k]:
        r["text"] = con.execute("SELECT text FROM provision WHERE prov_id=?",
                                (r["prov_id"],)).fetchone()[0]
        r["norm"] = [dict(n) for n in con.execute(
            "SELECT deontic,subject_text,action_text FROM norm WHERE prov_id=?",
            (r["prov_id"],))]
    return rows[:k]


def follow_referral(con, prov_id, as_of):
    meta = doc_meta(con)
    out = []
    for e in con.execute("SELECT * FROM edge WHERE rel='REFERS_TO' AND prov_id=?", (prov_id,)):
        t = meta.get(e["dst_id"])
        if not t:
            continue
        live = (t["valid_from"] or "0000") <= as_of and (t["valid_to"] is None or as_of <= t["valid_to"])
        out.append({"target": t["name"], "target_id": t["doc_id"],
                    "valid_to": t["valid_to"], "live_at_asof": live,
                    "evidence": e["evidence"]})
    return out


def duty_matrix(con, as_of):
    ok = docs_at(con, as_of)
    q = ("SELECT n.doc_id,n.deontic,n.subject_text FROM norm n WHERE n.counts_as_duty=1"
         " AND n.subject_text<>'' AND n.doc_id IN (%s)" % ",".join("?" * len(ok)))
    agg = {}
    for r in con.execute(q, tuple(ok)):
        key = r["subject_text"]
        agg.setdefault(key, {"obligation": 0, "power": 0, "prohibition": 0})
        agg[key][r["deontic"]] = agg[key].get(r["deontic"], 0) + 1
    return agg


def coverage(con, as_of):
    ok = docs_at(con, as_of)
    rows = []
    for e in con.execute("SELECT ent_id,name FROM entity WHERE etype='CoordDimension'"):
        docs = {r["doc_id"] for r in con.execute(
            "SELECT DISTINCT n.doc_id FROM edge e2 JOIN norm n ON n.norm_id=e2.src_id"
            " WHERE e2.rel='REQUIRES_UNIFICATION' AND e2.dst_id=?", (e["ent_id"],))}
        docs &= ok
        verdict = "空白" if not docs else ("单点依赖" if len(docs) == 1 else "多源支撑")
        rows.append((e["name"], len(docs), verdict, ",".join(sorted(docs)) or "-"))
    return rows


def referral_audit(con, as_of):
    meta = doc_meta(con)
    tot, bad = 0, 0
    detail = []
    for e in con.execute("SELECT * FROM edge WHERE rel='REFERS_TO'"):
        tot += 1
        t = meta.get(e["dst_id"])
        live = t and (t["valid_from"] or "0000") <= as_of and (t["valid_to"] is None or as_of <= t["valid_to"])
        if not live:
            bad += 1
            detail.append((e["prov_id"], t["name"] if t else e["dst_id"], e["evidence"],
                           (t["valid_to"] if t else "?")))
    return tot, bad, detail


def run(as_of, verbose=True):
    con = C.connect(fresh=False)
    lines = ["# 推理运行报告（as_of = %s）" % as_of, "",
             "> 自动生成，请勿手改。重跑：`python build/run_all.py --as-of %s`" % as_of, ""]

    # R1
    q = "水污染防治 依照规定执行"
    hits = find_law(con, q, as_of)
    lines += ["## R1 找法：`%s`" % q, ""]
    if not hits:
        lines.append("_该时点下无命中条文。_")
    for i, h in enumerate(hits, 1):
        lines.append("%d. **%s**（%s，%s）score=%s" % (i, h["prov_id"], h["doc"], h["hier"], h["score"]))
        lines.append("   - 原文：%s" % h["text"])
        for r in follow_referral(con, h["prov_id"], as_of):
            state = "有效" if r["live_at_asof"] else "**已于 %s 失效 → 悬空援引**" % r["valid_to"]
            lines.append("   - 援引 `%s`：%s（%s）" % (r["evidence"], r["target"], state))
    lines.append("")

    # R2
    m = duty_matrix(con, as_of)
    lines += ["## R2 定责矩阵（仅计义务/职权/禁止，倡导与定义不计）", "",
              C.md_table(["主体", "义务", "职权", "禁止"],
                         [(k, v.get("obligation", 0), v.get("power", 0), v.get("prohibition", 0))
                          for k, v in sorted(m.items(), key=lambda x: -sum(x[1].values()))]), ""]

    # R3
    lines += ["## R3 协同维度覆盖度", "",
              C.md_table(["协同维度", "支撑文件数", "判定", "文件"],
                         [(a, b, c, d) for a, b, c, d in coverage(con, as_of)]), ""]

    # R4
    tot, bad, det = referral_audit(con, as_of)
    rate = 100.0 * bad / max(tot, 1)
    lines += ["## R4 援引审计：repealed-citation rate = %.0f%%（%d/%d）" % (rate, bad, tot), ""]
    if det:
        lines += [C.md_table(["来源条文", "被援引文件", "援引片段", "失效日"], det), ""]
    lines.append("- 该指标只统计**图谱内已登记的援引边**；分母是全库 REFERS_TO 边数，"
                 "不是全部法条中出现的援引，故不能直接与基线模型对比，需在同一子集上算。")
    con.close()

    os.makedirs(C.OUT_DIR, exist_ok=True)
    path = os.path.join(C.OUT_DIR, "report_%s.md" % as_of)
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    if verbose:
        print("\n".join(lines))
    print("[S4] as_of=%s  repealed-citation=%.0f%%  报告 -> %s"
          % (as_of, rate, os.path.relpath(path, C.PROJ_DIR)))
    return rate


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=DEFAULT_ASOF)
    a = ap.parse_args()
    run(a.as_of)
