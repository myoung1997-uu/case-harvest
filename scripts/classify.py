#!/usr/bin/env python3
"""对全量 issue 做规则分类与打分，输出 classified.csv / classified.jsonl。

设计要点：
- 全量 8278 条都分类，不做筛选——版本等维度只是标签，不是过滤器。
- 纯规则、零 token。模型只在最后给 Top 候选写推荐理由时介入。
- 评论未抓到的 issue，groundTruthQuality 记为 -1（未知），不当成 0，
  免得把「还没抓到」和「确实没结论」混为一谈。

只做排序打分，不做过滤判定（dbdogFit/buildCost 是关键词分，误杀严重）。
select.py 只用其中的 issueType/ciNoise/layer/rootCauseSnippet/score 几列。

用法：
    python3 classify.py        # 读 $HARVEST_HOME/raw，写 $HARVEST_HOME/derived
"""
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

RAW = os.path.join(common.home(), "raw")
DERIVED = os.path.join(common.home(), "derived")
ISSUES_JSONL = os.path.join(RAW, "issues.jsonl")
CMT_DIR = os.path.join(RAW, "comments")

# openGauss 真实发版的 major.minor 集合。裸正则会把 IP(192.168.0.11)、
# 内核(4.19.90/5.10.0)、CentOS(7.6.1810)、JDBC(42.2.18) 误当版本，必须白名单收敛。
OG_MAJOR_MINOR = {"1.0", "1.1", "2.0", "2.1", "3.0", "3.1", "5.0", "5.1", "6.0", "6.1", "7.0"}

BOT_LOGINS = {"opengauss_bot", "openGauss_bot", "opengauss-bot"}

# 根因层关键词表。层的语义与取值沿用 dbdog-benchmark/diagbench/taxonomy/layers.json，
# 此处只补「文本→层」的映射，因为 sig/* 标签被机器人批量打给了几乎所有 issue，无区分度。
LAYER_KEYWORDS = {
    "query": ["慢查询", "执行计划", "explain", "索引", "index", "估算", "行数", "优化器",
              "seqscan", "顺序扫描", "走索引", "cost", "查询慢", "sql慢", "选择率",
              "hashjoin", "nestloop", "下推", "子查询", "统计信息", "analyze",
              "聚合", "排序", "order by", "group by", "谓词", "join", "关联查询",
              "复合索引", "联合索引", "视图", "cte", "导出", "gs_dump", "gs_restore",
              "查询计划", "扫描", "全表", "filter", "limit"],
    "concurrency": ["死锁", "deadlock", "锁等待", "lock", "阻塞", "并发冲突", "长事务",
                    "事务未提交", "idle in transaction", "等待事件", "waitevent",
                    "加锁", "持锁", "互斥", "抢锁"],
    "session": ["连接数", "connection", "会话", "session", "连接池", "线程池",
                "连接失败", "连接断开", "too many", "客户端", "stdin", "转义",
                "协议", "jdbc", "odbc", "libpq", "gsql"],
    "engine": ["内存", "memory", "checkpoint", "wal", "xlog", "vacuum", "膨胀", "bloat",
               "缓存", "buffer", "存储引擎", "ustore", "astore", "段页式", "落盘",
               "temp", "临时文件", "刷脏", "redo", "undo", "外键", "约束", "主键",
               "唯一性", "回滚", "mvcc", "rownum", "copy from", "导入", "事务隔离"],
    "host": ["cpu", "磁盘", "disk", "网络", "network", "oom", "主机", "io高", "iowait",
             "磁盘满", "空间不足", "内存不足"],
    "replication": ["主备", "备机", "备库", "复制", "replication", "流复制", "standby",
                    "双机", "主从", "同步延迟", "切换", "failover", "wal sender"],
    "change": ["参数", "guc", "配置", "ddl", "升级", "upgrade", "变更", "扩容",
               "reload", "重启生效", "online ddl", "append_mode", "alter table",
               "修改表", "加字段", "改类型"],
}

# 症状可观测性信号：分类依据是「症状在 DB 侧留下什么痕迹」，
# 而不是「是不是性能问题」——实测用户手挑的 9 个用例里有一半是结果错误类，
# 把结果错误当成 dbdog 够不着而扣分，与实际选型标准相反。
SYMPTOM_SLOW = ["慢", "性能", "耗时", "超时", "timeout", "hang", "卡住", "卡顿", "无响应",
                "夯住", "堆积", "飙升", "升高", "抖动", "劣化", "退化", "变慢", "延迟",
                "阻塞", "长时间", "不返回"]
SYMPTOM_ERROR = ["报错", "error", "失败", "异常", "不支持", "拒绝", "中断", "断开"]
SYMPTOM_WRONG = ["结果不正确", "结果错误", "结果不对", "返回错误", "计算错误",
                 "值不对", "数据不一致", "不一致", "偏差", "不生效", "未生效",
                 "不校验", "未校验", "丢失"]
