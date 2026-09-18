# 复验轮·单用例判读+供题 agent（逐条流水模板）

主会话实例化本模板：注入 `{N}`（用例号）、`{BATCH}`（批次目录）与下面标 {…} 的本轮事实
（版本标签、构建、实例、环境能力），按用例派子 agent。**本文件是单源**，工作区里的实例化
副本（`_percase_prompt.md` 之类）勿手改，改这里再重新实例化。

⚠ 已推用例优化(token 节约):主会话实例化本模板时若注明「该号已推过平台」,
你只执行第一步判读,跳过第二步推送材料——平台已有本体,重推会被 skipped,写脚本
是纯浪费。桶变更(翻案)的上报由主会话调 scripts/verdict_update.sh 处理,不归你管。

## 输入（主会话已备好，你只读不连库）
- 用例号: {N}
- 用例库六件套: {CASES_DIR}/OG-{N}/
  （case.env / setup.sql / workload.sql / ground-truth.md / issues.json）
- 本轮实跑日志（实例 {VERSION_TAG}，build {BUILD_ID}，{INSTANCE_DESC}，串行跑）:
  - setup 日志: {HARVEST_HOME}/results/{N}.{VERSION_TAG}.setup.log
  - work  日志: {HARVEST_HOME}/results/{N}.{VERSION_TAG}.work.log
  - 崩溃隔离复验（如有）: {HARVEST_HOME}/results-iso/{N}.{VERSION_TAG}.log
    —— 串行跑的崩溃记录归属可信，但仍以 iso 结果为准：iso 里重放能再打出断连=crash-confirmed，才可判崩溃型 yes；iso 重放不崩则崩溃记账作废，按其他证据判
- 实例环境事实（主会话按轮注入，注明实测日期）: {ENV_FACTS}
  （各兼容库的能力边界，例：@变量语法活/死、深度 dolphin 语法、扩展有无——判 missing_dep 全靠它）

## 第一步：判读（五值，只许其一）
`yes` / `no` / `construct_fail` / `missing_dep` / `uncertain`

铁律（违反任何一条即返工）:
1. **先验场景激活，再谈结论**。前置/探针语句没过、或报错形态属「能力未激活」（例：B 库里反引号报 syntax error、A 库里 @变量报 column does not exist、参数/函数不存在）→ `missing_dep` 或 `construct_fail`，**绝不许判 no**——「没看见缺陷」推不出「已修复」。
2. `yes` 必须机制对得上 issue 且**引用日志原文行**做证据；报错型对报错文本，结果型对 JUDGE 标签值。
3. work.log 里一条 `Time:` 行都没有（noexec）→ 一律 `construct_fail`，理由写「环境：没执行到」。
4. `no` = 触发语句确实执行到了、现象没出现（该版本无此缺陷或已修）。
5. 崩溃型：connection to server was lost / server closed the connection，且前后语句正常 → `yes` 候选（串行跑，归属可信），evidence 引用断连文案行。
6. **复现过的判定是粘性的（owner 2026-09-18）**：复验轮里「前次已复现、本次没跑出来」= 偶现问题，仍归 yes（可复现），**不许判 no**。降级只给一种场景——有确凿证据上次判定本身就错了（如探针没过就判了 yes），此时在 `judge_reason` 里写明翻案理由并注明 `REOPEN=yes`。目标=扩大复现数量，偶现问题恰恰是最有价值的用例。

写 `{HARVEST_HOME}/judged/{N}.json`（UTF-8）:
```json
{"id":"{N}","db":"<case.env 的 CASE_DB 归并后:bench/bench_b/bench_pg/bench_d>",
 "per_version":{"{VERSION_TAG}":{"verdict":"...","evidence":["日志原文行，至少一条"]}},
 "judge_reason":"一两句","symptom_class":"<case.env SYMPTOM_CLASS>",
 "phenomenon":"DBA 可观测现象一句话：发生了什么、在哪类对象上、报什么错/差多少。不写根因、不写定位手段、不写 issue 号",
 "script_bug":null,"flags":{"EVIDENCE_SUSPECT":null}}
```

## 第二步（仅 verdict=yes 且该号未推过）：写推送材料到 {BATCH}/（暂存区，主会话负责推送）
`{BATCH}/OG-{N}/{setup.sh,run.sh,cleanup.sh}` + `{BATCH}/manifest.json`（单条用例的 manifest）
统计字段(total_issues/filtered_issues/repro_stats)写成 1/1/reproducible=1 占位即可——主会话推送前会按台账改写为真实增量,**你别管统计,只把用例内容和脚本写对**。

脚本契约（照抄这个骨架，别发明）:
```bash
#!/usr/bin/env bash
set -uo pipefail
: "${PGHOST:?平台未注入 PGHOST}" "${PGDATABASE:?平台未注入 PGDATABASE}" "${PGUSER:?平台未注入 PGUSER}"
PSQL_BIN="${PSQL_BIN:-$(command -v psql || command -v gsql)}"
[ -n "$PSQL_BIN" ] || { echo "找不到 psql/gsql 客户端" >&2; exit 2; }
Q=("$PSQL_BIN" -X -q -tA)
```
- **setup.sh**：把 setup.sql 的建现场内容改写进来（对象名保持库内原名即可）；末尾自检（关键对象在/数据行数对），失败 exit 1
- **run.sh**：执行 workload 的触发+判读逻辑；关键值打成 `JUDGE_XXX|值` 行输出；**exit 0=复现 / 1=已修未复现 / 2=环境不适用（含缺依赖形态）**；判负前必须先验场景激活（探针/前置心跳）
- **cleanup.sh**：删 setup 建的所有对象 + 自查残留为 0；开头给最多 2 分钟等实例恢复的循环（崩溃型用例被打崩后要等）
- B 兼容（MySQL 型）用例：run.sh 里**自己写 `SET enable_set_variable_b_format=on;`**（平台没有跑批前导）
- 多行 SQL 用 heredoc（quoted 'SQL' 防 bash 展开，$$ 转义注意）

manifest.json（单条）字段——**全要，中文字段值**:
```json
{"total_issues":1,"filtered_issues":1,
 "repro_stats":{"reproducible":1,"nonrepro_script":0,"nonrepro_other":0,"negative":0,"suspect":0,"undecided":0},
 "cases":[{"case_number":"OG-{N}","phenomenon":"<judged.json 的 phenomenon>","engine":"opengauss",
   "compatibility":"<bench→A,bench_b→B,bench_pg→PG,bench_d→D>",
   "root_cause_fix":"【根因】\n<ground-truth.md 根因段提炼>\n\n【修复】\n- <PR 链接+一句话，GT=2 无 PR 就如实写无链接>",
   "judge_criteria":"<ground-truth.md 判读依据 + 本轮实测证据形态；写明激活前提与缺依赖形态>",
   "engine_version":"<本轮引擎版本>","commit_id":"<本轮构建>",
   "tags":["<现象类:coredump|慢|满|错|hang 之一>","<自由标签>","<自由标签>"],
   "reproduce":{"mode":"script","setup":"setup.sh","run":"run.sh","cleanup":"cleanup.sh"},
   "timeout_seconds":180}]}
```
现象类映射: failure→错、wrong-results→错、slowness→慢、resource→满、crash→coredump；断连型加 coredump。

## 回报（纯文本，三行以内）
`OG-{N} | <verdict> | <一句话理由>`
yes 的话再加一行：`PUSH_READY | 判据要点一句话`
