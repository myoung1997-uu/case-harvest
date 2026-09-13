#!/usr/bin/env python3
"""打印一个 issue 的完整上下文：正文 + 人类评论 + 关联 PR（标题、状态、【根因分析】【实现方案】栏）。
构造 agent、溯根因 agent 都靠它取材，别让 agent 自己去翻 raw/。

用法：python3 show_issue.py <n> [--max-chars 30000]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import pick  # noqa: E402

BOTS = {"opengauss_bot", "openGauss_bot", "opengauss-bot"}


def pr_sections(body):
    """PR 模板里的根因栏。大量 PR 根因写在【实现方案】里，只看【根因分析】会漏掉几百条。"""
    out = {}
    for name in ("根因分析", "实现方案", "问题描述", "修改内容"):
        m = re.search(r"【" + name + r"】\s*[:：]?\s*(.*?)(?=【|\Z)", body or "", re.S)
        if m and m.group(1).strip():
            out[name] = m.group(1).strip()[:2000]
    return out


def load_prs(raw):
    idx = {}
    for name, repo in (("pulls.jsonl", os.environ.get("OG_REPO", "opengauss/openGauss-server")),
                       ("pulls_plugin.jsonl", os.environ.get("OG_PLUGIN_REPO", "opengauss/Plugin"))):
        p = os.path.join(raw, name)
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                pr = json.loads(line)
                idx[(repo, str(pr.get("number")))] = pr
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n")
    ap.add_argument("--max-chars", type=int, default=30000)
    a = ap.parse_args()
    home = common.home()
    raw = os.path.join(home, "raw")
    issue = None
    for line in open(os.path.join(raw, "issues.jsonl"), encoding="utf-8"):
        it = json.loads(line)
        if str(it.get("number")) == a.n:
            issue = it
            break
    if not issue:
        sys.exit(f"没有 issue {a.n}")
    parts = [f"# issue {a.n}：{(issue.get('title') or '').strip()}",
             f"状态 {issue.get('state')} / {(issue.get('issue_state_detail') or {}).get('title')}  {issue.get('html_url')}",
             "", "## 正文", (issue.get("body") or "").strip()]
    cp = os.path.join(raw, "comments", f"{a.n}.json")
    if os.path.exists(cp):
        human = [c for c in json.load(open(cp, encoding="utf-8")) if (c.get("user") or {}).get("login") not in BOTS]
        parts += ["", f"## 评论（已剥 bot，{len(human)} 条）"]
        for c in human:
            parts.append(f"- [{(c.get('created_at') or '')[:10]} {(c.get('user') or {}).get('login')}] {(c.get('body') or '').strip()}")
    lp = os.path.join(raw, "issue_pulls", f"{a.n}.json")
    prs = json.load(open(lp, encoding="utf-8")) if os.path.exists(lp) else []
    idx = load_prs(raw)
    repo_idx = pick.load_pr_index(raw)
    parts += ["", f"## 关联 PR（{len(prs)} 个）"]
    for p in prs:
        repo = pick.resolve_repo(p, repo_idx)
        parts.append(f"### {repo or '⚠ 仓未定'} #{p.get('number')} [{p.get('state')}] {p.get('title')}  head={p.get('head')}")
        if not repo:
            parts.append("（repo 定不了：同号 PR 在 server/Plugin 仓标题都对不上，可能在 docs 等其他仓。不许按号去 server 仓取正文）")
            continue
        full = idx.get((repo, str(p.get("number"))))
        if not full:
            parts.append("（本地 pulls*.jsonl 没有这个 PR）")
            continue
        if _norm(full.get("title")) != _norm(p.get("title")):
            parts.append(f"⚠ 标题对不上：本地同号 PR 标题是「{full.get('title')}」，别用它的正文")
            continue
        secs = pr_sections(full.get("body"))
        if not secs:
            parts.append("（PR 正文没有模板栏，只能信标题）")
        for k, v in secs.items():
            parts.append(f"【{k}】{v}")
    text = "\n".join(parts)
    print(text if len(text) <= a.max_chars else text[: a.max_chars] + "\n…（截断）")


def _norm(t):
    return re.sub(r"\s+", "", t or "").lower()


if __name__ == "__main__":
    main()
