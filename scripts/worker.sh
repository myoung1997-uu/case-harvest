#!/usr/bin/env bash
# 单个 worker，由 run_batch.sh 拉起。用法: worker.sh <conf> <label> <w> <W>
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/lib.sh"
CONF=$1; LABEL=$2; WI=$3; W=$4
H=$(harvest_home); SRC="$H/constructed"; OUT="$H/results"
read -r label port osuser unit revive caps build core <<<"$(instance_line "$CONF" "$LABEL")"
[ -n "${port:-}" ] || die "instances.conf 里没有 $LABEL"
TMP="/tmp/harvest.$port.$WI"   # ★ 临时文件名必须带 worker 序号，否则并发 worker 互相覆盖
i=-1; n=0
while read -r id db target; do
  i=$((i+1)); [ $((i % W)) -eq "$WI" ] || continue
  [ -z "${id:-}" ] && continue
  [ "$target" = all ] || [ "$target" = "$label" ] || [ "$target" = "$port" ] || continue
  [ -f "$OUT/$id.$label.work.log" ] || [ -f "$OUT/$id.$label.skip" ] && continue
  cap=$(db_cap "$db")
  if ! has_cap "$caps" "$cap"; then
    echo "SKIP-missing-dep $cap" > "$OUT/$id.$label.skip"; continue   # 判「缺依赖」，不判「不复现」
  fi
  if ! alive "$osuser" "$port" && ! revive "$osuser" "$port" "$unit" "$revive"; then
    # 拉不起来只跳过这一条，绝不 break——踩过一次一条用例打挂实例后剩下 61 条全没跑
    echo "$id $label SKIP-instance-down" >> "$OUT/_skip.txt"; continue
  fi
  { preamble "$db"; cat "$SRC/$id/setup.sql" 2>/dev/null; } > "$TMP.s.sql"
  { preamble "$db"; cat "$SRC/$id/workload.sql" 2>/dev/null; } > "$TMP.w.sql"
  chmod 644 "$TMP".*.sql
  timeout "${CASE_TIMEOUT:-120}" bash -c "$(declare -f gsql_as); gsql_as $osuser -d $db -p $port -f $TMP.s.sql" \
    > "$OUT/$id.$label.setup.log" 2>&1
  timeout "${CASE_TIMEOUT:-120}" bash -c "$(declare -f gsql_as); gsql_as $osuser -d $db -p $port -f $TMP.w.sql" \
    > "$OUT/$id.$label.work.log" 2>&1
  # 「压根没跑」和「跑通没报错」在日志里长得一样（库连不上、脚本被吞）。
  # preamble 开了 \timing，只要有一条语句真执行过就会有 Time: 行；一行都没有 = 没跑到
  if ! grep -q '^Time: ' "$OUT/$id.$label.work.log"; then
    echo "$id $label noexec bytes=$(wc -c < "$OUT/$id.$label.work.log" | tr -d ' ')" >> "$OUT/_noexec.txt"
  fi
  if ! alive "$osuser" "$port"; then
    echo "$id $label CRASHED" >> "$OUT/_crash.txt"   # 并行下归属不可信，要 isolate.sh 复验
    revive "$osuser" "$port" "$unit" "$revive" || true
  fi
  echo "$id done" >> "$OUT/_rc.$label.txt"
  n=$((n+1)); [ $((n % 20)) -eq 0 ] && trim_cores "$core"
  sleep "${CASE_GAP:-30}"   # 用例间隔:等 undo/autovacuum 回收上一条的残留,慢类计时更干净;CASE_GAP=0 可关
done < "$H/jobs/sql.txt"
rm -f "$TMP".*.sql
touch "$OUT/_done.$label.$WI"
echo "$label worker $WI 跑完 $n 条"
