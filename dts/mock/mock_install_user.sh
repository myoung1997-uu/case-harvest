#!/usr/bin/env bash
# mock 用户安装步:落"实例"文件。存在 fail_flag 时模拟半截失败(留垃圾、吃掉旗子)
VAR="$1"; B="$2"
mkdir -p "$VAR/counters"
echo x >> "$VAR/counters/user_runs"
if [ -f "$VAR/fail_flag" ]; then
  rm -f "$VAR/fail_flag"
  echo "half-install garbage of $B" > "$VAR/slot/garbage"
  echo "mock install failed on purpose" >&2
  exit 1
fi
printf 'GaussDB (GaussDB %s build %s) mock instance\n' "$B" "$B" > "$VAR/slot/instance"
