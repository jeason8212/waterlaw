# build：可执行链路（标准库，零依赖）

```bash
python run_all.py                       # S1->S4 全流程 + 双基线对照
python s4_reason.py --as-of 2026-06-30  # 单时点推理（法典生效前）
```

| 文件 | 作用 | 输出 |
|---|---|---|
| `common.py` | 路径、中文数字、SQLite schema（7 表） | 无 |
| `s1_split.py` | 条文切分：原文 → 条/款记录；用 `corpus/01` 播种文件注册表 | `out/kg.db` 的 `doc`/`provision` |
| `tools/docx2raw.py` | 官方 docx → raw txt 逐字转换（按条号序列切分、丢页码噪声、写文件头） | `data/raw/<doc_id>.txt` |
| `s1b_audit.py` | 语料对账：条号连续性、条数 vs 官方、款层级、脏数据、无主语率 | `out/audit_raw.md`，FAIL 时退出码 1 |
| `tools/rebuild_and_verify.py` | 可复现性校验：删库 → 从 `data/raw` 全量重建 → 与基线逐项比对（表行数/边类型/模态/条文 md5）；`--baseline` 记录基线 | `out/repro_baseline.json`，漂移时退出码 1 |
| `s2_norm.py` | 规范命题抽取（规则 v0）+ 对金标的模态一致性 | `norm` 表 + 一致性报告 |
| `s3_graph.py` | 实体/边落库 + Neo4j Cypher 导出 + 一致性检查 | `edge`/`entity`/`issue`、`out/graph.cypher` |
| `s3b_conduct.py` | 规范行为物化：把每条可诉规范句抽成 `conduct` 节点（枚举清单项继承引导句模态），带 `valid_from/valid_to`；幂等重建 | `conduct` 表、`out/conduct.cypher` |
| `conduct_index.py` | 行为级索引：bigram+IDF 覆盖 × 时间效力 × 空间效力 × 前身检验，判定「该时点该事由有没有依据」 | 供 `answer_class`/评测调用 |
| `answer_class.py` | 弃权层（v1 条文级 + v2 行为级入口 `apply_abstention`） | 无 |
| `synmap.py` | 跨代概念对照词典（post-hoc）：`corpus/18` 的 5 条旧法↔法典术语对照，条文侧 term-anchored bigram 扩充；`find_law(syn=True)` 启用，df/IDF 不变 | `tools/eval_synonym.py` → `out/synonym_report.md` |
| `s4_reason.py` | R1 找法 / R2 定责 / R3 识缺 / R4 援引审计；检索为 bigram+IDF 覆盖率 × 位阶权重 × 三条可废止规则（R-a 流域降权 / R-b 特别法优先·含邻近性条件 / R-c 援引穿透；流域元信息经 `basin_hint` 参数独立传入，不进文本）；`rules` 可含 `d` 即 R-d 罚则降权（post-hoc，规范型查询门控，默认关） | `out/report_<as_of>.md` |
| `tools/make_query_batch1.py` / `batch2.py` | 生成 T1 查询集（Q01–Q20 / Q21–Q40），答案条文内嵌且逐条核验 | `corpus/09_*.csv` / `corpus/10_*.csv` |
| `tools/validate_queries.py` | 查询集校验：条文存在性、B0/B1 时点有效性、flip 与答案变化一致性、B0 空答案须声明语料边界、query_id 全局唯一 | `out/query_audit.md`，退出码 1 |
| `tools/eval_queries.py` | 检索基线评测：conduct/full 双输入设定 × 款级/条级双粒度；P@5、MRR、废止法引用率、flip accuracy、条件 flip、负对照漂移 | `out/query_eval.md` |
| `tools/apply_batch1_patch.py` | 应用批次 1 的待写入补丁（文件被编辑器占用时的兜底） | 无 |
| `data/raw/*.txt` | 条文原文，文件头为 `# key: value` | 无 |

检索打分的一个已知修正（2026-11-09）：原 `find_law` 用 `re.split(r"[\s，,。、]+")` 切查询串，中文无空格使整串成为一个「词」，`text.count(整串)` 近乎全零，排序实际由术语表别名与条文长度主导。现改为字符二元组（bigram）+ IDF 加权覆盖率：无需分词器，分母与条文长度无关，顺带消除了「长条文占优」。同一批 40 个查询，款级 P@5 由 17.5%/35.0% 修正为 70.0%/95.0%。

## 加一部新法

1. 从 flk.npc.gov.cn 下载官方 Word 版（不是 PDF、不是网页复制），存到任意路径；
2. 一条命令转换（自动写文件头、按条号序列切分、丢页码/公报页眉噪声）：
   ```bash
   python tools/docx2raw.py 法典.docx --doc-id L-001 --name 中华人民共和国生态环境法典 \
       --hierarchy 法律 --valid-from 2026-08-15 --expected 1242
   ```
   先加 `--dry-run` 只体检不写盘；若报「缺 N 条」，用 `--dry-run` 看尾部预览，多半是条文被包在表格/文本框里；
3. `corpus/01_规范文件清单.csv` 里要有同一 `doc_id` 的行（`s1` 用它播种注册表）；
4. `python s1b_audit.py <doc_id>` 对账，FAIL 就先修切分；
5. `python run_all.py`，看条文数与下游产出是否同步变化。

## 注意

- `s1_split.py` 会重建 `out/kg.db`（其余脚本用 `fresh=False` 追加）；
- 节选样本用 `{pN}` 前缀指定款号，全量文本不要用；
- 本包 `data/raw/` 为六部法律全量文本，与 `out/kg.db` 一致；仅做单条试跑时才用节选样本。
