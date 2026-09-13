# shellcheck shell=bash
# 跑批脚本共用函数。在数据库主机上 source。

die() { echo "✗ $*" >&2; exit 1; }
harvest_home() { local h="${HARVEST_HOME:-$PWD/harvest}"; (cd "$h" 2>/dev/null && pwd) || die "HARVEST_HOME=$h 不存在"; }

# 实例清单规整成 8 列（缺省补 -），去注释空行
instances() {
  sed -e 's/#.*$//' "$1" | awk 'NF>=5 { while (NF<8) $(NF+1)="-"; print $1,$2,$3,$4,$5,$6,$7,$8 }'
}
instance_line() { instances "$1" | awk -v l="$2" '$1==l'; }

# 以实例 OS 用户身份跑 gsql。
# ★ 所有 su 都要 </dev/null：while read 循环里的 su 会吃掉管道剩余输入，worker 提前「读完」退出
#   （症状：多个 worker 停在同一个数字上）。
# OG_ENV：实例用户的环境文件，默认 ~/.og_env（放 GAUSSHOME/PATH/LD_LIBRARY_PATH）。
# 注意 og_env 常是 PATH=...:$PATH 追加式，只能 source 一次，别放进循环。
gsql_as() {
  local u="$1"; shift
  local args; args=$(printf '%q ' "$@")
  local envf="${OG_ENV:-~/.og_env}"
  local cmd="[ -f $envf ] && . $envf >/dev/null 2>&1; gsql $args"
  if [ "$(id -un)" = "$u" ]; then bash -c "$cmd" </dev/null
  else su - "$u" -c "$cmd" </dev/null; fi
}
run_as() {  # run_as <user> <shell 命令串>
  local u="$1"; shift
  if [ "$(id -un)" = "$u" ]; then bash -c "$*" </dev/null; else su - "$u" -c "$*" </dev/null; fi
}

# alive <osuser> <port>
alive() { [ "$(gsql_as "$1" -d postgres -p "$2" -Atc 'select 1' 2>/dev/null | tail -1)" = 1 ]; }

# revive <osuser> <port> <unit> <revive>
# revive=yes  允许 systemctl start 拉起（专门的复现实例）
# revive=wait 只等它自己恢复，绝不主动 systemctl（现役/共享实例：openGauss postmaster 会自行重启）
# 同实例多 worker 只让一个去拉。
revive() {
  local u="$1" p="$2" unit="$3" mode="$4" lock="/tmp/.harvest-revive.$2.lock" i
  if [ "$mode" = yes ] && [ "$unit" != - ] && mkdir "$lock" 2>/dev/null; then
    systemctl start "$unit" >/dev/null 2>&1
    for i in $(seq 1 30); do alive "$u" "$p" && { rmdir "$lock"; return 0; }; sleep 3; done
    rmdir "$lock"; return 1
  fi
  for i in $(seq 1 40); do alive "$u" "$p" && return 0; sleep 3; done
  return 1
}

# 库名需要的插件能力（与 common.py DB_COMPAT 保持一致）
db_cap() { case "$1" in bench_b) echo dolphin;; bench_d) echo shark;; *) echo -;; esac; }
db_compat() { case "$1" in bench_b) echo B;; bench_d) echo D;; bench_pg) echo PG;; *) echo A;; esac; }
has_cap() { [ "$2" = - ] && return 0; case ",$1," in *",$2,"*) return 0;; esac; return 1; }

# 每个实例只留最近一个 core（一个几百 MB）
trim_cores() { [ "$1" = - ] && return 0; ls -t $1 2>/dev/null | tail -n +2 | xargs -r rm -f; }

preamble() {  # preamble <db>  —— 每条材料前面拼的头
  printf '\\set ON_ERROR_STOP off\n\\timing on\nSET statement_timeout=%s;\n' "'${STMT_TIMEOUT:-30s}'"
  # B 兼容库建好、dolphin 装好之后，@变量和反引号标识符还要这个开关，否则一律 syntax error at or near "@"
  [ "$1" = bench_b ] && printf 'SET enable_set_variable_b_format=on;\n'
  return 0
}