# core/coredump 用词边界匹配，否则 score/record/more 都会误命中
CRASH_SIGNALS = ["coredump", "core dump", "段错误", "segfault", "崩溃", "宕机",
                 "panic", "bbox", "signal handler", "core文件", "实例重启",
                 "数据库重启", "进程退出", "服务不可用", "assert失败", "断言失败"]

# 构造成本信号。ASCII 短词一律走词边界，避免 "om " 命中 SQL 的 "from "
COST_HEAVY_CN = ["主备", "双机", "集群", "扩容", "升级", "安装部署", "多节点",
                 "容灾", "同城", "异地", "级联备", "逻辑复制", "资源池化"]
COST_HEAVY_EN = ["failover", "switchover", "csn", "dcf", "cm_ctl", "gs_om"]
COST_MEDIUM_CN = ["万条", "百万", "千万", "大数据量", "压测", "并发", "分区表",
                  "重启", "参数修改", "内存不足", "磁盘满"]
COST_MEDIUM_EN = ["tpcc", "tpch", "benchmark", "sysbench", "ustore", "pgbench"]

# 测试框架/CI 自身的问题。结论往往很齐全，但不是用户能观察到的数据库故障，
# 构造不出「业务侧看到什么症状」的诊断场景，必须排除在用例候选之外。
CI_NOISE_CN = ["测试用例", "用例失败", "用例执行失败", "自动化用例", "测试脚本",
               "门禁", "流水线", "编译失败", "构建失败", "冒烟", "回归用例"]
CI_NOISE_EN = ["fastcheck", "parallel_schedule", "regress", "make check",
               "gitee-ci", "jenkins", "pipeline"]
# 需要特殊构建的实例（内存检测/消毒器），普通实例复现不了，构造成本按最高算
SPECIAL_BUILD = ["memcheck", "valgrind", "asan", "tsan", "ubsan", "debug版本"]


def _hit_cn(text, words):
    return [w for w in words if w in text]


def _hit_en(text, words):
    """英文/ASCII 词走词边界匹配。"""
    return [w for w in words
            if re.search(r"(?<![a-z0-9_])" + re.escape(w) + r"(?![a-z0-9_])", text)]


def strip_template(body):
    """去掉问题模板的固定小标题，只留用户填写的内容。

    不去掉的话，`预期输出`/`实际输出` 这类模板标题会出现在每个 A 档 issue 里，
    把全部模板 issue 误判成同一类症状。
    """
    out = re.sub(r"^#{1,4}\s*.+$", "", body or "", flags=re.M)
    return out

SQL_PAT = re.compile(
    r"\b(select |insert into|create table|explain|update |delete from|alter table|"
    r"copy |analyze |vacuum|gs_dump|gs_ctl|gsql|create index)\b", re.I)
STEP_PAT = re.compile(r"(复现|重现|操作步骤|步骤[:：]|\n\s*1[.、)]\s*\S)")
VER_PAT = re.compile(r"([0-9]+\.[0-9]+)\.([0-9]+)((?:[-.][A-Za-z0-9]+)*)")

ROOTCAUSE_PAT = re.compile(
    r"(根因|根本原因|原因分析|问题原因|定位到|定位结果|由于.{0,40}导致|"
    r"是因为|原因是|分析如下|问题出在)")
CODEREF_PAT = re.compile(r"(\.cpp|\.c\b|\.h\b|函数|接口|代码|src/|commit|MR|PR|!\d{3,}|#\d{3,})")
FIXED_PAT = re.compile(r"(已修复|已合入|已解决|修复完成|已提交|patch|补丁|合并|已回合)")
VERIFY_PAT = re.compile(r"(结论[:：]\s*通过|验证通过|测试通过|回归通过|复测ok|验证ok)", re.I)


def section(body, name):
    m = re.search(r"#{1,4}\s*" + re.escape(name) + r"\s*\n(.*?)(?=\n#{1,4}\s|\Z)",
                  body or "", re.S)
    return m.group(1).strip() if m else ""


def extract_version(issue):
    """优先取模板里的版本段，回落全文；major.minor 必须命中 openGauss 发版集合。"""
    body = issue.get("body") or ""
    for src in (section(body, "测试版本"), section(body, "数据库版本"),
                issue.get("title") or "", body):
        for m in VER_PAT.finditer(src or ""):
            mm = m.group(1)
            if mm in OG_MAJOR_MINOR:
                suffix = (m.group(3) or "").strip(".-")
                base = f"{mm}.{m.group(2)}"
                return (base + ("-" + suffix if suffix else ""), mm)
    return ("未知", "未知")


