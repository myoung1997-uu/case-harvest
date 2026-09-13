#!/usr/bin/env python3
"""阶段 3a：正文自带 SQL 的候选，程序化生成复现材料。

读 $HARVEST_HOME/candidates.jsonl，写：
- constructed/<n>/{setup.sql,workload.sql,meta.json,expect.txt}   能直接跑的
- queue/construct.jsonl   正文没 SQL、引用了没建的对象、要跑工具的——交给构造 agent（prompts/construct.md）
- queue/ext.jsonl         依赖实例上没装的扩展——先分流，别浪费一次执行

用法：
    python3 gen_cases.py [--limit N] [--force]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import sqlextract as sx  # noqa: E402


def section(body, name):
    m = re.search(r"(?:#{1,4}\s*" + name + r"|【" + name + r"】)\s*[:：]?\s*\n?(.{0,800}?)(?=\n#{1,4}\s|\n【|\Z)", body, re.S)
    return (m.group(1).strip() if m else "")[:800]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 constructed/<n>")
    a = ap.parse_args()
    home = common.home()
    cands = [json.loads(l) for l in open(os.path.join(home, "candidates.jsonl"), encoding="utf-8")]
    bodies = {}
    for line in open(os.path.join(home, "raw", "issues.jsonl"), encoding="utf-8"):
        it = json.loads(line)
        bodies[str(it.get("number"))] = it
    os.makedirs(os.path.join(home, "queue"), exist_ok=True)
    q_construct = open(os.path.join(home, "queue", "construct.jsonl"), "w", encoding="utf-8")
    q_ext = open(os.path.join(home, "queue", "ext.jsonl"), "w", encoding="utf-8")
    made = {"sql": 0, "construct": 0, "ext": 0, "exists": 0}
    for c in cands[: a.limit or None]:
        n = c["id"]
        it = bodies.get(n, {})
        body = it.get("body") or ""
        d = os.path.join(home, "constructed", n)
        if os.path.exists(os.path.join(d, "meta.json")) and not a.force:
            made["exists"] += 1
            continue
        ext = sx.needs_extension(body)
        if ext:
            q_ext.write(json.dumps({"id": n, "needs": sorted(ext), "title": c["title"]}, ensure_ascii=False) + "\n")
            made["ext"] += 1
            continue
        stmts = sx.extract(body)
        miss = sx.missing_objects(stmts) if stmts else set()
        reason = None
        if not stmts:
            reason = "正文没有可抽取的 SQL"
        elif sx.needs_shell(body):
            reason = "需要跑工具（gs_dump 等），要 tool.sh"
        elif miss:
            reason = f"引用了没建过的对象 {sorted(miss)[:5]}"
        if reason:
            q_construct.write(json.dumps({"id": n, "reason": reason, "title": c["title"], "db_guess": c["db_guess"],
                                          "prs": c["prs"]}, ensure_ascii=False) + "\n")
            made["construct"] += 1
            continue
        os.makedirs(d, exist_ok=True)
        schema = f"c{n}"
        with open(os.path.join(d, "setup.sql"), "w", encoding="utf-8") as f:
            f.write(f"DROP SCHEMA IF EXISTS {schema} CASCADE;\nCREATE SCHEMA {schema};\n")
        with open(os.path.join(d, "workload.sql"), "w", encoding="utf-8") as f:
            f.write(f"-- OG-{n} 正文原样抽取，未拆 setup/workload\nSET search_path={schema},public;\n")
            for s in stmts:
                f.write(s if s.rstrip().endswith((";", "/")) or s.startswith("\\") else s + ";")
                f.write("\n")
            f.write(f"RESET search_path;\nDROP SCHEMA IF EXISTS {schema} CASCADE;\n")
        expected, actual = section(body, "预期输出"), section(body, "实际输出")
        meta = {
            "id": n, "constructible": True, "source": "extracted", "db": c["db_guess"],
            "symptom_class": None, "needs_shell": False, "needs_restart": False, "target": None,
            "expected": expected, "actual": actual,
            "how_to_judge": "对照 expect.txt：实际输出里出现与 issue「实际输出」同一机制的报错/结果即复现；"
                            "出现语法错/对象不存在要先判是不是抽取问题。",
            # 抽出来的是整段正文，沉淀前要人工/agent 拆成 setup（建现场）与 workload（可反复跑的触发语句）
            "split_needed": True,
        }
        json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        with open(os.path.join(d, "expect.txt"), "w", encoding="utf-8") as f:
            f.write(f"# OG-{n} {c['title']}\n# 影响版本 {c['version']}\n\n## 问题描述\n{section(body, '问题描述')}\n\n"
                    f"## 预期输出\n{expected}\n\n## 实际输出\n{actual}\n")
        made["sql"] += 1
    q_construct.close()
    q_ext.close()
    print(f"程序化生成 {made['sql']}，转构造 agent {made['construct']}，缺扩展分流 {made['ext']}，已存在跳过 {made['exists']}")


if __name__ == "__main__":
    main()
