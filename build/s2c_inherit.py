# -*- coding: utf-8 -*-
"""S2c 主语继承：修复定责矩阵（R2）的最大噪声源——空主语义务。

背景：v0 抽取的 3088 条职责类规范中 1037 条 subject_text 为空（s3 记录 DUTY_WITHOUT_AGENCY
893 条），另有约 20 条把"并向/报送+主管部门"这类动词短语误抽成主语。空主语使 IMPOSES_DUTY_ON
边缺失，定责矩阵行不全；误抽主体则生成错误的主体边。

本脚本五条规则，全部"宁可漏判不可错判"，且全程留痕（extractor 字段记录规则号，
confidence 按规则折扣）：

  FIX  误抽修复：主体以 并/将/按/向/对/为/在… 开头或含 报送/提交/汇交 → 判定为误抽，清空
        后交给 R0 重抽（例：L-001#414-2-N1 原"并按年度向生态环境主管部门"）。
  R0   句内补全：主体在句子里，只是 v0 的 SUBJECT_PATTERNS 没覆盖——
        R0-a 句首名词短语＋道义词（应当/必须/有权，允许逗号；不得/可以/严禁，不允许逗号），
             句首条件词（在/对/违反/根据/超过/前款…）或"的"结尾条件短语不触发；
        R0-b 罚则主体："由负有生态环境保护监督管理职责的部门、机构"（法典罚则章签名句式）；
        R0-c 句首机关＋负责/主管。
  R4   列表项继承：款干为"X 应当履行/承担下列义务：（一）…"时，列表项继承款干主语；
        款干为"有下列情形之一的，由…处罚"的**罚则列表项不继承**（项描述的是违法行为人，
        继承处罚机关会把被罚者与处罚者颠倒）。
  R2   同条近邻继承：续行句（"不采纳的，应当说明理由""情节严重的，不得…"）省略了主语，
        向前在同款/前款找最近的合法主语继承。
  R3   禁止默认主体：仍为空的禁止性规范 → "任何单位和个人"（立法通例：无对象禁止规范
        约束普遍主体），置信度最低。
  R1   前款回指句（24 条）：**不自动继承**。"前款规定的义务应当在排污许可证中记载"的句法
        主语是"前款规定的义务"（话题而非行为人），机械继承会传播错误，单列报告留给
        人工/LLM 标注裁决。

用法: python s2c_inherit.py   （在 s2_norm 之后、s3_graph 之前运行）
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import s2_norm

OUT = os.path.join(C.OUT_DIR, "inherit_report.md")

# ---- FIX：误抽判定 ----
BAD_START = re.compile(r"^(并|将|按|按照|向|对|为|把|从(?!事)|被|在|经|以|已|自|与|同时|报|提)")
BAD_CONTAINS = re.compile(r"(报送|提交|汇交|报请)")

# ---- R0：句内补全 ----
COND_START = (r"^(在|对|违反|根据|依照|按照|超过|拒不|逾期|情节|前款|本法|本条例|未|因|"
              r"向|从|由|其|有|不|尚|已|经|自|为|把|被|将|并|但|同时|有关|任何)")
R0A_COMMA = re.compile(r"^([^，。；：]{2,36})，?(应当|必须|有权)")
R0A_STRICT = re.compile(r"^([^，。；：]{2,36})(不得|可以|严禁)")
R0B = re.compile(r"(负有[^，。；]{2,24}?职责的(?:部门、机构|部门|机构|单位))")
R0C = re.compile(r"^([^，。；：]{2,30})，?(负责(?!人)|主管(?!部门))")
R0D = re.compile(r"[，。；]由([^，。；]{2,20}?)(按照|会同|商|报|提出|编制|制定|组织)")

# ---- 话题型主语过滤：句法主语 ≠ 义务承担者 ----
INDICATOR = re.compile(r"(单位|企业|机构|部门|政府|机关|个人|组织|者|人|户|主体|媒体|公民|社会|"
                       r"国家|军队|集体|公司|院|厂|场|站|所|省|自治区|直辖市|市|县|乡|镇|村|河长|林长)")
TOPIC_SUFFIX = re.compile(r"(纠纷|活动|作业|情况|总量|采伐|规划|方案|标准|选址|距离|范围|污水|废水|废气)$")


def is_topic(subj):
    """长而无任何机构/人员指示词的句法主语，或以典型话题后缀结尾的，视为话题而非义务主体。"""
    if not subj:
        return False
    if TOPIC_SUFFIX.search(subj):
        return True
    if len(subj) > 4 and not INDICATOR.search(subj):
        return True
    return False


# ---- R2：续行句标记 ----
CONT_MARK = re.compile(r"^(不采纳|不受理|拒不改正|拒不履行|拒不执行|拒不缴纳|情节严重|逾期|"
                       r"超过|仍未|仍不|再次|又|未按照|未依法|未及时|未采取|不符合|危害|造成)")

# ---- R4：列表项 ----
ITEM_RE = re.compile(r"^（[一二三四五六七八九十]+）")
STEM_DUTY = re.compile(r"(应当|必须)[^。；]{0,24}(履行|承担)[^。；]{0,10}下列")

# ---- 继承源合法性（不得作为继承源的误抽主体） ----
def sane_subject(s):
    if not s or len(s) > 30:
        return False
    if BAD_START.search(s) or BAD_CONTAINS.search(s):
        return False
    if re.search(COND_START, s):
        return False
    return True


def r0_subject(text):
    """R0：句内补全。返回 (subject, rule) 或 (None, None)。"""
    if ITEM_RE.match(text):
        return None, None          # 列表项不句内补全（交 R4 判断继承与否）
    m = R0B.search(text)
    if m and not is_topic(m.group(1)):
        return m.group(1), "R0-b"
    if m:
        return None, None
    m = R0D.search(text)
    if m and not is_topic(m.group(1)):
        return m.group(1), "R0-d"
    for pat in (R0A_COMMA, R0A_STRICT, R0C):
        m = pat.match(text)
        if m:
            subj = m.group(1)
            if subj.endswith("的") or subj.endswith("或者"):
                continue          # "的"结尾=条件短语；"或者"结尾=知道或者应当知道等习语误配
            if re.search(COND_START, subj):
                continue
            subj = re.sub(r"(也)+$", "", subj)
            subj = re.sub(r"(对依法|依法|对|自)$", "", subj)
            if len(subj) < 2 or is_topic(subj):
                continue
            tag = "R0-c" if pat is R0C else "R0-a"
            return subj, tag
    return None, None


def para_of(prov_id):
    """L-001#414-3 -> ('L-001', 414, 3)；无款号的返回 para=1。"""
    doc, rest = prov_id.split("#", 1)
    parts = rest.split("-")
    art = int(parts[0])
    para = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    return doc, art, para


