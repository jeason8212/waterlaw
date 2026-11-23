# -*- coding: utf-8 -*-
"""条文检索小工具（不需要 SQL 知识，不需要安装任何东西）。

标注者 B 用法：

    python tools/lookup.py 排污口 饮用水          # 同时含这两个词的条文（AND）
    python tools/lookup.py 排污口 饮用水 --any    # 含任一关键词（OR）
    python tools/lookup.py 采砂 --doc L-003       # 只在黄河法里找
    python tools/lookup.py --id L-001#318         # 看某条条文全文
    python tools/lookup.py --list                 # 看 6 部法律及时点有效性
    python tools/lookup.py                        # 不带参数 = 交互模式，逐行输关键词

输出里每条都会标 (B0:有效|失效  B1:有效|失效)，
B0 = 2026-06-30（法典施行前），B1 = 2026-11-06（法典施行后）。
"""
import argparse
import os
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")   # 保持控制台原编码，防中文崩溃
if sys.stdout is None:
    # .py 文件被关联到 pythonw（无控制台）时会静默无输出，给出明确指引
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        None,
        "请用 python 前缀在命令行运行本工具，例如：\n\n"
        "    python lookup.py 排污口 饮用水\n\n"
        "（当前 .py 文件关联到了无窗口的 pythonw，看不到输出）",
        "lookup.py 运行方式提示", 0)
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
DB = os.path.join(BUILD, "out", "kg.db")

B0, B1 = "2026-06-30", "2026-11-06"
W = 96


