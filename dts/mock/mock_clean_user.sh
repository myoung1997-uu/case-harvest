#!/usr/bin/env bash
# mock 用户清场步:只计数(真正的清理由 CLEAN_PATHS 白名单删除完成)
VAR="$1"
mkdir -p "$VAR/counters"
echo x >> "$VAR/counters/clean_runs"
