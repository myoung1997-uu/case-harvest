#!/usr/bin/env bash
# preflight.sh — 进门自检:逐项活体检验配置。✗ 不许开跑;⚠ 提醒不拦。
# 用法: preflight.sh [--config FILE] [--live]
#   --live  对远程包 URL 真发一次探测(黄区用;本机 mock 不加此参数,零网络)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.sh"
LIVE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --live) LIVE=1; shift ;;
    *) break ;;
  esac
done

ERR=0
ok()   { echo "✓ $*"; }
bad()  { echo "✗ $*"; ERR=1; }
warn() { echo "⚠ $*"; }

if [ ! -f "$CONFIG" ]; then
  echo "✗ 缺配置 $CONFIG(从 config.example.sh 复制填写)"; exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG"
VAR_HOME="${VAR_HOME:-$SCRIPT_DIR/var}"
BLACKLIST="/ /bin /boot /dev /etc /home /lib /lib64 /opt /proc /root /run /sbin /sys /tmp /usr /var"

# ---- 1. 必填字段 ----
arr_len() { # 数组名 → 非空元素个数
  local n=0 c
  eval "local v=(\"\${$1[@]:-}\")"
  for c in "${v[@]}"; do [ -n "$c" ] && n=$((n+1)); done
  echo "$n"
}
for f in PKG_URL_TEMPLATE HEALTH_CHECK_CMD; do
  if [ -n "${!f:-}" ]; then ok "$f 已填"; else bad "$f 未填(config 问卷第 2/5 节)"; fi
done
if [ "${MOCK_ALLOW:-0}" = 1 ]; then
  warn "MOCK_ALLOW=1(测试配置):跳过身份必填检查"
elif [ -n "${RUN_AS_USER:-}" ]; then
  ok "RUN_AS_USER 已填"
else
  bad "RUN_AS_USER 未填(问卷 1 节:具体用户执行前缀,如 sudo -u omm -)"
fi
for a in INSTALL_ROOT_STEPS INSTALL_USER_STEPS CLEAN_USER_STEPS CLEAN_ROOT_STEPS CLEAN_PATHS RESET_USER_STEPS; do
  if ! declare -p "$a" >/dev/null 2>&1; then
    bad "$a 未定义(问卷 3/4/6 节)"
  elif [ "$(arr_len "$a")" -ge 1 ]; then
    ok "$a 已填($(arr_len "$a") 步)"
  else
    case "$a" in CLEAN_USER_STEPS|CLEAN_ROOT_STEPS|RESET_USER_STEPS) warn "$a 为空(可接受,但失败恢复手段变弱)" ;; *) bad "$a 为空" ;; esac
  fi
done

# ---- 2. 身份活体 ----
if [ -n "${RUN_AS_USER:-}" ]; then
  if $RUN_AS_USER bash -c true 2>/dev/null; then ok "具体用户身份可用"; else bad "RUN_AS_USER 探活失败: $RUN_AS_USER"; fi
fi
if [ -z "${RUN_AS_ROOT:-}" ] && [ "$(id -u)" != 0 ]; then
  warn "RUN_AS_ROOT 为空且当前不是 root——root 安装步将以普通用户执行,大概率失败"
fi

# ---- 3. 包地址 ----
U="${PKG_URL_TEMPLATE//\{BUILD\}/PROBE}"
if [[ "$U" == *'{BUILD}'* ]]; then warn "PKG_URL_TEMPLATE 没有 {BUILD} 占位符——所有构建会下同一个包"; fi
if [[ "$U" == /* ]]; then
  if [ -d "$(dirname "$U")" ]; then ok "本地包目录存在: $(dirname "$U")"; else bad "本地包目录不存在: $(dirname "$U")"; fi
elif [[ "$U" == http* ]]; then
  command -v wget >/dev/null 2>&1 && ok "wget 可用" || bad "远程包地址但本机没有 wget"
  if [ "$LIVE" = 1 ]; then
    if wget --spider -q -T 10 "$U"; then ok "包地址可达: $U"; else bad "包地址探不到: $U"; fi
  else
    warn "未加 --live,没真探远程地址: $U"
  fi
else
  bad "PKG_URL_TEMPLATE 不是本地路径也不是 http(s): $U"
fi

# ---- 4. 清场白名单 ----
for pre in "${CLEAN_PATHS[@]:-}"; do
  [ -n "$pre" ] || continue
  case " $BLACKLIST " in *" $pre "*) bad "CLEAN_PATHS 含系统目录: $pre" ;; esac
  case "$pre" in /*) : ;; *) bad "CLEAN_PATHS 必须绝对路径: $pre" ;; esac
  [ "$(echo "$pre" | awk -F/ '{print NF-1}')" -ge 2 ] || bad "CLEAN_PATHS 路径过浅,危险: $pre"
  [ "$pre" = "$HOME" ] && bad "CLEAN_PATHS 竟然是 HOME 本身"
  [ -e "$pre" ] || warn "CLEAN_PATHS 尚不存在(新机器正常): $pre"
done

# ---- 5. 步骤里引用的脚本是否存在 ----
check_step_scripts() { # <数组名> <标签>
  local arr="$1" label="$2" c tok
  eval "local v=(\"\${$arr[@]:-}\")"
  for c in "${v[@]}"; do
    [ -n "$c" ] || continue
    [ "$c" = true ] && continue
    for tok in $c; do
      tok="${tok//\'/}"; tok="${tok//\"/}"
      case "$tok" in *.sh)
        if [ -f "$tok" ]; then :; else bad "$label 引用的脚本不存在: $tok"; fi ;;
      esac
    done
  done
}
check_step_scripts INSTALL_ROOT_STEPS "INSTALL_ROOT_STEPS"
check_step_scripts INSTALL_USER_STEPS "INSTALL_USER_STEPS"
check_step_scripts CLEAN_USER_STEPS "CLEAN_USER_STEPS"
check_step_scripts CLEAN_ROOT_STEPS "CLEAN_ROOT_STEPS"
check_step_scripts RESET_USER_STEPS "RESET_USER_STEPS"

# ---- 6. 运行目录可写 ----
if mkdir -p "$VAR_HOME" 2>/dev/null && touch "$VAR_HOME/.pf_test" 2>/dev/null && rm -f "$VAR_HOME/.pf_test"; then
  ok "VAR_HOME 可写: $VAR_HOME"
else
  bad "VAR_HOME 不可写: $VAR_HOME"
fi

echo "----"
[ "$ERR" = 0 ] && echo "preflight 通过(可跑 provision --dry-run 先看一遍计划)" || echo "preflight 未过:按上面 ✗ 逐条修配置"
exit "$ERR"
