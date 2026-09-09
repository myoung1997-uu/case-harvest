---
name: case-harvest
description: 从 openGauss 社区 issue 收割可复现的诊断评测用例——抓 issue/PR 语料 → 初筛 → 构造复现脚本 → 多版本实例上实跑 → 判读 → 溯根因 → 沉淀成 loop/cases 八件套资产。diag-hunt 消费的用例库就来自这里。触发词：收割用例/case-harvest/补用例/扩用例库/从 issue 挖用例。
---

# case-harvest：把社区 issue 变成可复现的评测资产

产出是 `loop/cases/OG-<n>/` 八件套，供 `diag-hunt` 消费。仓库根目录 `/Users/david/repo/dbdog/opengauss-issue-corpus`。

七个阶段，**每个阶段都可单独跑**，不必一次走完。

---

## 阶段 1 · 抓语料

```bash
python3 scripts/fetch.py issues     # issue 列表
python3 scripts/fetch.py comments   # 每条 issue 的评论
python3 scripts/fetch.py pulls      # openGauss-server 仓的 PR 列表
python3 scripts/fetch.py linked     # ★ 每个 issue 的「关联 PR」（详情页字段）
```

**`linked` 是最重要的一步，也是最容易被忽略的。** `/issues/{n}/pull_requests` 端点给的是社区自己标注的正向关联，比任何反向推断都可靠。语料前期只抓了 issues+comments，导致找根因时绕了一大圈做词面匹配和语义推断，而正向关联一直挂在详情页上。

Plugin(dolphin) 仓要单独抓：B 兼容（MySQL 语义）缺陷的修复主体都在那儿，`opengauss/Plugin`，约 2500 个 PR。关联 PR 里**接近一半属于 Plugin 仓**。

## 阶段 2 · 初筛

保留：`issueType=缺陷` 且 `ciNoise=False` 且 `layer ∉ {replication, host}`。

排除这些（标题或结论片段命中即排）：

- **非数据库本体**：docker/容器/镜像/jdbc/odbc/驱动/dbeaver/navicat/编译失败/安装部署/门禁/流水线/文档链接/avx512/指令集/操作系统/k8s
- **单机造不出的形态**：core/coredump/宕机/主备/备机/容灾/双机/级联备/资源池化/共享存储/switchover/failover/cm_ctl/gs_om/多节点/扩容

**优先挑「有已合并关联 PR」的**。这是选题阶段就能锁死根因的唯一办法：进池前先查
`data/raw/issue_pulls/{n}.json` 里有没有 `state=merged` 的 PR，有就说明社区认了这个缺陷、修了、
而且根因白纸黑字写在 PR 的「根因分析」「实现方案」栏里。

两轮实测对比（同一套流程，只换选题口径）：

| | 不筛关联 PR | 要求有已合并 PR |
|---|---|---|
| 池子 | 3903 | 1206 |
| 正文自带 SQL | 41% | 75% |
| 复现 | 121 | 159 |
| 根因覆盖 | 事后满世界找，90% | **进池前就 100%** |
| 可用资产 GT=3 占比 | 46/145 | **158/159** |

不筛的那一轮，大量精力花在「复现出来了但找不到根因，只能降档或作废」。筛过之后这块成本直接归零，
构造 agent 还能拿 PR 根因当线索——照着 PR 说的边界条件构造，比照着现象猜准得多。

**不要用 dbdogFit / buildCost / groundTruth 的分档当过滤器。** 这三个都是关键词规则打出来的分，误杀严重——`COST_HEAVY_CN` 里有「升级」，正文写一句"升级到 X 版本后出现"的纯 SQL 缺陷就被判成单机造不出。它们只配当排序项。

**版本也不要当过滤器。** issue 标的影响版本经常对不上实际能复现的版本，按版本筛会漏掉大批。

## 阶段 3 · 构造复现脚本

两条路并行：

**a) 正文自带 SQL 的**——程序化抽取。抽取器的坑（全部踩过）：

