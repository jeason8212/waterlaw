# -*- coding: utf-8 -*-
"""跨代概念对照词典（corpus/18_跨代概念对照词典.csv）→ 条文侧 bigram 扩充。

问题：法典编纂把旧法概念替换为新概念（突发水污染事故→突发生态环境事件、
风景名胜区水体→自然保护地水体、新建→新设、环境保护主管部门→生态环境主管部门、
水污染损害赔偿纠纷调解→侵害他人合法权益调解等）。查询沿用旧概念时，
法典条文在 bigram-IDF 检索下**结构上不可见**（词汇漂移，B1 侧 Q22/Q43 病例）。

机制：**条文侧** term-anchored 双向扩充——某条文文本包含一条对照中某一侧的
**全部**术语时，为其 bigram 集合补上另一侧术语的 bigram。查询侧不动
（不往查询词袋里加词，避免稀释 IDF 权重分布），B0/B1 两侧对称受益。

用法：只被 s4_reason.find_law(syn=True) 调用（B8+SYN 变体）；冻结主系统 B8
（syn=False，默认）不受影响。

诚实声明（报告纪律）：本词典是 2026-11-16 在测试集错误分析（Q22/Q43/Q58）
之后构造的，属 **post-hoc 组件**。每条对照均有独立于测试集的法律依据
（旧法条文 ↔ 法典条文的逐字对照，见 CSV 的 legal_basis 列），但按报告纪律
只能以 post-hoc 扩展（B8+SYN）单独报告，不得并入冻结协议下的主系统 B8，
也不得回溯修改 B0/B1 主表数字。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
from s4_reason import bigrams

_SEP = "；"
_CACHE = {}


def load_pairs():
    """[(pair_id, old_terms[], new_terms[], basis)]；旧/新侧都是「全部出现才触发」。"""
    out = []
    for r in C.read_csv("18_跨代概念对照词典.csv"):
        old = [t for t in (r.get("old_side") or "").split(_SEP) if t]
        new = [t for t in (r.get("new_side") or "").split(_SEP) if t]
        if old and new:
            out.append((r.get("pair_id") or "?", old, new, r.get("legal_basis") or ""))
    return out


def _dict_mtime():
    p = os.path.join(C.CORPUS_DIR, "18_跨代概念对照词典.csv")
    try:
        return os.path.getmtime(p)
    except OSError:
        return -1


def provision_extra(con):
    """prov_id -> 追加 bigram 集合（term-anchored 双向）。

    例：L-001#117-1 文本含「突发生态环境事件」（P-01 新侧全命中）→
        补上 bigram(突发水污染事故)，查询里的「水污染事故」词簇即可命中它；
        反向：L-017#75-1 含「新建排污口」（P-03 旧侧）→ 补 bigram(新设排污口)。
    """
    key = (C.DB_PATH, _dict_mtime())
    if key in _CACHE:
        return _CACHE[key]
    pairs = load_pairs()
    extra = {}
    for pid, txt in con.execute("SELECT prov_id, text FROM provision"):
        if not txt:
            continue
        add = set()
        for pid_, old, new, _ in pairs:
            if all(t in txt for t in old):          # 旧侧全命中 → 补新侧 bigram
                add |= {g for t in new for g in bigrams(t)}
            if all(t in txt for t in new):          # 新侧全命中 → 补旧侧 bigram
                add |= {g for t in old for g in bigrams(t)}
        if add:
            extra[pid] = add
    _CACHE[key] = extra
    return extra
