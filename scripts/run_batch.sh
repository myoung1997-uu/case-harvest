#!/usr/bin/env bash
# SQL 批（在数据库主机上跑）：跑 jobs/sql.txt，后台运行。
# 用法: run_batch.sh <instances.conf> [W=1]
# ★ 强制串行：W 默认且锁定为 1（问题间并行会互相干扰、崩溃归属不可信、慢类计时被污染——2026-09-17 用户定为硬规矩）。
#   确有需要并行时显式 ALLOW_PARALLEL=1 越权，后果自负。
# 进度: wc -l $HARVEST_HOME/results/_rc.*.txt   完成标记: results/_done.<label>.<w>
# 可断点续跑：已有 work.log 的跳过；想重跑某条就删掉它的 results/<n>.<label>.*
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); . "$HERE/lib.sh"
CONF=${1:?用法: run_batch.sh <instances.conf> [W]}; W=${2:-1}
if [ "$W" -gt 1 ] && [ "${ALLOW_PARALLEL:-0}" != "1" ]; then
  die "W=$W 被拒绝：复现必须问题间串行（用户 2026-09-17 硬规矩）。确要并行: ALLOW_PARALLEL=1 $0 $CONF $W"
fi
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
