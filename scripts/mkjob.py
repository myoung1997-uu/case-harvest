#!/usr/bin/env python3
"""把 constructed/ 里可跑的材料排成任务清单。

- jobs/sql.txt    `<n> <db> <target>`   纯 SQL，交给 run_batch.sh 并行跑
- jobs/shell.txt  `<n> <db> <target>`   带 tool.sh/pre.sh 的、要重启的，交给 run_shell.sh 串行跑
target = all 或实例 label（meta.json 的 target 字段，比如只有某版本有 shark）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

home = common.home()
root = os.path.join(home, "constructed")
sql, sh = [], []
for n in sorted(os.listdir(root), key=lambda x: (len(x), x)):
    mp = os.path.join(root, n, "meta.json")
    if not os.path.exists(mp):
        continue
    try:
        m = json.load(open(mp, encoding="utf-8"))
    except ValueError:
        print(f"⚠ {mp} 不是合法 JSON，跳过")
        continue
    if not m.get("constructible"):
        continue
    db = m.get("db") or "bench"
    if db not in common.DB_COMPAT:
        print(f"⚠ OG-{n} db={db} 不认识，只许 {sorted(common.DB_COMPAT)}")
        continue
    target = m.get("target") or "all"
    d = os.path.join(root, n)
    shell = m.get("needs_shell") or m.get("needs_restart") or any(
        os.path.exists(os.path.join(d, f)) for f in ("tool.sh", "pre.sh", "post.sh"))
    (sh if shell else sql).append(f"{n} {db} {target}")
os.makedirs(os.path.join(home, "jobs"), exist_ok=True)
open(os.path.join(home, "jobs", "sql.txt"), "w").write("\n".join(sql) + ("\n" if sql else ""))
open(os.path.join(home, "jobs", "shell.txt"), "w").write("\n".join(sh) + ("\n" if sh else ""))
print(f"SQL 批 {len(sql)} 条，shell 批 {len(sh)} 条 → {home}/jobs/")
