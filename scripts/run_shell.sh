#!/usr/bin/env bash
# shell 批（在数据库主机上、以 root 跑）：需要 gs_dump/改参数重启的，串行在单个实例上跑，别跟 SQL 批混。
# 顺序：pre.sh(root) → setup.sql → tool.sh(实例用户) → workload.sql → post.sh(root，负责还原)
# 脚本里可用的环境变量：PORT DB OSUSER LABEL CASE_DIR
# 用法: run_shell.sh <instances.conf> <label>
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/lib.sh"
CONF=${1:?用法: run_shell.sh <instances.conf> <label>}; LABEL=${2:?label}
H=$(harvest_home); SRC="$H/constructed"; OUT="$H/results"
read -r label port osuser unit revive caps build core <<<"$(instance_line "$CONF" "$LABEL")"
[ -n "${port:-}" ] || die "instances.conf 里没有 $LABEL"
[ "$revive" = wait ] && echo "⚠ $LABEL 是 revive=wait 的实例，需要重启的用例不要在它上面跑"
mkdir -p "$OUT"; chmod 777 "$OUT"; chmod -R a+rX "$SRC" 2>/dev/null
while read -r id db target; do
  [ -z "${id:-}" ] && continue
  [ "$target" = all ] || [ "$target" = "$label" ] || [ "$target" = "$port" ] || continue
  [ -f "$OUT/$id.$label.work.log" ] && continue
  has_cap "$caps" "$(db_cap "$db")" || { echo "SKIP-missing-dep $(db_cap "$db")" > "$OUT/$id.$label.skip"; continue; }
  alive "$osuser" "$port" || revive "$osuser" "$port" "$unit" "$revive" || { echo "$id $label SKIP-instance-down" >> "$OUT/_skip.txt"; continue; }
  export PORT=$port DB=$db OSUSER=$osuser LABEL=$label CASE_DIR=$SRC/$id
  {
    echo "########## OG-$id db=$db @ $label ##########"
    [ -f "$SRC/$id/pre.sh" ] && { echo "----- pre.sh -----"; timeout 300 bash "$SRC/$id/pre.sh" 2>&1 | tail -30; alive "$osuser" "$port" || revive "$osuser" "$port" "$unit" "$revive"; }
    echo "----- setup.sql -----"
    { preamble "$db"; cat "$SRC/$id/setup.sql" 2>/dev/null; } > /tmp/harvest.sh.s.sql
    { preamble "$db"; cat "$SRC/$id/workload.sql" 2>/dev/null; } > /tmp/harvest.sh.w.sql
    chmod 644 /tmp/harvest.sh.*.sql
    timeout 180 bash -c "$(declare -f gsql_as); gsql_as $osuser -d $db -p $port -f /tmp/harvest.sh.s.sql" 2>&1 | tail -20
    [ -f "$SRC/$id/tool.sh" ] && { echo "----- tool.sh -----"; timeout 300 bash -c "$(declare -f run_as); run_as $osuser 'PORT=$port DB=$db bash $SRC/$id/tool.sh'" 2>&1 | tail -60; }
    echo "----- workload.sql -----"
    timeout 180 bash -c "$(declare -f gsql_as); gsql_as $osuser -d $db -p $port -f /tmp/harvest.sh.w.sql" 2>&1 | tail -40
    [ -f "$SRC/$id/post.sh" ] && { echo "----- post.sh -----"; timeout 300 bash "$SRC/$id/post.sh" 2>&1 | tail -15; }
    if alive "$osuser" "$port"; then echo "== 跑完实例存活 =="; else
      echo "== 实例已死 =="; echo "$id $label CRASHED" >> "$OUT/_crash.txt"; revive "$osuser" "$port" "$unit" "$revive" || true; fi
  } < /dev/null > "$OUT/$id.$label.work.log" 2>&1
  echo "$id $label done"
  trim_cores "$core"
done < "$H/jobs/shell.txt"
