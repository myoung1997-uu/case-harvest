#!/usr/bin/env bash
# 崩溃隔离复验（在数据库主机上跑，SQL 批全部结束后）：
# 并行跑批时一条打崩实例，同时在跑的几条也会被记账。这里单实例单会话逐条重跑，跑前跑后各探活。
# 实测 38 个崩溃候选隔离后只剩 17 个是真的。
# 用法: isolate.sh <instances.conf>
# 产出: results-iso/<n>.<label>.log，汇总 results-iso/_verdict.txt（crash-confirmed / not-crashed / pre-dead）
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/lib.sh"
CONF=${1:?用法: isolate.sh <instances.conf>}
H=$(harvest_home); SRC="$H/constructed"; OUT="$H/results-iso"
pgrep -f "worker.sh $CONF" >/dev/null && die "还有 worker 在跑，等 SQL 批结束再复验"
[ -s "$H/results/_crash.txt" ] || { echo "没有崩溃候选"; exit 0; }
mkdir -p "$OUT"; chmod 777 "$OUT"
sort -u "$H/results/_crash.txt" | while read -r id label _; do
  read -r l port osuser unit revive caps build core <<<"$(instance_line "$CONF" "$label")"
  db=$(awk -v i="$id" '$1==i {print $2}' "$H/jobs/sql.txt" | head -1); db=${db:-bench}
  log="$OUT/$id.$label.log"
  {
    if alive "$osuser" "$port" || revive "$osuser" "$port" "$unit" "$revive"; then echo "跑前: 存活"; else
      echo "跑前: 实例不活，结论不可信"; echo "$id $label pre-dead" >> "$OUT/_verdict.txt"; continue; fi
    { preamble "$db"; cat "$SRC/$id/setup.sql"; } > /tmp/harvest.iso.s.sql
    { preamble "$db"; cat "$SRC/$id/workload.sql"; } > /tmp/harvest.iso.w.sql
    chmod 644 /tmp/harvest.iso.*.sql
    timeout "${CASE_TIMEOUT:-120}" bash -c "$(declare -f gsql_as); gsql_as $osuser -d $db -p $port -f /tmp/harvest.iso.s.sql" 2>&1 | tail -5
    echo "--- workload ---"
    timeout "${CASE_TIMEOUT:-120}" bash -c "$(declare -f gsql_as); gsql_as $osuser -d $db -p $port -f /tmp/harvest.iso.w.sql" 2>&1 | tail -30
    sleep 3
    if alive "$osuser" "$port"; then echo "跑后: 存活 → 未崩"; echo "$id $label not-crashed" >> "$OUT/_verdict.txt"
    else
      echo "跑后: 实例已死 → 崩溃确认"; echo "$id $label crash-confirmed" >> "$OUT/_verdict.txt"
      [ "$core" != - ] && ls -t $core 2>/dev/null | head -1
      revive "$osuser" "$port" "$unit" "$revive" && echo "已恢复" || echo "拉不起来"
    fi
  } < /dev/null > "$log" 2>&1
  echo "$id $label → $(tail -1 "$OUT/_verdict.txt" | awk '{print $3}')"
done
