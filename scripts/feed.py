#!/usr/bin/env python3
"""供题组装（阶段 8 第二步）：把本轮可复现用例装进 .dbdog-outbox 发件箱。

不自己推送——dbdog-agent-obs:case-feed 的 Stop hook 扫发件箱自动推，平台拒收的
错误清单会回灌到下一回合（契约见 dbdog-push-kit/SPEC.md，机器可读版
GET /api/testcases/push-spec）。

用法：
    python3 feed.py prep [--round N] [--outbox DIR] [--cases DIR] [--close]
    python3 feed.py check <批次目录>

prep：
  - 读 rounds/<N>.stats.json（没有就提示先跑 stats.py；六桶统计唯一数据源）
  - 选例 = 本轮可复现桶 ∩ cases/OG-<n>（可复现已含「无三标记」，需重启的照常推）
  - 写 manifest.json（统计 + 每例字段，全部从盘上推导）+ 每例空壳目录 + work-queue.jsonl
  - --close 把本轮候选 id 补进 tried.jsonl（封账，下轮 pick 不再捞）
check：本地先拦一道（三脚本在、bash -n 过、没写死连接、有 JUDGE 标记或 exit 2 路径），
  终审在平台。
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import stats as st  # noqa: E402

CASE_NUM_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# 平台必填（caseingest/spec.go SpecFields）；本地照抄一份只为早发现，终审在平台
REQUIRED_CASE_FIELDS = ("case_number", "phenomenon", "engine", "root_cause_fix", "reproduce")


def env_value(case_dir, key):
    """case.env 的 KEY=VALUE 行取值（PHENOMENON_TEMPLATE 带引号）。"""
    p = os.path.join(case_dir, "case.env")
    for line in open(p, encoding="utf-8"):
        m = re.match(rf"{key}=(.*)", line.strip())
        if m:
            v = m.group(1).strip()
            if len(v) >= 2 and v[0] == v[-1] == '"':
                v = v[1:-1]
            return v
    return None


def root_cause_fix(j):
    """【根因】正文 + 来源 /【修复】PR 清单（无内核修复就如实写，不编造）。"""
    rc = j.get("root_cause") or {}
    roots = [(rc.get("text") or "").strip()]
    if (rc.get("source") or "").strip():
        roots.append(f"来源：{rc['source'].strip()}")
    fix = []
    for p in j.get("prs") or []:
        if (p.get("state") or "merged") == "merged":
            fix.append(f"- {p['repo']} PR #{p['number']} {(p.get('title') or '').strip()}"
                       f"（head {p.get('head') or '?'}）")
    if not fix:
        fix.append("- 语料没有已合并的内核修复 PR——如实说明，不编造链接。")
    return "【根因】\n" + "\n".join(x for x in roots if x) + "\n\n【修复】\n" + "\n".join(fix)


def case_entry(home, cases_dir, n, round_no):
    """从 cases/OG-<n> + judged/<n>.json + constructed/<n>/meta.json 推导 manifest 用例字段。"""
    d = os.path.join(cases_dir, f"OG-{n}")
    j = json.load(open(os.path.join(home, "judged", f"{n}.json"), encoding="utf-8"))
    meta_p = os.path.join(home, "constructed", n, "meta.json")
    meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
    db = env_value(d, "CASE_DB")
    compat = dict(common.DB_COMPAT).get(db, (None, None))[0]
    symptom = env_value(d, "SYMPTOM_CLASS") or ""
    timeout = 900 if symptom in ("slowness", "resource") else 600
    return {
        "case_number": f"OG-{n}",
        "phenomenon": env_value(d, "PHENOMENON_TEMPLATE") or "",
        "engine": "opengauss",
        "compatibility": compat,
        "root_cause_fix": root_cause_fix(j),
        "judge_criteria": meta.get("how_to_judge") or "",
        "engine_version": env_value(d, "REPRO_VERSION") or "",
        "commit_id": env_value(d, "REPRO_BUILD") or "",
        "tags": ["case-harvest", f"r{round_no}"],
        "reproduce": {"mode": "script", "setup": "setup.sh", "run": "run.sh", "cleanup": "cleanup.sh"},
        "timeout_seconds": timeout,
    }


def cmd_prep(a):
    home = common.home()
    cases_dir = a.cases or os.path.join(home, "cases")
    rec = st.load_round(home, a.round)
    stats_path = os.path.join(home, "rounds", f"{rec['round']}.stats.json")
    if not os.path.exists(stats_path):
        sys.exit(f"没有 {stats_path}——先跑 stats.py --round {rec['round']}")
    s = json.load(open(stats_path, encoding="utf-8"))
    if s["buckets"]["unfinished"]:
        sys.exit(f"本轮还有 {s['buckets']['unfinished']} 条未跑完——先查环境/判读再供题，别带着窟窿封账")

    picked, missing_case, missing_judged = [], [], []
    for n in s["ids"]["reproducible"]:
        if not os.path.isdir(os.path.join(cases_dir, f"OG-{n}")):
            missing_case.append(n)      # 判了 yes 但没沉淀（promote 没跑或被拒）
        elif not os.path.exists(os.path.join(home, "judged", f"{n}.json")):
            missing_judged.append(n)    # 沉淀了但判读文件没了（不该发生）
        else:
            picked.append(n)
    if missing_case:
        sys.exit(f"以下 {len(missing_case)} 条判了 yes 但 cases/ 里没有（先跑 promote.py）："
                 f"{' '.join('OG-' + x for x in missing_case)}")
    if missing_judged:
        sys.exit(f"以下用例缺 judged/<n>.json（不该发生，先查）：{' '.join(missing_judged)}")

    outbox = a.outbox or os.path.join(os.path.dirname(home.rstrip("/")), ".dbdog-outbox")
    date = (rec.get("at") or datetime.now().isoformat())[:10].replace("-", "")
    batch = os.path.join(outbox, f"harvest-r{rec['round']}-{date}")
    if os.path.exists(batch):
        sys.exit(f"批次目录已存在：{batch}（重跑请先删，或确认不是同一轮重复 prep）")
    os.makedirs(batch)

    manifest = {
        "total_issues": s["scope"]["total_issues"],
        "filtered_issues": s["scope"]["filtered_issues"],
        "repro_stats": {k: s["buckets"][k] for k in st.BUCKETS},
        "cases": [case_entry(home, cases_dir, n, rec["round"]) for n in picked],
    }
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    with open(os.path.join(batch, "work-queue.jsonl"), "w", encoding="utf-8") as f:
        for e, n in zip(manifest["cases"], picked):
            j = json.load(open(os.path.join(home, "judged", f"{n}.json"), encoding="utf-8"))
            f.write(json.dumps({
                "id": n, "case_dir": os.path.join(batch, e["case_number"]),
                "symptom_class": j.get("symptom_class"),
                "material": {"case": os.path.join(cases_dir, f"OG-{n}"),
                             "judged": os.path.join(home, "judged", f"{n}.json"),
                             "constructed": os.path.join(home, "constructed", n)},
            }, ensure_ascii=False) + "\n")
    for e in manifest["cases"]:
        os.makedirs(os.path.join(batch, e["case_number"]), exist_ok=True)

    if a.close:
        tried = os.path.join(home, "tried.jsonl")
        have = {str(json.loads(l)["id"]) for l in open(tried, encoding="utf-8") if l.strip()} \
            if os.path.exists(tried) else set()
        with open(tried, "a", encoding="utf-8") as f:
            for n in rec["candidates"]:
                if n not in have:
                    f.write(json.dumps({"id": n}) + "\n")
        print(f"已封账：{len(rec['candidates'])} 个候选补进 {tried}")

    print(f"批次 {batch}")
    print(f"  manifest：范围 {manifest['total_issues']} / 过滤后 {manifest['filtered_issues']} /"
          f" 可复现 {manifest['repro_stats']['reproducible']} → 用例 {len(manifest['cases'])} 条")
    print("  三脚本由子 agent 按 prompts/feed.md 写进各用例目录；写完跑 check，Stop hook 自动推")


def check_scripts(batch, e):
    """每例三脚本的机械校验，返回问题列表。"""
    problems = []
    d = os.path.join(batch, e["case_number"])
    run = os.path.join(d, "run.sh")
    if not os.path.exists(run):
        return [f"{e['case_number']}: 缺 run.sh"]
    for name in ("setup.sh", "run.sh", "cleanup.sh"):
        p = os.path.join(d, name)
        if not os.path.exists(p):
            if name == "run.sh":
                continue
            problems.append(f"{e['case_number']}: 缺 {name}")
            continue
        r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
        if r.returncode:
            problems.append(f"{e['case_number']}/{name}: 语法错误 {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '?'}")
        text = open(p, encoding="utf-8", errors="replace").read()
        # 连接只许读环境变量：PGHOST= 字面量赋值 / psql·gsql 带 -h/-p 字面量 都是写死连接
        if re.search(r"\b(PGHOST|PGPORT|PGUSER|PGPASSWORD|PGDATABASE)=[^\s$\"']", text):
            problems.append(f"{e['case_number']}/{name}: 连接参数写死了（只许读 PG* 环境变量）")
        if re.search(r"(psql|gsql)\b[^\n]*\s-[hp]\s+[0-9]", text):
            problems.append(f"{e['case_number']}/{name}: psql/gsql 命令行带 -h/-p 字面量")
    run_text = open(run, encoding="utf-8", errors="replace").read()
    if "JUDGE_" not in run_text and "exit 2" not in run_text:
        problems.append(f"{e['case_number']}: run.sh 既没有 JUDGE_ 标记也没有 exit 2（没法判/环境不适用）路径")
    return problems


def cmd_check(a):
    batch = a.batch
    mf = json.load(open(os.path.join(batch, "manifest.json"), encoding="utf-8"))
    problems = []
    for k in ("total_issues", "filtered_issues", "cases"):
        if k not in mf:
            problems.append(f"manifest 缺 {k}")
    rs = mf.get("repro_stats") or {}
    if set(rs) - set(st.BUCKETS):
        problems.append(f"repro_stats 冒出未知键 {set(rs) - set(st.BUCKETS)}")
    if any(v < 0 for v in rs.values()):
        problems.append("repro_stats 有负数")
    six = sum(rs.get(k, 0) for k in st.BUCKETS)
    if rs and six != mf.get("filtered_issues"):
        problems.append(f"repro_stats 六桶之和 {six} ≠ filtered_issues {mf.get('filtered_issues')}")

    numbers = [c["case_number"] for c in mf.get("cases", [])]
    if len(numbers) != len(set(numbers)):
        problems.append("批次内有重号")
    for c in mf.get("cases", []):
        cid = c.get("case_number", "")
        if not CASE_NUM_RE.match(cid) or cid in (".", ".."):
            problems.append(f"用例编号不合规：{cid!r}")
            continue
        for k in REQUIRED_CASE_FIELDS:
            if not c.get(k):
                problems.append(f"{cid}: 缺必填 {k}")
        rep = c.get("reproduce") or {}
        if rep.get("mode") == "script" and not rep.get("run"):
            problems.append(f"{cid}: script 模式缺 reproduce.run")
        problems += check_scripts(batch, c)
    dirs = {x for x in os.listdir(batch) if os.path.isdir(os.path.join(batch, x))}
    if dirs - set(numbers):
        problems.append(f"批次目录多出清单外的用例目录：{sorted(dirs - set(numbers))}")

    for p in problems:
        print(f"✗ {p}")
    if problems:
        sys.exit(f"共 {len(problems)} 个问题——修完重跑 check（平台终审在推送时）")
    print(f"✓ 批次自检通过：{len(numbers)} 条用例，等 Stop hook 推送（case-feed）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("prep", help="组装发件箱批次")
    p1.add_argument("--round", type=int, default=None)
    p1.add_argument("--outbox", default=None, help="默认 $HARVEST_HOME 同级的 .dbdog-outbox/")
    p1.add_argument("--cases", default=None)
    p1.add_argument("--close", action="store_true", help="顺手把本轮候选补进 tried.jsonl（封账）")
    p2 = sub.add_parser("check", help="批次自检")
    p2.add_argument("batch")
    a = ap.parse_args()
    if a.cmd == "prep":
        cmd_prep(a)
    else:
        cmd_check(a)


if __name__ == "__main__":
    main()