- **PL/SQL 体不能按 `;` 切句**——`CREATE PROCEDURE ... BEGIN x:=1; ... END;` 里第一个内部分号就会把过程腰斩，
  DB 一律回 `subprogram body is not ended correctly`。必须**进入 PL 体后忽略体内分号，靠 begin/end 深度配对收口**
  （`begin`/`case`/`loop`/`if` 加一层，`end` 减一层，归零且行尾是 `end...;` 才断句）。
  实测这一条占「构造失败」的最大头：修好后该类报错从 369 个版本格降到 82 个。
  注意 `if not exists` 里的 `if` 不算开块。
- **判「缓冲区为空」要忽略空白行**——PL 块识别若写成「缓冲区为空才认头」，正文里 `declare` 前的一个空行
  就会让整个识别失效。踩过，而且因为聚合指标还在变好，一度没发现。
- **有围栏代码块时别再按反引号扫全文**——同一段 SQL 会被取材两次，跑出满屏 `already exists`。
  取材完按「压空白+小写」做一次语句级去重。
- `DO $$…$$;` 之后多写一行 `/`，gsql 当独立语句报错**并吞掉后面所有判读语句**
- 一行里写多条语句（`drop ...;create ...;`）要按 `;` 拆
- 遇到以 SQL 关键字开头的新行要断句，否则前面的散文会和建表语句粘成一句，整句因不以 SQL 开头被丢弃
- markdown 表格行、shell 提示符行、中文散文要剔，但**以 SQL 关键字开头的行即使带中文也要留**
- 系统表（`pg_*`/`gs_*`/`dbe_*`/`information_schema`）不算「需要自己建的对象」

**b) 正文没 SQL 只有描述的**——派 agent 按描述构造。约束写进 prompt：对象名前缀、幂等、数据量 ≤100 万行、判读要机读（把关键值 select 出来，别靠肉眼看）、开关差分型要把两侧都写上。

**生成阶段就做自检**：脚本引用了却从没建过的对象 → 移出批跑队列，别浪费一次执行。实例上没有的扩展（bm25/vector/ogai/diskann）→ 提前分流。

## 阶段 4 · 在多个版本实例上实跑

vm203（`192.168.122.203`，从 `dbdog-server` 跳进去）上三个实例，**能力不对等，没有哪台能覆盖全部**：

| 端口 | 版本 | OS 用户 | shark(D兼容) | dolphin(B兼容) |
|---|---|---|:-:|:-:|
| 5432 | 7.0.0-RC1（dbdog 自编译 12c995f）| opengauss | ✓ | ✗ |
| 6432 | 5.0.0（官方 a07d57c3）| og500 | ✗ | ✓ |
| 7432 | 6.0.0（官方 aee4abd5）| og600 | ✗ | ✓ |

所以 **B 兼容用例在 RC1 上一律判「缺依赖」不判「不复现」**——判后者等于谎称 RC1 没这个缺陷。D 兼容用例反过来只能在 RC1 跑。

**兼容模式派库的三件事，缺一条就整批白跑：**

1. **别只看正文有没有写「B 兼容/MySQL 模式」**——大多数 issue 不写。要按 SQL 语法指纹判：
   反引号标识符、`@var` / `@@var`、`auto_increment`、`tinyint/mediumint/tinyblob`、`unsigned`、
   `on update current_timestamp`、`replace into` / `insert ignore`、`declare ... handler for`、
   `datediff/str_to_date/date_format/yearweek/group_concat/find_in_set/ifnull`、`engine=`、
   `charset ... collate`、`alter index ... invisible`。实测补上指纹后多认出 39 条。
2. **建了 B 兼容库不等于 B 语法能用**——`datcompatibility='B'` + 装了 dolphin 之后，
   `@变量`和反引号标识符**还需要 `SET enable_set_variable_b_format=on`**，否则一律 `syntax error at or near "@"`。
   踩过：149 条 B 兼容用例因为缺这个开关，根本没跑到触发语句。每条 B 兼容脚本开头都要带上。
3. **跑之前先确认目标库真的存在**。`bench_d` 一度根本没建，10 条 D 兼容用例连库都没连上——
   gsql 连不上库和「跑通了没报错」在日志里长得一样，取证脚本按 `clean` 记账，是纯假阴性。
   **给「输出字节 < 100」加一条告警**，这是唯一能自动发现它的信号。

跑批要点：

