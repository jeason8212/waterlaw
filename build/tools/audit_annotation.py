# -*- coding: utf-8 -*-
"""标注表单体检：格式 / 条文存在性 / 时点有效性 / 逻辑一致性 / 空答案声明 / 组列规范。

用法：
    python tools/audit_annotation.py                       # 默认审 15_ 预标注表单
    python tools/audit_annotation.py --b corpus/xxx.csv    # 审指定文件
    python tools/audit_annotation.py --fix-brackets        # 顺手清掉编号里的方括号（唯一的安全自动修复）

设计纪律：
- 只报"机械可判定"的问题（编号不存在、时点无效、字段缺失、两侧矛盾）；
- 判断性问题一律降级为"待确认"，并给出可核对的理由，不代替标注者下结论；
- 组 A 才与草稿（金标）比对；组 B 绝不打印金标内容，避免污染独立性。
"""
import argparse
import csv
import io
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
PROJ = os.path.dirname(BUILD)
sys.path.insert(0, BUILD)
import common as C  # noqa: E402

T = {"b0": "2026-06-30", "b1": "2026-11-06"}
DEFAULT_FORM = os.path.join(C.PROJ_DIR, "corpus", "15_预标注表单_待审核.csv")


def read_rows(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if "query_id" in text.split("\n", 1)[0]:
            return list(csv.DictReader(io.StringIO(text)))
    raise SystemExit("[错误] 无法识别编码：%s" % path)


def split_ids(s):
    s = re.sub(r"[\[\]【】]", "", (s or "")).strip()
    s = s.replace("；", ";").replace("，", ";")
    if not s or s.lower() in ("(none)", "none"):
        return []
    return [x.strip() for x in s.split(";") if x.strip() and x.strip().lower() != "(none)"]


def article(pid):
    if "#" not in pid:
        return pid
    d, rest = pid.split("#", 1)
    return d + "#" + rest.split("-")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--b", default=DEFAULT_FORM, help="待审的表单 CSV")
    ap.add_argument("--fix-brackets", action="store_true", help="清除编号中的方括号后重写（UTF-8 BOM）")
    a = ap.parse_args()

    rows = read_rows(a.b)
    if a.fix_brackets:
        fields = list(rows[0].keys())
        n = 0
        for r in rows:
            for k in ("b0_prov_ids", "b1_prov_ids"):
                v = r.get(k) or ""
                nv = re.sub(r"[\[\]【】]", "", v).strip()
                if nv != v.strip():
                    n += 1
                r[k] = nv
        with io.open(a.b, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print("[清理] 方括号单元格 %d 处，已按 UTF-8(BOM) 重写：%s" % (n, a.b))

    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    docs = {}
    for r in con.execute("SELECT doc_id,name,valid_from,valid_to FROM doc"):
        docs[r["doc_id"]] = {"name": r["name"], "vf": r["valid_from"] or "1900-01-01",
                             "vt": r["valid_to"] or "9999-12-31"}
    provs = {r["prov_id"] for r in con.execute("SELECT prov_id FROM provision")}

    def ok_at(doc, t):
        m = docs.get(doc)
        if not m:
            return False, "文件不存在"
        return (m["vf"] <= t <= m["vt"]), "%s~%s" % (m["vf"], m["vt"])

    hard, soft = [], []
    for r in rows:
        q, g = r["query_id"], r.get("group", "")
        I = {k: split_ids(r.get(k + "_prov_ids")) for k in ("b0", "b1")}

        for k in ("b0", "b1"):
            for pid in I[k]:
                if not re.match(r"^[A-Z]-\d+#\d+(-\d+)?$", pid):
                    hard.append((q, "编号格式", "%s %r" % (k, pid)))
                elif pid not in provs:
                    hard.append((q, "库里没有", "%s %s" % (k, pid)))
                else:
                    ok, why = ok_at(pid.split("#")[0], T[k])
                    if not ok:
                        hard.append((q, "时点无效", "%s %s（%s）" % (k, pid, why)))
            if I[k] and not (r.get(k + "_basis") or "").strip():
                soft.append((q, "缺理由", "%s_basis 为空" % k))
            if len(I[k]) > 3:
                hard.append((q, "超上限", "%s 有 %d 条（上限 3）" % (k, len(I[k]))))
            if len(set(I[k])) != len(I[k]):
                soft.append((q, "重复编号", k))
            if not I[k] and len((r.get("verify_note") or "").strip()) < 10:
                hard.append((q, "空答案未声明", "%s 为空，verify_note 未声明语料边界" % k))

        at = (r.get("answer_type") or "").strip()
        if at not in ("empty", "single", "multi"):
            hard.append((q, "answer_type 缺失", repr(at)))
        else:
            mx = max(len(I["b0"]), len(I["b1"]))
            if mx == 0 and at != "empty":
                hard.append((q, "answer_type 矛盾", "两侧均空却填 " + at))
            if mx > 1 and at == "single":
                basis = (r.get("b0_basis") or "") + (r.get("b1_basis") or "")
                if not re.search(r"另|重申|同旨|援引|变厚", basis):
                    soft.append((q, "answer_type 待确认", "最多 %d 条却填 single，basis 未说明'另/重申'" % mx))
            if mx <= 1 and at == "multi":
                hard.append((q, "answer_type 矛盾", "两侧≤1 条却填 multi"))

        fl = (r.get("flip") or "").strip()
        if fl not in ("Y", "N"):
            hard.append((q, "flip 缺失", repr(fl)))
        else:
            f0 = article(I["b0"][0]) if I["b0"] else None
            f1 = article(I["b1"][0]) if I["b1"] else None
            if f0 or f1:
                if f0 != f1 and fl == "N":
                    soft.append((q, "flip 待确认", "主依据 %s→%s 变化却填 N" % (f0, f1)))
                if f0 == f1 and fl == "Y":
                    soft.append((q, "flip 待确认", "主依据未变（%s）却填 Y" % f0))

        ra = (r.get("review_action") or "").strip()
        if g == "A" and ra not in ("接受", "修改", "换选"):
            hard.append((q, "组A review_action", "缺失/非法 " + repr(ra)))
        if g == "B" and ra:
            hard.append((q, "组B 不该填", ra))

        for k in ("b0", "b1"):
            if re.search(r"[\[\]【】]", r.get(k + "_prov_ids") or ""):
                soft.append((q, "方括号污染", r[k + "_prov_ids"]))

    print("=" * 70)
    print("文件：%s" % a.b)
    print("行数：%d（组 A %d / 组 B %d）" % (
        len(rows), sum(1 for r in rows if r.get("group") == "A"),
        sum(1 for r in rows if r.get("group") == "B")))
    print("硬错误 %d 项 / 待确认 %d 项" % (len(hard), len(soft)))
    if hard:
        print("\n--- 硬错误（必须改）---")
        for q, c, m in hard:
            print("  %-5s [%s] %s" % (q, c, m))
    if soft:
        print("\n--- 待确认（需人判断，不代改）---")
        for q, c, m in soft:
            print("  %-5s [%s] %s" % (q, c, m))
    print("\n提示：组 B 行不与金标比对（避免污染），组 A 的一致性用 kappa_iaa.py 看。")


if __name__ == "__main__":
    main()
