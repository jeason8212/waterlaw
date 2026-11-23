# -*- coding: utf-8 -*-
"""S3 建图：把 Norm 的槽位落成实体与边，并导出 Neo4j Cypher。

边分四层：
  结构层 HAS_PROVISION / HAS_NORM
  效力层 SUPERSEDES / LEX_SPECIALIS / REFERS_TO
  规范层 IMPOSES_DUTY_ON / CONFERS_POWER_ON / ESTABLISHES / REQUIRES_UNIFICATION
每条边强制带 prov_id 与 evidence —— 没有来源的边不允许入库（设计原则 1）。

用法: python s3_graph.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

DIM_RE = r"统一(规划|标准|监测|保护措施|执法|调度|信息共享)"
INST_RE = r"(?:建立|健全|实行|推行)([^，。；]{2,18}?(?:机制|制度|体系))"
REFER_RE = r"依照(?:《)?([^》，。；]{2,20}?)(?:》)?的?规定"

TYPE_PREFIX = {"Agency": "AG", "Institution": "IN", "CoordDimension": "CD", "Watershed": "WS"}


def ent(con, etype, name):
    if not name:
        return None
    row = con.execute("SELECT ent_id FROM entity WHERE etype=? AND name=?", (etype, name)).fetchone()
    if row:
        return row["ent_id"]
    eid = "%s-%s" % (TYPE_PREFIX[etype], re.sub(r"[^\w]", "", name)[:12])
    base, k = eid, 2
    while con.execute("SELECT 1 FROM entity WHERE ent_id=?", (eid,)).fetchone():
        eid, k = "%s-%d" % (base, k), k + 1
    con.execute("INSERT INTO entity VALUES(?,?,?)", (eid, etype, name))
    return eid


def edge(con, src, rel, dst, prov_id, evidence, conf=0.8):
    if not (src and dst):
        return
    con.execute("INSERT OR IGNORE INTO edge VALUES(?,?,?,?,?,?)",
                (src, rel, dst, prov_id, evidence[:60], conf))


def resolve_doc(con, phrase):
    """'水污染防治法' -> L-017；按"名称后缀包含"匹配，宁可漏判不可错判。"""
    cands = []
    for d in con.execute("SELECT doc_id,name FROM doc"):
        short = d["name"].replace("中华人民共和国", "")
        if short and (phrase == short or phrase.endswith(short) or short.endswith(phrase)):
            cands.append((len(short), d["doc_id"]))
    return max(cands)[1] if cands else None


def main():
    con = C.connect(fresh=False)
    docs = {d["doc_id"]: d for d in con.execute("SELECT * FROM doc")}

    for p in con.execute("SELECT * FROM provision"):
        edge(con, p["doc_id"], "HAS_PROVISION", p["prov_id"], p["prov_id"],
             p["article_cn"], 1.0)

    for n in con.execute("SELECT * FROM norm WHERE is_negative=0"):
        edge(con, n["prov_id"], "HAS_NORM", n["norm_id"], n["prov_id"], n["text"], 1.0)
        did = n["doc_id"]

        if n["subject_text"] and n["counts_as_duty"]:
            ag = ent(con, "Agency", n["subject_text"])
            rel = "CONFERS_POWER_ON" if n["deontic"] == "power" else "IMPOSES_DUTY_ON"
            if n["deontic"] in ("obligation", "prohibition", "power"):
                edge(con, n["norm_id"], rel, ag, n["prov_id"], n["text"], n["confidence"])

        m = re.search(INST_RE, n["text"])
        if m:
            edge(con, n["norm_id"], "ESTABLISHES", ent(con, "Institution", m.group(1)),
                 n["prov_id"], m.group(0), 0.7)

        for m in re.finditer(DIM_RE, n["text"]):
            edge(con, n["norm_id"], "REQUIRES_UNIFICATION",
                 ent(con, "CoordDimension", "统一" + m.group(1)), n["prov_id"], m.group(0), 0.8)

        if n["deontic"] == "referral":
            for m in re.finditer(REFER_RE, n["text"]):
                if re.search(r"[条款项编章节]|本法|本条例|有关法律|其他法律", m.group(1)):
                    continue  # 法内援引与泛指，不是跨文件 REFERS_TO
                tgt = resolve_doc(con, m.group(1))
                if tgt:
                    edge(con, n["norm_id"], "REFERS_TO", tgt, n["prov_id"], m.group(0), 0.75)
                else:
                    con.execute("INSERT INTO issue VALUES(?,?,?,?)",
                                ("UNRESOLVED_REFERRAL", "WARN",
                                 "援引目标未登记：%s（原文 %s）" % (m.group(1), n["prov_id"]),
                                 n["norm_id"]))

    # ---- 效力层：法典废止单行法 / 特别法关系 ----
    for did, d in docs.items():
        if did == "L-001":
            continue
        if d["valid_to"]:
            edge(con, "L-001", "SUPERSEDES", did, None, "法典附则废止清单", 0.9)
            edge(con, did, "REPEALED_BY", "L-001", None, "法典附则废止清单", 0.9)
    for did in ("L-002", "L-003"):
        if did in docs:
            edge(con, did, "LEX_SPECIALIS", "L-001", None, "法典保留为特别法", 0.95)
            edge(con, "L-001", "GENERAL_OF", did, None, "法典保留为特别法", 0.95)

    # ---- 约束校验 ----
    for d in docs.values():
        if d["valid_from"] and d["valid_to"] and d["valid_from"] > d["valid_to"]:
            con.execute("INSERT INTO issue VALUES(?,?,?,?)",
                        ("TIME_INCONSISTENT", "ERROR", "生效日晚于失效日", d["doc_id"]))
    for n in con.execute("SELECT norm_id,counts_as_duty FROM norm WHERE is_negative=0"):
        has = con.execute("SELECT 1 FROM edge WHERE src_id=? LIMIT 1", (n["norm_id"],)).fetchone()
        if has:
            continue
        if n["counts_as_duty"]:
            con.execute("INSERT INTO issue VALUES(?,?,?,?)",
                        ("DUTY_WITHOUT_AGENCY", "WARN", "义务/职权类规范无主体边（空主语或漏抽）", n["norm_id"]))
        else:
            con.execute("INSERT INTO issue VALUES(?,?,?,?)",
                        ("NON_DUTY_ORPHAN", "INFO", "定义/陈述/倡导类无出边（正常，待补关系时消化）", n["norm_id"]))
    con.commit()

    dump_cypher(con)
    ne = con.execute("SELECT COUNT(*) c FROM entity").fetchone()["c"]
    eg = con.execute("SELECT COUNT(*) c FROM edge").fetchone()["c"]
    print("[S3] 实体 %d 个，边 %d 条，问题 %d 条" % (ne, eg,
          con.execute("SELECT COUNT(*) c FROM issue").fetchone()["c"]))
    for r in con.execute("SELECT rel,COUNT(*) c FROM edge GROUP BY rel ORDER BY c DESC"):
        print("     %-22s %d" % (r["rel"], r["c"]))
    con.close()


def dump_cypher(con):
    lines = ["// 由 s3_graph.py 自动生成；可直接在 Neo4j Browser 执行", ""]
    for d in con.execute("SELECT * FROM doc"):
        lines.append("MERGE (d:LegalDocument {doc_id:'%s'}) SET d.name='%s', d.hierarchy='%s', "
                     "d.valid_from=date('%s'), d.valid_to=%s, d.status='%s';"
                     % (d["doc_id"], d["name"], d["hierarchy"] or "", d["valid_from"] or "1970-01-01",
                        ("date('%s')" % d["valid_to"]) if d["valid_to"] else "null", d["status"] or ""))
    for p in con.execute("SELECT * FROM provision"):
        lines.append("MERGE (p:Provision {prov_id:'%s'}) SET p.article_no=%s, p.para_no=%s, "
                     "p.text='%s';" % (p["prov_id"], p["article_no"], p["para_no"],
                                       p["text"].replace("'", "\\'")))
        lines.append("MATCH (d:LegalDocument {doc_id:'%s'}),(p:Provision {prov_id:'%s'}) "
                     "MERGE (d)-[:HAS_PROVISION]->(p);" % (p["doc_id"], p["prov_id"]))
    for n in con.execute("SELECT * FROM norm WHERE is_negative=0"):
        lines.append("MERGE (n:Norm {norm_id:'%s'}) SET n.deontic='%s', n.modality='%s', "
                     "n.confidence=%s;" % (n["norm_id"], n["deontic"], n["modality"], n["confidence"]))
        lines.append("MATCH (p:Provision {prov_id:'%s'}),(n:Norm {norm_id:'%s'}) "
                     "MERGE (p)-[:HAS_NORM]->(n);" % (n["prov_id"], n["norm_id"]))
    for e in con.execute("SELECT * FROM edge"):
        if e["rel"] in ("HAS_PROVISION", "HAS_NORM"):
            continue
        ls = "LegalDocument" if re.match(r"^[LA]-\d", e["src_id"]) else "Norm"
        ld = "LegalDocument" if re.match(r"^[LA]-\d", e["dst_id"]) else ""
        if ld:
            lines.append("MATCH (a:%s {doc_id:'%s'}),(b:%s {doc_id:'%s'}) MERGE (a)-[:%s]->(b);"
                         % (ls, e["src_id"], ld, e["dst_id"], e["rel"]))
    with open(os.path.join(C.OUT_DIR, "graph.cypher"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
