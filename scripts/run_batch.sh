#!/usr/bin/env bash
# SQL 批（在数据库主机上跑）：每个实例起 W 个 worker 并行跑 jobs/sql.txt，后台运行。
# 用法: run_batch.sh <instances.conf> [W=3]
# 进度: wc -l $HARVEST_HOME/results/_rc.*.txt   完成标记: results/_done.<label>.<w>
# 可断点续跑：已有 work.log 的跳过；想重跑某条就删掉它的 results/<n>.<label>.*
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/lib.sh"
CONF=${1:?用法: run_batch.sh <instances.conf> [W]}; W=${2:-3}
H=$(harvest_home); OUT="$H/results"
for c in timeout setsid gsql; do command -v $c >/dev/null || [ $c = gsql ] || die "缺命令 $c（在数据库主机的 Linux 上跑）"; done
[ -s "$H/jobs/sql.txt" ] || die "没有 $H/jobs/sql.txt，先跑 mkjob.py"
mkdir -p "$OUT"; chmod 777 "$OUT"
# 实例用户要能读到材料
chmod -R a+rX "$H/constructed" 2>/dev/null
rm -f "$OUT"/_done.* /tmp/.harvest-revive.*.lock 2>/dev/null; rmdir /tmp/.harvest-revive.*.lock 2>/dev/null
n=0
while read -r label _; do
  for w in $(seq 0 $((W-1))); do
    HARVEST_HOME="$H" nohup setsid bash "$HERE/worker.sh" "$CONF" "$label" "$w" "$W" \
      > "$OUT/_worker.$label.$w.log" 2>&1 < /dev/null &
    n=$((n+1))
  done
done < <(instances "$CONF")
echo "已起 $n 个 worker，任务 $(grep -c . "$H/jobs/sql.txt") 条 × 实例数"
