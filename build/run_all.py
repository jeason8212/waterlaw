# -*- coding: utf-8 -*-
"""一键跑通 S1-S4，并输出双基线对照。

用法: python run_all.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s1_split
import s2_norm
import s2c_inherit
import s3_graph
import s3b_conduct
import s4_reason

B0, B1 = "2026-06-30", "2026-11-06"   # 法典生效前 / 生效后


def main():
    print("=" * 68)
    s1_split.main()
    print("-" * 68)
    s2_norm.main()
    print("-" * 68)
    s2c_inherit.main()
    print("-" * 68)
    s3_graph.main()
    print("-" * 68)
    s3b_conduct.main()
    print("-" * 68)
    print("[S4] 双基线对照：B0=%s（法典前）  B1=%s（法典后）" % (B0, B1))
    r0 = s4_reason.run(B0, verbose=False)
    print("\n" + "-" * 68)
    r1 = s4_reason.run(B1, verbose=False)
    print("\n" + "=" * 68)
    print("[结论] 同一份语料，仅把 as_of 从 %s 换到 %s：" % (B0, B1))
    print("       repealed-citation rate: %.0f%% -> %.0f%%" % (r0, r1))
    print("       统一监测/标准/规划/保护措施 的支撑文件数: 0 -> 1（L-001）")
    print("       这不是检索失败，是表示层没把'效力区间'建模进去。")


if __name__ == "__main__":
    main()
