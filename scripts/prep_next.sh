#!/usr/bin/env bash
# prep_next.sh [条数=5] — 同步跑批日志，挑「未判读且不在途」的用例，实例化 prompt 到暂存区
#
# 在途标记：/tmp/percase_r<轮>_og<N>.md 存在即视为在途（同一台机器上多个会话共用这套标记，
#   所以它也是「两个 loop 不抢同一条」的那道互斥——别在这上面另起一套）。
#   **标记带轮次**：早先的写法是不带轮次的 /tmp/percase_og<N>.md，于是上一轮判完留下的标记
#   会让下一轮 pick 到同号时被判成「在途」而**静默跳过**（漏号且不留痕）。轮次取自
#   rounds.jsonl 最新一行；没有 rounds.jsonl 时记 r0。换轮次时旧标记天然失效，不必手工清。
# 崩溃候选压后：/tmp/harvest_crash_defer.txt 里列到的号等 iso 复验完再挑。
#
# 模板单源：prompts/judge-verify.md（**不是**工作区里的副本；副本勿手改）。
#   本脚本把模板里的 8 个占位符一次注入完：
#     {N} {BATCH}                  每条不同
#     {HARVEST_HOME} {CASES_DIR}   本机路径
#     {VERSION_TAG} {BUILD_ID}     取 instances.conf 第一行（行序=主落点优先级）
#     {INSTANCE_DESC} {ENV_FACTS}  按轮注入的本轮事实，见下面两个环境变量
#
# 环境变量：
#   HARVEST_HOME        数据目录（含 results/、judged/、pushed.jsonl）
#   STAGING             暂存区，默认 HARVEST_HOME 同级 .dbdog-outbox-stream/staging
#   CASES_DIR           用例库 cases 目录，默认 HARVEST_HOME 同级 opengauss-cases/cases
#   INSTANCES           实例清单，默认 HARVEST_HOME/instances.conf
#   HARVEST_INSTANCE_DESC  本轮实例描述（例：vm203 og700@8432）。缺省留空并警告
#   HARVEST_ENV_FACTS      本轮实例环境事实（各兼容库能力边界，注明实测日期）；
#                          不设则读数据目录的 env_facts.md
#   —— 远端同步（不设 HARVEST_DB_HOST 就整段跳过，只用本地已有 results/）——
#   HARVEST_DB_HOST        数据库主机（例：vm203）
#   HARVEST_DB_USER        登录用户，默认 og700
#   HARVEST_DB_PASS        密码；**不设则走 ssh 免密**。密码只经环境变量传，
#                          不落脚本、不进命令行（sshpass -p 会把密码暴露给同机任何用户）
#   HARVEST_REMOTE_RESULTS 远端 results 目录（设了 HOST 就必须给）
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

CNT=${1:-5}
H=$(harvest_home)
S=${STAGING:-$(dirname "$H")/.dbdog-outbox-stream/staging}
CASES_DIR=${CASES_DIR:-$(dirname "$H")/opengauss-cases/cases}
INSTANCES=${INSTANCES:-$H/instances.conf}
TPL=$HERE/../prompts/judge-verify.md

[ -f "$TPL" ] || die "模板不存在：$TPL"

# ── 轮次 → 在途标记前缀（标记按轮隔离，见文件头说明）──────────────────────
# 取 rounds.jsonl 最新一行的 round（与 stats.py 取「最新一轮」同一口径）；没有就记 r0。
ROUND=$(python3 - "$H" <<'PY'
import json, os, sys
p = os.path.join(sys.argv[1], "rounds.jsonl")
try:
    rows = [l for l in open(p, encoding="utf-8") if l.strip()]
    print(json.loads(rows[-1])["round"] if rows else 0)
except Exception:
    print(0)
PY
)
MARK="/tmp/percase_r${ROUND}_og"

# ── 远端跑批日志同步（可选）──────────────────────────────────────────────
if [ -n "${HARVEST_DB_HOST:-}" ]; then
  : "${HARVEST_REMOTE_RESULTS:?设了 HARVEST_DB_HOST 就必须给 HARVEST_REMOTE_RESULTS（远端 results 目录）}"
  U=${HARVEST_DB_USER:-og700}
  mkdir -p "$H/results"
  if [ -n "${HARVEST_DB_PASS:-}" ]; then
    SSHPASS=$HARVEST_DB_PASS sshpass -e rsync -az -e ssh \
      "$U@$HARVEST_DB_HOST:$HARVEST_REMOTE_RESULTS/" "$H/results/" 2>/dev/null
  else
    rsync -az -e ssh "$U@$HARVEST_DB_HOST:$HARVEST_REMOTE_RESULTS/" "$H/results/" 2>/dev/null
  fi
