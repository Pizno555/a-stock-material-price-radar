# 数据契约

## 目录

- 顶层字段
- 候选结构
- 证据结构
- 价格结构
- 供给结构
- A 股结构
- 评分结果

评分脚本接收 UTF-8 JSON。顶层结构如下：

```json
{
  "title": "材料涨价雷达",
  "as_of": "2026-08-06",
  "window_days": 10,
  "max_items": 30,
  "candidates": []
}
```

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | 否 | 默认“材料涨价雷达” |
| `as_of` | 是 | 截止日期，`YYYY-MM-DD` |
| `window_days` | 否 | 自然日窗口，默认 10，必须 ≥1 |
| `max_items` | 否 | 默认 30，范围 1–30 |
| `candidates` | 是 | 候选数组 |

## 候选结构

```json
{
  "material_chain": "电子级氢氟酸/AHF",
  "category": "半导体材料/氟化工",
  "core_catalyst": "供应链承压并出现可验证报价上调",
  "evidence": [],
  "price": {},
  "supply": {},
  "a_share": {},
  "counterevidence": [],
  "invalidation_conditions": [],
  "data_gaps": []
}
```

`material_chain` 必填且在一个输入中唯一。其余字段缺失时按 0 分或空列表处理，并自动记录缺口。

## 证据结构

```json
{
  "evidence_id": "source-row-001",
  "event_id": "ahf-price-notice-20260801",
  "independence_group": "producer-a-notice",
  "date": "2026-08-01",
  "title": "供应商调价通知",
  "publisher": "供应商A",
  "url": "https://example.com/source",
  "tier": "A",
  "source_class": "company",
  "claim": "指定规格自8月起上调报价"
}
```

- `evidence_id`：每条证据行的唯一标识，供各评分维度的 `evidence_refs` 引用；缺失或重复的证据不能绑定评分字段。
- `event_id`：同一原始事件及其转载必须相同；缺失时用 `independence_group` 代替。
- `independence_group`：同一发布者、转载链或非独立证据必须相同。
- `date`：用于窗口过滤和消息加速度。
- `tier`：只能是 S/A/B/C/D。
- `source_class`：只能使用 `regulator`、`statistics`、`exchange`、`company`、`association`、`price_agency`、`customs`、`media`、`research`、`market_discussion`；其他值不计来源多样性。
- `url`：应为 HTTP(S) 直达链接并直接支持该事实，不要使用搜索结果页；缺失或不安全的链接不计来源质量。

`price`、`supply`、`a_share` 均使用 `evidence_refs` 引用一个或多个窗口内 `evidence_id`。只有成功绑定的维度才计分；消息密度和来源质量也只使用至少被一个维度引用的证据。候选若没有成功绑定到价格或供给维度的窗口内证据，则不进入榜单。每个引用必须直接支持对应维度；只有同一来源明确包含多类事实时才能跨维度复用。脚本验证结构绑定，不替代对网页内容与字段语义的一致性核验。

## 价格结构

```json
{
  "evidence_refs": ["source-row-001"],
  "evidence_type": "public_quote",
  "change_pct": 8.2,
  "breadth_count": 3,
  "persistence_days": 5,
  "description": "权威报价近5日上涨8.2%，覆盖三个规格或地区"
}
```

`evidence_type` 只能是：`none`、`rumor`、`intent`、`supplier_notice`、`public_quote`、`transaction`。

## 供给结构

```json
{
  "evidence_refs": ["source-row-001"],
  "inventory": "tight",
  "utilization_leadtime": "rising",
  "disruption": "multiple",
  "demand_gap": "confirmed",
  "description": "库存偏低，多厂检修且交期延长"
}
```

枚举值：

- `inventory`: `missing` / `normal` / `declining` / `tight`
- `utilization_leadtime`: `missing` / `normal` / `rising` / `tight`
- `disruption`: `missing` / `none` / `isolated` / `multiple` / `structural`
- `demand_gap`: `missing` / `none` / `anecdotal` / `confirmed` / `quantified`

## A 股结构

```json
{
  "evidence_refs": ["source-row-001"],
  "beneficiaries": [
    {
      "code": "600000",
      "name": "示例公司",
      "directness": "direct",
      "basis": "拥有相关产品产能且披露销售"
    }
  ],
  "excess_return_pct": 7.5,
  "positive_breadth_pct": 80,
  "turnover_ratio": 1.6,
  "positive_excess_days": 4,
  "description": "直接受益股篮子跑赢宽基并放量"
}
```

`directness` 只能是 `direct` 或 `indirect`。只有 `direct` 计入三只股票覆盖门槛，且按股票代码（无代码时按名称）去重。`positive_breadth_pct` 必须位于 0–100；计数和持续天数字段必须是非负整数；非法值按 0 分并记录缺口。

## 评分结果

脚本为每条候选增加：

- `scores.message_density`、`source_quality`、`price_validation`、`supply_constraint`、`a_share_movement`
- `score_details`：每项子分和计算依据
- `total_score`：0–100
- `status`：高确定性/发酵中/观察/证据不足
- `status_gate`：门槛是否触发及原因
- `data_gaps`：人工缺口和脚本自动缺口合并去重后的列表

脚本输出顶层还包含 `method_version`、`generated_at` 和排序后的 `candidates`。

不满足资格的候选写入顶层 `excluded_candidates`，并保留 `material_chain` 和明确的 `reason`。缺少材料价格/供给信号，或没有窗口内可定日证据，都会在评分前被排除。