def connect():
    if not os.path.exists(DB):
        sys.exit("找不到条文库：%s\n请先在 build 目录运行 run_all.py 构建。" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def doc_meta(con):
    meta = {}
    for r in con.execute("SELECT doc_id,name,valid_from,valid_to,status FROM doc"):
        d = dict(r)
        d["short"] = (d.get("name") or d["doc_id"]).replace("中华人民共和国", "")
        meta[d["doc_id"]] = d
    return meta


def ok_at(m, date):
    if not m:
        return False
    vf, vt = m.get("valid_from") or "", m.get("valid_to") or ""
    if vf and date < vf:
        return False
    if vt and date > vt:
        return False
    return True


def flag(m):
    return "%s%s" % ("B0:有效 " if ok_at(m, B0) else "B0:失效 ",
                     "| B1:有效" if ok_at(m, B1) else "| B1:失效")


def label(pid, meta):
    doc = pid.split("#")[0]
    m = meta.get(doc, {})
    return "%s %s" % ((m.get("short") or doc)[:14], pid)


def text_of(pid, meta=None):
    """可读条款号：L-001#318-1 → 生态环境法典 第318条第1款"""
    if "#" not in pid:
        return pid
    doc, rest = pid.split("#", 1)
    art, _, para = rest.partition("-")
    nm = ((meta or {}).get(doc, {}).get("short") or doc)
    return "%s 第%s条%s" % (nm, art, ("第%s款" % para) if para else "")


def wrap(s, width=W):
    out, line = [], ""
    for ch in s:
        line += ch
        if len(line) >= width:
            out.append(line)
            line = ""
    if line:
        out.append(line)
    return out


def do_search(con, meta, kws, docs=None, any_mode=False, limit=60):
    where, args = [], []
    likes = []
    for k in kws:
        likes.append("p.text LIKE ?")
        args.append("%" + k + "%")
    where.append("(" + (" OR " if any_mode else " AND ").join(likes) + ")")
    if docs:
        where.append("p.doc_id IN (%s)" % ",".join("?" * len(docs)))
        args += docs
    sql = ("SELECT p.prov_id, p.doc_id, p.article_no, p.para_no, p.text "
           "FROM provision p WHERE %s ORDER BY p.doc_id, p.article_no, p.para_no"
           % " AND ".join(where))
    rows = list(con.execute(sql, args))
    if not rows:
        print("\n[无结果] 关键词 %s 未命中任何条文。" % " ".join(kws))
        print("提示：换更短/更通用的词；或去掉一个关键词用 --any；或确认是否用了法典新用语（如“自然保护地水体”而非“风景名胜区水体”）。")
        return
    print("\n命中 %d 条%s：\n" % (len(rows), "" if len(rows) <= limit else "（只显示前 %d 条）" % limit))
    for r in rows[:limit]:
        m = meta.get(r["doc_id"], {})
        print("─" * W)
        print("[%s]  %s   (%s)" % (r["prov_id"], text_of(r["prov_id"], meta), flag(m)))
        t = (r["text"] or "").replace("\n", " ")
        for line in wrap(t):
            print("  " + line)
    if len(rows) > limit:
        print("\n… 还有 %d 条未显示，请缩小关键词范围。" % (len(rows) - limit))
    print("─" * W)
    print("说明：B0=2026-06-30（法典前）  B1=2026-11-06（法典后）。标注时 B1 侧不得引用标记“B1:失效”的条文。")


def do_id(con, meta, pid):
    rows = list(con.execute(
        "SELECT prov_id, doc_id, text FROM provision WHERE prov_id LIKE ? "
        "ORDER BY prov_id", (pid.rstrip("-") + "%",)))
    if not rows:
        print("[无结果] 没有编号为 %s 的条文。" % pid)
        return
    for r in rows:
        m = meta.get(r["doc_id"], {})
        print("─" * W)
        print("[%s]  %s   (%s)" % (r["prov_id"], text_of(r["prov_id"], meta), flag(m)))
        for line in wrap((r["text"] or "").replace("\n", " ")):
            print("  " + line)
    print("─" * W)


def do_list(con, meta):
    rows = []
    for doc, m in sorted(meta.items()):
        n = con.execute("SELECT COUNT(*) FROM provision WHERE doc_id=?", (doc,)).fetchone()[0]
        if n:
            rows.append((doc, m, n))
    print("\n库里有全文的共 %d 部（另有 %d 部仅在清单登记、未入库全文，检索不到属正常）：\n"
          % (len(rows), len(meta) - len(rows)))
    print("  %-7s %-22s %-11s %-11s %s" % ("编号", "名称", "B0(06-30)", "B1(11-06)", "条文数"))
    print("  " + "-" * 74)
    for doc, m, n in rows:
        print("  %-7s %-22s %-11s %-11s %d" % (
            doc, (m.get("short") or "")[:20],
            "有效" if ok_at(m, B0) else "失效",
            "有效" if ok_at(m, B1) else "失效", n))
    print("\n要点：①水污染防治法（L-017）在 B1 已废止——B1 侧答案不得引用；"
          "\n      ②生态环境法典（L-001）在 B0（2026-06-30）尚未施行——B0 侧答案不得引用。")


def interactive():
    con = connect()
    meta = doc_meta(con)
    print("=" * W)
    print("条文检索 · 交互模式")
    print("输入关键词（空格分隔 = 同时满足），回车检索；输入 :id L-001#318 看单条；:q 退出")
    print("=" * W)
    while True:
        try:
            line = input("\n关键词> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":q", ":quit", "q", "quit", "exit"):
            break
        if line.startswith(":id"):
            do_id(con, meta, line[3:].strip())
            continue
        if line in (":list", ":l"):
            do_list(con, meta)
            continue
        do_search(con, meta, line.split())


def main():
    ap = argparse.ArgumentParser(description="条文检索（无需 SQL）")
    ap.add_argument("keywords", nargs="*", help="关键词，空格分隔")
    ap.add_argument("--any", action="store_true", help="任一关键词命中即可（默认需全部命中）")
    ap.add_argument("--doc", nargs="+", help="限定文件，如 --doc L-003 或 --doc L-001 L-004")
    ap.add_argument("--id", help="查看指定条文，如 --id L-001#318")
    ap.add_argument("--list", action="store_true", help="列出 6 部文件及时点有效性")
    args = ap.parse_args()

    con = connect()
    meta = doc_meta(con)
    if args.list:
        do_list(con, meta)
    elif args.id:
        do_id(con, meta, args.id)
    elif args.keywords:
        do_search(con, meta, args.keywords, args.doc, args.any)
    else:
        interactive()
    return 0


if __name__ == "__main__":
    sys.exit(main())
