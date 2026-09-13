#!/usr/bin/env python3
"""拉取 openGauss-server 全量 issue 列表与评论。

设计约束：
- 断点续传——已落盘的页/评论直接跳过，重跑只补缺口。
- 只用标准库，无外部依赖。
- 失败重试 + 退避；429/5xx 不当成致命错误。

数据落 $HARVEST_HOME/raw/（默认 ./harvest/raw/）。

用法：
    python3 fetch.py issues        # 全量 issue 列表（含正文）
    python3 fetch.py comments      # 逐 issue 评论（依赖 issues）
    python3 fetch.py linked        # ★ 每个 issue 的关联 PR（详情页字段，列表接口不返回；依赖 issues）
    python3 fetch.py pulls         # openGauss-server 仓全量 PR（body 里有【根因分析】【实现方案】栏）
    python3 fetch.py pulls-plugin  # Plugin(dolphin) 仓全量 PR——B 兼容缺陷的修复近一半在这个仓
    python3 fetch.py all           # 按依赖顺序全跑

全量约 4~5 小时（服务端 429 限流）；断点续传，中断后重跑只补缺口。
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = os.environ.get("OG_REPO", "opengauss/openGauss-server")
API = f"https://api.gitcode.com/api/v5/repos/{REPO}"
PER_PAGE = 100
WORKERS = int(os.environ.get("OG_WORKERS", "6"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

RAW = os.path.join(common.home(), "raw")
PAGE_DIR = os.path.join(RAW, "issues")
CMT_DIR = os.path.join(RAW, "comments")
ISSUES_JSONL = os.path.join(RAW, "issues.jsonl")
PR_DIR = os.path.join(RAW, "pulls")
PULLS_JSONL = os.path.join(RAW, "pulls.jsonl")
PLUGIN_REPO = os.environ.get("OG_PLUGIN_REPO", "opengauss/Plugin")
LINK_DIR = os.path.join(RAW, "issue_pulls")

_print_lock = threading.Lock()
_counter = {"done": 0}


def log(msg):
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


_cooldown_until = [0.0]  # 全局限流冷却；命中 429 时所有线程一起退让
_cooldown_lock = threading.Lock()


def _wait_cooldown():
    while True:
        with _cooldown_lock:
            remain = _cooldown_until[0] - time.time()
        if remain <= 0:
            return
        time.sleep(min(remain, 5))


def _trip_cooldown(seconds):
    with _cooldown_lock:
        _cooldown_until[0] = max(_cooldown_until[0], time.time() + seconds)


def get_json(url, tries=8):
    """GET 一个 JSON，带退避重试。返回 (data, headers)。

    429 单独处理：触发全局冷却，避免各线程各自重试把限流拖得更久。
    """
    last = None
    for i in range(tries):
        _wait_cooldown()
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "opengauss-issue-corpus/1.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8")), dict(r.headers)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:  # 确实没有，别重试
                return None, {}
            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else min(5 * (i + 1), 60)
                _trip_cooldown(wait)
                continue
            time.sleep(min(2 ** i, 30))
        except Exception as e:  # 网络抖动
            last = e
            time.sleep(min(2 ** i, 30))
    raise RuntimeError(f"GET 失败 {url}: {last}")


def total_pages():
    _, headers = get_json(f"{API}/issues?state=all&per_page={PER_PAGE}&page=1")
    total = int(headers.get("total_count") or headers.get("Total_count") or 0)
    pages = (total + PER_PAGE - 1) // PER_PAGE if total else 0
    return total, pages


def fetch_page(page):
    path = os.path.join(PAGE_DIR, f"page-{page:04d}.json")
    if os.path.exists(path) and os.path.getsize(path) > 2:
        return "skip"
    data, _ = get_json(f"{API}/issues?state=all&per_page={PER_PAGE}&page={page}")
    if data is None:
        data = []
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    return f"ok:{len(data)}"


def pr_total_pages(api=API):
    _, headers = get_json(f"{api}/pulls?state=all&per_page={PER_PAGE}&page=1")
    total = int(headers.get("total_count") or headers.get("Total_count") or 0)
    return total, (total + PER_PAGE - 1) // PER_PAGE if total else 0


def fetch_pr_page(page, api=API, pr_dir=PR_DIR):
    path = os.path.join(pr_dir, f"page-{page:04d}.json")
    if os.path.exists(path) and os.path.getsize(path) > 2:
        return "skip"
    data, _ = get_json(f"{api}/pulls?state=all&per_page={PER_PAGE}&page={page}")
    if data is None:
        data = []
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    return f"ok:{len(data)}"


def merge_pulls(pr_dir=PR_DIR, out=PULLS_JSONL):
    """分页文件合并成 jsonl，按 PR number 去重。"""
    seen = {}
    for name in sorted(os.listdir(pr_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(pr_dir, name), encoding="utf-8") as f:
            for item in json.load(f):
                n = item.get("number")
                if n is not None:
                    seen[n] = item
    with open(out, "w", encoding="utf-8") as f:
        for n in sorted(seen):
            f.write(json.dumps(seen[n], ensure_ascii=False) + "\n")
    log(f"合并 {len(seen)} 个 PR -> {out}")
    return len(seen)


def fetch_linked(num):
    """issue 详情页的「关联 PR」字段——列表接口不返回，必须单独取。"""
    path = os.path.join(LINK_DIR, f"{num}.json")
    if os.path.exists(path) and os.path.getsize(path) > 1:
        return "skip"
    data, _ = get_json(f"{API}/issues/{num}/pull_requests")
    if data is None:
        data = []
    # repo 必须留下来：openGauss 分 openGauss-server / Plugin / docs 三个仓，PR 号各自独立，
    # 只记号码就没法回查——拿 Plugin 的号去 server 仓会查到完全无关的同号 PR。
    # 踩过两次：一次 38 条根因里 12 条错，一次 231 个修复 diff 里 79 个抓错。
    # 2026-09 之前抓的存量数据没有这两个字段，消费侧遇到 repo 为空**必须报错停下，
    # 不能默认成 server 仓**，只能靠标题回 pulls.jsonl / pulls_plugin.jsonl 比对来定位。
    slim = [{"number": x.get("number"), "state": x.get("state"), "title": x.get("title"),
             "merged_at": x.get("merged_at"), "head": (x.get("head") or {}).get("sha"),
             "html_url": x.get("html_url"),
             "repo": (((x.get("base") or {}).get("repo")) or {}).get("full_name")}
            for x in data] if isinstance(data, list) else []
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False)
    os.replace(tmp, path)
    return f"ok:{len(slim)}"


def run_linked():
    os.makedirs(LINK_DIR, exist_ok=True)
    nums = [json.loads(l)["number"] for l in open(ISSUES_JSONL, encoding="utf-8")]
    log(f"取 {len(nums)} 个 issue 的关联 PR")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_linked, n): n for n in nums}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                fut.result()
            except Exception as e:
                log(f"issue {futs[fut]} 关联 PR 失败：{e}")
            if i % 500 == 0 or i == len(nums):
                log(f"关联 PR 进度 {i}/{len(nums)}")


def run_pulls(repo=REPO, pr_dir=PR_DIR, out=PULLS_JSONL):
    api = f"https://api.gitcode.com/api/v5/repos/{repo}"
    os.makedirs(pr_dir, exist_ok=True)
    total, pages = pr_total_pages(api)
    log(f"{repo} PR 总量 {total}，共 {pages} 页（per_page={PER_PAGE}）")
    todo = list(range(1, pages + 1))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_pr_page, p, api, pr_dir): p for p in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            p = futs[fut]
            try:
                fut.result()
            except Exception as e:
                log(f"PR 页 {p} 失败：{e}")
            if i % 10 == 0 or i == len(todo):
                log(f"PR 列表进度 {i}/{len(todo)}")
    merge_pulls(pr_dir, out)


def run_issues():
    total, pages = total_pages()
    log(f"issue 总量 {total}，共 {pages} 页（per_page={PER_PAGE}）")
    todo = list(range(1, pages + 1))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_page, p): p for p in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            p = futs[fut]
            try:
                fut.result()
            except Exception as e:
                log(f"页 {p} 失败：{e}")
            if i % 10 == 0 or i == len(todo):
                log(f"列表进度 {i}/{len(todo)}")
    merge_issues()


def merge_issues():
    """把分页文件合并成一个 jsonl，按 issue number 去重。"""
    seen = {}
    for name in sorted(os.listdir(PAGE_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(PAGE_DIR, name), encoding="utf-8") as f:
            for item in json.load(f):
                seen[str(item.get("number"))] = item
    with open(ISSUES_JSONL, "w", encoding="utf-8") as f:
        for num in sorted(seen, key=lambda x: (len(x), x)):
            f.write(json.dumps(seen[num], ensure_ascii=False) + "\n")
    log(f"合并完成：{len(seen)} 条 → {ISSUES_JSONL}")
    return seen


def _section(body, name):
    import re

    m = re.search(
        r"#{1,4}\s*" + re.escape(name) + r"\s*\n(.*?)(?=\n#{1,4}\s|\Z)",
        body or "",
        re.S,
    )
    return m.group(1).strip() if m else ""


def _priority(issue):
    """越小越先抓。让高价值 issue 的评论（ground truth 所在）先落盘，
    长尾在后台慢慢补，避免限流把交付卡死。"""
    import re

    body = issue.get("body") or ""
    resolved = (issue.get("issue_state_detail") or {}).get("title") in {"已验收", "已完成"}
    is_bug = issue.get("issue_type") == "缺陷"
    if not is_bug:
        return 9
    has_tmpl = bool(_section(body, "操作步骤")) and bool(_section(body, "预期输出"))
    has_sql = bool(
        re.search(
            r"\b(select |insert into|create table|explain|update |delete from|"
            r"alter table|copy |analyze |vacuum|gs_dump|gs_ctl|gsql)\b",
            body,
            re.I,
        )
    )
    if has_tmpl:
        return 0 if resolved else 4
    if has_sql:
        return 1 if resolved else 5
    return 2 if resolved else 6


def load_issue_numbers(prioritized=True):
    issues = []
    with open(ISSUES_JSONL, encoding="utf-8") as f:
        for line in f:
            issues.append(json.loads(line))
    if prioritized:
        issues.sort(key=lambda r: (_priority(r), -int(r.get("number") or 0)))
    return [str(r["number"]) for r in issues]


def fetch_comments(number):
    path = os.path.join(CMT_DIR, f"{number}.json")
    if os.path.exists(path) and os.path.getsize(path) > 1:
        return "skip"
    out = []
    page = 1
    while True:
        data, _ = get_json(
            f"{API}/issues/{number}/comments?per_page={PER_PAGE}&page={page}"
        )
        if not data:
            break
        out.extend(data)
        if len(data) < PER_PAGE:
            break
        page += 1
        if page > 20:  # 兜底，单 issue 不可能有 2000 条评论
            break
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, path)
    return f"ok:{len(out)}"


def run_comments():
    nums = load_issue_numbers()
    log(f"准备拉评论：{len(nums)} 个 issue")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_comments, n): n for n in nums}
        for i, fut in enumerate(as_completed(futs), 1):
            n = futs[fut]
            try:
                fut.result()
            except Exception as e:
                log(f"issue {n} 评论失败：{e}")
            if i % 200 == 0 or i == len(nums):
                log(f"评论进度 {i}/{len(nums)}")
    log("评论抓取完成")


if __name__ == "__main__":
    os.makedirs(PAGE_DIR, exist_ok=True)
    os.makedirs(CMT_DIR, exist_ok=True)
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    known = ("issues", "comments", "linked", "pulls", "pulls-plugin", "all")
    if mode not in known:
        sys.exit(f"未知子命令 {mode}，可选 {known}")
    # 顺序有依赖：comments / linked 都要先有 issues.jsonl
    if mode in ("issues", "all"):
        run_issues()
    if mode in ("comments", "all"):
        run_comments()
    if mode in ("linked", "all"):
        run_linked()
    if mode in ("pulls", "all"):
        run_pulls()
    if mode in ("pulls-plugin", "all"):
        run_pulls(PLUGIN_REPO, os.path.join(RAW, "pulls_plugin"), os.path.join(RAW, "pulls_plugin.jsonl"))
