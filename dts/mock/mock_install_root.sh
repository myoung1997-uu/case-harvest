#!/usr/bin/env bash
# mock root 安装步:建槽位目录 + 计数(证明"ready 绝不重跑")
VAR="$1"; B="$2"
mkdir -p "$VAR/slot" "$VAR/counters"
echo x >> "$VAR/counters/root_runs"
echo "root step done for $B" > "$VAR/slot/root_done"
