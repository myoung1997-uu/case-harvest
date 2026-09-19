#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复验轮组材：把 opengauss-cases 六件套铺进 HARVEST_HOME/constructed + raw/issues.jsonl。

- 11 条「仅作记录」型（靠 reproduce.sh 专用 runner 驱动）如实排除，打印清单
- 特殊库名映射：db_b_test→bench_b，db_d_test→bench_d
- 只拷 setup.sql/workload.sql，meta.json 由本脚本生成（供 mkjob.py 排任务）

环境变量：
  HARVEST_HOME  数据目录，默认 $PWD/harvest（同 common.home()）
  CASES_DIR     用例库 cases 目录，默认 HARVEST_HOME 同级 opengauss-cases/cases
"""
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

HOME = common.home()
CASES = os.environ.get("CASES_DIR") or os.path.join(os.path.dirname(HOME), "opengauss-cases", "cases")
EXCLUDE = {"7390", "7725", "7877", "7928", "7389", "7801", "7317", "7315", "7833", "7378", "7987"}
DB_MAP = {"db_b_test": "bench_b", "db_d_test": "bench_d"}


def env_parse(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Z_0-9]+)=(.*)$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1]
        else:
            v = v.split(" #", 1)[0].split("#", 1)[0].strip() if " #" in v or v.endswith("#") else v
            # 行内注释以「空格#」分隔才剥，避免误伤含 # 的值
            v = re.split(r"\s+#", v)[0].strip()
        out[k] = v
    return out


def main():
    if not os.path.isdir(CASES):
        sys.exit(f"✗ 用例库不存在：{CASES}（用 CASES_DIR 指定）")
    os.makedirs(os.path.join(HOME, "constructed"), exist_ok=True)
    os.makedirs(os.path.join(HOME, "raw"), exist_ok=True)
    staged, excluded, dbcount = [], [], {}
    with open(os.path.join(HOME, "raw", "issues.jsonl"), "w", encoding="utf-8") as ij:
        for d in sorted(os.listdir(CASES)):
            if not d.startswith("OG-"):
                continue
            n = d[3:]
            ce = os.path.join(CASES, d, "case.env")
            if not os.path.exists(ce):
                excluded.append((n, "缺 case.env"))
                continue
            env = env_parse(ce)
            if n in EXCLUDE:
                excluded.append((n, "专用 reproduce.sh runner，通用跑批跑不了"))
                continue
            db = env.get("CASE_DB", "bench").strip()
            db = DB_MAP.get(db, db)
            if db not in ("bench", "bench_b", "bench_pg", "bench_d"):
                excluded.append((n, f"未映射库名 {db}"))
                continue
            src = os.path.join(CASES, d)
            dst = os.path.join(HOME, "constructed", n)
            os.makedirs(dst, exist_ok=True)
            for f in ("setup.sql", "workload.sql"):
                shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
            meta = {
                "id": n,
                "constructible": True,
                "source": "library-reharvest",
                "db": db,
                "target": "all",
                "symptom_class": env.get("SYMPTOM_CLASS", "failure"),
                "needs_shell": False,
                "needs_restart": env.get("NEEDS_RESTART", "no") == "yes",
                "expected": env.get("PHENOMENON_TEMPLATE", ""),
                "how_to_judge": "",
                "notes": "复验轮：材料取自 opengauss-cases 六件套；判读对照库内 ground-truth.md",
                "split_needed": False,
            }
            json.dump(meta, open(os.path.join(dst, "meta.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            ij.write(json.dumps(json.load(open(os.path.join(src, "issues.json"), encoding="utf-8")),
                                ensure_ascii=False, separators=(",", ":")) + "\n")
            staged.append(n)
            dbcount[db] = dbcount.get(db, 0) + 1
    print(f"HARVEST_HOME={HOME}")
    print(f"CASES_DIR={CASES}")
    print(f"staged={len(staged)} excluded={len(excluded)}")
    print("db 分布:", json.dumps(dbcount, ensure_ascii=False))
    for n, why in excluded:
        print(f"  排除 OG-{n}: {why}")


if __name__ == "__main__":
    main()
