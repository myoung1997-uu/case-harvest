# case-harvest

Claude Code skill：从 openGauss 社区（gitcode）issue 批量收割**可复现、带根因**的数据库缺陷用例，产出可直接给诊断评测用的用例目录。

## 安装

把整个目录放到 Claude Code 的 skills 目录下：

```bash
cp -r case-harvest ~/.claude/skills/
```

重开 Claude Code 后说「收割用例」或 `/case-harvest` 触发。也可以不经过 Claude 直接跑脚本，流程见 `SKILL.md`。

## 需要什么

| | 用途 |
|---|---|
| Python 3.8+（只用标准库） | 抓取、筛选、抽取、判读包、沉淀 |
| 能访问 `api.gitcode.com` | 阶段 1 抓语料；不需要 token |
| 1~2 GB 磁盘 | 全量 issue/评论/PR 语料 |
| 一台或多台装了 openGauss 的 Linux 主机，root 权限，有 `timeout`/`setsid`/`gsql` | 阶段 4 实跑。建议挂多个版本（如 5.0.0/6.0.0/7.0.0），B 兼容需 dolphin、D 兼容需 shark |
| Claude Code | 阶段 3b 构造、阶段 5 判读、阶段 6 溯根因（派子 agent） |

**不要在生产库上跑**。用例会建表、灌数据、可能打崩实例；专用复现实例在 `instances.conf` 里设 `revive=yes`，共享实例设 `wait`。

## 目录

```
SKILL.md                  流程（Claude 读这个）
instances.example.conf    实例清单模板
scripts/
  fetch.py                抓 issue / 评论 / 关联 PR / server 与 Plugin 仓 PR（断点续传）
  classify.py             规则打分（只排序）
  pick.py                 初筛 → candidates.jsonl
  sqlextract.py           issue 正文 SQL 抽取（PL/SQL 块、兼容模式派库、缺对象检测）
  gen_cases.py            程序化生成复现材料，其余入构造队列
  show_issue.py           打印 issue + 评论 + 关联 PR 根因栏，给 agent 取材
  mkjob.py                生成跑批任务清单
  lib.sh preflight.sh run_batch.sh worker.sh isolate.sh run_shell.sh   数据库主机上跑
  xtriage.py              整理判读包
  promote.py              沉淀用例目录 + 别名表
prompts/                  构造 / 判读 / 溯根因 三个子 agent 的提示词
references/pitfalls.md    踩坑清单（改脚本前必读）
tests/                    python3 -m unittest discover -s tests
```

## 数据目录（$HARVEST_HOME）

```
raw/                 issues.jsonl comments/ issue_pulls/ pulls.jsonl pulls_plugin.jsonl
derived/             classified.{csv,jsonl}
candidates.jsonl     初筛结果
constructed/<n>/     复现材料：setup.sql workload.sql meta.json [expect.txt tool.sh pre.sh post.sh]
queue/               construct.jsonl ext.jsonl
jobs/                sql.txt shell.txt
results/             <n>.<版本>.{setup,work}.log  <n>.<版本>.skip  _crash.txt _noexec.txt _skip.txt _rc.*.txt
results-iso/         崩溃隔离复验日志与 _verdict.txt
triage/              <n>.json _index.jsonl
judged/<n>.json      判读 + 根因
cases/OG-<n>/        最终用例
cases/_alias-map.json
```

## 用例目录格式

| 文件 | 内容 |
|---|---|
| `case.env` | `CASE_DB` `REPEAT` `GT` `HAS_FIX` `CASE_ALIAS` `AFFECTED_VERSION` `REPRO_VERSION` `REPRO_BUILD` `REPRO_PORT`（主复现落点）`REPRO_ALL`（全部复现端口）`REPRO_ARCH` `SYMPTOM_CLASS` `SPLIT` `PHENOMENON_TEMPLATE` `REPRO_TOOL` `NEEDS_RESTART`，可选排除标记 `ISSUE_OPEN` `NON_DEFECT` `EVIDENCE_SUSPECT` |
| `setup.sql` | 建现场，幂等 |
| `workload.sql` | 可反复执行的触发语句 |
| `prompt.txt` | 题面：`{{WINDOW}}` 时间窗占位 + 库名 + 现象。不含根因、不含 issue 号 |
| `ground-truth.md` | 各版本实测结论与证据原文、根因与来源、关联 PR、判读理由。**不能给被测方看** |
| `issues.json` | issue 原始 JSON |

对象名里的 issue 号都换成别名：`sha256("dbdog-loop-alias-v1|OG-<n>")` 十六进制串里第一个首位非数字的 6 位窗口，映射存 `_alias-map.json`。

`GT`：3 = 根因到代码位置或有 PR 讲清机制；2 = 有机制说明；1 = 只有现象。

排除标记：`ISSUE_OPEN=yes`（社区未定论）、`NON_DEFECT=cancelled`（社区判非缺陷，可作负样本）、`EVIDENCE_SUSPECT=yes`（复现脚本存疑）；`ISSUE_OPEN=merged_pr`（单未关但修复已合入）照常用。