else
  echo "⚠ 未设 HARVEST_DB_HOST，跳过远端日志同步（只用本地 $H/results）" >&2
fi

# ── 本轮事实：版本标签/构建号取 instances.conf 第一行（行序 = 主落点优先级）────
VERSION_TAG=- ; BUILD_ID=-
if [ -f "$INSTANCES" ]; then
  read -r VERSION_TAG _ _ _ _ _ BUILD_ID _ < <(instances "$INSTANCES" | head -1) || true
fi
[ "$VERSION_TAG" = - ] && echo "⚠ instances.conf 没读到主落点，{VERSION_TAG} 会写成 '-'" >&2
: "${HARVEST_INSTANCE_DESC:=}"
# 环境事实：优先环境变量，否则读数据目录里的 env_facts.md（按轮实测的实例能力边界，
# 换实例/换构建后要更新——判 missing_dep 全靠它，过期会把「环境不支持」判成「没复现」）
if [ -z "${HARVEST_ENV_FACTS:-}" ] && [ -f "$H/env_facts.md" ]; then
  HARVEST_ENV_FACTS=$(sed '/^（本文件是/,$d' "$H/env_facts.md")
fi
: "${HARVEST_ENV_FACTS:=（本轮未注入环境事实）}"
[ -n "$HARVEST_INSTANCE_DESC" ] || echo "⚠ 未设 HARVEST_INSTANCE_DESC（例：vm203 og700@8432）" >&2
[ "$HARVEST_ENV_FACTS" = "（本轮未注入环境事实）" ] && \
  echo "⚠ 既没设 HARVEST_ENV_FACTS 也没有 $H/env_facts.md，{ENV_FACTS} 会是占位话术" >&2

# ── 挑号 → 实例化 prompt 到 /tmp ─────────────────────────────────────────
n=0
for f in $(ls "$H"/results/*.work.log 2>/dev/null | sed 's|.*/\([0-9]*\)\..*|\1|' | sort -n); do
  [ "$n" -ge "$CNT" ] && break
  [ -f "$H/judged/$f.json" ] && continue                                  # 已判
  [ -f "${MARK}$f.md" ] && continue                                       # 在途（本轮）
  [ -f /tmp/harvest_crash_defer.txt ] && grep -qx "$f" /tmp/harvest_crash_defer.txt && continue  # 崩溃候选压后
  mkdir -p "$S/b-og$f"
  ST=$(python3 - "$f" "$S" "$H" "$TPL" "$CASES_DIR" "$VERSION_TAG" "$BUILD_ID" \
         "$HARVEST_INSTANCE_DESC" "$HARVEST_ENV_FACTS" "$MARK" <<'PY'
import json, sys
i, s, home, tpl, cases_dir, vtag, build, idesc, efacts, mark = sys.argv[1:11]
t = open(tpl, encoding="utf-8").read()
for k, v in (("{N}", i), ("{BATCH}", f"{s}/b-og{i}"), ("{HARVEST_HOME}", home),
             ("{CASES_DIR}", cases_dir), ("{VERSION_TAG}", vtag), ("{BUILD_ID}", build),
             ("{INSTANCE_DESC}", idesc), ("{ENV_FACTS}", efacts)):
    t = t.replace(k, v)
# 已推过的号注入标注：判读 agent 只做第一步，跳过第二步推送材料（token 节约的大头）
pushed = False
try:
    for l in open(f"{home}/pushed.jsonl", encoding="utf-8"):
        if l.strip() and json.loads(l)["id"] == i:
            pushed = True; break
except FileNotFoundError:
    pass
if pushed:
    t = t.replace("## 输入（主会话已备好，你只读不连库）",
                  "## ⚠ 本轮状态：该号已推过平台——只执行第一步判读，第二步推送材料跳过\n\n"
                  "## 输入（主会话已备好，你只读不连库）", 1)
left = sorted(set(k for k in ("{N}", "{BATCH}", "{HARVEST_HOME}", "{CASES_DIR}", "{VERSION_TAG}",
                              "{BUILD_ID}", "{INSTANCE_DESC}", "{ENV_FACTS}") if k in t))
if left:
    sys.exit(f"实例化后还有没替掉的占位符：{left}——模板改了占位符名？改本脚本的映射表")
open(f"{mark}{i}.md", "w", encoding="utf-8").write(t)
print("已推,只判读" if pushed else "新号")
PY
) || exit 2
  echo "READY og$f [$ST]"
  n=$((n+1))
done
echo "total_done=$(ls "$H"/results/*.work.log 2>/dev/null | wc -l | tr -d ' ') judged=$(ls "$H"/judged/*.json 2>/dev/null | wc -l | tr -d ' ')"
