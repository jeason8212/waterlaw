# -*- coding: utf-8 -*-
"""可复现性校验：删库 → 从 data/raw 全量重建 → 与基线指标逐项比对。

用法:
    python tools/rebuild_and_verify.py            # 先记录当前指标为基线，再重建比对
    python tools/rebuild_and_verify.py --baseline # 只把当前指标存为基线（不重建）

判定：表行数、边类型分布、模态分布、条文文本 md5 全部一致 -> PASS（退出码 0）
      任一项漂移 -> FAIL（退出码 1），并打印差异项。

意义：论文的 reproducibility 声明必须能被第三方执行验证。本脚本保证
"S1–S4 全部输入为 data/raw/*.txt + corpus/*.csv，输出指标唯一确定"。
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
sys.path.insert(0, BUILD)

BASE = os.path.join(BUILD, "out", "repro_baseline.json")
TABLES = ("doc", "provision", "norm", "entity", "edge", "issue", "conduct")


def collect(db_path):
    con = sqlite3.connect(db_path)
    m = {t: con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in TABLES}
    m["edge_by_rel"] = dict(con.execute("SELECT rel,COUNT(*) FROM edge GROUP BY rel").fetchall())
    m["norm_by_deo"] = dict(con.execute("SELECT deontic,COUNT(*) FROM norm GROUP BY deontic").fetchall())
    h = hashlib.md5()
    for (text,) in con.execute("SELECT text FROM provision ORDER BY prov_id"):
        h.update((text or "").encode("utf-8"))
    m["provision_md5"] = h.hexdigest()
    con.close()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="只记录基线，不重建")
    ap.add_argument("--skip-rebuild", action="store_true", help="只在现有库上比对（不删库）")
    a = ap.parse_args()

    import common as C
    os.makedirs(os.path.dirname(BASE), exist_ok=True)

    if a.baseline:
        m = collect(C.DB_PATH)
        json.dump(m, open(BASE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[基线] 已记录 -> %s" % os.path.relpath(BASE, BUILD))
        return 0

    if not os.path.exists(BASE):
        print("[跳过] 尚无基线，请先运行 --baseline")
        return 0

    base = json.load(open(BASE, encoding="utf-8"))

    if not a.skip_rebuild:
        if os.path.exists(C.DB_PATH):
            os.remove(C.DB_PATH)
            print("[重建] 已删除 out/kg.db，从 data/raw 全量重建……")
        r = subprocess.run([sys.executable, os.path.join(BUILD, "run_all.py")],
                           cwd=BUILD, capture_output=True, text=True)
        if r.returncode != 0:
            print("[FAIL] run_all.py 执行失败：\n%s" % (r.stderr[-800:]))
            return 1

    now = collect(C.DB_PATH)
    bad = []
    print("%-18s %10s %10s %s" % ("指标", "基线", "重建后", "判定"))
    for k in TABLES:
        ok = base[k] == now[k]
        if not ok:
            bad.append(k)
        print("%-18s %10d %10d %s" % (k, base[k], now[k], "OK" if ok else "DIFF"))
    for grp in ("edge_by_rel", "norm_by_deo"):
        for k in sorted(set(base.get(grp, {})) | set(now.get(grp, {}))):
            b, n = base[grp].get(k, 0), now[grp].get(k, 0)
            if b != n:
                bad.append("%s:%s" % (grp, k))
                print("  %-24s %8d %8d DIFF" % (k, b, n))
    ok = base.get("provision_md5") == now.get("provision_md5")
    if not ok:
        bad.append("provision_md5")
    print("%-18s %10s %10s %s" % ("provision_md5", base.get("provision_md5", "")[:8],
                                  now.get("provision_md5", "")[:8], "OK" if ok else "DIFF"))

    if bad:
        print("\n[FAIL] %d 项漂移：%s" % (len(bad), ", ".join(bad)))
        return 1
    print("\n[PASS] 全量重建与基线完全一致（确定性流水线）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
