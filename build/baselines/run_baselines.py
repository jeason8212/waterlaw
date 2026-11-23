# -*- coding: utf-8 -*-
"""跑齐 T1 baseline 并生成 Table 3 报告。

用法：
    python build/baselines/run_baselines.py                 # 跑所有可跑的（本地全跑，缺配置的记 n/a）
    python build/baselines/run_baselines.py --systems bm25_date,full
    python build/baselines/run_baselines.py --emb local:BAAI/bge-m3 --llm gpt-4.1
    python build/baselines/run_baselines.py --n 1000        # bootstrap 次数

输出：
    build/out/baseline_results.md   论文 Table 3 可直接引用的矩阵（含 CI、McNemar、失败清单）
    build/out/baseline_results.json 原始数字（供 Figure 与复算）

严谨性自检：B8（full）走的是正式检索器 s4_reason.find_law，指标必须与
build/tools/eval_queries.py 的输出逐位一致；脚本会显式比对并在报告开头标注 PASS/FAIL。
"""
import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
for _p in (HERE, BUILD):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common as C
import bs_core as B

OUT_MD = os.path.join(BUILD, "out", "baseline_results.md")
OUT_JS = os.path.join(BUILD, "out", "baseline_results.json")
# 正式报告的参照值（eval_queries.py 冻结基线），用于 harness 自检
REF = {("conduct", "b0_para_p5"): 0.783, ("conduct", "b1_para_p5"): 0.967,
       ("full", "b0_para_p5"): 0.783, ("full", "b1_para_p5"): 0.967}


