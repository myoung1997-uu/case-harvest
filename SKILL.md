---
name: case-harvest
description: 从 openGauss 社区（gitcode）issue 批量收割可复现的缺陷用例——抓 issue/评论/关联 PR → 初筛（要求有已合并修复 PR）→ 程序化抽 SQL + agent 构造复现材料 → 在多个版本实例上并行实跑 → 崩溃隔离复验 → agent 判读 → 溯根因 → 沉淀成 cases/OG-<n>/ 用例目录 → 每轮六桶统计 + 组装发件箱供题到用例平台（推送用 tc-push.sh 显式执行）。脚本自带，数据目录与实例清单可配。触发词：收割用例、case-harvest、补用例、扩用例库、从 issue 挖用例、openGauss issue 复现、供题、推用例。
---

# case-harvest：把 openGauss 社区 issue 变成可复现的用例

八个阶段，每个阶段可单独跑、可断点续跑。脚本在本 skill 的 `scripts/`（下文 `$SKILL`），只用 Python 3 标准库和 bash。

- **数据目录** `$HARVEST_HOME`（默认 `./harvest`）：语料、材料、结果、用例、轮次记录（`rounds.jsonl` + `rounds/`）都在这里。**先问用户放哪**；全量语料约 1~2 GB。
- **实例清单** `instances.conf`：从 `$SKILL/instances.example.conf` 复制改。**没有实例就只能做到阶段 3**，先告诉用户。
- 每个 python 脚本都读 `HARVEST_HOME` 环境变量，下面命令默认已 `export HARVEST_HOME=...`。

开工前读一遍 `references/pitfalls.md`——每条都是实跑踩出来的，脚本改动前尤其要读。

## 阶段 1 · 抓语料（本机，联网）

```bash
python3 $SKILL/scripts/fetch.py all     # issues → comments → linked → pulls → pulls-plugin
```

全量 4~5 小时（429 限流，全局退让）。可分开跑，**`linked`（每个 issue 的关联 PR）最重要，不能省**。

## 阶段 2 · 初筛（本机）

```bash
python3 $SKILL/scripts/classify.py                          # 打分，只用于排序
python3 $SKILL/scripts/pick.py [--exclude-cases 已有用例库/cases]   # → candidates.jsonl
```

默认只留「有已合并关联 PR」的缺陷单，打印每级漏斗数量，报给用户。已经收割过的号写进 `$HARVEST_HOME/tried.jsonl`（每行 `{"id": "123"}`）下次会跳过——但这层去重只在**封账时**生效，多个会话并行收割**没有互斥**：两路同时 pick 会捞到同一批号、各跑各的、数字翻倍。要并行先分工，各自把 `candidates.jsonl` 里不归自己的号删掉。

## 阶段 3 · 构造复现材料（本机）

**a) 程序化**：`python3 $SKILL/scripts/gen_cases.py` → `constructed/<n>/`，其余进 `queue/construct.jsonl`、`queue/ext.jsonl`（缺扩展，先搁置）。

**b) agent 构造**：读 `$SKILL/prompts/construct.md`，按 `queue/construct.jsonl` 每 10~20 条派一个子 agent（Agent 工具，可并行），替换 `{HARVEST_HOME}` `{SKILL_DIR}` `{IDS}`。队列上千条时先问用户要构造多少、按什么排序（默认按候选顺序取前 N）。

构造产出必须自带三样防误判设计（细则在 `prompts/construct.md` 硬约束 11~13）：**前置探针**（正面动用触发体同一能力自证场景激活，探不过判缺依赖）、**同径对照**（对照走与触发体相同的能力路径、只差被测变量）、**负向判据绑定形态**（「已修复」必须匹配预期错误码/文本/值，并注明适用前提）。

然后 `python3 $SKILL/scripts/mkjob.py` → `jobs/sql.txt`、`jobs/shell.txt`。

## 阶段 4 · 实跑（数据库主机，root）

把 `$SKILL/scripts/`、`instances.conf`、`$HARVEST_HOME/{constructed,jobs}` 同步到数据库主机（rsync/scp），在主机上：

```bash
export HARVEST_HOME=/path/on/host
bash scripts/preflight.sh instances.conf           # 有 ⚠ 残留就加 --clean；✗ 不许开跑
bash scripts/run_batch.sh instances.conf           # 强制串行 W=1(脚本锁死,越权须 ALLOW_PARALLEL=1),后台跑
wc -l $HARVEST_HOME/results/_rc.*.txt              # 看进度；全部 _done.* 出现即结束
bash scripts/isolate.sh instances.conf             # SQL 批结束后，崩溃候选逐条隔离复验
bash scripts/run_shell.sh instances.conf <label>   # 有 jobs/shell.txt 时，按实例串行
```

