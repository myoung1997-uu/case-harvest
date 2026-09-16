#!/usr/bin/env python3
"""每轮统计：六桶口径报数（阶段 8 第一步，manifest 统计字段的唯一数据源）。

口径（六桶 + 未跑完 加总 = 本轮候选数，账面自洽）：
  可复现            任一版本判 yes，且不带 NON_DEFECT / EVIDENCE_SUSPECT=yes / ISSUE_OPEN=yes
  负样本            有 yes 但 NON_DEFECT（社区判已取消）
  存疑              有 yes 但 EVIDENCE_SUSPECT=yes（优先级在负样本之后）
  未定论            有 yes 但 ISSUE_OPEN=yes（merged_pr 属已定论，算可复现）
  不可复现-脚本     判定含 construct_fail 或 script_bug 非空；或材料没造出来（无 constructed/<n>/，
                    缺扩展/转构造队列都归这里——都是我们脚本侧没做到位，不是缺陷不重现）
  不可复现-引擎     判定过、无 yes、无 construct_fail（本地再细分 all_no / missing_dep / uncertain）
  未跑完            材料在、但没有判读（跑批或判读没走到）——只告警，不入桶

标记推导单源在 promote.derive_flags；已沉淀的以 cases/OG-<n>/case.env 为准。
写 $HARVEST_HOME/rounds/<N>.stats.json（feed.py 只消费这份文件，不重算）。

用法：python3 stats.py [--round N] [--cases DIR]（默认最新一轮）
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import promote  # noqa: E402

BUCKETS = ("reproducible", "nonrepro_script", "nonrepro_other", "negative", "suspect", "undecided")
# manifest 的 repro_stats 六键与 BUCKETS 同名；unfinished 不上报


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_round(home, n=None):
    rounds = read_jsonl(os.path.join(home, "rounds.jsonl"))
    if not rounds:
        sys.exit("没有 rounds.jsonl——先跑 pick.py（每跑一次 pick 记一轮）")
    if n is None:
        return rounds[-1]
    for r in rounds:
        if r.get("round") == n:
            return r
    sys.exit(f"rounds.jsonl 里没有第 {n} 轮（现有：r{rounds[0]['round']}~r{rounds[-1]['round']}）")


def case_env_flags(cases_dir, n):
    """cases/OG-<n>/case.env 里的排除标记。文件不存在返回 None（未沉淀），存在则返回 dict（可为空=干净）。"""
    p = os.path.join(cases_dir, f"OG-{n}", "case.env")
    if not os.path.exists(p):
        return None
    flags = {}
    for line in open(p, encoding="utf-8"):
        m = re.match(r"(NON_DEFECT|EVIDENCE_SUSPECT|ISSUE_OPEN)=(\S*)", line.strip())
        if m:
            flags[m.group(1)] = m.group(2)
    return flags


def issues_for(home, needed):
    """从 raw/issues.jsonl 只挑需要的 issue（一遍线性扫，不整表进内存）。"""
    out = {}
    p = os.path.join(home, "raw", "issues.jsonl")
    if not os.path.exists(p) or not needed:
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            it = json.loads(line)
            n = str(it.get("number"))
            if n in needed:
                out[n] = it
                if len(out) == len(needed):
                    break
    return out


def compute(home, cases_dir, rec):
    """返回 (buckets, ids, detail)。buckets 含 unfinished；ids[bucket] 是 id 列表。"""
    entries = []
    for n in rec["candidates"]:
        jp = os.path.join(home, "judged", f"{n}.json")
        j = json.load(open(jp, encoding="utf-8")) if os.path.exists(jp) else None
        entries.append((n, j, case_env_flags(cases_dir, n)))

    needed = set()
    for n, j, env in entries:
        if j and any(v.get("verdict") == "yes" for v in (j.get("per_version") or {}).values()) and env is None:
            needed.add(n)
    issues = issues_for(home, needed)

    ids = defaultdict(list)
    detail_nonrepro_other = defaultdict(int)
    for n, j, env in entries:
        if j is None:
            if os.path.exists(os.path.join(home, "constructed", n, "meta.json")):
                ids["unfinished"].append(n)          # 材料在、没判读——跑批/判读没走到
            else:
                ids["nonrepro_script"].append(n)     # 材料没造出来（缺扩展/转构造队列）
            continue
        verdicts = [v.get("verdict") for v in (j.get("per_version") or {}).values()]
        if "yes" in verdicts:
            flags = env if env is not None else promote.derive_flags(j, issues.get(n, {}))
            if flags.get("NON_DEFECT"):
                ids["negative"].append(n)
            elif flags.get("EVIDENCE_SUSPECT") == "yes":
                ids["suspect"].append(n)
            elif flags.get("ISSUE_OPEN") == "yes":
                ids["undecided"].append(n)
            else:
                ids["reproducible"].append(n)
        elif "construct_fail" in verdicts or (j.get("script_bug") or "").strip():
            ids["nonrepro_script"].append(n)
        else:
            ids["nonrepro_other"].append(n)
            if not verdicts:
                detail_nonrepro_other["uncertain"] += 1
            elif all(v == "no" for v in verdicts):
                detail_nonrepro_other["all_no"] += 1
            elif "missing_dep" in verdicts and all(v in ("no", "missing_dep") for v in verdicts):
                detail_nonrepro_other["missing_dep"] += 1
            else:
                detail_nonrepro_other["uncertain"] += 1
    buckets = {b: len(ids[b]) for b in list(BUCKETS) + ["unfinished"]}
    return buckets, dict(ids), dict(detail_nonrepro_other)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=None, help="默认最新一轮")
    ap.add_argument("--cases", default=None, help="默认 $HARVEST_HOME/cases")
    a = ap.parse_args()
    home = common.home()
    cases_dir = a.cases or os.path.join(home, "cases")
    rec = load_round(home, a.round)
    buckets, ids, detail = compute(home, cases_dir, rec)

    total = len(rec["candidates"])
    funnel = rec.get("funnel") or {}
    print(f"轮次 r{rec['round']}（{rec.get('at', '?')}）  筛选问题范围 {funnel.get('全量', '?')}  条件过滤后 {total}")
    print(f"  可复现          {buckets['reproducible']}")
    print(f"  不可复现        {buckets['nonrepro_script'] + buckets['nonrepro_other']}"
          f"（脚本 {buckets['nonrepro_script']} / 引擎 {buckets['nonrepro_other']}；"
          f"引擎细分 all_no {detail.get('all_no', 0)} / missing_dep {detail.get('missing_dep', 0)} /"
          f" uncertain {detail.get('uncertain', 0)}）")
    print(f"  负样本          {buckets['negative']}   存疑 {buckets['suspect']}   未定论 {buckets['undecided']}")
    checked = sum(buckets.values())
    if checked != total:
        print(f"⚠ 六桶+未跑完 = {checked} ≠ 候选 {total}——先查数据再报数")
    if buckets["unfinished"]:
        print(f"⚠ 未跑完 {buckets['unfinished']} 条（材料在、没判读）——先查环境/判读，别当不复现："
              f"{' '.join(ids['unfinished'][:10])}{' …' if len(ids['unfinished']) > 10 else ''}")

    os.makedirs(os.path.join(home, "rounds"), exist_ok=True)
    stats = {
        "round": rec["round"], "at": rec.get("at"),
        "scope": {"total_issues": funnel.get("全量"), "filtered_issues": total},
        "buckets": buckets,
        "detail_nonrepro_other": detail,
        "ids": ids,
    }
    out = os.path.join(home, "rounds", f"{rec['round']}.stats.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