def pct(x):
    return "%.1f%%" % (100 * x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default=",".join(B.SYSTEM_ORDER))
    ap.add_argument("--n", type=int, default=1000, help="bootstrap 重采样次数")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--emb", default=None, help="嵌入模型名，或 local:<hf path>")
    ap.add_argument("--llm", default=None, help="对话模型名")
    ap.add_argument("--skip-full", action="store_true", help="不跑 B7/B8（只跑对照基线时用）")
    a = ap.parse_args()

    names = [s.strip() for s in a.systems.split(",") if s.strip()]
    if a.skip_full:
        names = [n for n in names if n not in ("full", "ablation")]
    names = [n for n in B.SYSTEM_ORDER if n in names]

    con = sqlite3.connect(C.DB_PATH)
    con.row_factory = sqlite3.Row
    corpus = B.Corpus(con)
    rows = B.load_queries()
    cfg = {"emb": a.emb, "llm": a.llm}

    sys.stdout.write("语料：%d 款 / %d 部文件；查询 %d 个；B0 池 %d、B1 池 %d\n"
                     % (len(corpus.ids), len(corpus.name), len(rows),
                        len(corpus.pool(B.B0)), len(corpus.pool(B.B1))))

    results, skipped = {}, {}
    for name in names:
        t0 = time.time()
        call, why = B.build_system(name, corpus, con, cfg)
        if call is None:
            skipped[name] = why
            sys.stdout.write("  [n/a] %-14s %s\n" % (name, why))
            continue
        res = {}
        for mode in B.MODES:
            res[mode] = B.evaluate(call, corpus, rows, mode)
        results[name] = res
        r0 = res["conduct"]
        errs = sum(len(res[m].get("errors", [])) for m in B.MODES)
        tag = "ok " if not errs else "ERR"
        sys.stdout.write("  [%s] %-14s B0 %s  B1 %s  flipY %d/%d  (%.1fs)%s\n"
                         % (tag, name, pct(r0["b0_para_p5"]), pct(r0["b1_para_p5"]),
                            r0["flipY_article"][0], r0["flipY_article"][1], time.time() - t0,
                            ("  ⚠ %d 条查询调用异常（结果不可信，见 errors 字段）" % errs) if errs else ""))

    # ---------------- harness 自检
    check = "SKIP"
    if "full" in results:
        bad = []
        for (mode, key), want in REF.items():
            got = round(results["full"][mode][key], 3)
            if abs(got - want) > 0.001:
                bad.append("%s %s: %.3f vs %.3f" % (mode, key, got, want))
        check = "PASS" if not bad else "FAIL(" + "; ".join(bad) + ")"

    # ---------------- 报告
    L = ["# T1 baseline 矩阵（Table 3 数据源）", "",
         "> 生成：`python build/baselines/run_baselines.py`｜语料 %d 款 / %d 部；查询 %d 个；"
         "B0 池 %d 款、B1 池 %d 款。" % (len(corpus.ids), len(corpus.name), len(rows),
                                        len(corpus.pool(B.B0)), len(corpus.pool(B.B1))),
         "> harness 自检（B8 与 `build/tools/eval_queries.py` 冻结基线对齐）：**%s**" % check, "",
         "指标定义与 `build/tools/eval_queries.py` 完全一致（P@5 命中任一金标；款级＝必须命中该款；"
         "废止法引用率＝top-5 中出现该时点无效文件的法律的查询比例）。"
         "conduct 设定下查询串只有事由；full 设定额外把流域经 `basin_hint` 通道传入。"
         "**词法/向量/LLM 基线没有 basin_hint 通道，两设定结果必然相同**，故表中只列 conduct。",
         "末两列 **AC-P@5**（answer-class-aware P@5）把「金标为空时返回空」记对；"
         "检索基线永远返回 5 条，结构上拿不到这部分分。只有 `B8+AC`（带弃权层）会变化，"
         "详见 `build/out/answer_class.md`。**主表是 conduct 设定**（不带流域元信息），"
         "B8+AC 的 AC-P@5 因此只到 86.7%；full 设定到 95.0%，见下节两设定对照。", ""]

    hdr = ["| 系统 | B0 P@5（款级） | B1 P@5（款级） | B0 MRR | B1 MRR | flipY 条级 | flipN 条级 | "
           "废止引用率@B0 | B0 AC-P@5 | B1 AC-P@5 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    L += ["## 一、总体指标（conduct 设定）", ""] + hdr
    for name in names:
        if name not in results:
            L.append("| %s | *n/a — %s* |" % (B.SYSTEM_LABEL[name], skipped[name]))
            continue
        r = results[name]["conduct"]
        # 与 eval_ci.py 同一 RNG 流：每个 (系统, 设定) 重置一次，先 B0 后 B1
        rng = B.random.Random(a.seed)
        ci0 = B.bootstrap_ci(r["b0_para_hits"], a.n, rng=rng)
        ci1 = B.bootstrap_ci(r["b1_para_hits"], a.n, rng=rng)
        L.append("| %s | %s [%s, %s] | %s [%s, %s] | %.3f | %.3f | %d/%d | %d/%d | %s | %s | %s |"
                 % (B.SYSTEM_LABEL[name], pct(r["b0_para_p5"]), pct(ci0[0]), pct(ci0[1]),
                    pct(r["b1_para_p5"]), pct(ci1[0]), pct(ci1[1]),
                    r["b0_para_mrr"], r["b1_para_mrr"],
                    r["flipY_article"][0], r["flipY_article"][1],
                    r["flipN_article"][0], r["flipN_article"][1], pct(r["b0_rep"]),
                    pct(r["b0_para_acp5"]), pct(r["b1_para_acp5"])))

    if "full" in results:
        r = results["full"]["conduct"]
        L += ["", "### 本文方法的两设定对照（B7/B8 才有 basin_hint 通道）", "",
              "| 系统 | B0 P@5 conduct → full | B1 P@5 conduct → full | flipY 条级 conduct → full | "
              "B0 AC-P@5 conduct → full |",
              "|---|---|---|---|---|"]
        for name in ("ablation", "full", "full_ac", "full_syn", "full_syn_ac"):
            if name in results:
                c, f = results[name]["conduct"], results[name]["full"]
                L.append("| %s | %s → %s | %s → %s | %d/%d → %d/%d | %s → %s |"
                         % (B.SYSTEM_LABEL[name], pct(c["b0_para_p5"]), pct(f["b0_para_p5"]),
                            pct(c["b1_para_p5"]), pct(f["b1_para_p5"]),
                            c["flipY_article"][0], c["flipY_article"][1],
                            f["flipY_article"][0], f["flipY_article"][1],
                            pct(c["b0_para_acp5"]), pct(f["b0_para_acp5"])))
        L += ["", "**读法**：conduct → full 的增量就是流域元信息经 R-a/R-b 产生的贡献；"
              "B7 与 B8 的差就是三条可废止规则（优先关系边）的净贡献。"
              "**答案类别判定层也吃流域信息**：conduct 设定下它拿不到 basin_hint，"
              "空间效力过滤失效，B0 AC-P@5 只有 86.7%；full 设定下到 95.0%。"
              "这不是调参差异，而是「同一条查询在淮河与长江的答案本就不同」的直接后果"
              "（见 `build/out/answer_class.md` §三 消融第 1 行）。", ""]

    # ---------------- McNemar vs B8
    if "full" in results:
        base = results["full"]["conduct"]
        L += ["## 二、与本文方法（B8）的配对比较（conduct 设定，款级 P@5）", "",
              "McNemar 精确检验，b = 本文对/基线错，c = 本文错/基线对；Cohen's h 为效应量。", "",
              "| 对照系统 | B0 b/c | B0 p | B0 h | B1 b/c | B1 p | B1 h |", "|---|---|---|---|---|---|---|"]
        for name in names:
            if name not in results or name == "full":
                continue
            r = results[name]["conduct"]
            row = ["| %s " % B.SYSTEM_LABEL[name]]
            for tag in ("b0_para", "b1_para"):
                x = base[tag + "_hits"]
                y = r[tag + "_hits"]
                b01, c10, p = B.mcnemar_exact(x, y)
                h = B.cohens_h(sum(x) / len(x), sum(y) / len(y))
                row.append("| %d/%d | %s | %+.2f " % (b01, c10, "<0.001" if p < 0.001 else "%.3f" % p, h))
            L.append("".join(row) + "|")
        # ---------------- Holm–Bonferroni（预指定族）
        # 族定义（在看到任何结果之前由实验设计 §paper/03 预先指定）：
        # 主对照 = 四个带日期过滤的可比系统 B2/B2u/B3d/B6 × 两个 as-of 时点 = 8 个检验。
        # B4 zero-shot（不对等对照：无检索、无过滤）、B7 消融（b/c=0/0 退化）、
        # B8+S/B9（post-hoc，见报告纪律）不进族；进族与否不改变它们的呈现，只改变多重校正口径。
        FAM = ["bm25_date", "bm25uni_date", "dense_date", "llm_rag_date"]
        fam_tests = []
        for name in FAM:
            if name not in results:
                continue
            r = results[name]["conduct"]
            for tag, label in (("b0_para", "B0"), ("b1_para", "B1")):
                _, _, p = B.mcnemar_exact(base[tag + "_hits"], r[tag + "_hits"])
                fam_tests.append((p, B.SYSTEM_LABEL[name], label))
        if fam_tests:
            m = len(fam_tests)
            order = sorted(range(m), key=lambda i: fam_tests[i][0])
            adj = [0.0] * m
            prev = 0.0
            for rank, i in enumerate(order):
                q = (m - rank) * fam_tests[i][0]
                prev = max(prev, q)
                adj[i] = min(1.0, prev)
            L += ["", "**Holm–Bonferroni 多重比较校正（预指定族 = 主对照 B2/B2u/B3d/B6 × B0/B1，共 %d 个检验）**：" % m, "",
                  "| 对照 | 时点 | 原始 p | Holm 校正 p |", "|---|---|---|---|"]
            for (p, label, dtag), q in zip(fam_tests, adj):
                L.append("| %s | %s | %.3f | %.3f |" % (label, dtag, p, q))
            L += ["", "> 族内最小原始 p = %.3f，族内最大 Holm 校正 p = %.3f。"
                  "**正文显著性表述口径：与四个主对照的任何比较在名义水平与校正水平均不显著，"
                  "本文与最强基线为平局（parity），不作方向性主张。**"
                  % (min(t[0] for t in fam_tests), max(adj)), ""]
        L += ["", "> 样本 60 查询，McNemar 的最小可测 p 约为 0.002（b+c=10 时）。"
              "**任何 p 值超过 0.05 的比较都不得在正文写成「显著优于」**；"
              "报告时给出 b/c 原始计数，让读者自己判断。", ""]

    # ---------------- 失败差异
    ref_name = "bm25uni_date" if "bm25uni_date" in results else ("bm25_date" if "bm25_date" in results else None)
    if "full" in results and ref_name:
        L += ["## 三、最强词法基线与本文方法的差异查询（B1，条级 P@5 命中与否）", "",
              "| 查询 | %s | B8 本文 | 金标 | 差异 |" % B.SYSTEM_LABEL[ref_name].split(" ")[0] + " ",
              "|---|---|---|---|---|"]
        b2 = results[ref_name]["conduct"]
        b8 = results["full"]["conduct"]
        for r in rows:
            qid = r["query_id"]
            k2 = b2["per_q"][qid]["b1_article"]
            k8 = b8["per_q"][qid]["b1_article"]
            if k2 != k8:
                L.append("| %s | %s | %s | %s | %s |"
                         % (qid, "✓" if k2 else "✗", "✓" if k8 else "✗",
                            (r["b1_prov_ids"] or "(none)").replace("|", "/"),
                            "本文补回" if k8 else "仅基线命中"))
        L += ["", "> 「本文补回」的查询是论文 Discussion 的素材：要能说清是靠哪条规则补回的"
              "（R-a 流域降权 / R-b 特别法优先 / R-c 援引穿透），而不是靠参数调优。", ""]

    # ---------------- 未跑项
    if skipped:
        L += ["## 四、未跑项与确切原因", "", "| 系统 | 原因 |", "|---|---|"]
        for n, w in skipped.items():
            L.append("| %s | %s |" % (B.SYSTEM_LABEL[n], w))
        L += ["", "按 `paper/06_Baseline_Runbook.md` 配置环境变量后重跑本脚本即可补齐，"
              "无需改代码。", ""]

    # ---------------- LLM 基线运行口径披露（B4/B5/B6 存在时必须随行）
    if "llm_rag" in results or "llm_rag_date" in results or "llm_zero" in results:
        rag_note = cfg.get("rag_recall_note") or ""
        emb_line = rag_note if rag_note else "召回阶段为 Dense（嵌入端点已配置）"
        L += [
            "**B4/B5/B6 运行口径**：%s（模型 %s，温度 0，JSON 模式）。%s"
            % (emb_line,
               os.environ.get("WB_LLM_MODEL", "未配置"),
               ("在该口径下 LLM 重排惰性——B5 与 B1、B6 与 B2 在全部聚合指标上浮点级相同"
                "（20 次抽样对比 top-5 逐位一致）。引用 B5/B6 数字时必须连同本披露。"
                if rag_note else "")),
            "",
        ]

    with open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")

    slim = {n: {m: {k: v for k, v in res[m].items() if not k.endswith("_hits") and k != "per_q"}
                for m in res} for n, res in results.items()}
    payload = {"check": check, "skipped": skipped, "systems": slim,
               "pool": {"B0": len(corpus.pool(B.B0)), "B1": len(corpus.pool(B.B1))},
               "n_queries": len(rows)}
    with open(OUT_JS, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    sys.stdout.write("\n自检：%s\n报告：%s\n" % (check, OUT_MD))


if __name__ == "__main__":
    main()
