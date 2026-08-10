# 输出格式 v2.1

JSON 是 Markdown、HTML 和 PNG 的唯一数据源。任何输出层不得重新计算、改分或重排。

先生成评分 JSON，再从该文件渲染 Markdown：

```powershell
python scripts/score_radar.py input.json --format json --output scored.json
python scripts/score_radar.py scored.json --from-scored --format markdown --output report.md
```

## 默认 Markdown

先输出标题、截止日期、窗口、候选数量、schema和方法版本，再输出：

| 材料链 | 分类 | 总分 | 状态 | 确认路径 | 价格阶段 | 核心催化 |
|---|---|---:|---|---|---|---|

按脚本顺序展示，最多30条。

## 证据附录

每条候选依次展示：

1. 五项得分、总分、状态。
2. `gate_path`、Gate是否通过和失败原因。
3. 核心催化。
4. 价格阶段、`change_basis`、涨幅、覆盖和持续性。
5. 库存、开工/交期、扰动和需求证据。
6. Forward Catalyst 的 `status`、`type`、`timing_basis`、开始/结束/观察日期、证据引用和校验结果。
7. A股异动、直接/间接受益公司及依据。
8. ordinary 与 blocking 反证。
9. 失效条件。
10. 数据缺口和 legacy 提示。
11. 证据表：等级、日期、发布者、事实、`supports`、直达链接。

只引用直接支持相邻事实的来源，不引用搜索结果页。

## 本地网页

使用：

```powershell
python scripts/render_radar.py scored.json --output report.html
```

单文件HTML必须展示与Markdown相同的：

- 分数、顺序和状态；
- 确认路径与Gate原因；
- 价格阶段和Catalyst；
- 证据支持标签；
- A股受益公司；
- ordinary/blocking反证；
- 失效条件、缺口和排除候选。

评分后因超过 `max_items` 被截断的候选也必须在排除区显示，不能静默消失。

视觉继续使用浅暖灰背景、红橙横幅、五项分数卡和折叠证据详情；桌面端总览七列，窄屏转卡片。HTML只读JSON，不在浏览器端计算。

## PNG

先生成HTML，再用本地浏览器做全页截图。不得用生成式图片替代真实表格文字。桌面参考宽度1250px。

## JSON

保留全部规范化事实、支持标签、子分、信源封顶、Catalyst校验、Gate、反证、缺口和稳定排序。JSON必须包含 `schema_version` 和 `method_version`。
