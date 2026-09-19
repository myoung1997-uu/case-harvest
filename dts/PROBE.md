# PROBE —— 黄区第一次进入的 runbook(死清单,照着走,不自由发挥)

**目标只有一个:带回 2 份样本 dump + config 事实清单 + preflight 输出。**
不抓全量、不装实例、不跑复现。半天工作量。

产出物(全部落在 `$DTS_HOME/probe-out/`):

```
probe-out/
├── mcp-capabilities.txt    # 步骤0 的记录
├── list-<版本>.json|html   # 步骤2 的列表页原样
├── sample-sql/             # 步骤3:一条 SQL 型单子的原样 dump
├── sample-grtmgr/          # 步骤3:一条 grtmgr 型单子的原样 dump
├── config-facts.md         # 步骤4:抄到的事实(照抄,不总结)
└── preflight.log           # 步骤5 的输出
```

## 步骤 0 · 记录 MCP 能力(5 分钟)

列出 chrome-devtools MCP 实际提供的工具名,逐项记 yes/no:

- 能否执行页面内 JS(evaluate/script 类工具)?工具名叫什么?→ `mcp-capabilities.txt`
- 能否列出网络请求(network/performance 类工具)?
- 能否截图、能否取页面快照?

**三档策略全靠"能跑 JS"兜底**——连 JS 都跑不了才用 C 档(快照/截图手工搬运)。

## 步骤 1 · 按版本搜一次(用户给的版本号)

在 DTS 页面上用版本字段筛选,执行一次典型搜索。**同时观察**:

- URL 变了还是没变(query 参数?hash 路由?);
- 若有网络请求工具:列表数据是哪条 XHR(记 URL、方法、参数);
- 搜索结果表格的列名(抄下来,这是字段映射的原料)。

## 步骤 2 · dump 列表页(三档,从 A 往下降,落盘即停)

- **A 档·接口重放**:用页面内 JS `fetch()` 重放步骤 1 记下的 XHR,返回 JSON 落盘;
- **B 档·表格刮取**:A 不行(没 XHR 或重放失败)→ JS `querySelectorAll` 把结果表整表刮成 JSON;
- **C 档·整块搬运**:B 也不行 → JS 把结果区域 `innerHTML` 原样落盘。
- 每档只许试一次,失败就降档,**不调试、不恋战**——dump 到手就是胜利。

## 步骤 3 · dump 两条样本单(一条 SQL 型、一条 grtmgr 型)

从列表里挑:正文贴着 SQL 的一条(优先带建表语句的)、带 grtmgr 用例文件/附件的一条。
逐条打开详情页,同样 A→B→C 三档 dump **详情页原样**,外加:

- 附件/用例文件:能下载就下载原文件放样本目录;下载不了记链接和文件名;
- 抄下该单的 B版本字段值、问题描述里"集中式/分布式"字样所在的那句话。

## 步骤 4 · 抄事实进 config-facts.md(照抄,不总结)

按 `config.example.sh` 的字段顺序逐项填:能定的直接写值,定不了的写"未知+在哪能查到"。
重点:URL 模板的固定前缀、列表字段名→目标字段的映射、grtmgr 调用命令的样子(从 grtmgr-guide.md 抄)。

## 步骤 5 · 跑 preflight

`bash dts/preflight.sh` → 输出原样存 `preflight.log`。**红字是预期内的**,
首触的目的就是拿到这张红字清单,不是全绿。

## 回报格式(给用户看的)

```
MCP 能力: evaluate=yes network=no ...
列表档位: A(或 B/C)+ 落盘文件名
样本: SQL 型=<单号> grtmgr 型=<单号>,各有哪些字段能对上
preflight 红字: 逐条列
```

## 失败兜底

- 任一步被权限/登录挡住 → 停在该步,记录现象,继续后面的步骤;
- 两条样本都 dump 不下来 → 把 C 档的整页快照/截图也行,带回来就有救;
- **绝不因为某步失败就改抓全量或跳过落盘**——probe 的产出就是证据,证据比完整重要。
