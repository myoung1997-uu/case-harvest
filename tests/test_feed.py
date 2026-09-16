"""feed 的 prep/check 回归：manifest 字段映射、未沉淀拦截、封账去重、自检抓违规。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
import stats  # noqa: E402

from test_stats import judged, write_case_env  # noqa: E402

GOOD_RUN = """#!/usr/bin/env bash
set -euo pipefail
: "${PGHOST:?平台未注入 PGHOST}"
echo "JUDGE_DEMO|ok"
exit 0
"""
GOOD_SETUP = """#!/usr/bin/env bash
set -euo pipefail
: "${PGDATABASE:?平台未注入 PGDATABASE}"
exit 0
"""


class Feed(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.h = self.home.name
        ids = ["100", "101", "104", "105", "107"]
        self.rec = {"round": 1, "at": "2026-09-16T10:00:00",
                    "funnel": {"全量": 12000, "有已合并PR": 5}, "candidates": ids}
        with open(os.path.join(self.h, "rounds.jsonl"), "w") as f:
            f.write(json.dumps(self.rec) + "\n")

        os.makedirs(os.path.join(self.h, "judged"))
        cases = {"100": ("failure", []), "101": ("failure", ["NON_DEFECT=cancelled"]),
                 "104": ("slowness", ["ISSUE_OPEN=merged_pr"])}
        for n, (symptom, flag_lines) in cases.items():
            j = judged(n, ["yes"], symptom_class=symptom)
            with open(os.path.join(self.h, "judged", f"{n}.json"), "w") as f:
                json.dump(j, f, ensure_ascii=False)
            write_case_env(self.h, n, flag_lines)
            # feed 读 case.env 的 REPRO_*/SYMPTOM_CLASS/CASE_DB/PHENOMENON_TEMPLATE
            with open(os.path.join(self.h, "cases", f"OG-{n}", "case.env"), "a") as f:
                f.write("REPRO_VERSION=5.0.0\nREPRO_BUILD=12c995f\n"
                        f'SYMPTOM_CLASS={symptom}\nPHENOMENON_TEMPLATE="业务库上查询报错"\n')
            d = os.path.join(self.h, "constructed", n)
            os.makedirs(d)
            json.dump({"id": n, "how_to_judge": "看 JUDGE_ROWS 输出"}, open(os.path.join(d, "meta.json"), "w"))
        # 105 判 yes 但不沉淀 → prep 要拦；107 判 construct_fail（脚本侧，不进批次）
        with open(os.path.join(self.h, "judged", "105.json"), "w") as f:
            json.dump(judged("105", ["yes"]), f, ensure_ascii=False)
        with open(os.path.join(self.h, "judged", "107.json"), "w") as f:
            json.dump(judged("107", ["construct_fail"]), f, ensure_ascii=False)

        # stats.json 由 stats.compute 生成（feed 的唯一数据源，不手造）
        buckets, ids_map, detail = stats.compute(self.h, os.path.join(self.h, "cases"), self.rec)
        os.makedirs(os.path.join(self.h, "rounds"))
        with open(os.path.join(self.h, "rounds", "1.stats.json"), "w") as f:
            json.dump({"round": 1, "at": self.rec["at"],
                       "scope": {"total_issues": 12000, "filtered_issues": len(self.rec["candidates"])},
                       "buckets": buckets, "detail_nonrepro_other": detail, "ids": ids_map}, f)
        self.outbox = os.path.join(self.h, "outbox")

    def tearDown(self):
        self.home.cleanup()

    def _feed(self, *args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "feed.py"), *args],
                              capture_output=True, text=True, env={**os.environ, "HARVEST_HOME": self.h})

    def _batch(self):
        return os.path.join(self.outbox, "harvest-r1-20260916")

    def _promote_105(self):
        """105 判了 yes 但 setUp 里没沉淀——补一个 B 兼容的 case.env 再 prep。"""
        d = os.path.join(self.h, "cases", "OG-105")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "case.env"), "w") as f:
            f.write("CASE_DB=bench_b\nREPRO_VERSION=5.0.0\nREPRO_BUILD=12c995f\n"
                    "SYMPTOM_CLASS=failure\nPHENOMENON_TEMPLATE=\"查询返回行数不对\"\n")

    def test_prep_refuses_unpromoted_yes(self):
        r = self._feed("prep", "--outbox", self.outbox)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("promote", r.stdout + r.stderr)

    def test_prep_manifest_and_close(self):
        self._promote_105()
        r = self._feed("prep", "--outbox", self.outbox, "--close")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        mf = json.load(open(os.path.join(self._batch(), "manifest.json")))
        self.assertEqual(mf["total_issues"], 12000)
        self.assertEqual(mf["filtered_issues"], 5)
        self.assertEqual(mf["repro_stats"]["reproducible"], 3)   # 100/104/105；101 是负样本不进
        self.assertEqual(mf["repro_stats"]["nonrepro_script"], 1)  # 107
        self.assertEqual(mf["repro_stats"]["negative"], 1)        # 101
        self.assertEqual(sum(mf["repro_stats"].values()), 5)   # 六桶之和 = filtered
        self.assertEqual([c["case_number"] for c in mf["cases"]], ["OG-100", "OG-104", "OG-105"])
        c100, c104, c105 = mf["cases"]
        self.assertEqual(c100["engine"], "opengauss")
        self.assertEqual(c100["compatibility"], "A")           # CASE_DB=bench
        self.assertEqual(c105["compatibility"], "B")           # bench_b
        self.assertEqual(c100["timeout_seconds"], 600)         # failure
        self.assertEqual(c104["timeout_seconds"], 900)         # slowness
        self.assertEqual(c100["engine_version"], "5.0.0")
        self.assertEqual(c100["commit_id"], "12c995f")
        self.assertEqual(c100["judge_criteria"], "看 JUDGE_ROWS 输出")
        self.assertTrue(c100["root_cause_fix"].startswith("【根因】\n"))
        self.assertIn("【修复】", c100["root_cause_fix"])
        self.assertIn("PR #1", c100["root_cause_fix"])
        self.assertEqual(c100["reproduce"], {"mode": "script", "setup": "setup.sh",
                                             "run": "run.sh", "cleanup": "cleanup.sh"})
        q = [json.loads(l) for l in open(os.path.join(self._batch(), "work-queue.jsonl"))]
        self.assertEqual(len(q), 3)
        self.assertTrue(os.path.isdir(os.path.join(self._batch(), "OG-100")))
        # 封账：本轮候选全进 tried.jsonl
        tried = {json.loads(l)["id"] for l in open(os.path.join(self.h, "tried.jsonl"))}
        self.assertEqual(tried, set(self.rec["candidates"]))

    def test_check_rejects_then_passes(self):
        self._promote_105()
        self._feed("prep", "--outbox", self.outbox)
        # 没写脚本 → check 拦
        r = self._feed("check", self._batch())
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("缺 run.sh", r.stdout)
        # 写脚本：100 全合规；104 的 run.sh 写死连接；105 的 run.sh 没有判定路径
        d = os.path.join(self._batch(), "OG-100")
        for name, text in (("setup.sh", GOOD_SETUP), ("run.sh", GOOD_RUN),
                           ("cleanup.sh", GOOD_SETUP)):
            open(os.path.join(d, name), "w").write(text)
        d = os.path.join(self._batch(), "OG-104")
        for name, text in (("setup.sh", GOOD_SETUP), ("run.sh", GOOD_RUN.replace(
                ': "${PGHOST:?平台未注入 PGHOST}"', "PGHOST=127.0.0.1")),
                ("cleanup.sh", GOOD_SETUP)):
            open(os.path.join(d, name), "w").write(text)
        d = os.path.join(self._batch(), "OG-105")
        for name, text in (("setup.sh", GOOD_SETUP),
                           ("run.sh", GOOD_RUN.replace('echo "JUDGE_DEMO|ok"\n', "").replace("exit 0", "exit 1")),
                           ("cleanup.sh", GOOD_SETUP)):
            open(os.path.join(d, name), "w").write(text)
        r = self._feed("check", self._batch())
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("连接参数写死了", r.stdout)
        self.assertIn("OG-105: run.sh", r.stdout)   # 既无 JUDGE_ 也无 exit 2
        # 修正后通过
        open(os.path.join(self._batch(), "OG-104", "run.sh"), "w").write(GOOD_RUN)
        open(os.path.join(self._batch(), "OG-105", "run.sh"), "w").write(GOOD_RUN)
        r = self._feed("check", self._batch())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("自检通过", r.stdout)


if __name__ == "__main__":
    unittest.main()
