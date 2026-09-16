# 写推送脚本 agent prompt（阶段 8：供题）

主会话按批次目录里的 `work-queue.jsonl` 分批派子 agent（每批 10~20 条），把下面整段连同本批
issue 号发给它。`{HARVEST_HOME}` `{PUSH_KIT}` `{BATCH}` `{IDS}` 由主会话替换。
`{BATCH}` = `feed.py prep` 打印的批次目录（内含 manifest.json、work-queue.jsonl、每例空壳目录）。

---

你要为下面这些用例各写**三个可推送的复现脚本**（setup.sh / run.sh / cleanup.sh）。你不连数据库、
不执行 SQL，只写脚本文件；写完的脚本由用例平台在它自己的数据库环境上执行。

issue 号：{IDS}

## 先读规矩再动笔（规则单源在那边，这里不复制）

1. 通读 `{PUSH_KIT}/AGENTS.md`——连接方式、退出码语义、对象命名、判读标记、崩溃判定全在那边。
2. 用例属于卡死类（现象是挂住不返回）的，加读 `{PUSH_KIT}/HANG-DRIVER.md`，脚本套 hang_driver。
3. 有拿不准的契约细节，看 `{PUSH_KIT}/SPEC.md` 或 `GET /api/testcases/push-spec`。

## 取材

对每个 issue 号 n：

- `{HARVEST_HOME}/cases/OG-<n>/`：`setup.sql`（建现场，幂等）、`workload.sql`（触发+判读语句）、
  `ground-truth.md`（各版本实测结论、证据原文、判读依据——**判定逻辑照这里写**）
- `{HARVEST_HOME}/judged/<n>.json`：`per_version` 里 verdict=yes 的版本与 evidence（复现时看到的
  报错/现象原文）；`script_bug` 字段若非空，说明材料曾有已知问题，写脚本时要避开

## 产出：`{BATCH}/OG-<n>/{setup.sh,run.sh,cleanup.sh}`

- **setup.sh**：执行 setup.sql 建现场（对象名按 AGENTS.md 的命名规矩加前缀）；需要改会话参数
  （SET）就在这里设
- **run.sh**：执行 workload.sql，按 ground-truth 的判读依据判定，打印 `JUDGE_XXX|值` 标记行；
  退出码 0=复现 / 1=跑通但现象没出现 / 2=环境不适用没法判
- **cleanup.sh**：把 setup 建的对象删干净（幂等；能核验残留就核验）

## 硬约束

1. **连接只读环境变量** `PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE`，一行都不能写死；
   客户端 `PSQL_BIN="${PSQL_BIN:-$(command -v psql || command -v gsql)}"`。
2. 退出码语义是契约：判定不了的宁可 `exit 2`，**绝不把「没法判」报成 1**。
3. **B 兼容用例（CASE_DB=bench_b / compatibility=B）必须自己开 B 模式开关**：收割跑批有前导
   自动 `SET enable_set_variable_b_format=on`，**平台没有这个前导**——run.sh（或 setup.sh）连上库后
   第一条就要 `SET enable_set_variable_b_format = on;`，否则 `@变量`、反引号直接 syntax error
   （收割实测 149 条用例栽过）。其他会话级兼容参数（如 `behavior_compat_options`）按判读依据同样自带。
4. SQL 只许做两类改写：对象名加用例前缀、对象放进用例自有的 schema；不许改语句逻辑
   （改了就不是这道题了）。
5. workload 要能重复执行；慢类（symptom_class=slowness）的重复次数照 ground-truth 的判读依据来，
   别自己发明。
6. 崩溃类按 AGENTS.md 的崩溃判定写（连接断/实例探活）；卡死类必须用 hang_driver，不许裸跑。
7. 写完每个脚本自检：`bash -n` 过一遍；对照 AGENTS.md 的检查清单逐条打勾。
8. 不写复现结论——你写的是判定逻辑，结论由平台执行后得出。

## 回报

全部写完后回一张表：`issue | 三脚本齐 | 判定方式一句话 | 有无 hang_driver`。不要贴脚本内容。
写不了的（比如材料里判读依据缺失、没法机判）在表里写明原因，留空目录，**不要硬写一个假判定**。
