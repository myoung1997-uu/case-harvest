#!/usr/bin/env python3
"""dts/extract —— 中间格式 issue-raw.jsonl → 归一记录 + unmapped 账(零静默丢)。G0 版。

输入格式(probe 的 dump 经 parse_dump 归成的中间格式,字段不确定的原文一律放 raw_blob):
    {"id":..., "title":..., "body":..., "fields": {"B版本": ...}, "attachments": [{"name":...}]}

输出:每行一条 {id, engine, lane, form, form_evidence, version, title, sql, grtmgr_files, attachments}
unmapped:进不了输出的每条都落账,带 reasons 列表;total == out + unmapped 恒成立。
引擎不猜:落盘即 gaussdb(来源=DTS);集中式/分布式=问题描述关键词;版本=B版本字段原样抄。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlextract as sx  # 从主链路整份拷贝来的副本,与本目录共同演进

VERSION_FIELD = "B版本"          # probe 后若字段名不同,只改这里
GRTMR_HINT = re.compile(r"grtmgr|用例文件|测试工程", re.I)
CENTRAL = re.compile(r"集中式")
DISTRIB = re.compile(r"分布式")


def _evidence(body, m):
    i = m.start()
    return body[max(0, i - 20):i + 30].replace("\n", " ").strip()


def lane_of(rec):
    """判型(代码先猜,猜不定进账,不劳模型):附件名命中 > 正文 grtmgr 线索 > 正文有 SQL。"""
    atts = rec.get("attachments") or []
    body = rec.get("body") or ""
    sql = sx.extract(body)
    if any(GRTMR_HINT.search(a.get("name") or "") for a in atts):
        return "grtmgr", sql
    if GRTMR_HINT.search(body) and not sql:
        return "grtmgr", sql
    if sql:
        return "sql", sql
    return "unknown", sql


def process(rec):
    """→ (out_record | None, reasons_list)"""
    body = rec.get("body") or ""
    reasons = []
    lane, sql = lane_of(rec)
    if lane == "unknown":
        reasons.append("lane_unknown")
    md = DISTRIB.search(body)
    mc = CENTRAL.search(body)
    if md:
        form, ev = "distributed", _evidence(body, md)
    elif mc:
        form, ev = "centralized", _evidence(body, mc)
    else:
        form, ev = "unknown", ""
        reasons.append("no_form")
    version = (rec.get("fields") or {}).get(VERSION_FIELD)
    if not version:
        reasons.append("no_version")
    if form == "distributed":
        reasons.append("distributed")  # 单机造不出 → 排除,但必须留账
    if reasons:
        return None, reasons
    out = {
        "id": rec.get("id"),
        "engine": "gaussdb",
        "lane": lane,
        "form": form,
        "form_evidence": ev,
        "version": version,
        "title": rec.get("title"),
        "sql": sql,
        "grtmgr_files": [a.get("name") for a in (rec.get("attachments") or [])] if lane == "grtmgr" else [],
        "attachments": [a.get("name") for a in (rec.get("attachments") or [])],
    }
    return out, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--unmapped", required=True)
    a = ap.parse_args()
    n_total = n_out = 0
    reason_stat = {}
    with open(a.inp, encoding="utf-8") as fi, \
         open(a.out, "w", encoding="utf-8") as fo, \
         open(a.unmapped, "w", encoding="utf-8") as fu:
        for line in fi:
            line = line.strip()
            if not line:
                continue
            n_total += 1
            rec = json.loads(line)
            out, reasons = process(rec)
            if out:
                n_out += 1
                fo.write(json.dumps(out, ensure_ascii=False) + "\n")
            else:
                key = "+".join(sorted(reasons))
                reason_stat[key] = reason_stat.get(key, 0) + 1
                fu.write(json.dumps({"id": rec.get("id"), "reasons": reasons,
                                     "title": rec.get("title")}, ensure_ascii=False) + "\n")
    assert n_total == n_out + sum(reason_stat.values()), "零静默丢校验失败"
    detail = " ".join(f"{k}={v}" for k, v in sorted(reason_stat.items()))
    print(f"total={n_total} out={n_out} unmapped={n_total - n_out}"
          + (f" ({detail})" if detail else ""))


if __name__ == "__main__":
    main()