def evidence_tier(issue):
    body = issue.get("body") or ""
    if section(body, "操作步骤") and section(body, "预期输出"):
        return "A-模板齐全"
    has_sql = bool(SQL_PAT.search(body))
    has_step = bool(STEP_PAT.search(body))
    if has_sql and has_step:
        return "B-含SQL+步骤"
    if has_sql or has_step:
        return "C-部分线索"
    return "D-仅描述"


def classify_layer(text):
    """返回 (主层, 命中层数)。命中多层说明是跨层问题，难度更高。"""
    low = strip_template(text).lower()
    scores = {}
    for layer, kws in LAYER_KEYWORDS.items():
        n = sum(1 for kw in kws if kw in low)
        if n:
            scores[layer] = n
    if not scores:
        return ("unknown", 0)
    top = max(scores, key=lambda k: scores[k])
    strong = sum(1 for v in scores.values() if v >= 2)
    return (top, strong)


def layer_to_tier(layer, strong_layers):
    """映射到 benchmark 的诊断档 T1-T4（tiers.json 是单一事实源）。"""
    if strong_layers >= 2:
        return "T4-compound"
    return {
        "query": "T2-query",
        "concurrency": "T3-activity",
        "session": "T3-activity",
        "engine": "T2-query",
        "host": "T1-metric",
        "replication": "T1-metric",
        "change": "T4-compound",
    }.get(layer, "T2-query")


TIER_DIFFICULTY = {"T1-metric": 1, "T2-query": 2, "T3-activity": 3, "T4-compound": 5}


def score_dbdog_fit(text, layer, title=""):
    """0-5：症状能否被 dbdog 的采集面观察到，从而支撑定位。

    口径按用户手挑的 9 个用例校准：结果错误类同样入选（rownum 聚合错、
    COPY 转义错、外键不校验），因为 agent 拿到症状后仍可借执行计划/SQL 样本/
    错误信息定位。真正够不着的是纯 core dump 和纯文档类。

    宕机/core 类单独封顶：dbdog 看得到实例掉线与指标塌陷，能定位到「挂了」，
    但挂在哪个代码路径要 core 文件，采集面够不着，不该和可完整定位的并列满分。
    """
    low = strip_template(text).lower()
    crash_in_title = bool(_hit_cn(title.lower(), CRASH_SIGNALS))
    slow = len(_hit_cn(low, SYMPTOM_SLOW))
    err = len(_hit_cn(low, SYMPTOM_ERROR)) + len(_hit_en(low, ["error"]))
    wrong = len(_hit_cn(low, SYMPTOM_WRONG))
    crash = len(_hit_cn(low, CRASH_SIGNALS))

    fit = 1
    if slow:
        fit += 3               # 慢/hang：查询指标·样本·计划直接命中
    if err:
        fit += 2               # 报错：错误信息 + 会话上下文可定位
    if wrong:
        fit += 2               # 结果错误：靠计划/SQL 特征定位，用户已验证可用
    if layer in ("query", "concurrency", "session", "engine"):
        fit += 1
    elif layer in ("host", "replication", "change"):
        fit += 1
    elif layer == "unknown":
        fit -= 1
    if crash >= 2 and not slow:   # 纯 core dump：根因要 core 文件，采集面够不着
        fit -= 2
    fit = max(0, min(5, fit))
    # 缺陷本身就是宕机（写在标题里）：封顶 3——能测出「挂了」，测不出「为什么挂」
    if crash_in_title:
        fit = min(fit, 3)
    return fit


def is_ci_noise(title, body):
    """测试框架/CI 自身的问题——有结论但构造不出诊断场景，不能当用例。

    只看标题与正文首段：正文里顺带提一句「跑了下 fastcheck」不算，
    问题主体是测试框架才算。
    """
    head = (title + "\n" + strip_template(body)[:400]).lower()
    return bool(_hit_cn(head, CI_NOISE_CN) or _hit_en(head, CI_NOISE_EN))


def score_build_cost(text):
    """1-5：越大越难造。ASCII 短词走词边界，避免 'om ' 命中 SQL 的 'from '。"""
    low = strip_template(text).lower()
    # 需要 Memcheck/ASAN 等特殊构建，普通实例复现不了
    if _hit_cn(low, SPECIAL_BUILD) or _hit_en(low, SPECIAL_BUILD):
        return 5
    if _hit_cn(low, COST_HEAVY_CN) or _hit_en(low, COST_HEAVY_EN):
        return 5
    if _hit_cn(low, COST_MEDIUM_CN) or _hit_en(low, COST_MEDIUM_EN):
        return 3
    if SQL_PAT.search(low):
        return 1
    return 2


