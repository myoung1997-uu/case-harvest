#!/usr/bin/env python3
"""dts/desensitize —— dump 脱壳:只留结构,抹掉内容。带出黄区给外面调 parse/fieldmap 用。

规则(泄露风险主要在大段正文,不在键名/列名/格式):
- 键名全保留(字段映射靠它);短字符串(≤24 字)保留(列名/状态词/单号格式),
  但 URL 的 host 部分替换成 <host>;
- 长字符串(>24 字)→ "[...N 字]";数字保留;bool/null 保留;
- 列表 → 长度 + 首元素骨架;HTML → 只留标签和属性,文本节点变 [N 字]。

用法:
    python3 desensitize.py --in <dump文件|目录> --out skeleton.json
"""
import argparse
import glob
import json
import os
import re

HOST = re.compile(r"https?://[^/\s\"']+")
KEEP_LEN = 24


def mask_str(s):
    s = HOST.sub("<host>", s)
    if len(s) <= KEEP_LEN:
        return s
    return f"[...{len(s)} 字]"


def skel(v, depth=0):
    if depth > 12:
        return "..."
    if isinstance(v, dict):
        return {k: skel(x, depth + 1) for k, x in list(v.items())[:60]}
    if isinstance(v, list):
        return {"_list_len": len(v), "_first": skel(v[0], depth + 1) if v else None}
    if isinstance(v, str):
        return mask_str(v)
    return v


def skel_html(html):
    out, i = [], 0
    for m in re.finditer(r">([^<]+)<", html):
        out.append(html[i:m.start() + 1])
        t = m.group(1).strip()
        out.append(f">[{len(t)} 字]<" if t else "><")
        i = m.end() - 1
    out.append(html[i:])
    s = "".join(out)
    s = HOST.sub("<host>", s)
    s = re.sub(r"([a-zA-Z-]+)=\"([^\"]{25,})\"", r'\1="[...长值]"', s)
    return s[:20000]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    files = []
    for pat in a.inp:
        if os.path.isdir(pat):
            files += sorted(glob.glob(os.path.join(pat, "*.json"))) + sorted(glob.glob(os.path.join(pat, "*.html")))
        else:
            files += sorted(glob.glob(pat)) or [pat]
    result = {}
    for f in files:
        if not os.path.isfile(f):
            continue
        raw = open(f, encoding="utf-8", errors="replace").read()
        name = os.path.basename(f)
        try:
            result[name] = skel(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            result[name] = {"_html_skeleton": skel_html(raw)}
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(result, fo, ensure_ascii=False, indent=1)
    print(f"files={len(result)} → {a.out}")


if __name__ == "__main__":
    main()
