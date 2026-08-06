---
name: material-price-radar
description: Discover, verify, score, and rank material price-hike signals with a 100-point evidence rubric and A-share mapping. Use for 材料涨价雷达, 涨价链, 原材料价格异动, 供给收缩, 调价函核验, 产业链涨价线索, A股受益材料筛选, or when the user wants a ranked Markdown, JSON, local HTML, or PNG radar covering recent commodity, chemical, metal, semiconductor, PCB, battery, energy, or advanced-material signals.
---

# 材料涨价雷达

生成可审计的材料涨价排序。默认先研究事实，再按固定规则评分；网页和图片只是可选呈现。

## 执行流程

1. 确认参数。未指定时使用截止今日、近 10 个自然日、动态发现、最多 30 条、Markdown 对话输出。
2. 发现候选。扫描化工、金属与资源、半导体/电子材料、PCB、锂电、能源和先进材料；也接受用户指定材料或分类。
3. 核验证据。优先使用正式公告、官方统计、交易所、行业协会、权威价格机构、可追溯企业信息和产业链交叉验证。需要当前信息时必须检索，不能依赖记忆。
4. 去重归并。合并转载、同源稿、同一调价事件和材料别名；只把独立事件计入消息密度。
5. 剔除伪候选。若只有股票异动、市场讨论或概念炒作，却没有材料价格或供给信号，不纳入榜单。
6. 按 [methodology.md](references/methodology.md) 分级证据并整理为 [data-contract.md](references/data-contract.md) 的结构。为每条证据设置唯一 `evidence_id`，并由 `price`、`supply`、`a_share` 的 `evidence_refs` 明确绑定；未绑定字段不得计分。
7. 运行 `scripts/score_radar.py` 计算分项、总分、状态、证据门槛、缺口和稳定排序。不要手算后覆盖脚本结果。
8. 先给结论总览，再给证据附录。使用 [output-formats.md](references/output-formats.md) 的结构。

## 研究纪律

- 区分调价意向、报价上涨与已成交涨价；三者不能互换。
- 区分供给扰动、真实缺口与需求拉动；说明持续时间和可逆性。
- 将 A 股异动仅作为末端确认，不得用股价上涨反推材料涨价成立。
- 为每个关键事实记录日期、发布者、链接、证据等级、原始表述或数据点。
- 只把窗口内且被评分字段明确引用的证据用于消息密度和来源质量；来源质量只接受 HTTP(S) 直达链接。
- `evidence_refs` 引用的页面必须直接支持该维度事实；除非同一来源同时明确披露多类事实，不得为凑分跨维度复用。
- 同一发布者或转载链只算一个独立来源。市场讨论只能作为 D 级线索。
- 主动寻找价格回落、库存回升、产能恢复、需求不及预期等反证。
- 缺失字段按 0 分并写入数据缺口；不得猜测或用行业常识补值。
- 最终内容属于研究框架，不承诺收益；把公司受益逻辑与股票是否值得交易分开。

## 数据和工具选择

- 优先调用环境中可用的金融搜索、公告、行情和 A 股数据能力；不可用时改用公开网页和官方来源。
- 价格验证优先使用交易/结算、可复核价格序列、权威报价或多供应商一致证据。
- A 股异动使用至少 3 只直接受益股组成等权篮子，与中证全指或等价宽基比较。少于 3 只时保留结果，但脚本会标记低覆盖并限制该项得分。
- 搜索无结果或接口失败时继续使用其他来源，并明确记录失败和未覆盖项。

## 评分命令

规范化数据后运行：

```powershell
python scripts/score_radar.py input.json --format markdown
python scripts/score_radar.py input.json --format json --output scored.json
```

输入可以带人工写明的 `data_gaps`，脚本还会自动补充可计算字段的缺口。结果按总分、价格验证、来源质量、消息密度、材料名称稳定排序。

## 输出选择

- 用户未指定格式：直接返回 Markdown 总览和证据附录，不创建文件。
- 用户要求 JSON：输出评分后的审计数据。
- 用户要求网页：先生成评分 JSON，再运行 `scripts/render_radar.py scored.json --output report.html`；只生成本地文件。
- 用户要求 PNG：先生成本地 HTML，再用可用的本地浏览器做全页截图。不要用生成式图片替代真实表格。
- 用户要求完整研究报告：在标准附录后扩展产业链位置、受益路径、兑现节奏、反证和风险，不改变评分口径。

## 完成检查

- 确认榜单不超过 30 条且监控窗口写清楚。
- 确认总分等于五项分数之和，范围为 0–100。
- 确认“高确定性”同时满足总分、来源质量和价格验证门槛。
- 确认所有外部事实有近邻链接，所有受益股都有直接性标签与依据。
- 确认每个非零价格、供给和 A 股分项都有有效 `evidence_refs`，且直接受益股覆盖数已按代码或名称去重。
- 确认网页/图片保留与 Markdown 相同的数据、排序、门槛和缺口。