def load_comments(number):
    path = os.path.join(CMT_DIR, f"{number}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def score_ground_truth(comments, body=""):
    """0-3；未抓到评论返回 -1（未知），别跟「确实没结论」混淆。

    标准答案有两个来源，缺一不可：
    - 评论里的关闭结论（closed 轨的主来源）
    - 正文的「预期输出 vs 实际输出」（open 轨的主来源——bug 没修，
      但正文已写死正确行为该是什么，同样可判定）

    注意：closed 不等于有根因。实测有 issue 以「结论：通过」收尾，
    那是需求验收，不是缺陷根因。必须读评论正文判定。
    """
    has_expected = bool(section(body, "预期输出")) and bool(section(body, "实际输出"))
    if comments is None:
        return (2 if has_expected else -1), ""
    human = [c for c in comments
             if (c.get("user") or {}).get("login") not in BOT_LOGINS]
    if not human:
        return (2 if has_expected else 0), ""
    blob = "\n".join((c.get("body") or "") for c in human)
    best_snippet = ""
    for c in human:
        b = c.get("body") or ""
        if ROOTCAUSE_PAT.search(b):
            m = ROOTCAUSE_PAT.search(b)
            best_snippet = b[max(0, m.start() - 20):m.start() + 180].replace("\n", " ")
            break
    if ROOTCAUSE_PAT.search(blob) and CODEREF_PAT.search(blob):
        return 3, best_snippet
    if ROOTCAUSE_PAT.search(blob):
        return 3 if has_expected else 2, best_snippet
    if FIXED_PAT.search(blob):
        return 2, ""
    if VERIFY_PAT.search(blob):
        return 2 if has_expected else 1, ""
    return (2 if has_expected else 0), ""


def main():
    rows = []
    with open(ISSUES_JSONL, encoding="utf-8") as f:
        issues = [json.loads(line) for line in f]

    for it in issues:
        num = str(it.get("number"))
        title = it.get("title") or ""
        body = it.get("body") or ""
        text = title + "\n" + body
        state_detail = (it.get("issue_state_detail") or {}).get("title") or ""
        version, major = extract_version(it)
        ev = evidence_tier(it)
        layer, strong = classify_layer(text)
        tier = layer_to_tier(layer, strong)
        comments = load_comments(num)
        gt, snippet = score_ground_truth(comments, body)
        fit = score_dbdog_fit(text, layer, title)
        cost = score_build_cost(text)
        difficulty = TIER_DIFFICULTY[tier] + (1 if strong >= 2 else 0)

        # 两轨分开排名，各自内部比较，所以不再给「已解决」加分——
        # 那会让 open 轨（现有 9 个用例所在）被系统性压低。
        # closed 轨：ground truth 来自关闭结论，复现需要「影响版本」的实例。
        # open  轨：最新版可直接复现，ground truth 来自正文预期/实际输出。
        track = "closed" if it.get("state") == "closed" else "open"
        needs_version = version if track == "closed" else "最新版即可"

        gt_for_score = max(gt, 0)
        # 证据完整度：能不能复现是做用例的前提，权重与 fit 同级
        ev_score = {"A-模板齐全": 3, "B-含SQL+步骤": 2, "C-部分线索": 1, "D-仅描述": 0}[ev]
        is_bug = 1 if it.get("issue_type") == "缺陷" else 0
        score = (gt_for_score * 3 + fit * 3 + ev_score * 3 + difficulty * 2
                 + is_bug * 3 - cost)

        rows.append({
            "number": num,
            "ciNoise": is_ci_noise(title, body),
            "title": title[:120],
            "url": it.get("html_url"),
            "issueType": it.get("issue_type"),
            "state": it.get("state"),
            "stateDetail": state_detail,
            "createdAt": (it.get("created_at") or "")[:10],
            "track": track,
            "needsVersion": needs_version,
            "version": version,
            "versionMajor": major,
            "evidence": ev,
            "layer": layer,
            "strongLayers": strong,
            "tier": tier,
            "difficulty": difficulty,
            "dbdogFit": fit,
            "buildCost": cost,
            "groundTruth": gt,
            "commentCount": it.get("comments") or 0,
            "commentsFetched": comments is not None,
            "rootCauseSnippet": snippet[:200],
            "score": score,
        })

    rows.sort(key=lambda r: -r["score"])
    os.makedirs(DERIVED, exist_ok=True)
    with open(os.path.join(DERIVED, "classified.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cols = list(rows[0].keys())
    with open(os.path.join(DERIVED, "classified.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    fetched = sum(1 for r in rows if r["commentsFetched"])
    print(f"分类完成 {len(rows)} 条 → {DERIVED}/classified.{{csv,jsonl}}")
    print(f"其中评论已抓到 {fetched} 条（未抓到的 groundTruth=-1，抓完重跑本脚本即可修正）")


if __name__ == "__main__":
    main()