- 每实例多 worker 并行，但**临时文件名必须带 worker 序号**
- 每条用例前后各探活一次；实例挂了自动拉起，拉不起来**只跳过这一条**（`SKIP-instance-down`），不要 `break` 整个循环——踩过一次，一条 MOT 用例把实例搞挂后剩下 61 条一条没跑
- 挂看门狗常驻，每 10 秒探一次，掉了自动拉
- core 文件每个实例只留最近一个（一个 200MB）
- 需要 `gs_dump`/`gs_probackup`/改参数重启的**串行单独跑**，别跟 SQL 批混
- **`while read` 循环里的 `su`/`ssh` 会吃掉管道剩余的输入行**，worker 会提前「读完」退出。
  症状很好认：多个 worker 停在同一个数字上。所有 `su`/`ssh` 都要加 `< /dev/null`。
- **现役采集实例（RC1）不给自愈，但也不能直接放弃**——一条崩溃用例把它打崩后，
  若 revive 直接返回失败，剩下几百条会全部 `SKIP`。正确做法是**只等它自己恢复、绝不主动 systemctl**
  （openGauss postmaster 会自行重启），既守住「不重启现役实例」的纪律，也不丢用例。

### 跑批前必须清的环境残留

上一轮用例留下的全局对象会静默污染整批，且症状看起来像「数据库缺陷」：

```sql
-- FOR ALL TABLES 的逻辑复制发布：无 replica identity 的表 UPDATE/DELETE 一律被拒
select pubname from pg_publication where puballtables;
-- 用例留下的 DDL 审计事件触发器：别的用例会报 permission denied for relation <审计表>
select evtname from pg_event_trigger;
-- c<id> 前缀的残留 schema、以及 create database/tablespace/extension 的残留
select nspname from pg_namespace where nspname ~ '^c[0-9]+';
```

前两类必清。发布那一条是实测最毒的——四个判读 agent 从不同分片独立指认它。

## 阶段 5 · 判读

派 agent 逐条读日志，对着用例自带的 `how_to_judge` 判。**每个版本一个结论**：`yes` / `no` / `构造失败` / `缺依赖` / `不确定`。

判读口径里必须写死的三条：

1. **报错型要机制对得上**——期望「结果不对」却报了语法错，那是版本不支持该语法，不算复现
2. **崩溃归属看断连点**——并行跑批时一条打崩实例，同时在跑的另两条也会被记账。断连点落在自己的触发语句上才算，否则判「不确定」。**必须再做一轮单实例单会话的隔离复验**（跑前跑后各探活）：实测 33 次崩溃隔离后只剩 4 次是真的
3. **构造失败/缺依赖单独归类**，不跟「不复现」混——否则看不出是环境问题还是真没缺陷

判读还会揪出一批**我们自己脚本的 bug**（引用不存在的系统表列、`SET ROLE` 缺口令、对照组自己也报错、setup 数据一行没灌进去）。修完重跑，实测这一步净赚 12 条复现。

## 阶段 6 · 溯根因

**按这个顺序，别跳步**：

1. **`/issues/{n}/pull_requests` 的关联 PR** ← 正向数据源，先用它。命中率约 75%
2. **PR 正文的 `【根因分析】`**，**外加 `【实现方案】`**——大量 PR 根因写在实现方案栏，只看根因分析栏会漏掉 520 个
3. **PR 标题**——「增加 X 不支持 Y 的限制」「删除不存在的 Z 函数」这类，**处置方式本身就是根因**
4. **issue 的讨论**——派 agent 读，**绝不能用关键词匹配**。正则扫「根因/原因分析」这类规范措辞会漏掉三分之二，工程师实际写的是「导致」「因为」这种大白话。根因来源里 issue 评论占一多半
5. **源码回溯**——报错原文去源码树 grep。注意剔除通用抛出点（`pl_exec.cpp` 的通用语法错抛出点会被十几条命中，无定位价值）

**不要做的**：拿报错文本去匹 PR 根因描述。试过，9176 条 PR 里只匹出 2 条且全是假匹配——PR 写「修了什么」，我们手上是「报错长什么样」，用词不重叠。

### 溯根因的三个坑

