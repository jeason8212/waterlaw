# -*- coding: utf-8 -*-
"""把官方 .docx 逐字搬成 data/raw/<doc_id>.txt（不做任何改写）。

为什么不让你手工复制粘贴：
  1. 1000+ 条的法律，手工搬运必然丢条、串行；
  2. 让模型"重述"条文是学术事故——语料一旦被改写，后面所有推理结论都失效；
  3. 脚本搬运可复算：同一份 docx，谁跑都是同一份 txt。

用法:
  python tools/docx2raw.py 法典.docx --doc-id L-001 --name 中华人民共和国生态环境法典 \
      --hierarchy 法律 --valid-from 2026-08-15 --expected 1242
  python tools/docx2raw.py 法典.docx --doc-id L-001 --dry-run    # 只体检，不写盘
"""
import argparse
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common as C

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
CN = "一二三四五六七八九十百千零〇"
ART_ANY = re.compile(r"第([%s]+)条" % CN)
HEAD = re.compile(r"^第[%s]+[编章节]" % CN)
NOISE = re.compile(r"^(?:[—\-－\s]*\d+[—\-－\s]*|全国人民代表大会.*公报.*|.*flk\.npc\.gov\.cn.*)$")


def read_docx(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith("document.xml")]
        if not names:
            raise SystemExit("不是有效的 docx：缺 word/document.xml")
        xml = z.read(names[0])
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(W + "p"):
        buf = []
        for node in p.iter():
            if node.tag == W + "t" and node.text:
                buf.append(node.text)
            elif node.tag == W + "tab":
                buf.append(" ")
            elif node.tag == W + "br":
                buf.append("\n")
        text = "".join(buf).replace("\u3000", " ").replace("\xa0", " ").strip()
        if text:
            paras.append(text)
    return paras


def normalize(paras):
    """按'下一条应有的条号'切分，既处理逐条成段，也处理多条挤在一段。"""
    out, expected, split_inline, dropped = [], 1, 0, 0
    for text in paras:
        if NOISE.match(text):
            dropped += 1
            continue
        if HEAD.match(text):
            out.append(text)
            continue
        buf, pos = [], 0
        for m in ART_ANY.finditer(text):
            try:
                no = C.cn2int(m.group(1))
            except ValueError:
                continue
            if no == expected:
                if text[pos:m.start()].strip():
                    buf.append(text[pos:m.start()].strip())
                if buf:
                    out.extend(buf)
                    buf = []
                pos = m.start()
                expected += 1
                split_inline += 1
        tail = text[pos:].strip()
        if tail:
            out.append(tail)
        elif pos == 0:
            out.append(text)
    return out, expected - 1, split_inline, dropped


def to_raw(doc_id, name, hierarchy, valid_from, valid_to, expected, body, status="现行有效"):
    hdr = ["# doc_id: %s" % doc_id,
           "# name: %s" % name,
           "# doc_type: %s" % hierarchy,
           "# hierarchy: %s" % hierarchy,
           "# issuer: ",
           "# expected_articles: %s" % (expected or ""),
           "# partial: false",
           "# valid_from: %s" % (valid_from or ""),
           "# valid_to: %s" % (valid_to or ""),
           "# status: %s" % (status or "现行有效"),
           "# 由 tools/docx2raw.py 从官方 docx 逐字转换，未人工改写"]
    return "\n".join(hdr) + "\n\n" + "\n".join(body) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--doc-id", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--hierarchy", default="法律")
    ap.add_argument("--valid-from", default="")
    ap.add_argument("--valid-to", default="")
    ap.add_argument("--status", default="现行有效")
    ap.add_argument("--expected", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    paras = read_docx(a.docx)
    body, max_no, splits, dropped = normalize(paras)
    n_art = sum(1 for b in body if ART_ANY.match(b))

    print("[docx] 段落 %d → 正文行 %d，识别条文 %d 条（其中 %d 条由段内切出），丢弃噪声 %d 行"
          % (len(paras), len(body), n_art, splits, dropped))
    if a.expected:
        gap = a.expected - max_no
        flag = "OK" if gap == 0 else "**缺 %d 条**" % gap if gap > 0 else "**多 %d 条**" % (-gap)
        print("[对账] 最大条号 %d，官方公布 %d 条 -> %s" % (max_no, a.expected, flag))
    if any(len(b) > 2000 for b in body):
        print("[警告] 存在 >2000 字的长段，可能有多条未切开，务必人工抽查")

    if a.dry_run:
        print("\n--- 前 12 行预览 ---")
        for b in body[:12]:
            print(b[:100])
        return

    out = a.out or os.path.join(C.RAW_DIR, "%s.txt" % a.doc_id)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(
        to_raw(a.doc_id, a.name or a.doc_id, a.hierarchy,
               a.valid_from, a.valid_to, a.expected, body, a.status))
    print("[写盘] %s（%d 行）" % (out, len(body)))
    print("下一步: python s1b_audit.py %s" % a.doc_id)


if __name__ == "__main__":
    main()
