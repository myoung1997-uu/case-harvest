#!/usr/bin/env bash
# push_one.sh <用例号> — 流式收割：把暂存区里的**单条**批次推给用例平台
#
# 为什么要有这个脚本（而不是直接调 tc-push.sh）：
#   `tc-push.sh --outbox <DIR>` 的语义是「把 DIR 下所有带 manifest.json 的批次一次推光」，
#   而流式模式要求「一次只推一笔」。所以必须保证推的那一刻发件箱根目录里只有这一笔。
#   本脚本把「从暂存区搬进根目录 → 确认根目录没有别人 → 推」收成一个动作，不靠人记。
#   （2026-09-17 教训：搬运当时是主会话手工做的，没有任何东西拦着用错姿势——
#    直接把 staging 路径交给 tc-push，会把暂存区里**所有有料的批次**一起推走。）
#
# 环境变量：
#   HARVEST_HOME  数据目录（含 judged/ counted.jsonl pushed.jsonl）。默认 $PWD/harvest
#   OUTBOX        发件箱根目录，默认 HARVEST_HOME 同级的 .dbdog-outbox-stream
#   STAGING       暂存区，默认 $OUTBOX/staging
#   CASES_DIR     用例库 cases 目录，默认 HARVEST_HOME 同级 opengauss-cases/cases
#                  （判读值是 yes 的号在这里的 case.env 取桶标志）
#   PUSH_KIT      推送脚本，默认 HARVEST_HOME 同级的 dbdog-push-kit/tc-push.sh
#
# 统计口径：manifest 的 total_issues/filtered_issues/repro_stats 由本脚本改写为
#   「自上次推送以来新判读」的增量——平台按流水累加 => 平台侧累计扫描/累计六桶
#   自动成立且绝不重复。增量为空时如实报 0/0/全零，不虚增。
# case_verdicts（平台契约，benchweb #21）：同一份增量扫描顺手生成逐条判定归属。
# 台账（**两本都在平台收下之后**才写——要么都写、要么都不写）：
#   pushed.jsonl   已推送用例（推前先查，已推过直接跳过）
#   counted.jsonl  统计已上账的用例（与「用例推送」解耦：判了没复现的号也在增量里，
#                  其六桶份额随下一次**成功**推送一起上账）
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/lib.sh"

N=${1:?用法: push_one.sh <用例号>}
H=$(harvest_home)
OUTBOX=${OUTBOX:-$(dirname "$H")/.dbdog-outbox-stream}
STAGING=${STAGING:-$OUTBOX/staging}
CASES_DIR=${CASES_DIR:-$(dirname "$H")/opengauss-cases/cases}
KIT=${PUSH_KIT:-$(dirname "$H")/dbdog-push-kit/tc-push.sh}

BATCH_SRC="$STAGING/b-og$N"
BATCH="$OUTBOX/b-og$N"

# ① 防重推
if [ -f "$H/pushed.jsonl" ] && grep -q "\"id\":\"$N\"" "$H/pushed.jsonl"; then
  echo "SKIP-DUP: OG-$N 已推送过，不重复推" >&2; exit 3
fi

# ② 定位批次：暂存区里（首次推）或已在发件箱根目录（上次推失败留下的，直接重推）
if [ -d "$BATCH" ]; then
  echo "（$BATCH 已在发件箱根目录——上次没推成功留下的，直接重推）" >&2
