#!/usr/bin/env bash
# provision.sh — GaussDB 版本包下载 + 实例安装状态机。
# 安装脚本自身不幂等 → 幂等由本脚本包办:ready 绝不重跑;失败=dirty,重试先清场;
# 换版本先 evict 当前构建(共享槽);单机互斥锁防并发安装。
#
# 用法: provision.sh [--config FILE] [--dry-run] <install|reset|evict|status|rm> <BUILD|PATH> [--force]
#   install <BUILD>   下载(如需)+清场(如需)+安装阶梯+健康检查 → ready
#   reset  <BUILD>    数据目录重置(仅 ready 态;实例被打崩后的分钟级恢复)
#   evict  <BUILD>    清场并让出共享槽(默认仅限当前占用者;--force 强拆)
#   status            打印注册表
#   rm     <PATH>     删一个路径(仅限 CLEAN_PATHS 白名单内,系统目录一律拒绝)
# 退出码: 0 成功 / 1 一般失败 / 3 锁占用 / 5 槽位被占 / 6 非当前占用者 / 42 安装失败 / 43 健康检查失败 / 44 reset 失败
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.sh"
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) break ;;
  esac
done
CMD="${1:-help}"; shift 2>/dev/null || true

if [ ! -f "$CONFIG" ]; then
  echo "✗ 缺配置 $CONFIG(从 config.example.sh 复制填写)" >&2; exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG"
VAR_HOME="${VAR_HOME:-$SCRIPT_DIR/var}"
REG="$VAR_HOME/registry"

# 绝不许删的路径(前缀判断之外再加精确匹配兜底)
BLACKLIST="/ /bin /boot /dev /etc /home /lib /lib64 /opt /proc /root /run /sbin /sys /tmp /usr /var"

log() { echo "  $*"; }

get_state() { if [ -f "$REG/$1/state" ]; then cat "$REG/$1/state"; else echo absent; fi; }
say_state() { if [ "$DRY" -eq 1 ]; then log "[dry] state($1)=$2"; else mkdir -p "$REG/$1"; echo "$2" > "$REG/$1/state"; fi; }
current_build() { if [ -f "$REG/current" ]; then cat "$REG/current"; fi; }

subst() { # subst <str> <BUILD> <PKG>
  local s="$1"; s="${s//\{BUILD\}/$2}"; s="${s//\{PKG\}/$3}"; printf '%s' "$s"
}

run_cmd() { # run_cmd <root|user|local> <cmd-string>
  local prefix=""
  case "$1" in
    root) prefix="${RUN_AS_ROOT:-}" ;;
    user) prefix="${RUN_AS_USER:-}" ;;
  esac
  echo "  [$1] $2"
  [ "$DRY" -eq 1 ] && return 0
  if [ -n "$prefix" ]; then $prefix bash -c "$2"; else bash -c "$2"; fi
}

run_steps() { # run_steps <as> <数组名> <BUILD> <PKG>
  local as="$1" arr="$2" b="$3" p="$4" c
  eval "local vals=(\"\${$arr[@]:-}\")"
  for c in "${vals[@]}"; do
    [ -n "$c" ] || continue
    if ! run_cmd "$as" "$(subst "$c" "$b" "$p")"; then return 1; fi
  done
}

pkg_path() { # <BUILD>
  local u p
  u="$(subst "$PKG_URL_TEMPLATE" "$1" "")"
  p="$VAR_HOME/pkg/$1/$(basename "$u")"
  printf '%s' "$p"
}

