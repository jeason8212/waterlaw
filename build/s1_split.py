# -*- coding: utf-8 -*-
"""S1 条文切分：把规范原文切成 条/款 级记录，并与 corpus/01 的文件注册表对齐。

用法: python s1_split.py
输入: build/data/raw/*.txt（带 # key: value 头信息）+ corpus/01_规范文件清单.csv
输出: out/kg.db 的 doc / provision 表
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

ART_RE = re.compile(r"^第([一二三四五六七八九十百千零〇]+)条(之[一二三四五六七八九十]+)?\s*(.*)$")
ITEM_RE = re.compile(r"^[（(][一二三四五六七八九十百零〇\d]{1,3}[）)]")
HEAD_RE = re.compile(r"^第[一二三四五六七八九十百千零〇]+分?[编章节]\s*")
PARA_HINT = re.compile(r"^\{p(\d+)\}\s*")


def parse_meta(lines):
    meta = {}
    for ln in lines:
        s = ln.lstrip("#").strip()
        if ":" in s:
            k, v = s.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def parse_file(path):
    lines = [l.rstrip("\n") for l in open(path, encoding="utf-8").read().splitlines()]
    meta = parse_meta([l for l in lines if l.startswith("#")])
    arts, cur, heading = [], None, ""
    for ln in lines:
        if ln.startswith("#") or not ln.strip():
            continue
        if HEAD_RE.match(ln):
            heading = ln.strip()
            continue
        m = ART_RE.match(ln.strip())
        if m:
            no, suffix, rest = C.cn2int(m.group(1)), m.group(2) or "", m.group(3).strip()
            cur = {"cn": "第%s条%s" % (m.group(1), suffix), "no": no,
                   "suffix": suffix, "heading": heading, "paras": []}
            arts.append(cur)
            if rest:
                cur["paras"].append(rest)
            continue
        if cur is None:
            continue
        text = ln.strip()
        hint = PARA_HINT.match(text)
        # 项（（一）（二）…）不是款：并回上一款，否则 avg_para 会被"项"灌水
        if ITEM_RE.match(text) and cur["paras"]:
            last = cur["paras"][-1]
            if isinstance(last, tuple):
                cur["paras"][-1] = (last[0], last[1] + text)
            else:
                cur["paras"][-1] = last + text
            continue
        if hint:
            cur["paras"].append((int(hint.group(1)), text[hint.end():]))
        else:
            cur["paras"].append(text)
    return meta, arts


def seed_docs(con):
    rows = C.read_csv("01_规范文件清单.csv")
    seen = {}
    for r in rows:
        status = r["status_asof_20261106"]
        valid_to = None
        m = re.search(r"已废止[（(](\d{4}-\d{2}-\d{2})[)）]", status)
        if m:
            valid_to = C.d_minus_one(m.group(1))
        seen[r["doc_id"]] = (r["doc_name"], r["doc_type"], r["legal_hierarchy"],
                             r["issuer"], r["effective_date"] or None, valid_to, status)
    for did, t in seen.items():
        con.execute("INSERT INTO doc(doc_id,name,doc_type,hierarchy,issuer,valid_from,valid_to,status,source)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (did, t[0], t[1], t[2], t[3], t[4], t[5], t[6], "corpus/01_规范文件清单.csv"))
    return len(seen)


def main():
    con = C.connect(fresh=True)
    ndoc = seed_docs(con)
    nprov = 0
    for fn in sorted(os.listdir(C.RAW_DIR)):
        if not fn.endswith(".txt"):
            continue
        meta, arts = parse_file(os.path.join(C.RAW_DIR, fn))
        did = meta["doc_id"]
        # 覆盖式补全：raw 头的元数据优先于注册表（注册表可能标"待核验"）
        con.execute("UPDATE doc SET valid_from=COALESCE(?,valid_from), status=COALESCE(?,status) WHERE doc_id=?",
                    (meta.get("valid_from") or None, meta.get("status") or None, did))
        for a in arts:
            for i, p in enumerate(a["paras"], start=1):
                pno, text = (p if isinstance(p, tuple) else (i, p))
                key = "%d%s" % (a["no"], ("b" + a.get("suffix", "").replace("之", "")) if a.get("suffix") else "")
                pid = "%s#%s-%d" % (did, key, pno)
                con.execute("INSERT OR REPLACE INTO provision"
                            "(prov_id,doc_id,article_no,article_cn,para_no,heading,text,is_proviso)"
                            " VALUES(?,?,?,?,?,?,?,?)",
                            (pid, did, a["no"], a["cn"], pno, a["heading"], text,
                             1 if re.search(r"但是|但[，,]", text) else 0))
                nprov += 1
    con.commit()
    print("[S1] 文件 %d 个，条文（条/款）%d 条" % (ndoc, nprov))
    for r in con.execute("SELECT doc_id,name,valid_from,valid_to,status FROM doc"
                         " WHERE doc_id IN (SELECT DISTINCT doc_id FROM provision) ORDER BY doc_id"):
        print("     %-6s %-22s %s ~ %s  %s" % (r["doc_id"], r["name"], r["valid_from"],
                                               r["valid_to"] or "(现行)", r["status"]))
    con.close()


if __name__ == "__main__":
    main()
