# -*- coding: utf-8 -*-
"""应用批次 1 的待写入补丁（corpus/09_查询集_批次1.csv.new -> .csv）。

背景：该 CSV 曾被编辑器独占打开，导致生成脚本无法原地覆盖。生成脚本已把修正版写到
`.new`，本脚本负责在文件释放后原子替换。

修正内容（2026-11-09）：
- Q11：B0 侧由“(none)”改为 L-004#21-2（水法第21条第2款，生态环境用水原则性规定）。
  原判“B0 无法可依”不准确，改为“从原则到可执行”的翻转类型。
- Q20：B0 侧由“(none)”改为 L-002#47-3（长江法第47条第3款），flip 由 Y 改为 N。
  该款在 B0 已生效且作同类限制，故属“特别法先设、法典后同构”的负对照。

用法: python tools/apply_batch1_patch.py
退出码：0 已应用或无需应用；1 目标仍被占用。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
TARGET = os.path.join(PROJ, "corpus", "09_查询集_批次1.csv")
PATCH = TARGET + ".new"


def main():
    if not os.path.exists(PATCH):
        print("[跳过] 没有待应用的补丁：%s 不存在" % os.path.basename(PATCH))
        return 0
    try:
        os.replace(PATCH, TARGET)
        print("[完成] 已应用补丁 -> %s" % TARGET)
        return 0
    except PermissionError:
        print("[失败] 目标文件仍被占用（通常是编辑器/Excel 打开着）：")
        print("       %s" % TARGET)
        print("       请关闭该文件后重跑本脚本。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
