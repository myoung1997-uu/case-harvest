"""stats 的六桶口径回归：每桶判据、标记优先级、未跑完告警不入桶、加总自洽。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import stats  # noqa: E402


def judged(n, verdicts, **kw):
    """judged/<n>.json 夹具，形状照 test_promote._judged（真实判读产物）。"""
    j = {
        "id": n, "db": "bench_b",
        "per_version": {v: {"verdict": v, "evidence": []} for v in verdicts},
        "judge_reason": "", "symptom_class": "failure",
        "phenomenon": "现象",
        "root_cause": {"text": "根因", "source": "PR 标题", "gt": 2},
        "prs": [{"repo": "opengauss/openGauss-server", "number": 1, "title": "修复",
                 "state": "merged", "head": "abc1234"}],
        "flags": {},
    }
    j.update(kw)
    return j


def write_case_env(home, n, flag_lines=()):
    d = os.path.join(home, "cases", f"OG-{n}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "case.env"), "w") as f:
        f.write("CASE_DB=bench\n" + "".join(f"{l}\n" for l in flag_lines))


class SixBuckets(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        h = self.home.name
        # 13 个候选覆盖全部桶
        ids = ["100", "101", "102", "103", "104", "105",
               "106", "107", "108", "109", "110", "111", "112"]
        self.rec = {"round": 1, "at": "2026-09-16T10:00:00",
                    "funnel": {"全量": 12000, "有已合并PR": 13}, "candidates": ids}
        with open(os.path.join(h, "rounds.jsonl"), "w") as f:
            f.write(json.dumps(self.rec) + "\n")

        def wj(n, verdicts, **kw):
            with open(os.path.join(h, "judged", f"{n}.json"), "w") as f:
                json.dump(judged(n, verdicts, **kw), f, ensure_ascii=False)

        os.makedirs(os.path.join(h, "judged"))
        wj("100", ["yes"])                                   # 沉淀且干净 → 可复现
        wj("101", ["yes"])                                   # 负样本
        wj("102", ["yes"])                                   # 存疑
        wj("103", ["yes"])                                   # 未定论
        wj("104", ["yes"])                                   # merged_pr 属已定论 → 可复现
        wj("105", ["yes"])                                   # 未沉淀，靠 derive_flags 推导
        wj("108", ["no", "no"])                              # 非脚本 all_no
        wj("109", ["construct_fail"])                        # 脚本侧
        wj("110", ["no", "uncertain"])                       # 非脚本 uncertain
        wj("111", ["no"], script_bug="引用了不存在的列")       # 脚本侧（script_bug）
        wj("112", ["missing_dep"])                           # 非脚本 missing_dep

        write_case_env(h, "100")
        write_case_env(h, "101", ["NON_DEFECT=cancelled"])
        write_case_env(h, "102", ["EVIDENCE_SUSPECT=yes"])
        write_case_env(h, "103", ["ISSUE_OPEN=yes"])
        write_case_env(h, "104", ["ISSUE_OPEN=merged_pr"])

        # 105 未沉淀：issue 还开着但有已合并修复 → merged_pr → 可复现（推导路径）
        os.makedirs(os.path.join(h, "raw"))
        with open(os.path.join(h, "raw", "issues.jsonl"), "w") as f:
            f.write(json.dumps({"number": "105", "state": "open",
                                "issue_state_detail": {"title": "进行中"}}) + "\n")

        # 106 材料在、没判读 → 未跑完；107 连材料都没有 → 脚本侧
        os.makedirs(os.path.join(h, "constructed", "106"))
        json.dump({"id": "106"}, open(os.path.join(h, "constructed", "106", "meta.json"), "w"))

    def tearDown(self):
        self.home.cleanup()

    def test_buckets(self):
        buckets, ids, detail = stats.compute(self.home.name, os.path.join(self.home.name, "cases"), self.rec)
        self.assertEqual(buckets["reproducible"], 3)
        self.assertEqual(ids["reproducible"], ["100", "104", "105"])
        self.assertEqual(buckets["negative"], 1)
        self.assertEqual(buckets["suspect"], 1)
        self.assertEqual(buckets["undecided"], 1)
        self.assertEqual(buckets["nonrepro_script"], 3)
        self.assertEqual(sorted(ids["nonrepro_script"]), ["107", "109", "111"])
        self.assertEqual(buckets["nonrepro_other"], 3)
        self.assertEqual(detail, {"all_no": 1, "uncertain": 1, "missing_dep": 1})
        # 未跑完单独一桶，不算进六桶
        self.assertEqual(buckets["unfinished"], 1)
        self.assertEqual(ids["unfinished"], ["106"])
        # 加总自洽
        self.assertEqual(sum(buckets.values()), len(self.rec["candidates"]))

    def test_flag_precedence_negative_first(self):
        """NON_DEFECT + EVIDENCE_SUSPECT 同标时算负样本（口径定的优先级）。"""
        h = self.home.name
        write_case_env(h, "104", ["NON_DEFECT=cancelled", "EVIDENCE_SUSPECT=yes"])
        buckets, ids, _ = stats.compute(h, os.path.join(h, "cases"), self.rec)
        self.assertIn("104", ids["negative"])
        self.assertNotIn("104", ids["suspect"])
        self.assertEqual(buckets["reproducible"], 2)


if __name__ == "__main__":
    unittest.main()
