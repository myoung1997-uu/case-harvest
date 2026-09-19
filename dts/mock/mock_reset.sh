#!/usr/bin/env bash
# mock 数据目录重置:重建"实例"文件(分钟级恢复,不整装重装)
VAR="$1"; B="$2"
mkdir -p "$VAR/counters"
echo x >> "$VAR/counters/reset_runs"
printf 'GaussDB (GaussDB %s build %s) mock instance\n' "$B" "$B" > "$VAR/slot/instance"
