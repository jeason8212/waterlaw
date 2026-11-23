# -*- coding: utf-8 -*-
"""生成 60 查询集批次 1（20 个）并落盘 corpus/09_查询集_批次1.csv

构造纪律（见 05_查询集构造说明.md）：
1. 每个查询是五元组 (watershed, region, regulated_conduct, duty_bearer, as_of)
2. 每个答案条文必须是库内真实条文，条号与原文经核验
3. B0 侧不得出现 2026-08-15 才生效的法典条文；B1 侧不得出现已废止的水污染防治法
4. 翻转必须能指出"旧法第 X 条 → 法典第 Y 条"的具体对应
"""
import csv
import io
import os

ROWS = [
    # ---------------- ① 跨省界断面水质责任（4） ----------------
    dict(query_id="Q01", category="跨省界断面水质责任", watershed="长江", region="湖北—湖南（洞庭湖入江断面）",
         regulated_conduct="省界断面水质监测", duty_bearer="生态环境主管部门",
         b0_prov_ids="L-017#25-1", b0_basis="水污染防治法第25条：环保部门会同水行政部门组织监测网络，无省界水体专条",
         b1_prov_ids="L-001#273-1", b1_basis="法典第273条：生态环境主管部门及其流域生态环境监督管理机构负责监测省界水体水质",
         flip="Y", flip_reason="法典新增省界水体监测专门条款（旧法第25条→法典第273条），监测主体明确化并引入流域机构",
         answer_type="single", verify_note="L-017#25-1、L-001#273-1 原文已核对"),
    dict(query_id="Q02", category="跨省界断面水质责任", watershed="长江", region="江苏某市",
         regulated_conduct="断面水质未达标，编制限期达标规划", duty_bearer="市级人民政府",
         b0_prov_ids="L-017#17-1", b0_basis="水污染防治法第17条：按水污染防治规划确定的水环境质量改善目标制定限期达标规划",
         b1_prov_ids="L-001#62-1", b1_basis="法典第62条：未达生态环境质量标准的重点区域、流域地方政府编制限期达标规划及实施方案",
         flip="Y", flip_reason="旧法第17条→法典第62条；触发条件由“规划目标”改为“质量标准”，义务主体表述改为“有关地方人民政府”",
         answer_type="single", verify_note="两条原文已核对；注意第62条覆盖流域整体，非仅水"),
    dict(query_id="Q03", category="跨省界断面水质责任", watershed="黄河", region="甘肃某市",
         regulated_conduct="水环境保护目标完成情况考核", duty_bearer="地方人民政府负责人",
         b0_prov_ids="L-017#6-1", b0_basis="水污染防治法第6条：水环境保护目标责任制和考核评价制度",
         b1_prov_ids="L-001#27-1", b1_basis="法典第27条：生态环境保护目标责任制和考核评价制度",
         flip="Y", flip_reason="旧法第6条→法典第27条；考核对象由水环境保护升格为生态环境保护整体",
         answer_type="single", verify_note="第27条为总则性条款，适用于水领域但表述更宽"),
    dict(query_id="Q04", category="跨省界断面水质责任", watershed="长江", region="安徽某县",
         regulated_conduct="对本行政区域水环境质量负责", duty_bearer="县级人民政府",
         b0_prov_ids="L-017#4-2", b0_basis="水污染防治法第4条：地方各级人民政府对本行政区域的水环境质量负责",
         b1_prov_ids="L-001#8-2", b1_basis="法典第8条：地方各级人民政府对本行政区域及其管理的海域的生态环境保护工作和生态环境质量负责",
         flip="Y", flip_reason="旧法第4条→法典第8条；责任客体由“水环境质量”扩为“生态环境质量”，并增列管辖海域",
         answer_type="single", verify_note="条文为款级记录（L-017#4-2 为第2款）"),

    # ---------------- ② 岸线利用项目审批（3） ----------------
    dict(query_id="Q05", category="岸线利用项目审批", watershed="长江", region="江苏沿江",
         regulated_conduct="岸线利用项目布局与岸线划定", duty_bearer="建设单位",
         b0_prov_ids="L-002#26-1", b0_basis="长江保护法第26条：河湖岸线特殊管制，由流域协调机制统筹协调划定",
         b1_prov_ids="L-002#26-1", b1_basis="同左",
         flip="N", flip_reason="长江保护法未被法典废止（LEX_SPECIALIS），两时点同一依据",
         answer_type="single", verify_note="特别法保留条款，作为不翻转对照样本"),
    dict(query_id="Q06", category="岸线利用项目审批", watershed="长江", region="湖北",
         regulated_conduct="违法占用河湖岸线建设码头", duty_bearer="建设单位",
         b0_prov_ids="L-002#87-1", b0_basis="长江保护法第87条：责令停止、限期拆除、恢复原状并处罚款",
         b1_prov_ids="L-002#87-1", b1_basis="同左",
         flip="N", flip_reason="罚则未因法典生效改变",
         answer_type="single", verify_note="罚则条款，用于检验“找罚则”能力"),
    dict(query_id="Q07", category="岸线利用项目审批", watershed="黄河", region="河南",
         regulated_conduct="滩区居民迁建与向滩区迁入控制", duty_bearer="黄河流域管理机构/省级人民政府",
         b0_prov_ids="L-003#66-1", b0_basis="黄河保护法第66条：制定滩区名录、有序迁建、严格控制迁入",
         b1_prov_ids="L-003#66-1", b1_basis="同左",
         flip="N", flip_reason="黄河保护法为特别法，未被废止",
         answer_type="single", verify_note="黄河独有制度，长江法无对应条款"),

    # ---------------- ③ 取水与生态流量（4） ----------------
    dict(query_id="Q08", category="取水与生态流量", watershed="黄河", region="宁夏",
         regulated_conduct="直接从黄河干流取水", duty_bearer="取水单位",
         b0_prov_ids="L-003#50-1;L-004#48-1", b0_basis="黄河法第50条：取用水资源应依法取得取水许可；水法第48条：向水行政部门或流域管理机构申领",
         b1_prov_ids="L-003#50-1;L-004#48-1", b1_basis="同左",
         flip="N", flip_reason="特别法与水法并存，许可制度未被法典改变",
         answer_type="multi", verify_note="两依据并存，考多依据归并"),
    dict(query_id="Q09", category="取水与生态流量", watershed="黄河", region="内蒙古",
         regulated_conduct="用水超过强制性用水定额且未实施节水改造", duty_bearer="用水单位",
         b0_prov_ids="L-003#52-1;L-003#114-1", b0_basis="黄河法第52条：强制性用水定额管理制度；第114条：逾期未节水改造的处罚",
         b1_prov_ids="L-003#52-1;L-003#114-1", b1_basis="同左",
         flip="N", flip_reason="黄河法独有制度，法典未涉及，两时点一致",
         answer_type="multi", verify_note="制度+罚则配对，检验“依据+后果”联合检索"),
    dict(query_id="Q10", category="取水与生态流量", watershed="长江", region="湖北（汉江）",
         regulated_conduct="确定控制断面生态流量管控指标", duty_bearer="水行政主管部门/流域管理机构",
         b0_prov_ids="L-004#21-1", b0_basis="水法第21条：仅规定兼顾生态环境用水，未设生态流量管控指标确定主体",
         b1_prov_ids="L-001#748-1;L-004#21-1;L-002#31-1", b1_basis="法典第748条：水行政部门、流域管理机构按管理权限确定生态流量与生态水位管控指标",
         flip="Y", flip_reason="从原则性规定到具体管控职责：法典第748条新增确定主体，水法第21条仍作原则依据",
         answer_type="multi", verify_note="B0 侧为“弱依据”，评测时应允许标注为“无直接依据”。**修正记录（2026-11-09）**：B1 金标补入 L-002#31-1——长江法第31条第1款逐字对应“长江干流…控制断面的生态流量管控指标”，长江流域查询下特别法更贴合；此为单标注者修正，待第二标注者仲裁"),
    dict(query_id="Q11", category="取水与生态流量", watershed="黄河", region="青海",
         regulated_conduct="上游水利枢纽生态用水调度", duty_bearer="工程管理单位",
         b0_prov_ids="L-004#21-2", b0_basis="水法第21条第2款：在干旱和半干旱地区开发、利用水资源，应充分考虑生态环境用水需要（原则性规定，无调度程序义务）",
         b1_prov_ids="L-001#748-3", b1_basis="法典第748条：重要江河和重要湖泊上游的水利水电、航运枢纽应将生态用水调度纳入日常运行调度规程，建立常规生态调度机制",
         flip="Y", flip_reason="从原则到可执行型翻转：水法仅有“充分考虑生态环境用水需要”的原则性要求，法典第748条第3款将其落为程序义务（纳入运行调度规程＋建立常规生态调度机制）",
         answer_type="single", verify_note="**修正记录（2026-11-09）**：初版误记为 B0 空答案。水法第21条第2款在 B0 已生效，故 B0 侧并非无法可依，而是仅有不可执行的原则性规定。本条是“规范粒度”论证的样本。B0“无更细依据”仍限于本语料库范围"),

    # ---------------- ④ 采砂（3） ----------------
    dict(query_id="Q12", category="采砂", watershed="长江", region="江西",
         regulated_conduct="河道采砂", duty_bearer="采砂单位",
         b0_prov_ids="L-004#39-1;L-002#28-1", b0_basis="水法第39条：河道采砂许可制度；长江法第28条：流域采砂规划与许可",
         b1_prov_ids="L-004#39-1;L-002#28-1", b1_basis="同左",
         flip="N", flip_reason="许可制度未被法典改变，特别法+水法并存",
         answer_type="multi", verify_note="一般法+特别法组合"),
    dict(query_id="Q13", category="采砂", watershed="长江", region="湖南",
         regulated_conduct="在禁采区、禁采期采砂", duty_bearer="采砂单位",
         b0_prov_ids="L-002#91-1", b0_basis="长江法第91条：责令停止、没收违法所得并处罚款",
         b1_prov_ids="L-002#91-1", b1_basis="同左",
         flip="N", flip_reason="特别法罚则继续适用",
         answer_type="single", verify_note="与 Q06 同型（罚则检索）"),
    dict(query_id="Q14", category="采砂", watershed="淮河", region="安徽",
         regulated_conduct="河道采砂影响河势稳定、危及堤防安全", duty_bearer="采砂单位",
         b0_prov_ids="L-004#39-1;L-004#39-2", b0_basis="水法第39条：许可制度；影响河势或堤防安全的划定禁采区、规定禁采期",
         b1_prov_ids="L-004#39-1;L-004#39-2", b1_basis="同左",
         flip="N", flip_reason="无流域特别法覆盖，水法直接适用，不受法典影响",
         answer_type="multi", verify_note="非长江黄河流域，检验“无特别法时回落一般法”"),

    # ---------------- ⑤ 生态补偿（3） ----------------
    dict(query_id="Q15", category="生态补偿", watershed="长江", region="浙江—安徽（新安江）",
         regulated_conduct="上下游签订横向生态保护补偿协议", duty_bearer="两省人民政府",
         b0_prov_ids="A-009#17-1;A-009#14-1", b0_basis="补偿条例第17条：协议必备事项；第14条：协商建立横向补偿机制",
         b1_prov_ids="A-009#17-1;A-009#14-1;L-001#114-1", b1_basis="同左，另加法典第114条：受益地区与保护地区协商建立横向补偿",
         flip="N", flip_reason="条例未被废止，主导依据不变；B1 依据叠加法典第114条（法律与行政法规并存，不构成替代）",
         answer_type="multi", verify_note="“依据叠加但不翻转”样本，考冲突消解能否区分替代与并存"),
    dict(query_id="Q16", category="生态补偿", watershed="长江", region="湖北（丹江口）",
         regulated_conduct="上游水源涵养地财政补偿", duty_bearer="中央/省级财政",
         b0_prov_ids="L-017#8-1", b0_basis="水污染防治法第8条：通过财政转移支付建立水环境生态保护补偿机制",
         b1_prov_ids="L-001#111-1;L-001#112-1", b1_basis="法典第111条：财政纵向+地区间横向+市场机制三类补偿；第112条：稳定资金投入机制",
         flip="Y", flip_reason="旧法第8条→法典第111条；补偿机制由单一财政转移支付扩为三类，资金机制另设第112条",
         answer_type="multi", verify_note="典型“旧条被替代”样本"),
    dict(query_id="Q17", category="生态补偿", watershed="长江", region="贵州",
         regulated_conduct="补偿资金落实与用途监管", duty_bearer="地方人民政府",
         b0_prov_ids="A-009#18-1", b0_basis="补偿条例第18条：严格履行协议，保护地区落实措施、受益地区履行补偿责任",
         b1_prov_ids="A-009#18-1;L-001#113-4", b1_basis="同左，另加法典第113条：落实补偿资金并确保用于生态保护补偿",
         flip="N", flip_reason="条例继续有效；B1 叠加法典资金义务，主导依据不变",
         answer_type="multi", verify_note="与 Q15 同型，用于检验“叠加≠替代”"),

    # ---------------- ⑥ 入河排污口（3） ----------------
    dict(query_id="Q18", category="入河排污口", watershed="长江", region="江苏",
         regulated_conduct="新建入河排污口，确定同意机关", duty_bearer="建设单位",
         b0_prov_ids="L-017#19-2", b0_basis="水污染防治法第19条：应取得水行政主管部门或流域管理机构同意",
         b1_prov_ids="L-001#275-1", b1_basis="法典第275条：报经有管辖权的生态环境主管部门或国务院生态环境主管部门流域生态环境监督管理机构同意",
         flip="Y", flip_reason="审批主体由水行政主管部门改为生态环境主管部门（旧法第19条→法典第275条）——本批次最锋利的翻转",
         answer_type="single", verify_note="主体变更型翻转，检索若只匹配“排污口”会同时命中旧条，考验版本过滤"),
    dict(query_id="Q19", category="入河排污口", watershed="黄河", region="山东",
         regulated_conduct="在饮用水水源保护区内设置排污口", duty_bearer="排污单位",
         b0_prov_ids="L-017#64-1", b0_basis="水污染防治法第64条：保护区内禁止设置排污口",
         b1_prov_ids="L-001#318-1", b1_basis="法典第318条：保护区内禁止设置排污口",
         flip="Y", flip_reason="规则内容延续，但依据条随法典生效迁移，旧法第64条失效",
         answer_type="single", verify_note="“同规则异条号”样本，B1 引用旧条即计错"),
    dict(query_id="Q20", category="入河排污口", watershed="长江", region="湖南",
         regulated_conduct="在水生态环境质量不达标的水功能区新设入河排污口", duty_bearer="建设单位",
         b0_prov_ids="L-002#47-3", b0_basis="长江法第47条第3款：对未达到水质目标的水功能区，除污水集中处理设施排污口外，应严格控制新设、改设或扩大排污口",
         b1_prov_ids="L-002#47-3;L-001#276-1", b1_basis="同上（长江法第47条第3款仍有效）；另法典第276条为全国普遍性同构规定",
         flip="N", flip_reason="主依据仍是长江法第47条第3款；法典第276条为普遍性同构规定，不改变流域层面答案",
         answer_type="single", verify_note="**修正记录（2026-11-09）**：初版误判为 B0 空答案并记翻转；长江法第47条第3款在 B0 已生效且作同类限制，故改判 N。本条为“特别法先设、法典后同构”的负对照"),
]

HEADER = ["query_id", "category", "watershed", "region", "regulated_conduct", "duty_bearer",
          "b0_prov_ids", "b0_basis", "b1_prov_ids", "b1_basis", "flip", "flip_reason",
          "answer_type", "verify_note"]


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "corpus", "09_查询集_批次1.csv")
    with io.open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        for r in ROWS:
            assert set(r) == set(HEADER), (r["query_id"], set(HEADER) - set(r))
            w.writerow(r)
    flips = sum(1 for r in ROWS if r["flip"] == "Y")
    print("[写盘] %s" % out)
    print("[统计] %d 查询，翻转 %d / 不翻转 %d" % (len(ROWS), flips, len(ROWS) - flips))
    from collections import Counter
    for c, n in Counter(r["category"] for r in ROWS).items():
        print("  %-16s %d" % (c, n))


if __name__ == "__main__":
    main()
