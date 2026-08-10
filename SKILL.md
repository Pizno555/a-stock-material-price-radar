---
name: material-price-radar
description: Discover, verify, score, and rank material price-hike and supply-demand inflection signals with a deterministic 100-point rubric and A-share confirmation. Use for 材料涨价雷达, 涨价链, 原材料价格异动, 封盘惜售, 取消折扣, 供给收缩, 库存去化, 交期延长, 订单加速, 检修停产, 调价函核验, 产业链涨价线索, A股受益材料筛选, or ranked Markdown, JSON, local HTML, or PNG research covering commodity, chemical, metal, semiconductor, PCB, battery, energy, or advanced materials.
---

# 材料涨价与供需拐点雷达

发现最近窗口内已经发生的价格变化，以及已经发生但尚未完全传导到价格的高质量供需变化。先研究事实，再按固定规则评分；总分衡量涨价逻辑和供需变化强度，不代表股票买入价值。

## 默认参数

- 截止今天。
- 近 10 个自然日。
- 动态发现候选。
- 最多输出 30 条。
- 默认在对话中返回 Markdown。
- 新研究使用顶层 `schema_version: "2.1"`。

## 执行流程

1. 确认截止日期、窗口、类别、数量和输出格式。
2. 按 [methodology.md](references/methodology.md) 的七轮矩阵扫描直接涨价、价格前动作、未来供给收缩、需求加速、库存交期、贸易政策和反证。
3. 统一材料别名、规格、地区和计价口径；纯股票异动不能建立候选。
4. 优先核验公告、统计、交易所、协会、海关、权威价格机构、企业一手材料和实名产业链信息；当前信息必须检索，不依赖记忆。
5. 为证据设置唯一 `evidence_id`、去重用 `event_id`、独立性用 `independence_group`，并按 [data-contract.md](references/data-contract.md) 设置 `supports` 与各维度 `evidence_refs`。
6. 主动寻找价格回落、库存回升、复产扩产、进口增加、需求走弱、客户抵制和替代材料等反证。
7. 运行 `scripts/score_radar.py`；不得手算后覆盖脚本分数、Gate、状态或排序。
8. 按 [output-formats.md](references/output-formats.md) 先给总览，再给证据附录。

## 研究纪律

- 区分调价意向、已实施价格前动作、正式调价函、公开报价和成交结算。
- `preprice_action` 只表示封盘、惜售、取消折扣、缩短报价期、限量供应等已经发生的商务动作。
- 区分静态库存低与持续去库，区分单点检修与结构性约束，说明持续时间和可逆性。
- A 股异动只确认已有材料逻辑，不得用股价上涨反推材料涨价。
- 消息密度和来源质量只使用实际支持非零价格或供给子项的基本面证据；A 股行情和纯反证不得抬高这两项。
- 每个非零子项必须有窗口内唯一证据及相应 `supports` 标签；旧 v2 输入仅按 legacy coarse binding 兼容运行。
- 同一转载链、发布者或原始事件不得重复加分；D 级讨论只能作为线索。
- 缺失、错配或无支持字段按 0 分并记录缺口，不猜测补值。
- `forward_catalyst` 必须是可验证的 scheduled 或 ongoing 变化；强反证只阻断 Supply Forward，不否认已成立的 Price Confirmed。
- 研究结论不承诺收益；材料逻辑与股票是否值得交易分开。

## 五项评分与双路径

总分仍为：消息密度 20 + 来源质量 25 + 价格验证 25 + 供给约束 20 + A 股异动 10。

状态仍为：65–100 高确定性、50–64 发酵中、35–49 观察、0–34 证据不足。

总分达到 65 后还必须通过一个 Gate：

- `price_confirmed`：来源质量 ≥15、价格验证 ≥13，且阶段至少为正式调价函、公开报价或成交结算。
- `supply_forward`：来源质量 ≥15、消息密度 ≥12、供给约束 ≥14、Catalyst 有效且没有 Blocking Counterevidence。

两个路径同时满足时优先 `price_confirmed`；均不满足时固定降为“发酵中”。

## 评分命令

```powershell
python scripts/score_radar.py input.json --format json --output scored.json
python scripts/score_radar.py scored.json --from-scored --format markdown --output report.md
```

用户要求网页时，再运行：

```powershell
python scripts/render_radar.py scored.json --output report.html
```

`scored.json` 是 Markdown、HTML 和 PNG 的唯一数据源；输出阶段不得重新评分。

PNG 必须由本地 HTML 做真实全页截图，不使用生成式图片伪造表格。

## 完成检查

- 榜单不超过 30 条，截止日期和窗口明确。
- 总分严格等于五项之和，范围 0–100，方法版本为 `2.1.0`。
- `gate_path`、价格阶段、Catalyst、反证、受益股和缺口均可追溯。
- Price Confirmed 不能被 `intent` 或 `preprice_action` 绕过。
- 价格和供给封顶只使用各自维度的有效来源。
- Markdown、JSON、HTML、PNG 保持相同分数、排序、Gate、反证和缺口。
- 未完成历史 As-of Replay 时，只声明规则增强了早期信号识别，不宣称已经证明预测未来涨价或投资收益有效。
