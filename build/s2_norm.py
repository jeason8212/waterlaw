# -*- coding: utf-8 -*-
"""S2 规范命题抽取（规则基线 v0）：条文 -> Norm，并给出对 corpus/07 金标的准确率。

设计要点：
1. 先判"元条款"（目的/适用范围/生效/冲突规则）——这些不产生行为义务，禁止建 Norm；
2. 再判道义模态，顺序为 禁止 > 明示义务 > 援引 > 定义 > 职权 > 隐含义务 > 倡导；
3. 无模态词但用"建立/实行/制定"等构成性动词的，判"隐含义务"，置信度下调
   （S-002 即此类：无"应当"但金标为义务）；
4. 输出与金标不一致的条目——这些分歧本身就是标注规范要裁决的问题。

用法: python s2_norm.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

NEG_RULES = [
    (r"制定本法", "立法目的"),
    (r"^在.{0,80}适用本法", "适用范围"),
    (r"自\d{4}年\d{1,2}月\d{1,2}日起施行", "生效日期"),
    (r"国际条约|国际协定", "冲突规则"),
]

MODAL_RULES = [
    ("prohibition", r"不得|禁止|严禁"),
    ("obligation", r"应当|必须|负有|有责任"),
    ("referral", r"依照.{0,20}的规定|按照.{0,20}规定执行|适用.{0,12}的规定"),
    ("definition", r"所称|是指"),
    ("power", r"可以|有权|负责|行使|属于|主管|由[^，。；]{2,20}?(?:协调|协商|裁决|决定)"),
]
IMPLICIT_OBLIGATION = r"(建立|健全|实行|制定|组织|实施|承担|履行|编制|划定|公布)"
ADVOCACY = r"鼓励|支持|引导|推进|加强|统筹|促进|倡导|推动"

SUBJECT_PATTERNS = [
    r"国务院[^，。；]{0,40}?等部门",
    r"国务院[^，。；]{0,14}?主管部门",
    r"县级以上地方人民政府",
    r"县级以上人民政府",
    r"各级河湖长",
    r"各级林长",
    r"省级人民政府",
    r"地方人民政府",
    r"[^，。；]{0,6}人民政府",
    r"国家",
    r"[^，。；]{0,10}?(?:主管部门|工作部门)",
    r"有关部门",
    r"单位和个人",
    r"排污单位",
    r"^[^，。；对将向]{2,40}?的(?:单位|企业|事业单位|组织|个人|经营者|生产者|使用者|排放者|建设单位|施工单位)",
    r"(?:企业事业单位|生产经营单位|引进单位|建设单位|施工单位|审批部门|个体工商户)",
    r"^[^，。；]{2,40}(?:建设项目|双方|等行业)",
]
INST_RE = r"(?:建立|健全|实行|推行)([^，。；]{2,18}?(?:机制|制度|体系))"
DIM_RE = r"统一(规划|标准|监测|保护措施|执法|调度|信息共享|监测网络)"


def split_sents(text):
    parts = re.split(r"(?<=[。；])", text)
    return [p.strip() for p in parts if p.strip()]


def classify(sent):
    for deo, pat in MODAL_RULES:
        m = re.search(pat, sent)
        if m:
            return deo, m.group(0)
    if re.search(IMPLICIT_OBLIGATION, sent):
        return "obligation", "(隐含)"
    if re.search(ADVOCACY, sent):
        return "advocacy", ""
    return "declarative", ""


def find_subject(sent):
    for p in SUBJECT_PATTERNS:
        m = re.search(p, sent)
        if m:
            return re.sub(r"^(由|对|向)", "", m.group(0)) or ""
    return ""


def find_action(sent, subject, modality):
    t = sent.replace(subject, "", 1) if subject else sent
    t = re.sub(r"^(应当|必须|可以|有权|依法)+", "", t)
    t = re.sub(r"^(根据|依照|按照)[^，。]{0,24}[，,]", "", t)
    t = re.split(r"[，。；]", t)[0]
    return t[:40]


def find_condition(sent):
    m = re.search(r"(根据[^，。]{2,20}|前款规定以外|在[^，。]{2,20}内|依法)", sent)
    return m.group(0) if m else ""


def main():
    con = C.connect(fresh=False)
    provs = list(con.execute("SELECT * FROM provision ORDER BY doc_id, article_no, para_no"))
    seq = 0
    for p in provs:
        neg = None
        for pat, reason in NEG_RULES:
            if re.search(pat, p["text"]):
                neg = reason
                break
        sents = split_sents(p["text"])
        for i, s in enumerate(sents, start=1):
            nid = "%s-N%d" % (p["prov_id"], i)
            if neg:
                deo, modal, subj, act = "n/a", "", "", ""
                is_neg, duty = 1, 0
            else:
                deo, modal = classify(s)
                subj = find_subject(s) if deo in ("obligation", "prohibition", "power") else ""
                act = find_action(s, subj, modal) if deo in ("obligation", "prohibition", "power") else ""
                is_neg = 0
                duty = 1 if deo in ("obligation", "prohibition", "power") else 0
            conf = {"obligation": 0.72, "prohibition": 0.80, "power": 0.68,
                    "advocacy": 0.70, "definition": 0.85, "referral": 0.85,
                    "declarative": 0.45, "n/a": 0.0}[deo]
            if modal == "(隐含)":
                conf -= 0.15
            con.execute("INSERT OR REPLACE INTO norm VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (nid, p["prov_id"], p["doc_id"], i, s, deo, modal, subj, act,
                         "", find_condition(s), is_neg, duty, "rule-v0", round(conf, 2)))
            seq += 1
    con.commit()

    # ---- 与金标对比 ----
    gold = C.read_csv("07_条文级标注样例.csv")
    hit, tot, conf = 0, 0, []
    for g in gold:
        art, para = g["article_no"].split("-")
        pid = "%s#%s-%s" % (g["doc_id"], art, para)
        row = con.execute("SELECT deontic FROM norm WHERE prov_id=? ORDER BY sent_no LIMIT 1", (pid,)).fetchone()
        if not row:
            continue
        tot += 1
        pred = row["deontic"]
        exp = g["deontic_type"]
        if exp == "n/a":
            exp = "n/a"
        ok = (pred == exp)
        hit += ok
        if not ok:
            conf.append((g["sample_id"], g["provision_type"], exp, pred, g["article_text"][:26]))
    print("[S2] 生成规范命题 %d 条；金标可比对 %d 条，模态一致 %d 条（%.1f%%）"
          % (seq, tot, hit, 100.0 * hit / max(tot, 1)))
    if conf:
        print("\n     不一致条目（需在标注规范中裁决，不应当作模型错误直接调参）：")
        for c in conf:
            print("       %s 金标=%-12s 规则=%-12s | %s" % (c[0], c[1] + "/" + c[2], c[3], c[4]))
    for r in con.execute("SELECT deontic,COUNT(*) c,SUM(counts_as_duty) d FROM norm"
                         " GROUP BY deontic ORDER BY c DESC"):
        print("     %-12s %d 条，其中计入职责 %d" % (r["deontic"], r["c"], r["d"] or 0))
    con.close()


if __name__ == "__main__":
    main()
