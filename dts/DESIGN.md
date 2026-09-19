# dts/ 设计 —— 内部 GaussDB(DTS) 收割版

住在 case-harvest/dts/，与根上的 openGauss 链路**同仓不同命**：
不改 `scripts/` 任何现有文件、不共享 import；要借 `sqlextract.py` 就整份拷进来改，
副本与主链路各走各的演进。主链路的回归测试必须保持绿。

## 与主链路(case-harvest)的差异收口

| 环节 | 主链路(openGauss) | 本目录(GaussDB/DTS) |
|---|---|---|
| 获取 | gitcode API 直打 | chrome-devtools 浏览器 **按版本查** → 原样 dump |
| 筛选 | 有已合并修复 PR | 有复现材料(SQL 或 grtmgr 文件)+ B版本字段 + **集中式**(问题描述关键词) |
| 材料 | 从散文构造(最重) | **两车道**:SQL 型抽语句 / grtmgr 型打包调用,判型代码先猜、猜不定进 queue |
| 环境 | 实例现成 | **wget(路径+版本拼 URL) + 安装阶梯(root 步→用户步),安装不幂等→幂等由本套件包办** |
| 复现 | 串行跑+崩溃隔离 | 同规矩,每单 **3 次、分条记录** |
| 尾段 | 判读→根因→沉淀→供题 | 同一副骨架;根因单子自带(对照/结构化,不用溯) |

## 版本外层循环(机器时间是大头,按版本摊薄安装成本)

```
for ver in 版本列表(单量降序):
    阶段1: DTS 按版本查 → dump 原样落盘 → 本地筛(集中式/有材料/B版本)
    阶段2: install(ver)            # 幂等:ready 绝不重跑;失败=dirty→清场→重装
           for 单子: 跑 3 次,分条记录   # 串行;崩了 reset 数据目录,不整装重装
    记账(漏斗+六桶+环境失败桶),换下一版(先 evict 当前构建再装新的)
```

## 三层首触风险与承诺

| 层 | 内容 | 风险 | 承诺 |
|---|---|---|---|
| 零风险 | 状态机/幂等/断点/记账/记录格式 | 无 | 外面 mock 测死,进去不改 |
| 低风险 | 机器/身份/路径/安装阶梯/grtmgr 命令 | 配置字段 | preflight 进门活体检验,红字迭代,分钟级 |
| 真风险 | 网页抓取 | 须见真页面 | **probe 优先 + 先搬运后解析**:dump 落盘=证据在手,调解析永不重抓 |

## 目录规划(文件随实现逐步就位)

```
dts/
├── DESIGN.md            本文件
├── PROBE.md             黄区首触 runbook(死清单)
├── ACCEPTANCE.md        每道门的验收标准
├── config.example.sh    问卷式配置(机器/身份/URL模板/安装阶梯/清场/健康检查/reset)
├── preflight.sh         进门自检(逐项活体检验,✗不许开跑;--live 才真探远程)
├── provision.sh         下载+安装状态机(absent→downloaded→installing→ready|dirty|evicted)
├── probe/               dump 三档 JS(evaluate 喂进页面跑,CONFIG 块填空):
│   ├── dump-fetch.js    A 档:接口重放(fetch+credentials,翻页)
│   ├── dump-table.js    B 档:结果表整张刮成 JSON
│   └── dump-dom.js      C 档:关键区域 innerHTML 原样兜底
├── parse_dump.py        后厨:三档落盘 → issue-raw.jsonl(最大对象数组自动定位/
│                        fieldmap 覆盖键名/原文进 raw_blob/坏文件落账)
├── extract.py           解析(python3):两车道判型/字段抽取/版本与集中式过滤
├── sqlextract.py        主链路整份拷贝的副本(SQL 车道用;与主链路各自演进)
├── runloop.sh           版本外层循环 + 每单 3 次执行 + 分条记录(待做)
├── test_mock.sh         门0 测试(42 断言:状态机/幂等/dirty恢复/共享槽/锁/白名单/reset)
├── test_dump.sh         dump 套件测试(23 断言:三档解析/fieldmap/零静默丢/串链/JS语法)
└── mock/                外部测试夹具(假包/假安装脚本/假 dump/fieldmap)
```

## 引擎与产品区分(三道闸,零内容猜测)

1. **来源即引擎**:根目录链路=gitcode/openGauss;`dts/`=DTS/GaussDB。HARVEST_HOME 分开,
   记录落盘时盖 `engine=gaussdb`,不从正文猜;
2. **字段抄产品形态**:集中式/分布式=问题描述关键词(确定性规则、可复核、留命中片段);
   版本串=网页 B版本字段原样抄。**分布式单子单机造不出→排除或 queue**;
3. **实例防装错**:注册表按构建盖引擎标;健康检查版本串须同时匹配构建号+引擎特征;
   单子→构建→实例→结果四点引擎标一致才许记账。供题侧 compatibility 按引擎白名单(GaussDB 只许 A/B/M)。

## 幂等由套件包办(安装脚本不幂等的对策)

- 注册表 `registry/<BUILD>/state` 是唯一状态真相:**ready 绝不重跑安装**;
- 失败→`dirty`,重试前先清场(用户步→root 步→删 CLEAN_PATHS 白名单前缀内路径,系统目录一律拒绝);
- 换版本 = evict 当前构建(清场)→ 装新的;单机互斥锁防并发安装;
- 同构建被打崩 → reset 数据目录(分钟级),reset 救不回才 evict+重装。

## 记账

每轮按版本收口:漏斗各级 + 六桶(可复现/负样本/存疑/未定论/不可复现-脚本/不可复现-引擎)
+ **环境失败桶**(安装失败/起不来的版本,该版单子整体标"环境不可得",绝不冒充不可复现)
+ queue/unmapped 账(绝不静默丢)。判定粘性:前次复现本次没跑出=偶现,不降级。
