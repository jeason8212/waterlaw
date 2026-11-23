# -*- coding: utf-8 -*-
"""规范行为索引（conduct index）：把「条文」下沉到「可诉行为」，用于判定某时点该事由有没有依据。

## 为什么需要第二层

`answer_class.py` 的 v1 判据在**条文全文**上比：拿整条文本算覆盖率。它抓不住的一类查询，
共同特征是「旧法谈同一个题目，但不规制这个行为」——Q24（河湖岸线开发利用强度控制）与水法第37条
（禁止在河道内弃置阻碍行洪的物体）共享「河湖／岸线／管理范围」等上下文词，覆盖率天然很高，
可后者根本不涉及"控制开发利用强度"。**这是表示粒度问题，不是阈值问题。**

v2 把比较对象换成**规范行为**（conduct）：一个 conduct = 一条可诉规范句（deontic ∈
{obligation, prohibition, power}），或者枚举清单里的一项（继承引导句的模态）。于是：

- 水法第37条的 conduct 是「禁止在江河、湖泊…弃置、堆放阻碍行洪的物体」→ 与 Q24 的"控制开发利用强度"不重叠；
- 法典第749条的 conduct 是「对江河湖泊水域岸线等水生态空间实行分区管理，严格控制开发利用强度」→ 高度重叠。

再叠加两条该图本来就有的元数据：
  1. **时间效力**：doc.valid_from / valid_to —— 区分「t 时点有效」与「t 之后才施行」；
  2. **空间效力**：doc → Watershed（长江保护法第2条、黄河保护法第2条各自限定流域）
     —— 淮河的查询不能用长江法第31条充当"已有依据"。Q48 就是靠这一条才判对的。

## 判据（三条件）

    gap  = cov(将来才施行) − cov(t 时点有效)      # 落差
    cov_L                                         # 后法侧最佳行为的覆盖率
    sim  = cov(最佳后法行为的 core，落在 t 时点有效行为上的最佳覆盖)   # 前身

判 `none` 当且仅当：
    gap ≥ DELTA 且 cov_L ≥ KAPPA 且 sim < THETA

（另试过第四条「cov_t ≤ CEIL，旧法已答得不错则不弃权」，在 60 查询上与前三条件完全冗余，
已删去——少一个自由度比多一道闸更可取，网格表见 `build/out/answer_class.md` §三。）

`sim` 是关键的第四条：**法典重述旧法**时（Q02、Q11、Q20）落差同样很大——法典写得更规整，
覆盖率自然更高。若不查前身，就会把"换个说法重申"误判成"制度新设"。前身检验问的是：
这条后法行为，在旧法里找不找得到对应的一条？找得到（sim 高）→ 旧法已有依据 → 不弃权。

## 已知残留（必须写进论文）

12 个空答案查询里，v2 抓到 10 个，仍漏 2 个，且都不是调参能解决的：
  - Q42（跨行政区域联合保护的统一规划…）：旧法水污染防治法第31条「跨行政区域水污染纠纷协商解决」
    与法典第21条「联合保护协调机制」字面相近（sim 0.479），而落差只有 0.112——旧法确实有一套
    "跨区域协调"制度，只是不是"四统一"。这是**制度近邻**，不是制度空白。
  - Q23（跨省界断面监测数据共享与平台建设）：旧法第25条逐字含「建立监测数据共享机制」，
    落差 0.000。字面几乎相同，差别在于旧法把它放在监测网络制度里、法典把它升格为独立的共享平台义务。
    字符 bigram 无法表达"同一个词、不同的制度位置"。
两例的共同点是**词汇未漂移而制度已位移**，与 Q22/Q43/Q58 的"词汇漂移"正好相反，是表示层的另一面。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import s4_reason  # noqa: E402

# 可诉模态：只有这三类承载"该做什么/不许做什么/谁有权做"
ACTIONABLE = ("obligation", "prohibition", "power")

# 判据参数。不是手调最优值：在「召回 10/12 且误判 0/48」的可行域（560 组设定，delta∈[0.10,0.30]、
# kappa∈{0.40…0.45}、theta∈[0.18,0.41]）内取两位小数的整点，且离最近的单例仍有 0.018 余量。
DELTA = 0.25     # 时间溯源落差下限
KAPPA = 0.45     # later 侧须达到的最低覆盖：后法确实规制了该行为
THETA = 0.25     # 前身相似度上限：旧法有对应行为时不弃权

_ITEM = re.compile(r"^\s*（[一二三四五六七八九十]+）")


def _is_leadin(text):
    """引导句：含「下列/以下」且带冒号——后面跟的是枚举清单。"""
    return ("下列" in text or "以下" in text) and "：" in text


def extract(con, inherit=True):
    """从 norm 表抽 conduct。返回 [(conduct_id, norm_id, prov_id, doc_id, deontic, origin, core)]。

    枚举项继承：引导句为可诉模态时，其后的「（一）…（二）…」declarative 句子继承该模态。
    法理依据：枚举项不是独立陈述，而是引导句规范内容的分解，其可诉性与引导句同一。
    inherit=False 只取基础句，用于消融。
    """
    rows = [dict(r) for r in con.execute(
        "SELECT norm_id, prov_id, doc_id, deontic, sent_no, text FROM norm")]
    by_prov = {}
    for r in rows:
        by_prov.setdefault(r["prov_id"], []).append(r)

    out = []
    for r in rows:
        if r["deontic"] in ACTIONABLE:
            out.append((r["norm_id"], r["prov_id"], r["doc_id"], r["deontic"],
                        "base", r["text"] or ""))
    if inherit:
        for prov_id, rs in by_prov.items():
            lead = None
            for r in sorted(rs, key=lambda x: (x["sent_no"] or 0)):
                t = (r["text"] or "").strip()
                if r["deontic"] in ACTIONABLE:
                    lead = r if _is_leadin(t) else None
                    continue
                if r["deontic"] == "declarative" and lead is not None and _ITEM.match(t):
                    out.append((r["norm_id"], prov_id, r["doc_id"], lead["deontic"],
                                "inherit:" + lead["norm_id"], t))

    # 编稳定 id：CF-0001…（按 doc/条/句排序，重建时编号不变）
    out.sort(key=lambda x: (x[2], x[1], x[0]))
    return [("CF-%04d" % (i + 1),) + t for i, t in enumerate(out)]


class Index(object):
    """conduct 索引。构建一次，按查询复用。

    inherit / basin_gate 只用于消融，正式结果一律用默认（全开）。
    """

    def __init__(self, con, inherit=True, basin_gate=True):
        import math
        self.math = math
        self.basin_gate = basin_gate
        raw = extract(con, inherit=inherit)
        self.meta = s4_reason.doc_meta(con)
        self.basin = s4_reason._basins(self.meta)
        self.terms = s4_reason.load_terms()

        docs = {}
        for r in con.execute("SELECT doc_id, valid_from, valid_to FROM doc"):
            docs[r[0]] = (r[1] or "", r[2] or "")
        self.docs = docs

        self.items = []          # [(conduct_id, prov_id, doc_id, deontic, origin, bg)]
        df = {}
        for cid, nid, pid, did, de, origin, core in raw:
            bg = s4_reason.bigrams(core)
            if not bg:
                continue
            self.items.append((cid, pid, did, de, origin, bg))
            for g in bg:
                df[g] = df.get(g, 0) + 1
        self.df = df
        self.N = len(self.items)
        self._by_prov = {}
        for it in self.items:
            self._by_prov.setdefault(it[1], []).append(it)

    # ---------- 时间与空间效力 ----------
    def docset(self, as_of):
        """(t 时点有效, t 之后才施行) 两组 doc_id。"""
        now, later = set(), set()
        for d, (vf, vt) in self.docs.items():
            if vf and as_of < vf:
                later.add(d)
            elif (not vf or as_of >= vf) and (not vt or as_of <= vt):
                now.add(d)
        return now, later

    def _applicable(self, did, qb):
        """流域效力：长江法/黄河法的条文只对其本流域查询有效。"""
        if not self.basin_gate:
            return True
        db = self.basin.get(did)
        return not (db and qb and db != qb)

    # ---------- 覆盖 ----------
    def weights(self, qbg):
        w = {g: self.math.log(1.0 + self.N / (1.0 + self.df.get(g, 0))) for g in qbg}
        tot = sum(w.values()) or 1.0
        return w, tot

    def _cov(self, w, tot, bg):
        return sum(wt for g, wt in w.items() if g in bg) / tot

    def best(self, qbg, docset, qb=None):
        if not qbg:
            return 0.0, None, None
        w, tot = self.weights(qbg)
        best, bp, bd = 0.0, None, None
        for cid, pid, did, de, origin, bg in self.items:
            if did not in docset or not self._applicable(did, qb):
                continue
            c = self._cov(w, tot, bg)
            if c > best:
                best, bp, bd = c, pid, did
        return best, bp, bd

    def predecessor(self, prov_id, docset, qb=None):
        """后法行为的 core，在 t 时点有效行为里能找到的最佳对应（前身）。"""
        it = self._by_prov.get(prov_id) or []
        if not it:
            return 0.0, None
        w, tot = self.weights(it[0][5])
        best, bp = 0.0, None
        for cid, pid, did, de, origin, bg in self.items:
            if pid == prov_id or did not in docset or not self._applicable(did, qb):
                continue
            c = self._cov(w, tot, bg)
            if c > best:
                best, bp = c, pid
        return best, bp

    # ---------- 信号与判定 ----------
    def signals(self, query, as_of, basin_hint=None):
        qbg = s4_reason.expand(query, self.terms)
        qb = basin_hint or s4_reason._query_basin(query)
        now, later = self.docset(as_of)
        ca, pa, da = self.best(qbg, now, qb)
        cl, pl, dl = self.best(qbg, later, qb)
        sim, pred = self.predecessor(pl, now, qb) if pl else (0.0, None)
        return {"cov_asof": round(ca, 4), "prov_asof": pa, "doc_asof": da,
                "cov_later": round(cl, 4), "prov_later": pl, "doc_later": dl,
                "gap": round(cl - ca, 4), "sim": round(sim, 4), "predecessor": pred,
                "n_conducts": self.N}

    def decide(self, query, as_of, basin_hint=None,
               delta=DELTA, kappa=KAPPA, theta=THETA):
        s = self.signals(query, as_of, basin_hint)
        hit = s["gap"] >= delta and s["cov_later"] >= kappa and s["sim"] < theta
        s["fired"] = {"gap": s["gap"] >= delta,
                      "later": s["cov_later"] >= kappa,
                      "no_predecessor": s["sim"] < theta}
        return ("none" if hit else "provision"), s


_INDEX = {}


def index(con, inherit=True, basin_gate=True, fresh=False):
    """索引只依赖语料内容（构建后全部驻留内存），与连接身份无关，故按选项缓存。"""
    key = (inherit, basin_gate)
    if fresh or key not in _INDEX:
        _INDEX[key] = Index(con, inherit=inherit, basin_gate=basin_gate)
    return _INDEX[key]


if __name__ == "__main__":
    import sqlite3
    import common as C

    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    idx = index(con)
    base = sum(1 for it in idx.items if it[4] == "base")
    sys.stdout.write("conduct 总数 %d（基础句 %d，枚举继承 %d）\n"
                     % (idx.N, base, idx.N - base))
    qs = sys.argv[1:] or ["在自然湿地擅自采砂", "河湖岸线开发利用强度控制",
                          "生态水量纳入年度水量调度计划与枯水期生态流量保障"]
    for q in qs:
        cls, ev = idx.decide(q, "2026-06-30", basin_hint="淮河" if "淮河" in q else None)
        sys.stdout.write("\n[%s] → %s\n" % (q, cls))
        for k in ("cov_asof", "cov_later", "gap", "sim"):
            sys.stdout.write("   %-9s %s\n" % (k, ev[k]))
        sys.stdout.write("   as-of %s ← %s\n   later %s ← %s\n   pred  %s\n"
                         % (ev["prov_asof"], ev["doc_asof"], ev["prov_later"],
                            ev["doc_later"], ev["predecessor"]))
