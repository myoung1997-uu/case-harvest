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
11. **前置探针（场景激活自证）**：workload 判读语句的最前面放一条**最小化动用触发体同一能力**的语句并打标（如 `SELECT 'JUDGE_ARMED', <求值结果>;`）。例：@变量用例先 `select @probe := 5` 并断言求值成功；多字符集用例先在开关关闭态建一次目标对象。`how_to_judge` 里写死：**探针不过 → 判「缺依赖/环境不适用」，不许判已修复**。注意 GUC 存在（pg_settings 有一行）≠ GUC 生效（语法/路径真的被激活），探针要验的是后者——2026-09-16 曾因在 A 库跑 @变量用例、语法没激活而把「场景没搭起来」误判成「已修」。
12. **对照走同一能力路径**：对照组必须动用与触发体**相同的能力/路径**，只差被测变量（开关取值、索引有无、写法变体）。「普通语句能跑通」不构成场景激活的证据（假对照）。三态开关型（基线/触发/对照换值）是标准形态——三条走同一条路径、只差开关取值。
13. **负向判据绑定证据形态与适用域**：`how_to_judge` 里「什么算已修复」必须写**预期形态**——具体错误码/错误文本/值，并写明什么形态属「前提没激活」要排除。例：「被修复拦住应报 `0A000 View's SELECT contains a variable or parameter`；若报 `42703 column ... does not exist` 或 `42601`，是 @变量语法未激活=缺依赖，不是已修」。同时注明判据的适用前提（哪个库/哪个开关激活时才成立）。不许只写「没看到现象=已修」。

## 回报

全部写完后回一张表：`issue | constructible | db | symptom_class | 一句话`。不要贴文件内容。
