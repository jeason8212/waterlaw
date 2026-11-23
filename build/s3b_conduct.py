# -*- coding: utf-8 -*-
"""S3b 物化：把「规范行为（conduct）」作为一等节点写进图谱。

为什么单独一步：`conduct_index.py` 是判定逻辑，`s3b_conduct.py` 是**数据产物**。论文要主张
"conduct → 首次规范依据" 是图谱构件而非特征工程，就必须能在库里点到这个节点、并沿
conduct → prov_id → doc → valid_from 走到生效日。

表结构（幂等重建）：
    conduct(conduct_id, norm_id, prov_id, doc_id, deontic, origin, core_text, valid_from, valid_to)

同时导出 `build/out/conduct.cypher`（Neo4j 可直接执行），便于审稿人复查。

用法：
    python build/s3b_conduct.py            # 写入 kg.db
    python build/s3b_conduct.py --dry      # 只统计不写
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import common as C          # noqa: E402
import conduct_index as CI  # noqa: E402

DDL = """
DROP TABLE IF EXISTS conduct;
CREATE TABLE conduct (
  conduct_id TEXT PRIMARY KEY,
  norm_id    TEXT NOT NULL,
  prov_id    TEXT NOT NULL,
  doc_id     TEXT NOT NULL,
  deontic    TEXT NOT NULL,
  origin     TEXT,
  core_text  TEXT NOT NULL,
  valid_from TEXT,
  valid_to   TEXT
);
CREATE INDEX idx_conduct_prov ON conduct(prov_id);
CREATE INDEX idx_conduct_doc  ON conduct(doc_id);
"""


def main():
    dry = "--dry" in sys.argv
    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = CI.extract(con)          # [(cid, norm_id, prov_id, doc_id, deontic, origin, core)]

    # 完整性检查：每条 conduct 必须能落到存在的条文与文件上
    provs = {r[0] for r in con.execute("SELECT prov_id FROM provision")}
    docs = {r[0]: (r[1] or "", r[2]) for r in con.execute(
        "SELECT doc_id, valid_from, valid_to FROM doc")}
    bad = [r for r in rows if r[2] not in provs or r[3] not in docs]
    if bad:
        sys.stdout.write("FAIL：%d 条 conduct 指向不存在的条文/文件，例 %s\n"
                         % (len(bad), bad[0]))
        return 1

    recs = [(cid, nid, pid, did, de, origin, core,
             docs[did][0], docs[did][1]) for cid, nid, pid, did, de, origin, core in rows]

    sys.stdout.write("conduct %d 条（基础句 %d，枚举继承 %d）\n"
                     % (len(recs), sum(1 for r in recs if r[5] == "base"),
                        sum(1 for r in recs if r[5] != "base")))
    dist = {}
    for r in recs:
        dist[r[3]] = dist.get(r[3], 0) + 1
    sys.stdout.write("覆盖文件 %d 部：\n" % len(dist))
    names = {r[0]: r[1] for r in con.execute("SELECT doc_id, name FROM doc")}
    for did, n in sorted(dist.items(), key=lambda x: -x[1])[:8]:
        sys.stdout.write("   %-6s %-28s %4d\n" % (did, names.get(did, "")[:26], n))
    sys.stdout.write("有明确生效日的 %d / %d 条（其余落在无起始日的文件上）\n"
                     % (sum(1 for r in recs if r[7]), len(recs)))

    if dry:
        sys.stdout.write("\n--dry：未写入。\n")
        return 0

    con.executescript(DDL)
    con.executemany("INSERT INTO conduct VALUES(?,?,?,?,?,?,?,?,?)", recs)
    con.commit()
    n = con.execute("SELECT count(*) FROM conduct").fetchone()[0]
    sys.stdout.write("\n已写入 kg.db：conduct %d 行\n" % n)

    # Cypher 导出（只导 conduct 与其所属条文，避免文件过大）
    out = os.path.join(C.OUT_DIR, "conduct.cypher")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("// 由 s3b_conduct.py 生成；conduct 节点 + 与条文/文件的结构关系\n")
        for r in recs:
            f.write("MERGE (c:Conduct {conduct_id:'%s'}) SET c.deontic='%s', "
                    "c.origin='%s', c.core='%s', c.valid_from=date('%s');\n"
                    % (r[0], r[4], r[5].split(":")[0],
                       r[6].replace("'", "\\'").replace("\n", " ")[:80],
                       r[7] or "1970-01-01"))
            f.write("MATCH (p:Provision {prov_id:'%s'}),(c:Conduct {conduct_id:'%s'}) "
                    "MERGE (p)-[:HAS_CONDUCT]->(c);\n" % (r[2], r[0]))
    sys.stdout.write("Cypher 导出：%s\n" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
