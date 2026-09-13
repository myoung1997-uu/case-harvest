# 溯根因 agent prompt（阶段 6，只对有版本 `yes` 的用例）

---

你负责给下面这些已复现的 openGauss 缺陷写根因。你不连数据库。

issue 号：{IDS}

## 材料与顺序（按顺序，别跳步）

对每个 issue 先跑 `HARVEST_HOME={HARVEST_HOME} python3 {SKILL_DIR}/scripts/show_issue.py <n>`，再按下面顺序取根因：

1. **关联 PR**（输出里的「关联 PR」段）——社区自己标的正向关联，最可靠。
2. **PR 正文的【根因分析】，外加【实现方案】**——大量 PR 把根因写在实现方案栏，只看根因分析会漏。
3. **PR 标题**——「增加 X 不支持 Y 的限制」「删除不存在的 Z 函数」这类，处置方式本身就是根因。
4. **issue 评论**——逐条读语义。工程师写的是「导致」「因为」这种大白话，不会规范地写「根因」。
5. **源码回溯**（有源码树时）——报错原文去源码 grep 定位抛出点。通用抛出点（如 `pl_exec.cpp` 的通用语法错）没有定位价值，别拿它当根因。

## 三个坑

- **跨仓同号**：标「⚠ 仓未定」或「标题对不上」的 PR，正文一个字都不许用；PR 的 `repo` 必须如实填，定不了就不列这个 PR。
- **PR 正文误粘模板**：标题讲 A、正文讲 B 时只信标题。
- **别拿报错文本去匹配 PR 描述**：PR 写「修了什么」，报错写「长什么样」，用词不重叠，匹配出来的都是假的。

## GT 分档

- `3`：根因说到代码位置（文件/函数），或有明确的 PR/commit 且 PR 讲清了机制
- `2`：有机制说明但没到代码行——**够用，不必强求代码级**
- `1`：只有现象，没有机制

别用根因文字长短定档。PR 只有标题、讲不清机制的，给 2 并在 `gt_note` 写原因。

## 社区状态

- issue `stateDetail=已取消` → `flags.NON_DEFECT="cancelled"`（社区判非缺陷，多为兼容模式既定语义）。**留着当负样本**，根因写社区给的「为什么这是设计行为」。
- issue 未关闭且没有已合并 PR → `flags.ISSUE_OPEN="yes"`；未关闭但修复 PR 已合入 → `"merged_pr"`。

## 产出

在 `{HARVEST_HOME}/judged/<n>.json` 里**合并写入**（保留判读员写的字段）：

```json
{
  "root_cause": {"text": "机制说明", "source": "PR #4648 标题 / PR #xxx【实现方案】/ issue 评论 2024-03-01", "gt": 2, "gt_note": "为什么是这一档"},
  "prs": [{"repo": "opengauss/openGauss-server", "number": 4648, "title": "…", "state": "merged", "head": "916f711…"}],
  "affected_version": "issue 标的影响版本，没有写 未知",
  "flags": {"NON_DEFECT": null, "ISSUE_OPEN": null}
}
```

## 回报

一张表：`issue | GT | 根因来源 | 一句话根因`。
