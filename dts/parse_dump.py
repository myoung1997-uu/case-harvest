#!/usr/bin/env python3
"""dts/parse_dump —— probe 三档 dump 落盘(A 接口/B 表格/C DOM)→ 中间格式 issue-raw.jsonl。

原则:
- 只归一结构,不丢原文:归不动的整块进 raw_blob;每个输入文件要么出记录、要么落 unmapped 账。
- 字段映射:候选键名表 + 可选 --fieldmap 覆盖;认不出的字段原样留在 fields 里。
- A 档响应里"记录数组"在哪不知道 → 自动找"最大的对象数组"(可 --items-path 指定)。

用法:
    python3 parse_dump.py --in <文件|目录|glob...> --out issue-raw.jsonl --unmapped unmapped.log \
        [--items-path data.records] [--fieldmap fieldmap.json]
"""
import argparse
import glob
import json
import os
import sys
from html.parser import HTMLParser

DEFAULT_MAP = {
    "id": ["id", "issue_id", "issueId", "number", "编号", "单号", "问题单号"],
    "title": ["title", "标题", "问题标题", "summary"],
    "body": ["body", "description", "问题描述", "内容", "detail", "详情", "正文", "描述"],
    "attachments": ["attachments", "附件", "files", "用例文件"],
}


# ---------- 通用 ----------
def pick(d, keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k):
            return d[k]
    return None


def norm_atts(v):
    """附件字段归一成 [{"name":..., "link":...}];不认识的形态也保住。"""
    out = []
    if isinstance(v, str):
        out.append({"name": v})
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, str):
                out.append({"name": x})
            elif isinstance(x, dict):
                name = x.get("name") or x.get("filename") or x.get("title") or json.dumps(x, ensure_ascii=False)
                out.append({"name": name, "link": x.get("url") or x.get("link")})
    return out


def largest_obj_array(obj, path=""):
    """递归找最长的"元素全是 dict"的数组 → (长度, 路径, 数组)。"""
    best = (0, "", [])
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            best = (len(obj), path or "$", obj)
        for i, x in enumerate(obj):
            c = largest_obj_array(x, f"{path}[{i}]")
            if c[0] > best[0]:
                best = c
    elif isinstance(obj, dict):
        for k, v in obj.items():
            c = largest_obj_array(v, f"{path}.{k}" if path else k)
            if c[0] > best[0]:
                best = c
    return best


# ---------- HTML(C 档)----------
class _Text(HTMLParser):
    BLOCK = {"p", "div", "tr", "li", "h1", "h2", "h3", "h4", "br", "table", "pre"}

    def __init__(self):
        super().__init__()
        self.parts, self.title = [], ""

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if getattr(self, "_in_title", False):
            self.title += data.strip()
        self.parts.append(data)

    def text(self):
        return "".join(self.parts)


def parse_html(html, stem):
    p = _Text()
    try:
        p.feed(html)
    except Exception as e:  # noqa: BLE001
        return None, f"parse_failed:{e}"
    return {
        "id": stem,
        "title": p.title or stem,
        "body": p.text(),
        "fields": {},
        "raw_blob": html[:200000],
    }, None


# ---------- A 档 / B 档 ----------
def parse_a_item(item, fmap, fallback_id):
    rec = {
        "id": pick(item, fmap["id"]) or fallback_id,
        "title": pick(item, fmap["title"]) or "",
        "body": pick(item, fmap["body"]) or "",
        "fields": dict(item),
        "attachments": norm_atts(pick(item, fmap["attachments"])),
    }
    return rec


def parse_b(env, fmap, stem):
    headers, rows = env.get("headers") or [], env.get("rows") or []
    recs = []
    for i, cells in enumerate(rows):
        fields, atts = {}, []
        for j, (h, c) in enumerate(zip(headers, cells)):
            text = c["text"] if isinstance(c, dict) else str(c)
            link = c.get("link") if isinstance(c, dict) else None
            fields[h or f"col{j}"] = text
            if link:
                atts.append({"name": text or link, "link": link})
        recs.append({
            "id": pick(fields, fmap["id"]) or f"{stem}-{i}",
            "title": pick(fields, fmap["title"]) or "",
            "body": pick(fields, fmap["body"]) or "",
            "fields": fields,
            "attachments": atts,
        })
    return recs