def main():
    con = C.connect(fresh=False)
    norms = list(con.execute(
        "SELECT * FROM norm WHERE is_negative=0 AND counts_as_duty=1 ORDER BY doc_id, prov_id, sent_no"))
    empty0 = sum(1 for n in norms if not n["subject_text"])
    stats = {"FIX": 0, "R0": 0, "R4": 0, "R2": 0, "R3": 0}
    samples = {k: [] for k in ("FIX", "R0", "R4", "R2", "R3")}
    unresolved_items, unresolved_qiankuan, unresolved_rest = [], [], []

    def reload():
        return list(con.execute(
            "SELECT * FROM norm WHERE is_negative=0 AND counts_as_duty=1"
            " ORDER BY doc_id, prov_id, sent_no"))

    # ---- 步骤 1：FIX 误抽修复（清空，交 R0 重抽）----
    for n in norms:
        s = n["subject_text"]
        if s and (BAD_START.search(s) or BAD_CONTAINS.search(s)):
            act = s2_norm.find_action(n["text"], "", n["modality"])
            con.execute("UPDATE norm SET subject_text='', action_text=?, extractor='rule-v0+FIX' "
                        "WHERE norm_id=?", (act, n["norm_id"]))
            stats["FIX"] += 1
            if len(samples["FIX"]) < 10:
                samples["FIX"].append((n["norm_id"], s, act[:30], n["text"][:46]))
    con.commit()

    # ---- 步骤 2：R0 句内补全（重载后再跑，FIX 清空的句子此时可见）----
    norms = reload()
    for n in norms:
        if n["subject_text"]:
            continue
        subj, rule = r0_subject(n["text"])
        if subj:
            act = s2_norm.find_action(n["text"], subj, n["modality"])
            conf = min(n["confidence"], 0.65)
            con.execute("UPDATE norm SET subject_text=?, action_text=?, extractor='rule-v0+R0', "
                        "confidence=? WHERE norm_id=?", (subj, act, conf, n["norm_id"]))
            stats["R0"] += 1
            if len(samples["R0"]) < 12:
                samples["R0"].append((n["norm_id"], subj, n["text"][:46]))
    con.commit()
    norms = reload()

    # ---- 步骤 3：按条文组织，建立主语索引 ----
    arts = {}
    for n in norms:
        doc, art, para = para_of(n["prov_id"])
        arts.setdefault((doc, art), {}).setdefault(para, []).append(n)
    for key in arts:
        arts[key] = dict(sorted(arts[key].items()))

    def para_subject(doc, art, para):
        """该款第一个合法主语（用于继承）。"""
        for n in arts.get((doc, art), {}).get(para, []):
            if sane_subject(n["subject_text"]):
                return n["subject_text"]
        return None

    # ---- 步骤 4：R4 列表项继承 ----
    for n in norms:
        if n["subject_text"] or not ITEM_RE.match(n["text"]):
            continue
        doc, art, para = para_of(n["prov_id"])
        stem = arts.get((doc, art), {}).get(para, [None])[0]
        if not stem or not STEM_DUTY.search(stem["text"]):
            unresolved_items.append(n["norm_id"])
            continue
        subj = stem["subject_text"]
        if not sane_subject(subj):
            unresolved_items.append(n["norm_id"])
            continue
        conf = min(n["confidence"], 0.60)
        con.execute("UPDATE norm SET subject_text=?, extractor='rule-v0+R4', confidence=? "
                    "WHERE norm_id=?", (subj, conf, n["norm_id"]))
        n = dict(n, subject_text=subj)
        stats["R4"] += 1
        if len(samples["R4"]) < 8:
            samples["R4"].append((n["norm_id"], subj, n["text"][:46]))

    # ---- 步骤 5：R2 同条近邻续行句继承 ----
    for n in norms:
        if n["subject_text"] or not CONT_MARK.match(n["text"]):
            if not n["subject_text"] and "前款" in n["text"] and not ITEM_RE.match(n["text"]):
                unresolved_qiankuan.append((n["norm_id"], n["text"][:40]))
            continue
        doc, art, para = para_of(n["prov_id"])
        # 来源优先级：同款前句（最近优先）→ 前一款 → 前两款
        src = None
        same = [x for x in arts.get((doc, art), {}).get(para, []) if x["sent_no"] < n["sent_no"]]
        for x in reversed(same):
            if sane_subject(x["subject_text"]):
                src = x["subject_text"]
                break
        if not src:
            for p in (para - 1, para - 2):
                src = para_subject(doc, art, p)
                if src:
                    break
        if not src:
            if "前款" in n["text"]:
                unresolved_qiankuan.append((n["norm_id"], n["text"][:40]))
            else:
                unresolved_rest.append((n["norm_id"], n["text"][:40]))
            continue
        conf = min(n["confidence"], 0.55)
        con.execute("UPDATE norm SET subject_text=?, extractor='rule-v0+R2', confidence=? "
                    "WHERE norm_id=?", (src, conf, n["norm_id"]))
        n = dict(n, subject_text=src)
        stats["R2"] += 1
        if len(samples["R2"]) < 10:
            samples["R2"].append((n["norm_id"], src, n["text"][:46]))

    # ---- 步骤 6：R3 禁止默认主体（重载，避免覆盖 R2 已继承的主体）----
    norms = reload()
    for n in norms:
        if n["subject_text"] or n["deontic"] != "prohibition":
            continue
        conf = min(n["confidence"], 0.50)
        con.execute("UPDATE norm SET subject_text='任何单位和个人', extractor='rule-v0+R3', "
                    "confidence=? WHERE norm_id=?", (conf, n["norm_id"]))
        stats["R3"] += 1
        if len(samples["R3"]) < 8:
            samples["R3"].append((n["norm_id"], "任何单位和个人", n["text"][:46]))

    con.commit()

    # ---- 报告 ----
    norms2 = list(con.execute(
        "SELECT * FROM norm WHERE is_negative=0 AND counts_as_duty=1"))
    empty1 = sum(1 for n in norms2 if not n["subject_text"])
    still = [n for n in norms2 if not n["subject_text"]]

    L = ["# 主语继承报告（S2c）", "",
         "- 输入：职责类规范 %d 条，其中空主语 %d 条、误抽主体 %d 条" % (len(norms), empty0, stats["FIX"]),
         "- 输出：空主语降至 **%d 条**（−%.0f%%）；规则命中 %s" %
         (empty1, 100.0 * (empty0 - empty1) / max(empty0, 1),
          "、".join("%s %d" % kv for kv in stats.items())), "",
         "## 规则样本抽查", ""]
    for k, rows_ in samples.items():
        if not rows_:
            continue
        L.append("### %s" % k)
        if k == "FIX":
            L.append("| norm | 原误抽主体 | 重抽行为 | 原文 |")
            L.append("|---|---|---|---|")
            for r in rows_:
                L.append("| %s | %s | %s | %s |" % r)
        else:
            L.append("| norm | 继承/补全主体 | 原文 |")
            L.append("|---|---|---|")
            for r in rows_:
                L.append("| %s | %s | %s |" % r)
        L.append("")
    # 剩余空主语分型
    from collections import Counter as _C
    cat = _C()
    for n in still:
        t = n["text"]
        if ITEM_RE.match(t):
            cat["罚则/义务列表项"] += 1
        elif "前款" in t:
            cat["前款回指句"] += 1
        elif any(p.match(t) and is_topic(p.match(t).group(1)) for p in (R0A_COMMA, R0A_STRICT, R0C)):
            cat["话题型句法主语（过滤拒绝）"] += 1
        elif R0B.search(t) or R0D.search(t):
            cat["由X式但主体被话题过滤"] += 1
        else:
            cat["句式未覆盖（条件先行句等）"] += 1
    L += ["", "## 剩余空主语分型（%d 条）" % len(still), ""]
    for k, v in cat.most_common():
        L.append("- %s：%d" % (k, v))
    L.append("")
    L += ["## 未解决（%d 条）" % len(still), "",
          "- 罚则列表项（项描述违法行为人，不可继承处罚机关）：%d 条，如 %s" %
          (len(unresolved_items), "、".join(unresolved_items[:6])),
          "- 前款回指句（句法主语为话题，不自动继承，留人工裁决）：%d 条，如 %s" %
          (len(unresolved_qiankuan), "；".join("%s %s" % x for x in unresolved_qiankuan[:4])),
          "- 其他（续行句无合法前款主体等）：%d 条，如 %s" %
          (len(unresolved_rest), "；".join("%s %s" % x for x in unresolved_rest[:4])), "",
          "## 已知局限（论文须报告）", "",
          "1. 句法主语 ≠ 义务机关：R0 会把\"火葬场\"\"大气环境质量限期达标规划\"这类话题型句法主语"
          "填入主体槽，Agency 实体类型未区分机构/设施/规划——主体类型过滤是后续工作。",
          "2. R3 默认主体\"任何单位和个人\"是立法通例推断，非文本证据，confidence 上限 0.5。",
          "3. 前款回指句 24 条不自动继承，是本轮刻意保留的保守边界。", ""]
    io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("[S2c] FIX %d，R0 %d，R4 %d，R2 %d，R3 %d" %
          (stats["FIX"], stats["R0"], stats["R4"], stats["R2"], stats["R3"]))
    print("      空主语 %d → %d（−%.0f%%）；未解决：罚则项 %d，前款句 %d，其他 %d" %
          (empty0, empty1, 100.0 * (empty0 - empty1) / max(empty0, 1),
           len(unresolved_items), len(unresolved_qiankuan), len(unresolved_rest)))
    print("[写盘] %s" % OUT)
    con.close()


if __name__ == "__main__":
    main()