**串行是硬规矩**（2026-09-17 用户定）：问题间并行会互相干扰、崩溃归属不可信、慢类计时被污染——`run_batch.sh` 已把 W 锁死为 1，越权须显式 `ALLOW_PARALLEL=1`。用例间隔默认 30 秒（`CASE_GAP` 可调，0=关），给 undo/autovacuum 回收上一条残留留窗口。

**写操作只在 instances.conf 列出的实例上做**；`revive=wait` 的实例不重启、不改配置（建/清测试库里的对象可以）。跑批期间别让用户在这些实例上跑别的东西。

跑完把 `results/` `results-iso/` 同步回本机。

## 阶段 5 · 判读（本机）

```bash
python3 $SKILL/scripts/xtriage.py --instances instances.conf   # → triage/<n>.json + _index.jsonl
```

脚本输出里有 `noexec`（没执行到）警告的，**先查环境再判读**，别当「不复现」。然后读 `$SKILL/prompts/judge.md`，按 `_index.jsonl` 顺序每 20~30 条派子 agent，写 `judged/<n>.json`。

判「已修/不复现」前先确认**缺陷场景真的激活了**：材料的激活探针没过、或报错形态与预期不符（例：被修复拦住的应报 0A000，@变量按列名解析的 42703/42601 是语法没激活）——一律判缺依赖/判不了，不许判已修。「没看见缺陷」推不出「已修复」。

判读回报里有 `script_bug` 的：修 `constructed/<n>/`，删掉 `results/<n>.*` 重跑阶段 4 那几条。这一步实测净赚一成复现，别跳。

## 阶段 6 · 溯根因（本机）

只对有版本 `yes` 的：读 `$SKILL/prompts/rootcause.md` 派子 agent，合并写入 `judged/<n>.json`。

## 阶段 7 · 沉淀（本机）

```bash
python3 $SKILL/scripts/promote.py --instances instances.conf --all [--cases 目标用例库目录]
```

每条校验不过会打印拒收原因（没有 yes、PR 没定仓、还没拆 setup/workload、题面带 issue 号……），按原因补齐再跑。产出格式见 `README.md`「用例目录格式」。

进评测集的筛法：

```bash
grep -L -E '^(EVIDENCE_SUSPECT=yes|NON_DEFECT=|ISSUE_OPEN=yes)' cases/OG-*/case.env
```

## 阶段 8 · 统计与供题（本机）

每轮（= 一次 pick，登记进 `rounds.jsonl`，`--round N` 指定、缺省最新一轮）跑完判读/沉淀后收口。统计六桶口径（六桶 + 未跑完 = 本轮候选数）：

| 桶 | 判据 |
|---|---|
| 可复现 | 任一版本判 yes，且 case.env 无 NON_DEFECT / EVIDENCE_SUSPECT=yes / ISSUE_OPEN=yes |
| 负样本 | 有 yes 但社区判已取消（NON_DEFECT） |
| 存疑 | 有 yes 但 EVIDENCE_SUSPECT=yes |
| 未定论 | 有 yes 但 ISSUE_OPEN=yes（merged_pr 算已定论归可复现） |
| 不可复现-脚本 | 判定含 construct_fail / script_bug；或材料没造出来（无 constructed/<n>/） |
| 不可复现-引擎 | 判定过、无 yes、无 construct_fail（细分 all_no/missing_dep/uncertain 只进本地报告） |
| 未跑完 | 材料在、没判读——**只告警不入桶** |

```bash
python3 $SKILL/scripts/stats.py --round N        # 六桶报数 → rounds/<N>.stats.json
python3 $SKILL/scripts/feed.py prep --round N --outbox <项目根>/.dbdog-outbox [--close]
```

prep 组装发件箱批次（manifest 统计 + 可复现用例清单 + 空壳目录 + work-queue.jsonl，用例带
`case-harvest` + `r<N>` 标签，平台上可按轮追溯）。然后读
`$SKILL/prompts/feed.md`，按 work-queue 每 10~20 条派子 agent 写三脚本（setup.sh/run.sh/cleanup.sh，
规矩单源在 dbdog-push-kit/AGENTS.md），写完：

**流式收割模式**（复验轮标准，2026-09-17 用户定）：不等整轮收口——**判读判出 yes 即单条推送**，
推送时连带六桶增量上账。规矩：
- 每个批次只装 1 条用例，批次先落 `staging/`，推送时才移入发件箱根目录（保证 tc-push 一次只推一笔，防捎带）
- manifest 统计字段由推送脚本统一改写：`total_issues = filtered_issues = 自上次推送以来新判读的条数`，
  `repro_stats` 为这批新判读的六桶分布（**六桶和 = filtered**，平台流水累加即累计、自洽可对账）；
  增量为空时如实报 0/0/全零，绝不虚报
