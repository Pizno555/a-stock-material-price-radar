# 数据契约 v2.1

## 目录

- [顶层输入](#顶层输入)
- [候选结构](#候选结构)
- [证据结构](#证据结构)
- [价格结构](#价格结构)
- [供给结构](#供给结构)
- [Forward Catalyst](#forward-catalyst)
- [A 股结构](#a-股结构)
- [反证结构](#反证结构)
- [评分输出](#评分输出)
- [Legacy 兼容](#legacy-兼容)

## 顶层输入

评分脚本接收 UTF-8 JSON。新研究使用：

```json
{
  "schema_version": "2.1",
  "title": "材料涨价雷达",
  "as_of": "2026-08-10",
  "window_days": 10,
  "max_items": 30,
  "candidates": []
}
```

`as_of` 必填；`window_days` 默认10且≥1；`max_items` 为1–30；`candidates` 必须为数组。缺少 `schema_version` 的旧输入按 `2.0-legacy` 兼容。

## 候选结构

```json
{
  "material_chain": "电子级氢氟酸/AHF",
  "category": "半导体材料/氟化工",
  "core_catalyst": "库存去化且报价条件收紧",
  "evidence": [],
  "price": {},
  "supply": {},
  "forward_catalyst": {},
  "a_share": {},
  "counterevidence": [],
  "invalidation_conditions": [],
  "data_gaps": []
}
```

`material_chain` 必填且输入内唯一。其余缺失按0分或空列表处理。

## 证据结构

```json
{
  "evidence_id": "source-row-001",
  "event_id": "ahf-notice-20260801",
  "independence_group": "producer-a",
  "date": "2026-08-01",
  "title": "供应商调价通知",
  "publisher": "供应商A",
  "url": "https://example.com/source",
  "tier": "A",
  "source_class": "company",
  "claim": "指定规格自8月起上调报价",
  "time_series": false,
  "supports": ["price.stage", "price.change_pct"]
}
```

- `evidence_id`：窗口内唯一；缺失或重复时不能绑定。
- `event_id`：同一原始事件及转载共用；缺失时使用独立组代替。
- `independence_group`：同一发布者或转载链共用；strict 模式下缺失时，该证据不能进入来源多样性、来源质量或价格/供给信源封顶。legacy 模式允许按发布者、URL或标题回退。
- `date`：证据发布日期或首次可验证日期。
- `tier`：S/A/B/C/D。
- `source_class`：`regulator`、`statistics`、`exchange`、`company`、`association`、`price_agency`、`customs`、`media`、`research`、`market_discussion`。
- `url`：HTTP(S)直达链接；搜索结果页不合法。
- `time_series`：来源是否直接包含可验证时间序列；用于 intent 持续性和 ongoing Catalyst。
- `supports`：该证据直接支持的评分子项。

合法 `supports`：

```text
price.stage
price.change_pct
price.breadth
price.persistence
supply.inventory
supply.utilization_leadtime
supply.disruption
supply.demand_gap
forward_catalyst
counterevidence.supply
a_share.market_data
```

严格模式下非零子项没有对应支持标签时按0分。一个证据可支持多个子项，但网页必须直接支持每项事实。

## 价格结构

```json
{
  "evidence_refs": ["source-row-001"],
  "evidence_type": "preprice_action",
  "change_basis": "terms",
  "change_pct": 5,
  "breadth_count": 2,
  "persistence_days": 3,
  "description": "两家供应商已取消5%折扣并持续三日"
}
```

`evidence_type`：`none`、`rumor`、`intent`、`preprice_action`、`supplier_notice`、`public_quote`、`transaction`。

`change_basis`：`intent`、`terms`、`notice`、`quote`、`transaction`，必须与阶段一致。数值字段必须为有限数；计数和天数为非负整数。

## 供给结构

```json
{
  "evidence_refs": ["source-row-002"],
  "inventory": "declining",
  "utilization_leadtime": "rising",
  "disruption": "multiple",
  "demand_gap": "confirmed",
  "description": "库存去化、多厂检修且订单确认"
}
```

枚举：

- `inventory`: `missing` / `normal` / `declining` / `tight`
- `utilization_leadtime`: `missing` / `normal` / `rising` / `tight`
- `disruption`: `missing` / `none` / `isolated` / `multiple` / `structural`
- `demand_gap`: `missing` / `none` / `anecdotal` / `confirmed` / `quantified`

## Forward Catalyst

```json
{
  "forward_catalyst": {
    "status": "scheduled",
    "type": "maintenance",
    "timing_basis": "source_explicit",
    "start_date": "2026-09-01",
    "end_date": "2026-10-15",
    "observed_start": null,
    "description": "主要供应商集中检修",
    "evidence_refs": ["source-row-002"]
  }
}
```

`status`：`scheduled` / `ongoing`。

`timing_basis`：`source_explicit` / `observed_trend` / `research_inference`。

`type`：`maintenance`、`shutdown`、`quota_reduction`、`export_restriction`、`capacity_exit`、`capacity_delay`、`restart_delay`、`demand_commissioning`、`inventory_tightening`、`leadtime_extension`、`order_acceleration`、`utilization_ramp`、`supply_gap_widening`。

scheduled 必须有合法 `start_date`；ongoing 必须有合法 `observed_start`，并由一份 B级以上 `time_series:true` 证据或两个不同日期观察支持。Catalyst 引用必须同时位于 `supply.evidence_refs`；至少一条引用证据必须同时包含 `forward_catalyst` 标签、达到B级并带合法直达链接。

## A 股结构

```json
{
  "evidence_refs": ["market-row-001"],
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

`directness` 为 `direct` 或 `indirect`。严格模式下非零行情指标要求 `a_share.market_data` 标签。广度为0–100，计数与天数为非负整数。

## 反证结构

旧字符串继续作为 ordinary 展示。结构化反证：

```json
{
  "description": "新增同等级产能即将投产",
  "effect": "blocking",
  "dimension": "supply",
  "evidence_refs": ["counter-row-001"]
}
```

`effect` 为 `ordinary` 或 `blocking`。Blocking 必须作用于 supply，并由至少一条同时绑定 `counterevidence.supply`、达到B级且带合法直达链接的证据支持；否则自动降为 ordinary。

## 评分输出

候选增加：

- `scores`：五项分数。
- `score_details`：子项、来源封顶、Catalyst和反证校验。
- `total_score`：0–100。
- `status`：高确定性/发酵中/观察/证据不足。
- `gate_path`：`price_confirmed` / `supply_forward` / `none`。
- `status_gate`：路径要求、是否通过和原因。
- `data_gaps`：人工与自动缺口去重结果。

顶层增加 `schema_version`、`method_version`、`generated_at`、`candidate_count`、`excluded_candidates`。

评分排序后超过 `max_items` 的候选不得静默丢弃，必须进入 `excluded_candidates` 并注明排名截断原因。

## Legacy 兼容

缺少顶层 `schema_version` 时：

- 使用维度级 `evidence_refs`；
- 推断缺失 `change_basis`；
- 字符串反证按 ordinary；
- 缺少 Catalyst 时不能通过 Supply Forward；
- 每个候选记录 `legacy coarse binding`；
- 可运行不等于完成 v2.1 严格审计。
