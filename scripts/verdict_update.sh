#!/usr/bin/env bash
# verdict_update.sh <用例号> [--force] — 已推用例的判定变更上报(翻案通道)
# 环境变量:
#   HARVEST_HOME  工作目录(含 judged/ counted.jsonl pushed.jsonl)
#   CASES_DIR     用例库,默认 HARVEST_HOME 同级 opengauss-cases/cases
#   PUSH_CONFIG   推送凭证,默认 HARVEST_HOME 同级 dbdog-push-kit/push-config.json
# 四个分支:
#   ① 该号没推过            → 不归本脚本管(走全量推送)             exit 4
#   ② 已推 + 桶没变         → 平台数字本来就对,什么都不做          exit 0
#   ③ 已推 + 桶变了(翻案)   → 组纯 verdict 推送(cases=[]),一条换桶  exit 0
#   ④ 降级拦截:可复现→其他桶 默认拒绝(偶现问题复现过就是可复现,
#     不能因为这次没跑出来就降级)。确证上次判定本身有错才 --force,
#     且透传 force:true——平台侧同款粘性守卫(benchweb PR#27)只认它。  exit 5
# 策略(owner 2026-09-18):目标=扩大复现数量。前次复现→本次不复现=偶现,
# 仍归可复现桶;降级只在上次判定本身有错时人工 --force。粘性同时保护
# 手工重标的平台归属不被他人/台账重建的自动重算冲掉。
# 用法时机:复验轮判读完一条已推过的用例后调本脚本;判定结果与台账一致时
# 零成本通过,变了才发一笔几字节的推送。
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"
N=${1:?用法: verdict_update.sh <用例号> [--force]}
FORCE=${2:-}
H=$(harvest_home)
CASES_DIR=${CASES_DIR:-$(dirname "$H")/opengauss-cases/cases}
CFG=${PUSH_CONFIG:-$(dirname "$H")/dbdog-push-kit/push-config.json}

# ① 没推过 → 走全量推送
[ -f "$H/pushed.jsonl" ] && grep -q "\"id\":\"$N\"" "$H/pushed.jsonl" || { echo "NOT-PUSHED: OG-$N 走全量推送"; exit 4; }

# 算当前桶(与全量推送同一套判定逻辑:判读值 × case.env 标志)
BUCKET=$(python3 - "$N" "$H" "$CASES_DIR" <<'PY'
import json, os, re, sys
n, home, cases_dir = sys.argv[1], sys.argv[2], sys.argv[3]
j = json.load(open(f"{home}/judged/{n}.json", encoding="utf-8"))
vs = [v.get("verdict") for v in (j.get("per_version") or {}).values()]
envp = f"{cases_dir}/OG-{n}/case.env"
flags = {}
if os.path.exists(envp):
    for line in open(envp, encoding="utf-8"):
        m = re.match(r"(NON_DEFECT|EVIDENCE_SUSPECT|ISSUE_OPEN)=(\S*)", line.strip())
        if m: flags[m.group(1)] = m.group(2)
if "yes" in vs:
    if flags.get("NON_DEFECT"): b = "negative"
    elif flags.get("EVIDENCE_SUSPECT") == "yes": b = "suspect"
    elif flags.get("ISSUE_OPEN") == "yes": b = "undecided"
    else: b = "reproducible"
elif "construct_fail" in vs or (j.get("script_bug") or "").strip(): b = "nonrepro_script"
else: b = "nonrepro_other"
print(b)
PY
) || exit 2

# ② 上次上账的桶
OLD=$(grep "\"id\": *\"$N\"" "$H/counted.jsonl" | tail -1 | python3 -c "import json,sys; print(json.loads(sys.stdin.read())['bucket'])" 2>/dev/null || echo "?")

if [ "$BUCKET" = "$OLD" ]; then
  echo "UNCHANGED: OG-$N 桶=$BUCKET(平台已对,零推送)"
  exit 0
fi

# ④ 降级拦截:可复现 → 其他桶,默认拒绝
# 偶现问题前次复现过,这次没跑出来不等于不复现——目标是扩大复现数量。
# --force 只给一种场景:有确凿证据上次判定本身就判错了(如探针没过判了 yes)。
NONREPRO="nonrepro_other nonrepro_script negative suspect undecided"
if [ "$OLD" = "reproducible" ] && echo " $NONREPRO " | grep -q " $BUCKET " && [ "$FORCE" != "--force" ]; then
  echo "DOWNGRADE-BLOCKED: OG-$N $OLD → $BUCKET(可复现→其他桶 默认拒绝;确证上次判错才 --force)" >&2
  exit 5
fi

# ③ 翻案:纯 verdict 推送(--force 透传 force:true,平台侧同款守卫才放行降级)
M=$(mktemp /tmp/vupd.XXXXXX.json)
python3 - "$M" "$N" "$BUCKET" "$FORCE" <<'PY'
import json, sys
cv = {"case_number": f"OG-{sys.argv[2]}", "bucket": sys.argv[3]}
if sys.argv[4] == "--force": cv["force"] = True
m = {"total_issues": 0, "filtered_issues": 0, "cases": [], "case_verdicts": [cv]}
json.dump(m, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
TOK=$(python3 -c "import json; c=json.load(open('$CFG')); print(c['token'])")
BASE=$(python3 -c "import json; c=json.load(open('$CFG')); print(c['base_url'].rstrip('/'))")
OUT=$(curl -s --noproxy '*' -X POST "$BASE/api/testcases/push" -H "X-Auth-Token: $TOK" \
  -F "manifest=@$M;type=application/json")
rm -f "$M"
echo "$OUT" | grep -q '"verdicts_applied":1' && {
  printf '{"id":"%s","bucket":"%s","at":"%s","note":"翻案换桶(旧=%s)"}\n' "$N" "$BUCKET" "$(date +%Y-%m-%dT%H:%M:%S)" "$OLD" >> "$H/counted.jsonl"
  echo "RELABELED: OG-$N $OLD → $BUCKET"
  exit 0
}
if echo "$OUT" | grep -q 'verdicts_skipped'; then
  echo "DOWNGRADE-BLOCKED(平台): OG-$N $OLD → $BUCKET 被粘性守卫拦下;确证上次判错加 --force 重推" >&2
  exit 5
fi
echo "推送失败: $OUT" >&2; exit 1