- 两本台账防重：`counted.jsonl`（统计只上账一次）、`pushed.jsonl`（用例只推一次）
- 已推过的号复验判完**不走全量重推**，调 `scripts/verdict_update.sh <号>`：四分支——没推过(exit 4，走全量)/
  桶没变零推送/换桶发一笔纯 verdict(`cases=[]`，几字节)/可复现降级被拦(exit 5)。`--force` 人工改判时
  透传 `force:true`——平台侧同款粘性守卫（benchweb#27）只认这个字段
- 平台按流水累加，**严禁报累积值**（接口原话「不要报 agent 自己的累积值」）；对账口径=平台各笔回执之和 = counted 条数 = judged 落盘数

```bash
python3 $SKILL/scripts/feed.py check <批次目录>   # 本地自检；平台终审在推送时
```

**推送显式执行** `dbdog-push-kit/tc-push.sh --outbox <项目根>/.dbdog-outbox`（2026-09-16 实证：插件
0.22.33 起 case-feed 的 Stop hook **不再自动推**，等 hook 会白等）。推成功的批次移入 `sent/`——
**sent/ 是推送存档，进去了就只读**；要修正就开新批次或改源头用例库再重推，别在存档里改（会造出
「档案≠平台实际收到」的断链）。平台拒收时错误清单会给出，修好后重推即可。平台对**已存在的编号是
跳过、不覆盖**（响应报 `skipped`/`skipped_count`，`accepted` 只数新号，重推同号不算失败）——但重推
也改不了库里已入库的那份；要修正已入库的用例，先在平台上删掉这条再推。

`--close` 把本轮候选**全部**补进 tried.jsonl 封账（下轮 pick 跳过）——不只是可复现的，没造出材料、
没判读到的号也在黑名单里。脚本只拦「材料在但没判读」这一种窟窿，**构造只做了一半的轮照样能 close**，
没造的号会被拉黑、下轮永捞不回（除非手工清 tried.jsonl）。所以只许整轮跑完再 close；反过来漏了
close 不丢数据——下轮 pick 会重捞这些号，各轮各记各的数，不会算成同一轮的重复。

## 汇报

每个阶段结束报数字：漏斗各级、生成/转构造/缺扩展、各实例跑完/崩溃/隔离确认/noexec、各结论计数、GT 分布、沉淀/拒收；**轮次收口时报六桶**——筛选范围 / 过滤后 / 可复现 / 不可复现（脚本·引擎）/ 负样本 / 存疑 / 未定论 / 未跑完。有异常数字（某实例 noexec 成片、某版本全是 construct_fail、未跑完非 0）先停下来查，不要继续往下推。

## 铁律

- 关键词规则只配排序，不配判定；判根因、判是否同一缺陷交给模型读语义。
- 穷尽正向数据源（关联 PR、PR 栏位、评论）再做反向推断。
- PR 没有 `repo` 就不用它的正文，不许默认 server 仓。
- 复现 ≠ 缺陷（看 stateDetail）；默认配置下复现 ≠ 没修（看 GUC）。
- 并行下的崩溃归属不可信，必须隔离复验。
- 「跑通没报错」和「压根没跑」长得一样，noexec 告警不许忽略。
- 「没看见缺陷」推不出「已修复」——负向判定的资格由**场景激活探针**授予，报错形态（如 0A000 vs 42703/42601）是判定的一部分。
- 复现过的判定是**粘性**的：「前次复现、本次没跑出来」= 偶现，仍归可复现，不许自动降级——本地 `verdict_update.sh` 与平台粘性守卫（benchweb#27）双侧拦截，也保护手工重标的归属不被他人/台账重建的自动重算冲掉；确证上次判定本身判错才 `--force`，一次一条留痕。
- 用例环境按**触发体**推导（@变量/dolphin 语法→bench_b，shark 语法→bench_d，GUC/普通语法→bench 就够），不抄历史复现位置；`CASE_DB` 只在构建一致时才是有效落点，**实例换构建后能力会漂移**（dbdog 构建无 dolphin 是实例）。
- 平台兼容字段（manifest 的 `compatibility`）**独立判别，不做 CASE_DB→字母映射**（owner 2026-09-19）：从问题描述和用例 SQL 判——B:反引号/@变量/MySQL 型语法；D:`top`/`[]`引用/`nvarchar`；PG:明确 PG 兼容模式相关；**判不出 B/PG/D 特征默认 `A`**。值只许 A/B/PG/D 四个字母（曾有 4 条把映射提示 `bench→A` 原样抄进字段的脏数据）。
- 一切结论钉 commit_id：**同版本号不同构建=不同环境**（7.0.0-RC1 的 12c995f 与 cff7b04d 在 OG-7964/1602 上行为相反是实例）。
- 改 `sqlextract.py` 先加回归用例：`python3 -m unittest discover -s $SKILL/tests`。