def parse_file(path, fmap, items_path):
    stem = os.path.splitext(os.path.basename(path))[0]
    raw = open(path, encoding="utf-8", errors="replace").read()
    # 非 JSON 一律按 C 档 HTML 处理
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        rec, err = parse_html(raw, stem)
        return ([rec] if rec else []), (err or None), "C"

    if isinstance(data, dict) and data.get("tier") == "B":
        recs = parse_b(data, fmap, stem)
        return recs, (None if recs else "no_records_found"), "B"

    if isinstance(data, dict) and data.get("tier") == "C":
        rec, err = parse_html(data.get("html") or "", stem)
        if rec:
            rec["title"] = data.get("title") or rec["title"]
        return ([rec] if rec else []), (err or None), "C"

    # A 档:信封里 pages[].body / details[].body,逐个找记录数组
    recs = []
    pages = data.get("pages", []) if isinstance(data, dict) else []
    details = data.get("details", []) if isinstance(data, dict) else []
    if not pages and not details and isinstance(data, (list, dict)):
        pages = [{"page": 1, "body": data}]
    for pg in pages:
        body = pg.get("body") if isinstance(pg, dict) else None
        if body is None:
            continue
        if items_path:
            cur = body
            for seg in items_path.split("."):
                cur = (cur or {}).get(seg)
            arr = cur if isinstance(cur, list) else []
            used = items_path
        else:
            n, used, arr = largest_obj_array(body)
            if n == 0:
                arr, used = [], None
        for j, item in enumerate(arr or []):
            r = parse_a_item(item, fmap, f"{stem}-p{pg.get('page')}-{j}")
            r["_array_path"] = used
            recs.append(r)
    for d in details:
        if not isinstance(d, dict) or d.get("body") is None:
            continue
        r = parse_a_item(d["body"] if isinstance(d["body"], dict) else {"raw": d["body"]}, fmap, str(d.get("id")))
        r["detail_of"] = d.get("id")
        recs.append(r)
    return recs, (None if recs else "no_records_found"), "A"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", nargs="+", required=True, help="文件/目录/glob")
    ap.add_argument("--out", required=True)
    ap.add_argument("--unmapped", required=True)
    ap.add_argument("--items-path", default="", help="A 档记录数组路径,如 data.records;缺省自动找最大对象数组")
    ap.add_argument("--fieldmap", default="", help="JSON 文件,覆盖默认候选键 {id|title|body|attachments: [键...]}")
    a = ap.parse_args()

    fmap = {k: list(v) for k, v in DEFAULT_MAP.items()}
    if a.fieldmap:
        fmap.update(json.load(open(a.fieldmap, encoding="utf-8")))

    files = []
    for pat in a.inp:
        if os.path.isdir(pat):
            files += sorted(glob.glob(os.path.join(pat, "*.json"))) + sorted(glob.glob(os.path.join(pat, "*.html")))
        else:
            files += sorted(glob.glob(pat)) or [pat]

    n_files = n_recs = n_unmapped = 0
    with open(a.out, "w", encoding="utf-8") as fo, open(a.unmapped, "w", encoding="utf-8") as fu:
        for f in files:
            if not os.path.isfile(f):
                continue
            n_files += 1
            try:
                recs, err, tier = parse_file(f, fmap, a.items_path)
            except Exception as e:  # noqa: BLE001
                recs, err, tier = [], f"parse_failed:{e}", "?"
            if recs:
                n_recs += len(recs)
                for r in recs:
                    r["_source_file"] = os.path.basename(f)
                    fo.write(json.dumps(r, ensure_ascii=False) + "\n")
            else:
                n_unmapped += 1
                fu.write(json.dumps({"file": os.path.basename(f), "tier": tier,
                                     "reason": err or "no_records_found"}, ensure_ascii=False) + "\n")
    print(f"files={n_files} records={n_recs} unmapped_files={n_unmapped}")


if __name__ == "__main__":
    main()
