#!/usr/bin/env python3
"""阶段 5 前置：把跑批结果整理成每条用例一份判读包，交给判读 agent。

读 results/ results-iso/ constructed/，写：
- triage/<n>.json   每个版本的报错摘要、最慢语句、崩溃/隔离复验结论、缺依赖、没跑到，
                    外加日志节选和构造时写的判读依据
- triage/_index.jsonl  一行一条，按「信号强弱」排序，判读从上往下派

这里只做机械整理、不下结论——报错文本对不对得上机制、是不是版本不支持该语法，交给模型读。
用法：python3 xtriage.py [--instances instances.conf]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ERR = re.compile(r"^(?:gsql:[^\n]*?:\d+: )?(ERROR|FATAL|PANIC):\s*(.*)$", re.M)
TIME = re.compile(r"^Time: ([\d.]+) ms", re.M)
LOST = re.compile(r"connection to server was lost|server closed the connection|terminating connection", re.I)
SYNTAX = re.compile(r"syntax error|unterminated|is not ended correctly|name end is not match", re.I)
MISSING = re.compile(r"(relation|table|column|type|function|schema|index|extension|operator|database|package|procedure) "
                     r".*does not exist|could not open extension control file", re.I)
CFG = re.compile(r"must be superuser|permission denied|requires wal_level|not supported|unsupported", re.I)
PREAMBLE = re.compile(r"^(\\set ON_ERROR_STOP off|\\timing on|SET statement_timeout=.*|SET enable_set_variable_b_format=on;|SET)$")


def excerpt(text, limit=6000):
    lines = [l for l in text.splitlines() if not PREAMBLE.match(l.strip())]
    t = "\n".join(lines)
    return t if len(t) <= limit else t[:1500] + "\n…（中略）…\n" + t[-(limit - 1500):]


def read(p):
    return open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", default=None, help="给出则按清单顺序排版本列")
    a = ap.parse_args()
    home = common.home()
    res, iso = os.path.join(home, "results"), os.path.join(home, "results-iso")
    labels = [i["label"] for i in common.read_instances(a.instances)] if a.instances else None

    def pairs(path, col):
        out = {}
        if os.path.exists(path):
            for l in open(path, encoding="utf-8"):
                p = l.split()
                if len(p) > col:
                    out[(p[0], p[1])] = p[col]
        return out

    crash = pairs(os.path.join(res, "_crash.txt"), 2)
    noexec = pairs(os.path.join(res, "_noexec.txt"), 2)
    down = pairs(os.path.join(res, "_skip.txt"), 2)
    iso_v = pairs(os.path.join(iso, "_verdict.txt"), 2)

    ids, seen_labels = {}, set()
    for f in os.listdir(res) if os.path.isdir(res) else []:
        m = re.match(r"^(\d+)\.(.+)\.(work\.log|skip)$", f)
        if m:
            ids.setdefault(m.group(1), set()).add(m.group(2))
            seen_labels.add(m.group(2))
    for (i, l) in down:
        ids.setdefault(i, set()).add(l)
    order = labels or sorted(seen_labels)
    os.makedirs(os.path.join(home, "triage"), exist_ok=True)
    index = []
    for n in sorted(ids, key=lambda x: int(x)):
        cdir = os.path.join(home, "constructed", n)
        meta = json.loads(read(os.path.join(cdir, "meta.json")) or "{}")
        pack = {"id": n, "db": meta.get("db"), "source": meta.get("source"), "expected": meta.get("expected"),
                "how_to_judge": meta.get("how_to_judge"), "expect_txt": read(os.path.join(cdir, "expect.txt")),
                "per_version": {}}
        signal = 0
        for lab in order:
            if lab not in ids[n]:
                continue
            v = {}
            skip = read(os.path.join(res, f"{n}.{lab}.skip"))
            if skip:
                v["mechanical"] = "missing_dep"
                v["skip"] = skip.strip()
            elif (n, lab) in down:
                v["mechanical"] = "instance_down"
            else:
                setup = read(os.path.join(res, f"{n}.{lab}.setup.log")) or ""
                work = read(os.path.join(res, f"{n}.{lab}.work.log")) or ""
                errs = [e[1].strip() for e in ERR.findall(work)]
                cats = {"syntax": 0, "missing": 0, "cfg": 0, "other": 0}
                for e in errs:
                    k = "syntax" if SYNTAX.search(e) else "missing" if MISSING.search(e) else "cfg" if CFG.search(e) else "other"
                    cats[k] += 1
                times = [float(t) for t in TIME.findall(work)]
                v.update({
                    "errors": errs[:8], "n_errors": len(errs), "err_kinds": cats,
                    "setup_errors": [e[1].strip() for e in ERR.findall(setup)][:5],
                    "max_ms": max(times) if times else 0, "n_stmts_timed": len(times),
                    "conn_lost": bool(LOST.search(work)),
                    "crash_in_batch": (n, lab) in crash,
                    "crash_isolated": iso_v.get((n, lab)),     # crash-confirmed / not-crashed / pre-dead / None
                    "noexec": (n, lab) in noexec,
                    "log_excerpt": excerpt(work),
                })
                if v["noexec"] and not v["conn_lost"]:
                    v["mechanical"] = "noexec"            # 没跑到：多半是库/连接/脚本被吞，先修环境
                if v["crash_isolated"] == "crash-confirmed":
                    signal = max(signal, 4)
                elif cats["other"] or cats["cfg"]:
                    signal = max(signal, 3)
                elif v["max_ms"] >= 1000 or v["conn_lost"]:
                    signal = max(signal, 2)
                elif errs:
                    signal = max(signal, 1)
            pack["per_version"][lab] = v
        errsets = {tuple(sorted(set(x.get("errors") or []))) for x in pack["per_version"].values() if "errors" in x}
        pack["cross_version_differs"] = len(errsets) > 1   # 版本间行为不同：缺陷有版本边界，最有价值
        if pack["cross_version_differs"]:
            signal += 1
        json.dump(pack, open(os.path.join(home, "triage", f"{n}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        index.append({"id": n, "signal": signal, "differs": pack["cross_version_differs"],
                      "mech": {k: x.get("mechanical") for k, x in pack["per_version"].items() if x.get("mechanical")}})
    index.sort(key=lambda x: -x["signal"])
    with open(os.path.join(home, "triage", "_index.jsonl"), "w", encoding="utf-8") as f:
        for x in index:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    ne = sum(1 for x in index if "noexec" in x["mech"].values())
    print(f"判读包 {len(index)} 份 → {home}/triage/")
    print(f"  崩溃已隔离确认 {sum(1 for x in index if x['signal'] >= 4)}；版本间行为不同 {sum(x['differs'] for x in index)}；"
          f"无信号 {sum(1 for x in index if x['signal'] == 0)}")
    if ne:
        print(f"  ⚠ {ne} 条有版本一条语句都没执行到（noexec）——先查库在不在、连接、脚本是否被吞，别当「不复现」")
    if crash and not iso_v:
        print("  ⚠ 有崩溃候选但还没隔离复验（isolate.sh），并行下的崩溃归属不可信")


if __name__ == "__main__":
    main()