else
  [ -d "$BATCH_SRC" ] || die "暂存区和发件箱里都没有 b-og$N（找过 $BATCH_SRC 和 $BATCH）"

  # ③ 守门：根目录除了 sent/staging 不许有别的待推批次。
  #    有的话推这一笔会把它们**捎带**上去（tc-push 扫的是整个目录），所以拒绝，不硬推。
  others=$(for d in "$OUTBOX"/*/; do
    [ -d "$d" ] || continue
    b=$(basename "$d")
    # 写成 if 而不是 case：`case ... continue;;` 单独跑没事，但放进 $( ) 里会被 bash 解析成语法错误
    if [ "$b" = sent ] || [ "$b" = staging ]; then continue; fi
    [ -f "$d/manifest.json" ] && echo "    $b"
  done)
  [ -z "$others" ] || die "发件箱根目录还有别的待推批次，推 OG-$N 会把它们一起捎带走：
$others
先处理干净（推掉，或移回 ${STAGING}）再来推这一笔。"

  # ④ 搬进根目录——保证 tc-push 扫到的只有这一笔
  mv "$BATCH_SRC" "$BATCH" || die "搬运失败：$BATCH_SRC → $BATCH"
fi

# ⑤ 算增量、改写 manifest 统计字段。台账**先不写**——见下面 python 里的说明
INC=$(mktemp /tmp/inc.XXXXXX.jsonl)
python3 - "$BATCH/manifest.json" "$N" "$H" "$CASES_DIR" "$INC" <<'PY' || { rm -f "$INC"; exit 2; }
import json, os, re, sys
from datetime import datetime
mp, n, home, cases_dir, incp = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
m = json.load(open(mp, encoding="utf-8"))
nums = [c["case_number"] for c in m["cases"]]
assert nums == ["OG-" + n], f"manifest 用例编号不对：{nums}"
# 兼容字段白名单，按引擎分家（owner 2026-09-19）：openGauss 只许 A/B/PG/D，
# GaussDB 只许 A/B/M（M=GaussDB 新 M 兼容）。曾有 4 条把映射提示 bench→A
# 原样抄进字段成脏数据——机器兜底，别指望判读 agent 每次都仔细。
BY_ENGINE = {"opengauss": {"A", "B", "PG", "D"}, "gaussdb": {"A", "B", "M"}}
for c in m["cases"]:
    eng = (c.get("engine") or "").strip().lower()
    allowed = BY_ENGINE.get(eng)
    if allowed is None:
        sys.exit(f"engine 非法：OG-{n}={eng!r}，本流程只认 opengauss/gaussdb")
    comp = (c.get("compatibility") or "").strip()
    if comp not in allowed:
        sys.exit(f"compatibility 非法：OG-{n} engine={eng} compatibility={comp!r}；"
                 f"openGauss 只许 A/B/PG/D，GaussDB 只许 A/B/M（判不出默认 A）——修 manifest 再推")

counted = set()
cp = os.path.join(home, "counted.jsonl")
if os.path.exists(cp):
    counted = {json.loads(l)["id"] for l in open(cp) if l.strip()}
inc = []   # (id, bucket)
for f in sorted(os.listdir(os.path.join(home, "judged"))):
    if not f.endswith(".json"): continue
    i = f[:-5]
    if i in counted: continue
    try:
        j = json.load(open(os.path.join(home, "judged", f), encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"judged/{f} JSON 损坏（{e}），修好再推： python3 -c 'import json;json.load(open(...))' 定位")
    verdicts = [v.get("verdict") for v in (j.get("per_version") or {}).values()]
    # 桶标志读 case.env（不是 judged/*.json 里的 flags——那里可能带解释长句，
    # 而 case.env 是 promote 规范化过的，值就是干净的 yes）
    flags = {}
    envp = os.path.join(cases_dir, f"OG-{i}", "case.env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8"):
            mm = re.match(r"(NON_DEFECT|EVIDENCE_SUSPECT|ISSUE_OPEN)=(\S*)", line.strip())
            if mm: flags[mm.group(1)] = mm.group(2)
    if "yes" in verdicts:
        if flags.get("NON_DEFECT"): b = "negative"
        elif flags.get("EVIDENCE_SUSPECT") == "yes": b = "suspect"
        elif flags.get("ISSUE_OPEN") == "yes": b = "undecided"
        else: b = "reproducible"
    elif "construct_fail" in verdicts or (j.get("script_bug") or "").strip(): b = "nonrepro_script"
    elif verdicts and all(v in ("no", "missing_dep", "uncertain") for v in verdicts): b = "nonrepro_other"
    else: b = "nonrepro_other"
    inc.append((i, b))

stats = {k: 0 for k in ("reproducible","nonrepro_script","nonrepro_other","negative","suspect","undecided")}
for i, b in inc: stats[b] += 1
# 口径：total=filtered=新判读数，六桶和=filtered —— 平台流水累加后累计扫描/筛选/六桶自洽可对账
# 增量为空（该条已在先前推送的增量里上过账）时如实报 0/0/全零，不虚增
m["total_issues"] = len(inc)
m["filtered_issues"] = len(inc)
m["repro_stats"] = stats
# 逐条判定归属：同一次增量扫描的副产物，inc 里每条都生成 {case_number, bucket}。
# 平台按编号 UPSERT（latest-wins），所以只报本次新判的——已上过账的重报无意义
# （同桶幂等不伤，但列表越滚越长；counted 台账已挡）。
m["case_verdicts"] = [{"case_number": f"OG-{i}", "bucket": b} for i, b in inc]
json.dump(m, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
# 台账**先不写**：等平台确认收下这笔再上账。写在推前的话，平台整笔拒收时这些号
# 已被提前认领，重推时不再计入增量 => 那份统计永久丢失（实测：3 条变 1 条，
# counted 说已上账、平台却没收到，对账口径「平台回执之和 = counted」直接不平）。
# 兄弟脚本 verdict_update.sh 就是这个姿势（拿到平台回执 verdicts_applied:1 才落台账）。
with open(incp, "w", encoding="utf-8") as f:
    for i, b in inc:
        f.write(json.dumps({"id": i, "bucket": b, "at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
print(f"统计增量：scanned+{len(inc)} {stats}")
print(f"case_verdicts：{len(m['case_verdicts'])} 条随行")
PY

# ⑥ 推送（扫的是根目录，此刻里头只有这一笔）。pipefail 已开，rc 反映 tc-push 的退出码。
bash "$KIT" --outbox "$OUTBOX" 2>&1 | tail -3
rc=$?
if [ $rc -eq 0 ]; then
  # 平台收下了，这时才上账：两者要么都写，要么都不写
  cat "$INC" >> "$H/counted.jsonl"
  printf '{"id":"%s","at":"%s"}\n' "$N" "$(date +%Y-%m-%dT%H:%M:%S)" >> "$H/pushed.jsonl"
  echo "LEDGER + OG-$N (pushed)"
else
  echo "✗ 推送失败，批次留在 ${BATCH}（未进 sent/）；统计**没上账**，修好直接重跑本脚本即可" >&2
fi
rm -f "$INC"
exit $rc
