#!/usr/bin/env bash
# 跑批前体检（在数据库主机上跑）。三件事：
#  1. 实例活着
#  2. bench / bench_b / bench_pg / bench_d 按实例能力建好且连得上
#     ——库不存在时 gsql 连不上和「跑通没报错」在日志里长得一样，会整批记成 clean
#  3. 上一轮留下的全局残留：FOR ALL TABLES 发布（无 replica identity 的表 UPDATE/DELETE 全被拒）、
#     事件触发器（别的用例报 permission denied）、c<数字> 前缀 schema
# 用法: preflight.sh <instances.conf> [--clean]
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/lib.sh"
CONF=${1:?用法: preflight.sh <instances.conf> [--clean]}; CLEAN=${2:-}
bad=0
while read -r label port osuser unit revive caps build core; do
  echo "== $label :$port ($osuser) caps=$caps"
  if ! alive "$osuser" "$port"; then echo "  ✗ 实例不活"; bad=1; continue; fi
  for db in bench bench_b bench_pg bench_d; do
    cap=$(db_cap $db)
    if ! has_cap "$caps" "$cap"; then echo "  - $db 跳过（实例没有 $cap，这类用例在本实例判「缺依赖」）"; continue; fi
    exists=$(gsql_as "$osuser" -d postgres -p "$port" -Atc "select 1 from pg_database where datname='$db'" 2>/dev/null | tail -1)
    if [ "$exists" != 1 ]; then
      gsql_as "$osuser" -d postgres -p "$port" -c "CREATE DATABASE $db DBCOMPATIBILITY '$(db_compat $db)'" >/dev/null 2>&1
      echo "  + 建库 $db"
    fi
    if [ "$(gsql_as "$osuser" -d $db -p "$port" -Atc 'select 1' 2>/dev/null | tail -1)" != 1 ]; then
      echo "  ✗ $db 连不上"; bad=1; continue
    fi
    if [ "$cap" != - ]; then
      n=$(gsql_as "$osuser" -d $db -p "$port" -Atc "select count(*) from pg_extension where extname='$cap'" 2>/dev/null | tail -1)
      [ "$n" = 1 ] || { gsql_as "$osuser" -d $db -p "$port" -c "CREATE EXTENSION IF NOT EXISTS $cap" >/dev/null 2>&1; echo "  ! $db 里补装 $cap（请确认成功）"; }
    fi
    pubs=$(gsql_as "$osuser" -d $db -p "$port" -Atc "select string_agg(pubname,',') from pg_publication where puballtables" 2>/dev/null | tail -1)
    evts=$(gsql_as "$osuser" -d $db -p "$port" -Atc "select string_agg(evtname,',') from pg_event_trigger" 2>/dev/null | tail -1)
    nsps=$(gsql_as "$osuser" -d $db -p "$port" -Atc "select string_agg(nspname,',') from pg_namespace where nspname ~ '^c[0-9]+'" 2>/dev/null | tail -1)
    echo "  ✓ $db  发布(全表)=${pubs:-无}  事件触发器=${evts:-无}  残留schema=$(echo "${nsps:-}" | tr ',' '\n' | grep -c . )"
    if [ "$CLEAN" = --clean ]; then
      for x in $(echo "${pubs:-}" | tr ',' ' '); do gsql_as "$osuser" -d $db -p "$port" -c "DROP PUBLICATION \"$x\"" >/dev/null 2>&1; done
      for x in $(echo "${evts:-}" | tr ',' ' '); do gsql_as "$osuser" -d $db -p "$port" -c "DROP EVENT TRIGGER \"$x\"" >/dev/null 2>&1; done
      for x in $(echo "${nsps:-}" | tr ',' ' '); do gsql_as "$osuser" -d $db -p "$port" -c "DROP SCHEMA \"$x\" CASCADE" >/dev/null 2>&1; done
      [ -n "${pubs:-}${evts:-}${nsps:-}" ] && echo "    已清理"
    elif [ -n "${pubs:-}${evts:-}" ]; then
      echo "    ⚠ 有全局残留，加 --clean 清掉再跑，否则整批会出假缺陷"; bad=1
    fi
  done
done < <(instances "$CONF")
[ $bad = 0 ] && echo "体检通过" || { echo "体检未通过，先处理上面的 ✗/⚠"; exit 1; }
