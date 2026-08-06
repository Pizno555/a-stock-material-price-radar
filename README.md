# Material Price Radar

`material-price-radar` 是一个面向材料涨价线索的研究型 Codex Skill。它会发现候选、核验证据、去重信源，按 100 分规则排序，并映射可能受益的 A 股公司。

它关注材料价格与实物供需，不会把单纯的股价上涨或概念炒作反推为材料基本面改善。

> 当前评分方法版本：`2.0.0`

## 快速使用

在 Codex 中直接调用：

```text
使用 $material-price-radar 扫描近10天材料涨价信号。
```

临时指定参数：

```text
使用 $material-price-radar，截至2026-08-01，观察近20个自然日，
最多输出15条，只看半导体材料和PCB，生成Markdown总览与证据附录。
```

研究指定材料：

```text
使用 $material-price-radar 研究电子级氢氟酸近30天的价格、供给和A股受益链。
```

生成本地网页：

```text
使用 $material-price-radar 扫描近10天材料涨价信号，并生成本地HTML报告。
```

## 默认参数

| 参数 | 默认值 | 可调整范围 |
|---|---:|---|
| 截止日期 | 当天 | 任意有效日期 |
| 监控窗口 | 近 10 个自然日 | `window_days >= 1` |
| 榜单数量 | 最多 30 条 | 1–30 条 |
| 候选范围 | 动态发现 | 可指定材料或分类 |
| 输出 | 对话内 Markdown | Markdown、JSON、本地 HTML 或 PNG |

窗口和榜单数量可以在每次调用时临时修改，不会改变 Skill 的默认设置。

## 评分体系

| 维度 | 满分 | 主要衡量内容 |
|---|---:|---|
| 消息密度 | 20 | 独立事件数量、来源类型、近期消息加速度 |
| 来源质量 | 25 | 最强三个独立信源的 S/A/B/C/D 等级 |
| 价格验证 | 25 | 证据形态、已验证涨幅、覆盖面、持续时间 |
| 供给约束 | 20 | 库存、开工率或交期、供给扰动、供需缺口 |
| A 股异动 | 10 | 直接受益股篮子的超额收益、广度、成交与持续性 |

状态区间：

- 65–100：高确定性
- 50–64：发酵中
- 35–49：观察
- 0–34：证据不足

“高确定性”还必须同时满足：

- 来源质量不低于 15 分
- 价格验证不低于 13 分

任一门槛不满足时，即使总分达到 65，也会降为“发酵中”。

完整评分细则见 [references/methodology.md](references/methodology.md)。

## 证据规则

- 优先使用公告、政府统计、交易所、行业协会、海关、权威价格机构和可追溯企业信息。
- 合并转载、同源稿和同一原始事件，避免重复加分。
- 每条评分证据必须处于监控窗口内，并提供 HTTP(S) 直达链接。
- `price`、`supply`、`a_share` 必须通过 `evidence_refs` 绑定支持该事实的证据。
- 调价意向、公开报价和实际成交不能混为一谈。
- 缺失数据按 0 分处理，并写入数据缺口，不猜测、不补值。
- 只有股票异动而没有材料价格或供给信号的候选不会进入榜单。

规范化 JSON 结构见 [references/data-contract.md](references/data-contract.md)。

## 输出内容

默认输出包括：

1. 标题、截止日期、监控窗口和覆盖数量。
2. 按分数排序的材料链总览。
3. 每条候选的五项得分理由。
4. 日期、来源等级、发布者、事实与直达链接。
5. 2–5 家直接或间接受益 A 股公司及映射依据。
6. 反证、失效条件和数据缺口。

HTML 和 PNG 只在用户明确要求时生成，并默认保存在本地，不发布到外部。详细格式见 [references/output-formats.md](references/output-formats.md)。

## 命令行使用

准备符合数据契约的 UTF-8 JSON 后，在 Skill 目录运行：

```powershell
python scripts/score_radar.py input.json --format markdown
python scripts/score_radar.py input.json --format json --output scored.json
python scripts/render_radar.py scored.json --output report.html
```

评分 JSON 是 Markdown、HTML 和 PNG 的唯一数据源；不要在输出阶段手工改分或重新排序。

## 验证

运行评分回归测试：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
python scripts/test_score_radar.py
```

运行 Skill 结构校验：

```powershell
$env:PYTHONUTF8='1'
python ..\.system\skill-creator\scripts\quick_validate.py .
```

## 目录说明

```text
material-price-radar/
├── SKILL.md                 # Codex 执行说明与触发信息
├── agents/openai.yaml       # UI 名称和默认调用提示
├── scripts/                 # 评分、测试和 HTML 渲染脚本
├── references/              # 方法、数据契约和输出规范
└── assets/                  # 网页模板、样式和视觉参考
```

## 免责声明

本 Skill 用于研究排序和证据审计，不构成投资建议，不承诺收益。材料涨价逻辑、上市公司受益路径与股票是否值得交易应分别判断。
