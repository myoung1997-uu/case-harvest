#!/usr/bin/env bash
# test_dump.sh —— dump 套件测试:三档 parse_dump + JS 语法 + 与 extract 串链。
# 全部本机 mock,零网络零 sudo。
set -u
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
D="$KIT/mock/dumps"
OUT=/tmp/dts_dump_test.out
PASS=0; FAIL=0

t() { local name="$1" want="$2"; shift 2
  "$@" >"$OUT" 2>&1; local rc=$?
  if [ "$rc" = "$want" ]; then PASS=$((PASS+1)); echo "✓ $name"
  else FAIL=$((FAIL+1)); echo "✗ $name (rc=$rc 期望 $want)"; sed 's/^/    /' "$OUT" | head -8; fi
}
a() { local name="$1"; shift
  if "$@" >/dev/null 2>&1; then PASS=$((PASS+1)); echo "✓ $name"
  else FAIL=$((FAIL+1)); echo "✗ $name"; fi
}
py() { python3 - "$@" <<'PY'
import sys
exec(sys.argv[1])
PY
}

echo "== D1 A 档:自动找最大对象数组 =="
t "D1 parse a-list" 0 python3 "$KIT/parse_dump.py" --in "$D/a-list.json" --out /tmp/a1.jsonl --unmapped /tmp/a1.log
a "D1 出 2 条" py 'import json,sys; rs=[json.loads(l) for l in open("/tmp/a1.jsonl")]; assert len(rs)==2, len(rs)' arg
a "D1 id/B版本/body 抽对" py 'import json; rs=[json.loads(l) for l in open("/tmp/a1.jsonl")]; r=rs[0]; assert r["id"]=="DTS-301" and r["fields"]["B版本"]=="GaussDB-505.3.0" and "create table" in r["body"], r' arg
a "D1 记录数组路径留痕(_array_path)" py 'import json; r=json.loads(open("/tmp/a1.jsonl").readline()); assert r["_array_path"].endswith("records"), r["_array_path"]' arg

echo "== D2 fieldmap 覆盖默认键名 =="
t "D2 parse a-list2(带 fieldmap)" 0 python3 "$KIT/parse_dump.py" --in "$D/a-list2.json" --out /tmp/a2.jsonl --unmapped /tmp/a2.log --fieldmap "$D/fieldmap-test.json"
a "D2 自定义键名(issueId/subject/detail)抽对" py 'import json; r=json.loads(open("/tmp/a2.jsonl").readline()); assert r["id"]=="DTS-501" and r["title"]=="备份恢复超时" and "gs_probackup" in r["body"], r' arg

echo "== D3 B 档:表格 → 记录 =="
t "D3 parse b-table" 0 python3 "$KIT/parse_dump.py" --in "$D/b-table.json" --out /tmp/b1.jsonl --unmapped /tmp/b1.log
a "D3 出 2 条,id 取自单号列" py 'import json; rs=[json.loads(l) for l in open("/tmp/b1.jsonl")]; assert [r["id"] for r in rs]==["DTS-201","DTS-202"]' arg
a "D3 链接列归一成附件" py 'import json; r=[json.loads(l) for l in open("/tmp/b1.jsonl")][0]; assert r["attachments"] and r["attachments"][0]["name"]=="case_grtmgr_201.py" and r["attachments"][0]["link"], r' arg
a "D3 B版本进了 fields" py 'import json; r=[json.loads(l) for l in open("/tmp/b1.jsonl")][0]; assert r["fields"]["B版本"]=="GaussDB-505.3.0"' arg

echo "== D4 C 档:HTML → 记录(原文进 raw_blob) =="
t "D4 parse c-detail" 0 python3 "$KIT/parse_dump.py" --in "$D/c-detail.html" --out /tmp/c1.jsonl --unmapped /tmp/c1.log
a "D4 出 1 条,标题取自 title" py 'import json; r=json.loads(open("/tmp/c1.jsonl").readline()); assert r["id"]=="c-detail" and r["title"].startswith("DTS-401"), r' arg
a "D4 正文剥标签后含 SQL 与集中式" py 'import json; r=json.loads(open("/tmp/c1.jsonl").readline()); assert "create table p1" in r["body"] and "集中式" in r["body"]' arg
a "D4 原文进 raw_blob" py 'import json; r=json.loads(open("/tmp/c1.jsonl").readline()); assert "<html" in r.get("raw_blob","")' arg

echo "== D5 坏 dump 落账不静默 =="
t "D5 parse a-bad" 0 python3 "$KIT/parse_dump.py" --in "$D/a-bad.json" --out /tmp/bad.jsonl --unmapped /tmp/bad.log
a "D5 零记录但有账" py 'import json; assert open("/tmp/bad.jsonl").read()=="" and json.loads(open("/tmp/bad.log").readline())["reason"]=="no_records_found"' arg

echo "== D6 串链:parse 全量 → extract(零静默丢贯通) =="
t "D6 parse 全部夹具" 0 python3 "$KIT/parse_dump.py" --in "$D" --out /tmp/all.jsonl --unmapped /tmp/all.log
t "D6 extract 吃 parse 产物" 0 python3 "$KIT/extract.py" --in /tmp/all.jsonl --out /tmp/rec2.jsonl --unmapped /tmp/unm2.log
if grep -q "total=6 out=3 unmapped=3" "$OUT"; then PASS=$((PASS+1)); echo "✓ D6 链路计数 total=6 out=3 unmapped=3"
else FAIL=$((FAIL+1)); echo "✗ D6 链路计数"; cat "$OUT"; fi
a "D6 A档SQL型出线" py 'import json; rs={json.loads(l)["id"]:json.loads(l) for l in open("/tmp/rec2.jsonl")}; assert rs["DTS-301"]["lane"]=="sql" and rs["DTS-301"]["form"]=="centralized"' arg
a "D6 B档附件grtmgr型出线" py 'import json; rs={json.loads(l)["id"]:json.loads(l) for l in open("/tmp/rec2.jsonl")}; assert rs["DTS-201"]["lane"]=="grtmgr" and rs["DTS-201"]["grtmgr_files"]==["case_grtmgr_201.py"]' arg
a "D6 C档因无版本字段落账(诚实)" py 'import json; u={json.loads(l)["id"]:json.loads(l)["reasons"] for l in open("/tmp/unm2.log")}; assert "no_version" in u["c-detail"], u' arg

echo "== D7 JS 片段语法检查 =="
if command -v node >/dev/null 2>&1; then
  ok=1
  for f in "$KIT"/probe/dump-*.js; do
    node --check "$f" 2>"$OUT" || { ok=0; echo "    $f:"; cat "$OUT"; }
  done
  if [ "$ok" = 1 ]; then PASS=$((PASS+1)); echo "✓ D7 三段 JS 语法全过(node --check)"
  else FAIL=$((FAIL+1)); echo "✗ D7 JS 语法"; fi
else
  echo "⚠ D7 本机无 node,跳过 JS 语法检查(进黄区前自查)"
fi

echo "=========================================="
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ] && echo "dump 套件测试全绿" || echo "dump 套件未过,修完重跑"
exit "$FAIL"
