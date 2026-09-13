#!/usr/bin/env python3
"""把判读完、溯完根因的用例沉淀成用例目录（cases/OG-<n>/）。

输入：$HARVEST_HOME/judged/<n>.json（格式见 references/judged-schema.md）
     + $HARVEST_HOME/constructed/<n>/{setup.sql,workload.sql,meta.json}
     + $HARVEST_HOME/raw/issues.jsonl
输出：<cases>/OG-<n>/{case.env,setup.sql,workload.sql,prompt.txt,ground-truth.md,issues.json}
     + <cases>/_alias-map.json

用法：
    python3 promote.py --instances instances.conf [--cases DIR] [--arch x86_64] judged/1723.json ...
    python3 promote.py --instances instances.conf --all       # judged/ 下全部
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SALT = "dbdog-loop-alias-v1"
VERDICTS = {"yes": "✔ 复现", "no": "✘ 不复现", "construct_fail": "✗ 构造失败（我们的脚本没跑到触发点）",
            "missing_dep": "— 缺依赖（该版本没有相关能力，判不了）", "uncertain": "? 不确定"}
SYMPTOMS = {"failure", "wrong-results", "slowness", "resource", "crash"}
Instance = namedtuple("Instance", "label port build")


class PromoteError(Exception):
    pass


def alias_for(case_id):
    """sha256(salt|OG-n) 十六进制串里第一个首位非数字的 6 位窗口。
    目录名和对象名都是题面泄漏面：被测 agent 看到 c7601_t 就能去搜 issue 7601。"""
    h = hashlib.sha256(f"{SALT}|{case_id}".encode()).hexdigest()
    for i in range(len(h) - 5):
        w = h[i:i + 6]
        if not w[0].isdigit() and re.search(r"[a-f]", w):
            return w
    raise PromoteError(f"{case_id} 算不出别名")


def rename_identifiers(text, num, alias):
    """把 c<n>_xxx / c<n> / repro_<n> 这类带 issue 号的对象名换成别名。"""
    mapping = {}

    def sub(m):
        old = m.group(0)
        new = m.group(1) + alias + (m.group(2) or "")
        mapping[old] = new
        return new

    pat = re.compile(r"\b([A-Za-z_]*?)" + re.escape(num) + r"(_\w+)?\b")
    out = pat.sub(lambda m: sub(m) if m.group(1) or m.group(2) else m.group(0), text)
    return out, {k: v for k, v in mapping.items() if k != v}


def validate(j):
    for k in ("id", "db", "per_version", "phenomenon", "symptom_class", "root_cause", "prs"):
        if k not in j:
            raise PromoteError(f"judged 缺字段 {k}")
    bad = {v.get("verdict") for v in j["per_version"].values()} - set(VERDICTS)
    if bad:
        raise PromoteError(f"未知判读结论 {bad}，只许 {sorted(VERDICTS)}")
    if not any(v.get("verdict") == "yes" for v in j["per_version"].values()):
        raise PromoteError(f"OG-{j['id']} 没有任何版本复现，不进用例库")
    if j["symptom_class"] not in SYMPTOMS:
        raise PromoteError(f"symptom_class 只许 {sorted(SYMPTOMS)}")
    gt = (j["root_cause"] or {}).get("gt")
    if gt not in (1, 2, 3):
        raise PromoteError("root_cause.gt 只许 1/2/3")
    if gt >= 2 and not (j["root_cause"].get("source") or "").strip():
        raise PromoteError("GT>=2 必须写根因来源")
    for pr in j["prs"]:
        # 跨仓同号：Plugin 的 PR 号拿去 server 仓查会查到无关 PR。repo 空就停，不许默认。
        if not pr.get("repo"):
            raise PromoteError(f"PR #{pr.get('number')} 没有 repo 字段——先按标题回 pulls*.jsonl 定位仓")
    if re.search(r"\b" + re.escape(str(j["id"])) + r"\b", j["phenomenon"]):
        raise PromoteError("现象里带了 issue 号")


def load_instances(path):
    out = []
    for inst in common.read_instances(path):
        out.append(Instance(inst["label"], int(inst["port"]), inst.get("build") or "unknown"))
    return out


def _issue(home, num):
    with open(os.path.join(home, "raw", "issues.jsonl"), encoding="utf-8") as f:
        for line in f:
            it = json.loads(line)
            if str(it.get("number")) == str(num):
                return it
    raise PromoteError(f"raw/issues.jsonl 里没有 issue {num}")


def promote(home, j, cases_dir, instances, arch="x86_64", repeat=None):
    validate(j)
    num = str(j["id"])
    cid = f"OG-{num}"
    alias = alias_for(cid)
    src = os.path.join(home, "constructed", num)
    if not os.path.isdir(src):
        raise PromoteError(f"找不到构造材料 {src}")
    meta = {}
    if os.path.exists(os.path.join(src, "meta.json")):
        meta = json.load(open(os.path.join(src, "meta.json"), encoding="utf-8"))
    if meta.get("split_needed"):
        raise PromoteError(f"{cid} 的材料是整段正文抽取，还没拆 setup/workload（拆完把 meta.split_needed 置 false）")
    issue = _issue(home, num)

    by_label = {i.label: i for i in instances}
    unknown = set(j["per_version"]) - set(by_label)
    if unknown:
        raise PromoteError(f"per_version 里的版本 {unknown} 不在 instances.conf 里")
    yes = [i for i in instances if j["per_version"].get(i.label, {}).get("verdict") == "yes"]
    main = yes[0]

    d = os.path.join(cases_dir, cid)
    os.makedirs(d, exist_ok=True)
    identifiers = {}
    for name in ("setup.sql", "workload.sql", "tool.sh", "pre.sh", "post.sh"):
        p = os.path.join(src, name)
        if os.path.exists(p):
            text, ids = rename_identifiers(open(p, encoding="utf-8", errors="replace").read(), num, alias)
            identifiers.update(ids)
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)
            if name.endswith(".sh"):
                os.chmod(os.path.join(d, name), 0o755)
    if not os.path.exists(os.path.join(d, "setup.sql")):
        open(os.path.join(d, "setup.sql"), "w").close()

    with open(os.path.join(d, "issues.json"), "w", encoding="utf-8") as f:
        json.dump(issue, f, ensure_ascii=False, indent=2)

    state_detail = (issue.get("issue_state_detail") or {}).get("title") or ""
    merged = [p for p in j["prs"] if (p.get("state") or "merged") == "merged"]
    flags = dict(j.get("flags") or {})
    if state_detail == "已取消":
        flags.setdefault("NON_DEFECT", "cancelled")
    if issue.get("state") != "closed" and "ISSUE_OPEN" not in flags:
        flags["ISSUE_OPEN"] = "merged_pr" if merged else "yes"

    rc = j["root_cause"]
    phen = j["phenomenon"].replace('"', "'")
    lines = [
        f"# {cid} case 参数",
        f"CASE_DB={j['db']}",
        f"REPEAT={repeat or j.get('repeat') or 2000}",
        f"GT={rc['gt']}            # 3=根因+代码/PR，2=有根因说明，1=仅现象",
        f"HAS_FIX={'yes' if merged else 'unknown'}",
        f"CASE_ALIAS={alias}",
        "",
        f"AFFECTED_VERSION={j.get('affected_version') or '未知'}",
        f"REPRO_VERSION={main.label}",
        f"REPRO_BUILD={main.build}",
        f"REPRO_PORT={main.port}          # 主复现落点；全部复现版本见 REPRO_ALL",
        f"REPRO_ALL={','.join(str(i.port) for i in yes)}",
        f"REPRO_ARCH={arch}",
        f"SYMPTOM_CLASS={j['symptom_class']}",
        "SPLIT=holdout",
        f'PHENOMENON_TEMPLATE="{phen}"',
        "REPRO_TOOL=script",
        f"NEEDS_RESTART={'yes' if meta.get('needs_restart') else 'no'}",
    ]
    for k in ("ISSUE_OPEN", "NON_DEFECT", "EVIDENCE_SUSPECT"):
        if flags.get(k):
            lines.append(f"{k}={flags[k]}")
    with open(os.path.join(d, "case.env"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    with open(os.path.join(d, "prompt.txt"), "w", encoding="utf-8") as f:
        f.write(f"诊断: {{{{WINDOW}}}}(UTC+8)，openGauss 业务库 {j['db']} 上有问题，帮我定位。\n现象：{phen}\n")

    gt = [f"# {cid} ground truth（不喂 agent，只人工打分用）", "",
          f"gitcode issue {num}，{issue.get('state')}/{state_detail}。issue 标的影响版本 {j.get('affected_version') or '未知'}。",
          "", f"标题：{(issue.get('title') or '').strip()}", "", "## 各版本实测", ""]
    for inst in instances:
        pv = j["per_version"].get(inst.label)
        if not pv:
            continue
        gt.append(f"- **{inst.label}**（:{inst.port}，build {inst.build}）：{VERDICTS[pv['verdict']]}")
        for e in pv.get("evidence") or []:
            gt.append(f"  - 证据：`{e}`")
    gt += ["", f"复现于：**{main.label}**（端口 {main.port}；全部复现端口 {','.join(str(i.port) for i in yes)}）",
           "", "## 根因", "", rc["text"], "", f"来源：{rc.get('source') or '无'}"]
    if rc.get("gt_note"):
        gt += ["", f"分档说明：{rc['gt_note']}"]
    gt += ["", "## 关联 PR / commit", ""]
    if j["prs"]:
        for p in j["prs"]:
            gt.append(f"- {p['repo']} PR #{p['number']} {p.get('title') or ''}（head {p.get('head') or '?'}）")
    else:
        gt.append("- 无")
    gt += ["", "## 判读理由（实跑时记的）", "", j.get("judge_reason") or "", "",
           "## 判读依据（构造时写的）", "", meta.get("how_to_judge") or ""]
    gt_text, ids = rename_identifiers("\n".join(gt) + "\n", num, alias)
    # 标题和 issue 号本身要留着给人看，只换对象名
    gt_text = gt_text.replace(f"gitcode issue {alias}", f"gitcode issue {num}")
    identifiers.update({k: v for k, v in ids.items() if k != num})
    with open(os.path.join(d, "ground-truth.md"), "w", encoding="utf-8") as f:
        f.write(gt_text)

    amap_path = os.path.join(cases_dir, "_alias-map.json")
    amap = json.load(open(amap_path, encoding="utf-8")) if os.path.exists(amap_path) else {
        "_note": "盲测别名映射表——只供人工排查/对标，绝不进 prompt。生成规则：sha256(\"dbdog-loop-alias-v1|<CASE-ID>\") "
                 "的十六进制串里第一个首位非数字的 6 位窗口。",
        "_salt": SALT, "cases": {}}
    amap["cases"][cid] = {"alias": alias, "identifiers": identifiers}
    with open(amap_path, "w", encoding="utf-8") as f:
        json.dump(amap, f, ensure_ascii=False, indent=1)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("judged", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--instances", required=True)
    ap.add_argument("--cases", default=None, help="默认 $HARVEST_HOME/cases")
    ap.add_argument("--arch", default="x86_64")
    ap.add_argument("--repeat", type=int, default=None)
    a = ap.parse_args()
    home = common.home()
    cases = a.cases or os.path.join(home, "cases")
    files = sorted(glob.glob(os.path.join(home, "judged", "*.json"))) if a.all else a.judged
    instances = load_instances(a.instances)
    ok = bad = 0
    for p in files:
        try:
            d = promote(home, json.load(open(p, encoding="utf-8")), cases, instances, a.arch, a.repeat)
            print(f"✓ {d}")
            ok += 1
        except PromoteError as e:
            print(f"✗ {p}: {e}")
            bad += 1
    print(f"沉淀 {ok} 条，拒收 {bad} 条")


if __name__ == "__main__":
    main()
