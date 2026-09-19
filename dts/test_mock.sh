#!/usr/bin/env bash
# test_mock.sh —— 门0(G0)端到端:provision 状态机 / preflight / extract,全在本机 mock,零网络零 sudo。
# 全绿才算过门 0(标准见 ACCEPTANCE.md)。
set -u
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAR="$KIT/mock/var"
P="$KIT/provision.sh --config $KIT/config.mock.sh"
PF="$KIT/preflight.sh"
OUT=/tmp/dts_test.out
PASS=0; FAIL=0

t() { # t <名称> <期望rc> <命令...>
  local name="$1" want="$2"; shift 2
  "$@" >"$OUT" 2>&1; local rc=$?
  if [ "$rc" = "$want" ]; then PASS=$((PASS+1)); echo "✓ $name"
  else FAIL=$((FAIL+1)); echo "✗ $name (rc=$rc 期望 $want)"; sed 's/^/    /' "$OUT" | head -8; fi
}
a() { # a <名称> <断言命令...>
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then PASS=$((PASS+1)); echo "✓ $name"
  else FAIL=$((FAIL+1)); echo "✗ $name"; fi
}
cnt() { [ -f "$VAR/counters/$1" ] && wc -l < "$VAR/counters/$1" | tr -d ' ' || echo 0; }

rm -rf "$VAR"

echo "== provision:安装/幂等/失败恢复/共享槽 =="
t "S1  全新安装 A → ready" 0 $P install A
a "S1  state=ready" grep -q ready "$VAR/registry/A/state"
a "S1  实例落位且带构建号" grep -q A "$VAR/slot/instance"
a "S1  root 步跑了 1 次" test "$(cnt root_runs)" = 1

t "S2  重跑 install A → 跳过" 0 $P install A
a "S2  安装脚本零重跑(root 仍=1)" test "$(cnt root_runs)" = 1
a "S2  安装脚本零重跑(user 仍=1)" test "$(cnt user_runs)" = 1

t "S3a evict A 让出槽位" 0 $P evict A
a "S3a current 已清" test ! -e "$VAR/registry/current"
a "S3a 槽位清干净" test ! -e "$VAR/slot"

touch "$VAR/fail_flag"
t "S3  注入失败装 B → dirty(42)" 42 $P install B
a "S3  state=dirty" grep -q dirty "$VAR/registry/B/state"
a "S3  半截残留存在" test -f "$VAR/slot/garbage"
t "S3b 重试 B → 清场后装好" 0 $P install B
a "S3b state=ready" grep -q ready "$VAR/registry/B/state"
a "S3b 清场确实跑过" test "$(cnt clean_runs)" -ge 1
a "S3b 半截残留已清" test ! -e "$VAR/slot/garbage"

t "S4  未 evict 装 C → 拒(5)" 5 $P install C
t "S5  evict 非当前占用者 A → 拒(6)" 6 $P evict A
t "S6  evict 当前占用者 B → ok" 0 $P evict B
a "S6  槽位清干净" test ! -e "$VAR/slot"
t "S7  evict 后装 C → ok" 0 $P install C
a "S7  current=C" test "$(cat "$VAR/registry/current")" = C

echo "== provision:并发锁 / 白名单 / reset / dry-run =="
sleep 60 & LOCKPID=$!
mkdir -p "$VAR/.lock"; echo "$LOCKPID" > "$VAR/.lock/pid"
t "S8  并发第二把 → 拒(3)" 3 $P install A
kill "$LOCKPID" 2>/dev/null; rm -rf "$VAR/.lock"

t "S9  白名单外(/etc)拒删" 1 $P rm /etc
t "S9b 槽内子路径可删" 0 $P rm "$VAR/slot/instance"
a "S9b 实例文件已删" test ! -e "$VAR/slot/instance"

t "S10 非ready态reset → 拒(1)" 1 $P reset A
echo CRASHED > "$VAR/slot/instance"
t "S10b 崩溃后 reset C → ok" 0 $P reset C
a "S10b 实例恢复带构建号" grep -q C "$VAR/slot/instance"
a "S10b reset 跑了 1 次" test "$(cnt reset_runs)" = 1

t "S12 dry-run 装 D 被槽位闸拦(5)——预演不越安全闸" 5 $P --dry-run install D
t "S12b dry-run install C(ready) → 跳过且零副作用" 0 $P --dry-run install C
a "S12b C 仍 ready" grep -q ready "$VAR/registry/C/state"
a "S12b D 无状态文件" test ! -e "$VAR/registry/D/state"
a "S12b root 步计数不变(仍=4)" test "$(cnt root_runs)" = 4

t "S11 status 正常" 0 $P status

echo "== preflight =="
t "P1  mock 配置通过(⚠ 允许)" 0 $PF --config "$KIT/config.mock.sh"
sed "s#mock_install_user.sh#/nope/nope.sh#" "$KIT/config.mock.sh" > "$KIT/config.broken.test.sh"
t "P2  坏配置(脚本不存在) → 拒(1)" 1 $PF --config "$KIT/config.broken.test.sh"
rm -f "$KIT/config.broken.test.sh"

echo "== extract:两车道/字段/零静默丢 =="
python3 "$KIT/extract.py" --in "$KIT/mock/fixtures/issue-raw.jsonl" --out /tmp/rec.jsonl --unmapped /tmp/unm.log > "$OUT" 2>&1
if grep -q "total=5 out=2 unmapped=3" "$OUT"; then PASS=$((PASS+1)); echo "✓ E1 计数 total=5 out=2 unmapped=3"
else FAIL=$((FAIL+1)); echo "✗ E1 计数"; cat "$OUT"; fi
if python3 - <<'PY'
import json
r = {x["id"]: x for x in map(json.loads, open("/tmp/rec.jsonl"))}
a = r["DTS-101"]
assert a["lane"] == "sql" and a["form"] == "centralized" and a["version"] == "GaussDB-505.1.0"
assert a["sql"], a
g = r["DTS-103"]
assert g["lane"] == "grtmgr" and g["grtmgr_files"] == ["test_case_grtmgr_103.py"]
PY
then PASS=$((PASS+1)); echo "✓ E2 SQL 型/grtmgr 型字段全抽对"
else FAIL=$((FAIL+1)); echo "✗ E2 字段断言"; fi
if python3 - <<'PY'
import json
u = [json.loads(l) for l in open("/tmp/unm.log")]
ids = {x["id"]: x["reasons"] for x in u}
assert ids.get("DTS-102") and "distributed" in ids["DTS-102"]
assert ids.get("DTS-104") and "no_version" in ids["DTS-104"]
assert ids.get("DTS-105") and "lane_unknown" in ids["DTS-105"]
PY
then PASS=$((PASS+1)); echo "✓ E3 unmapped 三条都有理由(零静默丢)"
else FAIL=$((FAIL+1)); echo "✗ E3 unmapped 断言"; fi

echo "=========================================="
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ] && echo "门0(G0)全绿" || echo "门0 未过,修完重跑"
exit "$FAIL"
