#!/usr/bin/env python3
"""初筛：从全量 issue 里挑出值得复现的候选，写 $HARVEST_HOME/candidates.jsonl。

口径（两轮实测定下来的，别随手放宽）：
- issueType=缺陷、非 CI 噪声、layer 不是 replication/host
- 标题/结论片段命中「非数据库本体」或「单机造不出的形态」直接排
- 默认要求有已合并的关联 PR：进池前就锁死根因。实测不筛时复现出来也有一成找不到根因，
  筛了之后根因覆盖 100%，构造 agent 还能照 PR 说的边界条件构造
- 不用 dbdogFit/buildCost/groundTruth 分档过滤（关键词分，误杀严重），也不按版本过滤——只拿 score 排序

用法：
    python3 pick.py [--allow-no-pr] [--exclude-cases DIR ...] [--limit N]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import sqlextract as sx  # noqa: E402

EXTERNAL = re.compile(r"docker|容器|镜像|jdbc|odbc|驱动|dbeaver|navicat|编译失败|构建失败|安装失败|安装部署|门禁|流水线|"
                      r"文档链接|官网|avx512|指令集|操作系统|虚拟机|k8s|kubernetes|helm", re.I)
FORM = re.compile(r"\bcore\b|coredump|宕机|主备|备机|备库|容灾|双机|主从|级联备|资源池化|共享存储|switchover|failover|"
                  r"cm_ctl|gs_om|多节点|扩容", re.I)


def _norm_title(t):
    return re.sub(r"\s+", "", (t or "")).lower()


def load_pr_index(raw):
    """{repo: {number: pr}}。关联 PR 数据里 repo 为空时靠它按标题回查。"""
    idx = {}
    for name, repo in (("pulls.jsonl", os.environ.get("OG_REPO", "opengauss/openGauss-server")),
                       ("pulls_plugin.jsonl", os.environ.get("OG_PLUGIN_REPO", "opengauss/Plugin"))):
        p = os.path.join(raw, name)
        if not os.path.exists(p):
            continue
        d = idx.setdefault(repo, {})
        for line in open(p, encoding="utf-8"):
            pr = json.loads(line)
            d[str(pr.get("number"))] = pr
    return idx


def resolve_repo(pr, idx):
    """跨仓同号：Plugin 的号拿去 server 仓查会查到无关 PR。repo 定不下来就标 None，绝不默认。"""
    if pr.get("repo"):
        return pr["repo"]
    hits = [repo for repo, d in idx.items()
            if str(pr.get("number")) in d and _norm_title(d[str(pr["number"])].get("title")) == _norm_title(pr.get("title"))]
    return hits[0] if len(hits) == 1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-no-pr", action="store_true", help="不要求已合并关联 PR（不建议）")
    ap.add_argument("--exclude-cases", action="append", default=[], help="已有用例库目录，里面的 OG-<n> 跳过")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    home = common.home()
    raw = os.path.join(home, "raw")
    cls_path = os.path.join(home, "derived", "classified.jsonl")
    if not os.path.exists(cls_path):
        sys.exit("先跑 classify.py")
    link_dir = os.path.join(raw, "issue_pulls")
    if not os.path.isdir(link_dir) and not a.allow_no_pr:
        sys.exit("没有 raw/issue_pulls/——先跑 fetch.py linked（选题靠它锁根因）")

    have = set()
    for d in a.exclude_cases + [os.path.join(home, "cases")]:
        if os.path.isdir(d):
            have |= {x.split("-", 1)[1] for x in os.listdir(d) if re.match(r"OG-\d+$", x)}
    tried_path = os.path.join(home, "tried.jsonl")
    if os.path.exists(tried_path):
        have |= {str(json.loads(l)["id"]) for l in open(tried_path, encoding="utf-8") if l.strip()}

    bodies = {}
    for line in open(os.path.join(raw, "issues.jsonl"), encoding="utf-8"):
        it = json.loads(line)
        bodies[str(it.get("number"))] = it
    idx = load_pr_index(raw)
    if not idx:
        print("⚠ 没有 pulls.jsonl / pulls_plugin.jsonl，repo 为空的关联 PR 无法定仓（fetch.py pulls / pulls-plugin）")

    funnel = {"全量": 0, "缺陷且非CI": 0, "层可单机": 0, "非外部/非特殊形态": 0, "未收割过": 0, "有已合并PR": 0}
    out = []
    for line in open(cls_path, encoding="utf-8"):
        r = json.loads(line)
        funnel["全量"] += 1
        if r["issueType"] != "缺陷" or r["ciNoise"]:
            continue
        funnel["缺陷且非CI"] += 1
        if r["layer"] in ("replication", "host"):
            continue
        funnel["层可单机"] += 1
        t = (r["title"] or "") + " " + (r["rootCauseSnippet"] or "")
        if EXTERNAL.search(t) or FORM.search(t):
            continue
        funnel["非外部/非特殊形态"] += 1
        n = str(r["number"])
        if n in have:
            continue
        funnel["未收割过"] += 1
        prs = []
        lp = os.path.join(link_dir, f"{n}.json")
        if os.path.exists(lp):
            for pr in json.load(open(lp, encoding="utf-8")):
                repo = resolve_repo(pr, idx)
                prs.append({"repo": repo, "number": pr.get("number"), "title": pr.get("title"),
                            "state": pr.get("state"), "head": pr.get("head"), "merged_at": pr.get("merged_at")})
        merged = [p for p in prs if p["state"] == "merged"]
        if not merged and not a.allow_no_pr:
            continue
        funnel["有已合并PR"] += 1
        it = bodies.get(n, {})
        body = it.get("body") or ""
        stmts = sx.extract(body)
        out.append({
            "id": n, "title": r["title"], "url": r["url"], "state": r["state"], "stateDetail": r["stateDetail"],
            "version": r["version"], "layer": r["layer"], "score": r["score"],
            "has_sql": bool(stmts), "db_guess": sx.guess_db("\n".join(stmts), (it.get("title") or "") + body),
            "non_defect": r["stateDetail"] == "已取消",
            "prs": prs, "repo_unresolved": any(p["repo"] is None for p in merged),
        })
    out.sort(key=lambda x: (-x["has_sql"], -float(x["score"])))
    if a.limit:
        out = out[:a.limit]
    with open(os.path.join(home, "candidates.jsonl"), "w", encoding="utf-8") as f:
        for x in out:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    for k, v in funnel.items():
        print(f"  {k:<14}{v}")
    print(f"候选 {len(out)} 条 → {home}/candidates.jsonl")
    print(f"  正文自带 SQL {sum(x['has_sql'] for x in out)}；社区判已取消(留作负样本) {sum(x['non_defect'] for x in out)}；"
          f"关联 PR 定不了仓 {sum(x['repo_unresolved'] for x in out)}")


if __name__ == "__main__":
    main()
