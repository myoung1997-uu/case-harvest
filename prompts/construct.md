# 构造 agent prompt（阶段 3b：正文没 SQL / 程序化抽不全的 issue）

主会话按 `queue/construct.jsonl` 分批派子 agent（每批 10~20 条），把下面整段连同本批 issue 号发给它。
`{HARVEST_HOME}` `{SKILL_DIR}` `{IDS}` 由主会话替换。

---

你要为下面这些 openGauss issue 各写一份**可在单机实例上自动执行的复现材料**。你不连数据库、不执行任何东西，只写文件。

issue 号：{IDS}

## 取材

对每个 issue 运行 `HARVEST_HOME={HARVEST_HOME} python3 {SKILL_DIR}/scripts/show_issue.py <n>`，
它会给出正文、人类评论、关联 PR 的【根因分析】【实现方案】。**关联 PR 写的边界条件是最好的构造线索**——照 PR 说的触发路径造，比照着现象猜准得多。
标了「⚠ 仓未定」或「标题对不上」的 PR，正文不许用。

## 产出：`{HARVEST_HOME}/constructed/<n>/`

| 文件 | 必须 | 内容 |
|---|---|---|
| `meta.json` | ✓ | 见下 |
| `setup.sql` | 可构造时 ✓ | 建现场：建对象、灌数据。**幂等**（先 `DROP ... IF EXISTS`） |
| `workload.sql` | 可构造时 ✓ | 触发缺陷的语句 + **判读语句**。要能反复执行（沉淀后会被循环跑上千次） |
| `tool.sh` | 需要跑工具时 | 以实例 OS 用户执行。可用环境变量 `$PORT` `$DB` |
| `pre.sh` / `post.sh` | 需要改参数/重启时 | 以 root 执行，`post.sh` 必须把改动还原 |

`meta.json`：

```json
{
  "id": "7376",
  "constructible": true,
  "reason": "不可构造时写原因，可构造时省略",
  "source": "agent",
  "db": "bench | bench_b | bench_pg | bench_d",
  "target": null,
  "symptom_class": "failure | wrong-results | slowness | resource | crash",
  "needs_shell": false,
  "needs_restart": false,
  "expected": "一句话：正确行为是什么、缺陷时实际是什么",
  "how_to_judge": "看哪条语句的哪个输出、什么值/什么报错算复现；什么算已修复；什么算缺依赖",
  "notes": "取舍、近似判据、没把握的地方",
  "split_needed": false
}
```

## 硬约束

1. **对象名一律 `c<n>_` 前缀**（`c7376_t1`），setup 开头先清同名对象。沉淀时会整体换成不可反推的别名；issue 正文里有辨识度的原始对象名也换掉。
2. **数据量 ≤ 100 万行**，单条语句预计 ≤ 30 秒（跑批有 `statement_timeout`）。慢类缺陷用「两侧对比」表达倍数，不要靠绝对耗时。
3. **判读必须机读**：把关键值 `SELECT` 出来并带标签，比如 `SELECT 'JUDGE_ROWS', count(*) FROM ...;`，别让人肉眼看执行计划长相。
4. **开关差分型两侧都写**：GUC 开/关、有索引/无索引、字面量/变量，两侧都跑、都打标签。
5. **兼容模式派库看语法指纹，不看有没有写「B 兼容」**：反引号标识符、`@var`、`auto_increment`、`tinyint/mediumint`、`unsigned`、`replace into`、`insert ignore`、`group_concat/ifnull/date_format` 等 → `bench_b`。D 兼容（SQL Server 语法/shark）→ `bench_d`。
6. **B 兼容脚本**不用自己写 `SET enable_set_variable_b_format=on`，跑批前导会加；但 `tool.sh` 里自己连库时要加。
7. **禁止**：`DROP DATABASE`、`ALTER SYSTEM`、改 `postgresql.conf`（要改参数走 `pre.sh`+`post.sh` 并设 `needs_restart`）、杀会话、读写实例目录外的文件、`COPY ... FROM PROGRAM`。
8. **系统表列名别凭记忆写**。不确定某个视图/列在该版本存在，就在 `how_to_judge` 里注明「若报列不存在属缺依赖」。
9. **不可构造就老实标 false**，别硬造。以下一律 `constructible=false`：主备/多节点/资源池化/升级回滚、需要 core 文件或 gdb 才能看到、需要 JDBC/ODBC 客户端行为、关键信息只在截图里、需要特殊编译（memcheck/ASAN）、纯文档/需求单。`reason` 写清哪一条。
10. 不写复现结论——你不跑数据库，结论由跑批和判读给出。

## 回报

全部写完后回一张表：`issue | constructible | db | symptom_class | 一句话`。不要贴文件内容。