do_download() { # <BUILD> → 成功时 stdout 输出包路径
  local b="$1" u p
  u="$(subst "$PKG_URL_TEMPLATE" "$b" "")"
  p="$(pkg_path "$b")"
  if [ -f "$REG/$b/pkg.ok" ]; then log "包已校验,跳过下载"; printf '%s' "$p"; return 0; fi
  if [ "$DRY" -eq 1 ]; then log "[dry] 下载 $u → $p"; printf '%s' "$p"; return 0; fi
  mkdir -p "$(dirname "$p")" "$REG/$b"
  if [[ "$u" == /* ]]; then
    [ -f "$u" ] || { echo "✗ 包不存在: $u" >&2; return 1; }
    run_cmd local "cp -f '$u' '$p'" || return 1
  else
    command -v wget >/dev/null 2>&1 || { echo "✗ 需要 wget(装包地址是远程)" >&2; return 1; }
    run_cmd local "wget -c -q -O '$p' '$u'" || return 1
  fi
  if [ "${PKG_CHECKSUM:-0}" = 1 ]; then
    if [ -n "${PKG_CHECKSUM_CMD:-}" ]; then
      run_cmd local "$(subst "$PKG_CHECKSUM_CMD" "$b" "$p")" || { echo "✗ 包校验失败" >&2; return 1; }
    elif ! [ -s "$p" ]; then
      echo "✗ 包文件为空" >&2; return 1
    fi
  fi
  touch "$REG/$b/pkg.ok"
  printf '%s' "$p"
}

safe_rm() { # <path> — 必须落在 CLEAN_PATHS 某个前缀内,且不是系统目录、深度≥2
  local t="$1" pre ok=0 depth
  for pre in "${CLEAN_PATHS[@]:-}"; do
    [ -n "$pre" ] || continue
    if [ "$t" = "$pre" ] || [[ "$t" == "$pre"/* ]]; then ok=1; fi
  done
  case " $BLACKLIST " in *" $t "*) ok=0 ;; esac
  depth="$(echo "$t" | awk -F/ '{print NF-1}')"
  [ "$depth" -ge 2 ] || ok=0
  if [ "$ok" != 1 ]; then echo "✗ 拒删(不在 CLEAN_PATHS 白名单内/系统路径/过浅): $t" >&2; return 1; fi
  run_cmd local "rm -rf '$t'"
}

do_clean() { # <BUILD> — 用户步→root步→删白名单路径
  run_steps user CLEAN_USER_STEPS "$1" "" || log "⚠ 用户清场步失败,继续"
  run_steps root CLEAN_ROOT_STEPS "$1" "" || log "⚠ root 清场步失败,继续"
  local pre
  for pre in "${CLEAN_PATHS[@]:-}"; do
    [ -n "$pre" ] || continue
    if [ -e "$pre" ]; then safe_rm "$pre" || return 1; fi
  done
}

take_lock() {
  [ "$DRY" -eq 1 ] && return 0
  mkdir -p "$VAR_HOME"
  local d="$VAR_HOME/.lock" pid
  if mkdir "$d" 2>/dev/null; then
    echo $$ > "$d/pid"; trap 'rm -rf "$VAR_HOME/.lock"' EXIT INT TERM; return 0
  fi
  pid="$(cat "$d/pid" 2>/dev/null)"
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    log "陈旧锁(pid $pid 已死),接管"
    rm -rf "$d"; mkdir "$d"; echo $$ > "$d/pid"
    trap 'rm -rf "$VAR_HOME/.lock"' EXIT INT TERM; return 0
  fi
  echo "✗ 另一个 provision 在跑(锁 pid=${pid:-?});单机互斥" >&2; return 3
}

cmd_install() { # <BUILD>
  local b="$1" st cur p
  st="$(get_state "$b")"
  if [ "$st" = ready ]; then log "$b 已 ready,跳过(安装不幂等,成功后绝不重跑)"; return 0; fi
  cur="$(current_build)"
  if [ -n "$cur" ] && [ "$cur" != "$b" ] && [ "$(get_state "$cur")" = ready ]; then
    echo "✗ 共享槽被 $cur 占着(ready),先 evict $cur 再装 $b" >&2; return 5
  fi
  case "$st" in
    absent) : ;;
    dirty|installing|downloading) log "上次状态 $st,先清场再装"; do_clean "$b" || return 1 ;;
    evicted) log "曾装过已拆,清场后重装"; do_clean "$b" || return 1 ;;
  esac
  p="$(do_download "$b")" || return 1
  say_state "$b" installing
  if ! run_steps root INSTALL_ROOT_STEPS "$b" "$p"; then
    say_state "$b" dirty; echo "✗ root 安装步失败 ⇒ dirty(下次先清场)" >&2; return 42
  fi
  if ! run_steps user INSTALL_USER_STEPS "$b" "$p"; then
    say_state "$b" dirty; echo "✗ 用户安装步失败 ⇒ dirty(下次先清场)" >&2; return 42
  fi
  if ! run_cmd "${HEALTH_AS:-user}" "$(subst "$HEALTH_CHECK_CMD" "$b" "$p")"; then
    say_state "$b" dirty; echo "✗ 健康检查不过 ⇒ dirty" >&2; return 43
  fi
  say_state "$b" ready
  if [ "$DRY" -eq 0 ]; then
    printf 'build=%s at=%s\n' "$b" "$(date +%FT%T)" > "$REG/$b/info"
    echo "$b" > "$REG/current"
  fi
  echo "✓ $b ready"
}

cmd_reset() { # <BUILD>
  local b="$1" p
  if [ "$(get_state "$b")" != ready ]; then
    echo "✗ 仅 ready 态可 reset(当前 $(get_state "$b"));崩得连 ready 都不是就走 evict+重装" >&2; return 1
  fi
  p="$(pkg_path "$b")"
  run_steps user RESET_USER_STEPS "$b" "$p" || { echo "✗ reset 用户步失败" >&2; return 44; }
  run_steps root RESET_ROOT_STEPS "$b" "$p" || { echo "✗ reset root 步失败" >&2; return 44; }
  if ! run_cmd "${HEALTH_AS:-user}" "$(subst "$HEALTH_CHECK_CMD" "$b" "$p")"; then
    echo "✗ reset 后健康检查不过" >&2; return 44
  fi
  echo "✓ reset $b 完成"
}

cmd_evict() { # <BUILD> [--force]
  local b="$1" force=0 cur
  [ "${1:-}" = "--force" ] && force=1
  [ "${2:-}" = "--force" ] && force=1
  cur="$(current_build)"
  if [ "$cur" = "$b" ]; then :;
  elif [ "$force" = 1 ]; then log "--force 强拆";
  else echo "✗ $b 不是当前占用者(current=${cur:-无});确要拆加 --force" >&2; return 6; fi
  do_clean "$b" || return 1
  say_state "$b" evicted
  if [ "$DRY" -eq 0 ] && [ "$(current_build)" = "$b" ]; then rm -f "$REG/current"; fi
  echo "✓ evicted $b"
}

cmd_status() {
  echo "current: $(current_build || true)"
  local d b
  [ -d "$REG" ] || { echo "(注册表为空)"; return 0; }
  for d in "$REG"/*/; do
    [ -d "$d" ] || continue
    b="$(basename "$d")"
    printf '%-24s %s\n' "$b" "$(get_state "$b")"
  done
}

case "$CMD" in
  install) take_lock || exit 3; cmd_install "${1:-}"; exit $? ;;
  reset)   take_lock || exit 3; cmd_reset "${1:-}"; exit $? ;;
  evict)   take_lock || exit 3; cmd_evict "$@"; exit $? ;;
  status)  cmd_status; exit 0 ;;
  rm)      [ -n "${1:-}" ] || { echo "用法: rm <PATH>" >&2; exit 1; }; safe_rm "$1"; exit $? ;;
  *) cat <<'EOF'
用法: provision.sh [--config FILE] [--dry-run] <install|reset|evict|status|rm> <BUILD|PATH> [--force]
EOF
     exit 1 ;;
esac
