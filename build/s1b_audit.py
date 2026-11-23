# -*- coding: utf-8 -*-
"""S1b 语料对账：补完全文后，判断"切分器有没有崩、文本有没有脏"。

C2 补全文唯一可验收的动作就是这一步。它回答四个问题：
  1. 条号连不连续（缺条/重号/跳号 —— 多半是切分漏了或原文有"第X条之一"）
  2. 条数对不对得上官方公布的"共 N 条"
  3. 款/项层级是否异常（平均款数突变、超长条 = 可能没切开）
  4. 语料脏不脏（全角空格、OCR 残留、空条）

用法:
  python s1b_audit.py                  # 审计 data/raw 下全部文件
  python s1b_audit.py L-001            # 只审一部
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import s1_split
import s2_norm

OCR_SUSPECT = re.compile(r"[\u25a1\ufffd]|[a-zA-Z]{3,}")
FULLWIDTH_SPACE = re.compile(r"[\u3000\u00a0]")


def audit_one(path):
    meta, arts = s1_split.parse_file(path)
    expected = meta.get("expected_articles")
    expected = int(expected) if expected and expected.isdigit() else None
    partial = meta.get("partial", "").lower() == "true"

    nos = [a["no"] for a in arts]
    dup = sorted({n for n in nos if nos.count(n) > 1})
    gaps = []
    if nos:
        present = set(nos)
        gaps = [n for n in range(min(nos), max(nos) + 1) if n not in present]

    paras = [(a["no"], i, p if isinstance(p, str) else p[1])
             for a in arts for i, p in enumerate(a["paras"], 1)]
    n_para = len(paras)
    lens = [len(t) for _, _, t in paras]

    # 无主语条款：有义务/职权但抽不出主体 —— ORPHAN_NORM 的根因
    duty, no_subj = 0, 0
    for _, _, t in paras:
        for s in s2_norm.split_sents(t):
            deo, _m = s2_norm.classify(s)
            if deo in ("obligation", "prohibition", "power"):
                duty += 1
                if not s2_norm.find_subject(s):
                    no_subj += 1

    proviso = sum(1 for _, _, t in paras if re.search(r"但是|但[，,]", t))
    heading = sum(1 for a in arts if a["heading"])
    dirty = sum(1 for _, _, t in paras
                if OCR_SUSPECT.search(t) or FULLWIDTH_SPACE.search(t))
    empty = sum(1 for _, _, t in paras if not t.strip())

    return {
        "doc_id": meta.get("doc_id", os.path.basename(path)),
        "name": meta.get("name", ""),
        "partial": partial,
        "expected": expected,
        "n_art": len(arts),
        "n_para": n_para,
        "avg_para": round(n_para / max(len(arts), 1), 2),
        "max_len": max(lens) if lens else 0,
        "dup": dup,
        "gaps": gaps,
        "duty": duty,
        "no_subj": no_subj,
        "proviso": proviso,
        "heading_cov": round(100.0 * heading / max(len(arts), 1)),
        "dirty": dirty,
        "empty": empty,
    }


def verdict(r):
    """返回 (等级, 说明)。等级: OK / WARN / FAIL"""
    v, note = "OK", []
    if r["dup"]:
        v = "FAIL"
        note.append("重号 %s" % r["dup"][:6])
    if r["gaps"] and not r["partial"]:
        v = "FAIL"
        note.append("缺条 %d 处（首 %s）" % (len(r["gaps"]), r["gaps"][:5]))
    if r["expected"] and not r["partial"]:
        if r["n_art"] != r["expected"]:
            v = "FAIL"
            note.append("条数 %d ≠ 官方 %d" % (r["n_art"], r["expected"]))
    if r["max_len"] > 1500:
        v = "WARN" if v == "OK" else v
        note.append("最长款 %d 字，疑未切开" % r["max_len"])
    if r["empty"] or r["dirty"]:
        v = "WARN" if v == "OK" else v
        note.append("脏数据 %d 处（空条 %d）" % (r["dirty"], r["empty"]))
    if r["heading_cov"] < 60 and not r["partial"]:
        v = "WARN" if v == "OK" else v
        note.append("章节标题覆盖率仅 %d%%" % r["heading_cov"])
    if r["duty"] and r["no_subj"] / r["duty"] > 0.35:
        v = "WARN" if v == "OK" else v
        note.append("无主语义务 %d/%d" % (r["no_subj"], r["duty"]))
    return v, "；".join(note) or "—"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    rows, lines = [], ["# 语料对账报告", ""]
    for fn in sorted(os.listdir(C.RAW_DIR)):
        if not fn.endswith(".txt"):
            continue
        if only and not fn.startswith(only):
            continue
        r = audit_one(os.path.join(C.RAW_DIR, fn))
        lv, note = verdict(r)
        rows.append((r, lv, note))

    hdr = ["doc_id", "条数", "款数", "均款", "最长", "重号", "缺条", "但书",
           "无主语/义务", "判定"]
    body = []
    for r, lv, note in rows:
        body.append((r["doc_id"], r["n_art"], r["n_para"], r["avg_para"], r["max_len"],
                     len(r["dup"]), "-" if r["partial"] else len(r["gaps"]), r["proviso"],
                     "%d/%d" % (r["no_subj"], r["duty"]), lv))
    lines += [C.md_table(hdr, body), "", "## 逐文件判定", ""]
    for r, lv, note in rows:
        tag = "（节选）" if r["partial"] else ""
        exp = "，官方 %d 条" % r["expected"] if r["expected"] else ""
        lines.append("- **%s** %s%s：%s — %s" % (r["doc_id"], r["name"], tag, lv, note))
        lines.append("  条 %d%s，款 %d，章节标题覆盖 %d%%，脏数据 %d"
                     % (r["n_art"], exp, r["n_para"], r["heading_cov"], r["dirty"]))
    lines += ["", "## 判据", "",
              "- **FAIL**：重号 / 非节选文件缺条 / 条数与官方不符 —— 切分或原件有问题，不要往下抽取",
              "- **WARN**：最长款 >1500 字（疑未切）、脏数据、章节标题覆盖 <60%、无主语义务 >35%",
              "- 节选文件自动跳过缺条与条数对账（头部 `# partial: true`）；补完全文后必须删掉这行"]

    txt = "\n".join(lines)
    print(txt)
    os.makedirs(C.OUT_DIR, exist_ok=True)
    p = os.path.join(C.OUT_DIR, "audit_raw.md")
    open(p, "w", encoding="utf-8").write(txt + "\n")
    print("\n[写盘] %s" % os.path.relpath(p, C.PROJ_DIR))
    bad = [r for r, lv, _ in rows if lv == "FAIL"]
    print("[结论] %d 个文件，FAIL %d 个" % (len(rows), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
