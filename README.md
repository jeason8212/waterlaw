# waterlaw

Code and data release for the manuscript *A Water Law Knowledge Graph for
As-Of Legal Retrieval: Water Resources Regulation Across China's 2026
Environmental Codification*.

## Contents

A pipeline that builds a norm-proposition knowledge graph over six Chinese
water-law instruments, written against the Python 3.10 standard library only.
The corpus covers the Ecological Environment Code (in force 2026-08-15) and
the repealed Water Pollution Prevention and Control Law (valid through
2026-08-14). The release also ships a doubly annotated 60-query gold standard
with the full adjudication record, and the evaluation harness used in the
paper.

## Layout

The directory structure follows the development tree, so the scripts run as
shipped.

```
build/                pipeline and baselines (standard library only)
  data/raw/           six statutory texts, official sources (see corpus/08)
  out/                evaluation outputs shipped with the paper
    kg.db             frozen evaluation database (all Table 3 numbers)
    repro_baseline.json          deterministic-rebuild reference
    emb_cache/chat_*.json        LLM response cache (replays B4-B6, no API key)
corpus/               query sets, both annotators' raw judgements, adjudication record
  pre-adjudication/   annotator A's raw judgements (pre-adjudication gold)
```

## Reproduce

```bash
# 0. Verify the shipped database against a deterministic rebuild (optional, recommended).
#    Deletes build/out/kg.db, re-runs S1 to S4 from the raw texts, compares seven tables and the provision md5.
python build/tools/rebuild_and_verify.py

# 1. Rebuild the graph from the raw texts
python build/run_all.py

# 2. Full baseline matrix B0 to B9 (B4/B5/B6 replay from the shipped cache)
python build/baselines/run_baselines.py

# 3. Ablation tables
python build/baselines/analyze_ablation.py
```

The core pipeline and the lexical (BM25) baselines need no third-party
package. The LLM baselines replay from `build/out/emb_cache/chat_*.json`, so
no API key is required. To regenerate them instead, set `WB_LLM_BASE`,
`WB_LLM_KEY` and `WB_LLM_MODEL` to an OpenAI-compatible endpoint; the paper
used `deepseek-chat`. Embedding requests honour `WB_EMB_BASE`, `WB_EMB_KEY`
and `WB_EMB_MODEL`. The dense baselines (B3, B3d) also require
`pip install sentence-transformers` and a local copy of BAAI/bge-m3, for
example from ModelScope (https://modelscope.cn/models/BAAI/bge-m3), since the
HF hub is often unreachable behind some proxies; run
`python build/baselines/run_baselines.py --emb local:<path-to-bge-m3>`. The
embedding cache (63 MB) is not shipped, and the vectors recompute locally in
minutes.

## Acceptance numbers (paper Table 3)

| Metric (ours, full input) | B0 (2026-06-30) | B1 (2026-11-06) |
|---|---|---|
| P@5 (conduct / full) | 78.3% / 78.3% | 96.7% / 96.7% |
| MRR (conduct / full) | 0.689 / 0.700 | 0.849 / 0.869 |
| Repealed-instrument citations | 0 | 0 |

Two further figures belong to the same table: answer-class P@5 (AC) reaches
95.0% at B0, and zero-shot LLM citation (B4) resolves 0 of 120 instances at
either date.

The gold standard contains 28 flip queries. Of these, 19/28 flip = Y cases are
correct at condition level, and 21/28 with the post-hoc synonym extension
described below. A re-run should reproduce these values to the last digit.
Since the pipeline is deterministic, a deviation indicates a defect in the
run.

## Frozen database and post-hoc extensions

`build/out/kg.db` is the frozen evaluation database on which the paper's
Table 3 numbers were computed, including the answer-class layer and its 3,211
conduct nodes. The pipeline is deterministic:
`python build/tools/rebuild_and_verify.py` deletes the database, re-runs S1 to
S4 from `build/data/raw`, and checks that the seven table counts, the edge and
modality distributions, and the provision-text MD5 match
`build/out/repro_baseline.json`.

Two post-hoc extensions are off by default and appear separately in the paper
(marked with a dagger): the cross-generation synonym dictionary (`corpus/18`,
wired through `build/synmap.py`) and the R-d penalty rule in
`build/s4_reason.py`. Neither changes the frozen numbers.

## Annotation record

- `corpus/09`, `corpus/10`, `corpus/11` (`_查询集_批次*.csv`): the frozen gold
  standard, 60 queries after adjudication.
- `corpus/pre-adjudication/`: annotator A's judgements before adjudication.
  They sit in a subdirectory so that the evaluation glob
  `corpus/*查询集*.csv` does not pick them up.
- `corpus/15_预标注表单_待审核.csv`: annotator B's judgements.
- `corpus/17_仲裁底稿.md`: the adjudication record, 16 queries with 25
  paragraph-level disagreements, each resolved against the statutory text.
- `corpus/13_影子标注_算法B.csv`: a shadow algorithmic annotation kept for
  transparency. It is not one of the two human annotators.

The two annotators appear as A and B throughout. No identifying information
is included.

## Licensing

- Code: MIT (`LICENSE`).
- Annotations, query sets and derived data: CC BY 4.0 (`DATA-LICENSE.md`).
- Statutory texts (`build/data/raw/*.txt`): official texts of the People's
  Republic of China, obtained from the National Laws and Regulations Database
  (flk.npc.gov.cn) and ministry portals. `corpus/08_采集来源与入口.csv`
  records the source, version and retrieval date of each instrument. They are
  reproduced for research purposes with that attribution.
