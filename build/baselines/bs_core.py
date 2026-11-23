# -*- coding: utf-8 -*-
"""T1 baseline 骨架：把「检索器」抽象成 `f(query, as_of, k, basin_hint) -> [prov_id]`，
所有系统（B1–B8）走同一套评测代码，指标定义与 `build/tools/eval_queries.py` 完全一致，
因此 B8（本文完整方法）在 harness 里的数字必须与正式报告逐位相同 —— 这是 harness 的自检线。

系统清单（编号与 `paper/03_Experiment_Design.md` 表一致）：
  B1  bm25_nofilter   BM25，无时间过滤（传统下限，池 = 全库 2,847 款）
  B2  bm25_date       BM25 + as-of 过滤
  B2u bm25uni_date    BM25（字一元+二元）+ as-of 过滤（分词鲁棒性检查）
  B3  dense           向量检索（无时间过滤）
  B3d dense_date      向量检索 + as-of 过滤
  B4  llm_zero        LLM zero-shot：只给事由，模型自己报法名+条号，回填 prov_id
  B5  llm_rag         RAG：召回 top-20 → 模型在候选内排序（无时间过滤）
  B6  llm_rag_date    RAG + as-of 过滤（论文的"主对手"）
  B7  ablation        本文 − R-a/R-b/R-c（证明优先关系边必要）
  B8  full            本文完整方法

统计：P@5 / MRR（款级、条级）× 两时点 × 两输入设定；flip 准确率（Y/N）；条件 flip；
废止法引用率；bootstrap 95% CI（按查询重采样）；与 B8 的 McNemar 精确检验 + Cohen's h。
"""
import csv
import glob
import io
import math
import os
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
TOOLS = os.path.join(BUILD, "tools")
for p in (BUILD, TOOLS, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import common as C
import s4_reason
import eval_queries as E

B0, B1 = E.B0, E.B1
MODES = ("conduct", "full")
CONTENT_RE = re.compile(r"[^\u4e00-\u9fa5A-Za-z0-9]")


# ------------------------------------------------------------------ corpus
def tok_bigram(text):
    """字符二元组（与 s4_reason.bigrams 同一切分口径，但不做停用二元过滤）。"""
    t = CONTENT_RE.sub("", text or "")
    return [t[k:k + 2] for k in range(len(t) - 1)]


def tok_unibi(text):
    t = CONTENT_RE.sub("", text or "")
    out = list(t)
    out += [t[k:k + 2] for k in range(len(t) - 1)]
    return out


class Corpus:
    def __init__(self, con):
        self.con = con
        self.ids, self.text, self.doc, self.art = [], [], [], []
        self.name, self.hier, self.valid = {}, {}, {}
        for r in con.execute("SELECT doc_id,name,hierarchy,valid_from,valid_to FROM doc"):
            self.name[r[0]], self.hier[r[0]] = r[1], r[2]
            self.valid[r[0]] = (r[3] or "", r[4] or "")
        for r in con.execute("SELECT prov_id,doc_id,article_no,text FROM provision ORDER BY prov_id"):
            self.ids.append(r[0])
            self.doc.append(r[1])
            self.art.append(r[2])
            self.text.append(r[3] or "")
        self.pos = {p: i for i, p in enumerate(self.ids)}
        self._tok = {}

    def doc_ok(self, did, as_of):
        vf, vt = self.valid.get(did, ("", ""))
        return (not vf or as_of >= vf) and (not vt or as_of <= vt)

    def pool(self, as_of=None):
        if as_of is None:
            return list(range(len(self.ids)))
        return [i for i, d in enumerate(self.doc) if self.doc_ok(d, as_of)]

    def toks(self, name):
        if name not in self._tok:
            f = {"bigram": tok_bigram, "unibi": tok_unibi}[name]
            self._tok[name] = [f(t) for t in self.text]
        return self._tok[name]

    def art_index(self, doc_id, article_no):
        """(法文件, 条号) -> 该条第 1 款 prov_id（zero-shot 回填用）。"""
        for i, d in enumerate(self.doc):
            if d == doc_id and self.art[i] == article_no:
                return self.ids[i]
        return None

    def doc_by_name(self, raw):
        """中文法律名 -> doc_id（容忍书名号、全称/简称、空白）。"""
        raw = (raw or "").strip().strip("《》<>「」\"' ")
        raw = re.sub(r"\s+", "", raw)
        if not raw:
            return None
        best = None
        for did, n in self.name.items():
            nn = re.sub(r"\s+", "", n or "")
            if not nn:
                continue
            if nn == raw or nn.strip("中华人民共和国") == raw:
                return did
            if raw in nn or nn in raw:
                best = best or did
        return best


# ------------------------------------------------------------------ lexical
class BM25:
    def __init__(self, corpus, tokenizer="bigram", k1=1.2, b=0.75):
        self.c, self.k1, self.b = corpus, k1, b
        self.tf_fn = {"bigram": tok_bigram, "unibi": tok_unibi}[tokenizer]
        toks = corpus.toks(tokenizer)
        self.N = len(toks)
        self.dl = [len(t) or 1 for t in toks]
        self.avg = sum(self.dl) / max(self.N, 1)
        self.inv = defaultdict(list)
        df = Counter()
        for i, ts in enumerate(toks):
            for t, f in Counter(ts).items():
                self.inv[t].append((i, f))
                df[t] += 1
        self.idf = {t: math.log(1.0 + (self.N - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def rank(self, query, pool, k=5):
        sc = defaultdict(float)
        for t in set(self.tf_fn(query)):
            idf = self.idf.get(t)
            if not idf:
                continue
            for i, f in self.inv[t]:
                dl = self.dl[i]
                sc[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
        allow = pool if isinstance(pool, set) else set(pool)
        best = sorted(((s, i) for i, s in sc.items() if i in allow), key=lambda x: (-x[0], x[1]))[:k]
        return [self.c.ids[i] for _, i in best]


class TFIDF:
    """余弦相似度（sublinear tf + L2 归一），与 BM25 互补的传统检索基线。"""

    def __init__(self, corpus, tokenizer="unibi"):
        toks = corpus.toks(tokenizer)
        self.c = corpus
        self.tf_fn = {"bigram": tok_bigram, "unibi": tok_unibi}[tokenizer]
        df = Counter()
        tfs = []
        for ts in toks:
            c = Counter(ts)
            tfs.append(c)
            for t in c:
                df[t] += 1
        n = len(tfs)
        self.idf = {t: math.log((1.0 + n) / (1.0 + d)) + 1.0 for t, d in df.items()}
        self.vec, self.norm = [], []
        for c in tfs:
            v = {t: (1 + math.log(f)) * self.idf[t] for t, f in c.items() if t in self.idf}
            nn = math.sqrt(sum(x * x for x in v.values())) or 1.0
            self.vec.append(v)
            self.norm.append(nn)

    def rank(self, query, pool, k=5):
        toks = self.tf_fn(query)
        qv = {t: (1 + math.log(f)) * self.idf[t] for t, f in Counter(toks).items() if t in self.idf}
        qn = math.sqrt(sum(x * x for x in qv.values())) or 1.0
        allow = pool if isinstance(pool, set) else set(pool)
        sc = []
        for i in allow:
            v = self.vec[i]
            small, big = (qv, v) if len(qv) < len(v) else (v, qv)
            dot = sum(x * big.get(t, 0.0) for t, x in small.items())
            if dot > 0:
                sc.append((dot / (qn * self.norm[i]), i))
        sc.sort(key=lambda x: (-x[0], x[1]))
        return [self.c.ids[i] for _, i in sc[:k]]


class Dense:
    """向量检索。嵌入由 providers.Embedder 提供（未配置则抛 Unavailable）。"""

    def __init__(self, corpus, embedder):
        from providers import Unavailable  # 延迟导入，避免无依赖时也要 import
        self.c, self.emb = corpus, embedder
        self.vec = embedder(corpus.text)
        self.norm = [math.sqrt(sum(x * x for x in v)) or 1.0 for v in self.vec]

    def rank(self, query, pool, k=5):
        q = self.emb([query])[0]
        qn = math.sqrt(sum(x * x for x in q)) or 1.0
        allow = pool if isinstance(pool, set) else set(pool)
        sc = []
        for i in allow:
            v = self.vec[i]
            dot = sum(a * b for a, b in zip(q, v))
            if dot:
                sc.append((dot / (qn * self.norm[i]), i))
        sc.sort(key=lambda x: (-x[0], x[1]))
        return [self.c.ids[i] for _, i in sc[:k]]


# ------------------------------------------------------------------ LLM
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}


def cn2num(s):
    """中文条号 -> int：第三十一条 -> 31，第一百一十二条 -> 112，748 -> 748，十 -> 10。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in CN_DIGIT:
            num = CN_DIGIT[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        elif ch == "千":
            total += (num or 1) * 1000
            num = section = 0
        else:
            return None
    total += section + num
    return total or None


SYS_PROMPT_ZS = (
    "你是中国水法律领域的法学研究者。用户给出一个受规制行为的描述，请指出该行为在指定时点"
    "依据哪一部法律的哪一条。只输出 JSON：{\"cites\":[{\"law\":\"法律全称\",\"article\":\"条号（阿拉伯数字）\"}]}，"
    "按相关性从高到低最多 5 条。不确定时宁可少列。"
)
SYS_PROMPT_RAG = (
    "你是中国水法律领域的法学研究者。下面给出检索到的候选条文，请判断哪些是用户问题的**规范依据**，"
    "按相关性从高到低排出最多 5 条。只输出 JSON：{\"ids\":[\"条号ID\", ...]}。只能从候选中选，不得编造。"
)


def llm_zero_shot(chat, corpus, date_filter):
    def call(query, as_of, k=5, basin_hint=None):
        prompt = "时点：%s\n受规制行为：%s" % (as_of, query)
        js = chat([{"role": "system", "content": SYS_PROMPT_ZS},
                   {"role": "user", "content": prompt}], json_mode=True)
        cites = _json_field(js, "cites")
        out = []
        for c in cites or []:
            did = corpus.doc_by_name(c.get("law"))
            no = cn2num(str(c.get("article") or ""))
            if not did or not no:
                continue
            if date_filter and not corpus.doc_ok(did, as_of):
                continue
            pid = corpus.art_index(did, no)
            if pid and pid not in out:
                out.append(pid)
        return out[:k]
    return call


def llm_rag(chat, corpus, retriever, date_filter, recall=20):
    def call(query, as_of, k=5, basin_hint=None):
        pool = corpus.pool(as_of if date_filter else None)
        cand = retriever.rank(query, pool, k=recall)
        lines = []
        for pid in cand:
            i = corpus.pos[pid]
            lines.append("[%s] %s" % (pid, corpus.text[i][:220].replace("\n", " ")))
        prompt = "时点：%s\n受规制行为：%s\n\n候选条文：\n%s" % (as_of, query, "\n".join(lines))
        js = chat([{"role": "system", "content": SYS_PROMPT_RAG},
                   {"role": "user", "content": prompt}], json_mode=True)
        ids = [x for x in (_json_field(js, "ids") or []) if x in cand]
        for pid in cand:                     # 模型输出不足 5 条时按召回序补足，保持可比
            if len(ids) >= k:
                break
            if pid not in ids:
                ids.append(pid)
        return ids[:k]
    return call


def _json_field(text, key):
    try:
        js = json.loads(text or "{}")
        v = js.get(key)
        return v if isinstance(v, list) else []
    except Exception:  # noqa: BLE001 模型可能带 ```json 包裹或注释
        m = re.search(r"\[.*\]", text or "", re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return []
        return []


# ------------------------------------------------------------------ systems
def build_system(name, corpus, con, cfg):
    """返回 f(query, as_of, k, basin_hint) -> [prov_id,...]；未配置时返回 (None, 原因)。"""
    if name == "full":
        return (lambda q, d, k=5, b=None: [x["prov_id"] for x in
                                           s4_reason.find_law(con, q, d, k=k, basin_hint=b, rules="abc")]), ""
    if name == "ablation":
        return (lambda q, d, k=5, b=None: [x["prov_id"] for x in
                                           s4_reason.find_law(con, q, d, k=k, basin_hint=b, rules="")]), ""
    if name.startswith("ablate_"):
        # 留一规则消融：ablate_noa = 去掉 R-a（流域范围），no b = 去掉 R-b（特别法优先），
        # no c = 去掉 R-c（援引穿透）。论文 B7「去掉 priority 边」对应 ablate_nob。
        keep = {"ablate_noa": "bc", "ablate_nob": "ac", "ablate_noc": "ab"}[name]
        return (lambda q, d, k=5, b=None: [x["prov_id"] for x in
                                           s4_reason.find_law(con, q, d, k=k, basin_hint=b, rules=keep)]), ""
    if name == "full_syn":
        # post-hoc 扩展（2026-11-16 测试集错误分析后加入，报告纪律见
        # build/synmap.py 与 build/out/synonym_report.md）：B8 + 跨代概念对照词典
        # （corpus/18，条文侧 bigram 扩充）+ R-d 罚则降权。不得并入冻结主系统 B8。
        return (lambda q, d, k=5, b=None: [x["prov_id"] for x in
                                           s4_reason.find_law(con, q, d, k=k, basin_hint=b,
                                                              rules="abcd", syn=True)]), ""
    if name == "full_syn_ac":
        # 全栈 post-hoc 变体：B8 + 答案类别判定层(v2) + 跨代概念词典 + R-d。
        import answer_class as AC
        cov = AC._scorer(con)

        def call(q, d, k=5, b=None):
            return AC.apply_abstention(con, q, d, k=k, basin_hint=b, rules="abcd", syn=True)[0]
        return call, ""
    if name == "full_ac":
        # 本文排序 + 答案类别判定层（build/answer_class.py）：判 none 则返回空列表。
        import answer_class as AC
        cov = AC._scorer(con)

        def call(q, d, k=5, b=None):
            return AC.apply_abstention(con, q, d, k=k, basin_hint=b, rules="abc")[0]
        return call, ""
    if name == "hybrid_rrf":
        # 诊断用混合：本文排序 ∪ BM25(一元+二元, as-of 池)，Reciprocal Rank Fusion (k=60)。
        # 之所以做这个：两者 flip 命中集合交集只有 16/19，错误互补；融合是检验
        # "互补性能否变成增益"的最短路径。**注意：这是在看过测试集之后加的实验**，
        # 只能作为诊断与下一步假设，不能当成冻结协议的正式结果写进论文主表。
        m = BM25(corpus, "unibi")

        def call(q, d, k=5, b=None):
            a = [x["prov_id"] for x in s4_reason.find_law(con, q, d, k=20, basin_hint=b, rules="abc")]
            bb = m.rank(q, corpus.pool(d), 20)
            sc = {}
            for lst in (a, bb):
                for i, pid in enumerate(lst):
                    sc[pid] = sc.get(pid, 0.0) + 1.0 / (60 + i + 1)
            return sorted(sc, key=lambda p: -sc[p])[:k]
        return call, ""
    if name == "bm25_nofilter":
        m = BM25(corpus, "bigram")
        return (lambda q, d, k=5, b=None: m.rank(q, corpus.pool(None), k)), ""
    if name == "bm25_date":
        m = BM25(corpus, "bigram")
        return (lambda q, d, k=5, b=None: m.rank(q, corpus.pool(d), k)), ""
    if name == "bm25uni_date":
        m = BM25(corpus, "unibi")
        return (lambda q, d, k=5, b=None: m.rank(q, corpus.pool(d), k)), ""
    if name == "tfidf_date":
        m = TFIDF(corpus, "unibi")
        return (lambda q, d, k=5, b=None: m.rank(q, corpus.pool(d), k)), ""
    if name in ("dense", "dense_date"):
        from providers import Embedder, Unavailable
        emb = Embedder(cfg.get("emb"))
        if emb.why_unavailable():
            return None, emb.why_unavailable()
        try:
            m = Dense(corpus, emb)
        except Unavailable as e:
            return None, str(e)
        filt = name == "dense_date"
        return (lambda q, d, k=5, b=None: m.rank(q, corpus.pool(d if filt else None), k)), ""
    if name in ("llm_zero", "llm_rag", "llm_rag_date"):
        from providers import Chat, Embedder, Unavailable
        chat = Chat(cfg.get("llm"))
        if chat.why_unavailable():
            return None, chat.why_unavailable()
        try:
            if name == "llm_zero":
                return llm_zero_shot(chat, corpus, date_filter=True), ""
            emb = Embedder(cfg.get("emb"))
            if not emb.why_unavailable():
                base = Dense(corpus, emb)
            else:
                base = BM25(corpus, "bigram")   # 无嵌入时降级为 BM25 召回，报告里注明
                cfg["rag_recall_note"] = "recall=BM25（嵌入未配置，降级）"
            return llm_rag(chat, corpus, base, date_filter=(name == "llm_rag_date")), ""
        except Unavailable as e:
            return None, str(e)
    raise SystemExit("未知系统：%s" % name)


SYSTEM_ORDER = ["bm25_nofilter", "bm25_date", "bm25uni_date", "tfidf_date",
                "dense", "dense_date", "llm_zero", "llm_rag", "llm_rag_date",
                "ablation", "ablate_noa", "ablate_nob", "ablate_noc", "full", "full_ac",
                "full_syn", "full_syn_ac", "hybrid_rrf"]
SYSTEM_LABEL = {
    "bm25_nofilter": "B1 BM25（无时间过滤）",
    "bm25_date": "B2 BM25 + as-of 过滤",
    "bm25uni_date": "B2u BM25 一元+二元 + as-of",
    "tfidf_date": "B2t TF-IDF 余弦 + as-of",
    "dense": "B3 Dense（无时间过滤）",
    "dense_date": "B3d Dense + as-of 过滤",
    "llm_zero": "B4 LLM zero-shot",
    "llm_rag": "B5 LLM + RAG",
    "llm_rag_date": "B6 LLM + RAG + as-of 过滤",
    "ablation": "B7a 本文 −R-a/R-b/R-c（全消融）",
    "ablate_noa": "B7b 本文 −R-a（去流域范围）",
    "ablate_nob": "B7c 本文 −R-b（去 LEX_SPECIALIS 优先）",
    "ablate_noc": "B7d 本文 −R-c（去援引穿透）",
    "full": "B8 本文（完整）",
    "full_ac": "B8+AC 本文 + 答案类别判定层",
    "full_syn": "B8+S 本文 + 跨代概念词典 + 罚则降权（post-hoc 扩展）",
    "full_syn_ac": "B8+S+AC 本文 + 词典 + 罚则降权 + 答案类别（全栈 post-hoc）",
    "hybrid_rrf": "B9 混合RRF（本文∪BM25，post-hoc 诊断）",
}


# ------------------------------------------------------------------ eval
def load_queries():
    return E.load()


def evaluate(call, corpus, rows, mode, k=5):
    """在单一输入设定下跑全套 T1 指标，返回 dict（含逐查询命中位）。"""
    res = {"mode": mode, "per_q": {}, "n": len(rows)}
    cache = {}

    def get(r, as_of):
        key = (r["query_id"], as_of)
        if key not in cache:
            try:
                cache[key] = call(E.qstr(r, mode), as_of, k, E.qhint(r, mode))
            except Exception as e:  # noqa: BLE001 单条失败不该毁掉整轮评测
                cache[key] = []
                res.setdefault("errors", []).append("%s@%s: %s" % (r["query_id"], as_of, e))
        return cache[key]

    def hit_rank(gold, ids, level):
        if not gold:
            return None
        if level == "article":
            gk = {E.to_article(x) for x in gold}
            return next((i + 1 for i, p in enumerate(ids) if E.to_article(p) in gk), None)
        return next((i + 1 for i, p in enumerate(ids) if p in gold), None)

    for tag, as_of, key in (("b0", B0, "b0_prov_ids"), ("b1", B1, "b1_prov_ids")):
        for level in ("para", "article"):
            p5 = mrr = 0.0
            hit_flags = []
            for r in rows:
                gold = E.parse_ids(r[key])
                ids = get(r, as_of)
                rk = hit_rank(gold, ids, level)
                ok = bool(rk and rk <= k)
                p5 += 1 if ok else 0
                mrr += 1 / rk if rk else 0.0
                hit_flags.append(1 if ok else 0)
                res["per_q"].setdefault(r["query_id"], {})["%s_%s" % (tag, level)] = ok
            res["%s_%s_p5" % (tag, level)] = p5 / max(len(rows), 1)
            res["%s_%s_mrr" % (tag, level)] = mrr / max(len(rows), 1)
            res["%s_%s_hits" % (tag, level)] = hit_flags
            if level == "para":
                # AC-P@5：金标为空时「返回空」记对（否则记错）；金标非空时同 P@5。
                # 只有带弃权层的系统能得分，检索基线结构上拿不到这部分的任何一分。
                ac = []
                for r, ok_flag in zip(rows, hit_flags):
                    gold = E.parse_ids(r[key])
                    ids = get(r, as_of)
                    ac.append(1 if ((not gold and not ids) or ok_flag) else 0)
                res["%s_para_acp5" % tag] = sum(ac) / max(len(ac), 1)
                res["%s_para_ac" % tag] = ac

    # 废止法引用率：top-5 里出现该时点无效文件的查询比例
    for tag, as_of in (("b0", B0), ("b1", B1)):
        bad = 0
        for r in rows:
            ids = get(r, as_of)
            if any(not corpus.doc_ok(corpus.doc[corpus.pos[p]], as_of) for p in ids if p in corpus.pos):
                bad += 1
        res["%s_rep" % tag] = bad / max(len(rows), 1)

    # flip 准确率
    for level in ("para", "article"):
        for cls in ("Y", "N"):
            ok = n = 0
            for r in rows:
                if r["flip"] != cls:
                    continue
                n += 1
                g0, g1 = E.parse_ids(r["b0_prov_ids"]), E.parse_ids(r["b1_prov_ids"])
                ids = get(r, B1)
                top1 = ids[0] if ids else None
                if top1 is None:
                    continue
                if level == "article":
                    t1 = E.to_article(top1)
                    g0k, g1k = {E.to_article(x) for x in g0}, {E.to_article(x) for x in g1}
                    good = (t1 in g1k and t1 not in g0k) if cls == "Y" else (t1 in g0k)
                else:
                    good = (top1 in g1 and top1 not in g0) if cls == "Y" else (top1 in g0)
                ok += 1 if good else 0
                res["per_q"].setdefault(r["query_id"], {})["flip%s_%s" % (cls, level)] = good
            res["flip%s_%s" % (cls, level)] = (ok, n)

    # 条件 flip：B0 条级已命中的翻转题
    ok = n = 0
    for r in rows:
        if r["flip"] != "Y" or not E.parse_ids(r["b0_prov_ids"]):
            continue
        g0k = {E.to_article(x) for x in E.parse_ids(r["b0_prov_ids"])}
        if not any(E.to_article(p) in g0k for p in get(r, B0)):
            continue
        n += 1
        g1k = {E.to_article(x) for x in E.parse_ids(r["b1_prov_ids"])}
        ids = get(r, B1)
        ok += 1 if ids and E.to_article(ids[0]) in g1k else 0
    res["cond"] = (ok, n)
    return res


# ------------------------------------------------------------------ stats
def bootstrap_ci(flags, n=1000, seed=2026, rng=None):
    """按查询重采样（百分位法）。flags 为 0/1 列表。

    与 `build/tools/eval_ci.py` 的 boot() 同一算法、同一 RNG 流（传入同一个 rng 时结果逐位相同），
    这样论文里 B8 的 CI 与 baseline 的 CI 不会因随机数流不同而出现 1–2 个百分点的无谓差异。
    """
    if not flags:
        return (0.0, 0.0)
    rng = rng or random.Random(seed)
    m = len(flags)
    stats = []
    for _ in range(n):
        s = 0
        for _ in range(m):
            s += flags[rng.randrange(m)]
        stats.append(s / m)
    stats.sort()
    return (stats[int(0.025 * n)], stats[min(int(0.975 * n), n - 1)])


def mcnemar_exact(a, b):
    """配对检验：a/b 为同一批查询上两个系统的 0/1 正确性。返回 (b01, c10, p)。"""
    n01 = sum(1 for x, y in zip(a, b) if x and not y)
    n10 = sum(1 for x, y in zip(a, b) if not x and y)
    n = n01 + n10
    if n == 0:
        return n01, n10, 1.0
    k = min(n01, n10)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return n01, n10, min(1.0, 2 * tail)


def cohens_h(p1, p2):
    f = lambda p: 2 * math.asin(math.sqrt(min(max(p, 0.0), 1.0)))
    return f(p1) - f(p2)