- **跨仓同号**：关联 PR 里近一半属于 Plugin 仓，拿它的号回 server 仓查会查到完全无关的同号 PR。**必须核对 `repo` 字段或标题一致性**。踩过一次，38 条根因里 12 条是错的
- **PR 正文误粘模板**：标题讲 A、正文讲 B，已发现 5 例以上。正文和标题对不上时只信标题
- **修复藏在默认关闭的 GUC 后面**：`sql_beta_feature`、`handle_toast_in_autovac`、`behavior_compat_options` 都是例子。**默认配置下跑出「仍复现」不代表社区没修**，判「是否已修」要先按 PR 说的把开关打开再跑

## 阶段 7 · 沉淀资产

写 `loop/cases/OG-<n>/`：`case.env` / `setup.sql` / `workload.sql` / `ground-truth.md` / `issues.json` / `prompt.txt`（需要时加 `tool.sh` / `pre.sh` / `post.sh`）。

**对象名前缀换成别名**（`sha256("dbdog-loop-alias-v1|OG-<n>")` 取首个非数字开头的 6 位窗口），登记 `_alias-map.json`——目录名和对象名都是题面泄漏面。

`case.env` 必带：`CASE_DB`（bench/bench_b/bench_pg/bench_d）、`REPRO_PORT`（主复现落点）、`REPRO_ALL`（全部复现的端口）、`REPRO_VERSION`/`REPRO_BUILD`、`SYMPTOM_CLASS`、`GT`。

`ground-truth.md` 要有：三版本实测结论（各带决定性证据原文）、根因（注明来源）、关联 PR/commit、判读依据。

### 排除标记（进不进评测集靠这三个）

```bash
grep -L -E '^(EVIDENCE_SUSPECT=yes|NON_DEFECT=|ISSUE_OPEN=yes)' loop/cases/OG-*/case.env
```

- **`ISSUE_OPEN=yes`** —— issue 未关闭**且无已合并 PR**，社区结论未定
- **`ISSUE_OPEN=merged_pr`** —— 单未关但修复 PR 已合入，**这类照常用**，是社区改完没回来关单
- **`NON_DEFECT=cancelled`** —— `stateDetail=已取消`，社区判非缺陷（多为兼容模式既定语义）。**留着当负样本**：考 agent 会不会把设计行为误报成缺陷
- **`EVIDENCE_SUSPECT=yes`** —— 我们的复现脚本写歪了，修好前不判分

### GT 分档

- **3** = 根因说到代码位置或有 PR/commit 号
- **2** = 有机制说明但没到代码行（**够用**，不必强求代码级）
- **1** = 只有现象

**别用「根因文字长度」当判据**——试过用「超过 60 字给 GT=3」，结果批跑用例的 GT=3 占比是人工资产的三倍，明显虚高，61 条要降档。

---

## 铁律

- **关键词规则只配做排序，不配做判定。** 判「有没有根因」「是不是同一个缺陷」一律交给模型读语义。这条是本 skill 最大的教训，全流程反复踩。
- **穷尽正向数据源再做反向推断。** 关联 PR 端点、PR 正文栏位、issue 评论——都是现成的。绕过它们去做词面匹配和语义推断，是拿复杂度换本可以直接读到的事实。
- **跨仓、同号、误粘模板** 三件事让 PR 数据不可无条件信，取用前核对 `repo` 和标题。
- **复现不等于是缺陷。** 社区判「已取消」的占比不低（复现集里约 27%），必须按 `stateDetail` 筛掉或标为负样本。
- **默认配置下复现，不代表没修。** 先查有没有 GUC 开关。
- **并行跑批的崩溃归属不可信**，必须隔离复验。
- **「跑通了没报错」和「压根没跑」在日志里长得一样。** 连不上库、脚本被吞、判读语句执行不到，
  取证脚本都会记成 `clean`。唯一的自动信号是**输出字节异常小**，必须加告警。
- **改抽取器/取证脚本后，要对具体用例做前后 diff，不能只看聚合指标。** 踩过两次：
  一次 heredoc 多重转义把正则写成 `\\s`（raw string 里是字面反斜杠），修复空转三轮而指标还在变好；
  一次「过滤 gsql 回显」的对象名写成可选，把 PL 块里独占一行的 `begin` 当 banner 删了，比修之前更糟。
- 远端只在 vm203 的三个复现实例上做写操作；RC1（5432）是现役采集实例，**不重启它、不改它的配置**
  （建测试库、清测试库里的残留对象不算改配置，可以做）。
