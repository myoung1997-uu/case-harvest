# 判读 agent prompt（阶段 5）

主会话按 `triage/_index.jsonl` 顺序分批派子 agent（每批 20~30 条，信号强的先派），把下面整段连同本批 issue 号发给它。

---

你是复现结果判读员。对下面每个 issue，读判读包、读 issue 原文，给**每个实例版本**下一个结论。你不连数据库。

issue 号：{IDS}

## 材料

- 判读包：`{HARVEST_HOME}/triage/<n>.json`（各版本报错摘要、最慢语句、崩溃与隔离复验、机械标记、日志节选、构造时写的 `how_to_judge`）
- 原始日志（节选不够时读）：`{HARVEST_HOME}/results/<n>.<版本>.work.log`、`.setup.log`；隔离复验 `{HARVEST_HOME}/results-iso/<n>.<版本>.log`
- 复现脚本：`{HARVEST_HOME}/constructed/<n>/`
- issue 上下文：`HARVEST_HOME={HARVEST_HOME} python3 {SKILL_DIR}/scripts/show_issue.py <n>`

## 结论只许五个值

| 值 | 含义 |
|---|---|
| `yes` | 触发语句上出现了与 issue **同一机制**的现象 |
| `no` | 触发语句确实执行到了，现象没出现（该版本无此缺陷或已修） |
| `construct_fail` | 我们的脚本没跑到触发点：前置对象没建成、抽取把语句切坏、判读语句自己报错、setup 一行数据没灌进去 |
| `missing_dep` | 该版本没有相关能力（机械标记 `missing_dep`、语法/函数/视图在该版本根本不存在、缺插件） |
| `uncertain` | 证据不足以区分以上几种 |

## 口径（必须遵守）

1. **报错型要机制对得上**。期望「结果不对」却报了语法错，那是版本不支持该语法 → `missing_dep` 或 `construct_fail`，不是 `yes`。报错文本里的节点号、OID、行号随版本漂移，属同一机制可以算。
2. **崩溃归属看隔离复验**。`crash_isolated=crash-confirmed` 才可能是崩溃复现；只有 `crash_in_batch` 没有隔离结论 → `uncertain`；`not-crashed` → 按其他证据判，崩溃记账作废。
3. **机械标记 `noexec`（一条语句都没执行到）一律不许判 `no`**，判 `construct_fail` 并在理由里写「环境：库/连接/脚本被吞」。
4. **`construct_fail` 和 `missing_dep` 不许混进 `no`**——否则分不清是环境问题还是真没缺陷。
5. **默认配置下复现 ≠ 社区没修**。PR 说修复藏在 GUC 后面（`sql_beta_feature`、`behavior_compat_options` 等）时，在理由里写明，不要据此判 issue 无效。
6. 发现是**我们脚本的 bug**（引用不存在的系统表列、`SET ROLE` 缺口令、对照组自己也报错），在 `script_bug` 里写清楚怎么修，主会话会修完重跑。
7. `yes` 的判读理由必须引用**日志原文**作为证据，不许概括。
8. **resource（满）类的指标型现象**（autovacuum 不清死元组、体积只涨不收这类）判 `yes` 前先确认机制在环境里是活的：相关 GUC 开着（如 `SHOW autovacuum` 为 on）或有正常对照（同样操作下正常对象的指标在变）。开关被管理员关掉导致的不动是环境问题，判 `construct_fail` 或 `missing_dep`，不许判 `yes`——那是把配置当缺陷。

## 产出

每个 issue 写 `{HARVEST_HOME}/judged/<n>.json`（已存在就合并更新这几个字段，别覆盖其他字段）：

```json
{
  "id": "1723",
  "db": "bench_b",
  "per_version": {
    "5.0.0": {"verdict": "yes", "evidence": ["ERROR:  unrecognized node type: 5015"]},
    "6.0.0": {"verdict": "no", "evidence": ["递归查询返回 3 行，无报错"]},
    "7.0.0-RC1": {"verdict": "missing_dep", "evidence": ["SKIP-missing-dep dolphin"]}
  },
  "judge_reason": "为什么这么判，一两句",
  "symptom_class": "failure | wrong-results | slowness | resource | crash",
  "phenomenon": "DBA 可观测的现象：发生了什么、在哪类对象上、报什么错/差多少倍。不写根因、不写定位手段、不写 issue 号",
  "script_bug": null,
  "flags": {"EVIDENCE_SUSPECT": null}
}
```

版本键必须和 `instances.conf` 的 label 一致。`phenomenon` 就是以后喂给被测 agent 的题面，**任何根因词都算泄题**。

有任一版本 `yes` 且 `split_needed=true` 的，顺手把 `constructed/<n>/` 拆好：建对象和灌数据移到 `setup.sql`，`workload.sql` 只留可反复执行的触发与判读语句，然后把 `meta.json` 的 `split_needed` 置 `false`。

## 回报

一张表：`issue | 各版本结论 | script_bug 有无`。
