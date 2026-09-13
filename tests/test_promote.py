"""promote 的回归用例：别名规则必须与既有用例库 _alias-map.json 一字不差。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import promote  # noqa: E402


class Alias(unittest.TestCase):
    def test_matches_existing_library(self):
        # 取自 opengauss-issue-corpus/loop/cases/_alias-map.json
        known = {"OG-1723": "cdfef6", "OG-7601": "c0f587", "OG-6939": "abc6f3",
                 "OG-3837": "a3f489", "OG-7317": "f47f78", "OG-8177": "a133e2"}
        for cid, alias in known.items():
            self.assertEqual(promote.alias_for(cid), alias, cid)

    def test_rename_identifiers(self):
        sql = "create table c1723_t(a int); select * from c1723_t; -- c17230 不动"
        out, ids = promote.rename_identifiers(sql, "1723", "cdfef6")
        self.assertIn("ccdfef6_t", out)
        self.assertNotIn("c1723_t", out)
        self.assertIn("c17230", out)
        self.assertEqual(ids, {"c1723_t": "ccdfef6_t"})


class Write(unittest.TestCase):
    def _judged(self):
        return {
            "id": "1723",
            "db": "bench_b",
            "per_version": {
                "5.0.0": {"verdict": "yes", "evidence": ["ERROR:  unrecognized node type: 5015"]},
                "6.0.0": {"verdict": "no", "evidence": ["递归查询跑通"]},
                "7.0.0-RC1": {"verdict": "missing_dep", "evidence": ["无 dolphin"]},
            },
            "judge_reason": "5.0.0 报 unrecognized node type",
            "symptom_class": "failure",
            "phenomenon": "B 兼容库里 SET @变量后在 WITH RECURSIVE 起始查询里引用，报 unrecognized node type",
            "root_cause": {"text": "@变量节点在递归 CTE 复制路径上未被识别", "source": "PR #4648 标题", "gt": 2,
                            "gt_note": "PR 只有标题"},
            "prs": [{"repo": "opengauss/openGauss-server", "number": 4648, "title": "修复", "head": "916f711"}],
            "flags": {},
        }

    def test_writes_case_dir(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(f"{home}/constructed/1723")
            open(f"{home}/constructed/1723/setup.sql", "w").write("create table c1723_t(a int);\n")
            open(f"{home}/constructed/1723/workload.sql", "w").write("select * from c1723_t;\n")
            json.dump({"id": "1723", "db": "bench_b", "how_to_judge": "看报错"},
                      open(f"{home}/constructed/1723/meta.json", "w"))
            os.makedirs(f"{home}/raw")
            with open(f"{home}/raw/issues.jsonl", "w") as f:
                f.write(json.dumps({"number": "1723", "title": "t", "state": "closed", "body": "b",
                                    "html_url": "u", "issue_state_detail": {"title": "已验收"}}) + "\n")
            instances = [promote.Instance("7.0.0-RC1", 5432, "12c995f"), promote.Instance("5.0.0", 6432, "a07d57c3"),
                         promote.Instance("6.0.0", 7432, "aee4abd5")]
            cases = f"{home}/cases"
            d = promote.promote(home, self._judged(), cases, instances, arch="x86_64")
            env = open(f"{d}/case.env").read()
            self.assertIn("CASE_ALIAS=cdfef6", env)
            self.assertIn("REPRO_PORT=6432", env)
            self.assertIn("REPRO_ALL=6432", env)
            self.assertIn("GT=2", env)
            self.assertIn("CASE_DB=bench_b", env)
            self.assertIn("ccdfef6_t", open(f"{d}/setup.sql").read())
            self.assertNotIn("1723", open(f"{d}/prompt.txt").read())
            self.assertIn("{{WINDOW}}", open(f"{d}/prompt.txt").read())
            gt = open(f"{d}/ground-truth.md").read()
            self.assertIn("PR #4648", gt)
            self.assertIn("缺依赖", gt)
            amap = json.load(open(f"{cases}/_alias-map.json"))
            self.assertEqual(amap["cases"]["OG-1723"]["alias"], "cdfef6")

    def test_refuses_without_yes(self):
        j = self._judged()
        for v in j["per_version"].values():
            v["verdict"] = "no"
        with tempfile.TemporaryDirectory() as home:
            with self.assertRaises(promote.PromoteError):
                promote.validate(j)

    def test_refuses_pr_without_repo(self):
        # 跨仓同号：repo 为空不许默认成 server 仓
        j = self._judged()
        j["prs"][0]["repo"] = None
        with self.assertRaises(promote.PromoteError):
            promote.validate(j)


if __name__ == "__main__":
    unittest.main()
