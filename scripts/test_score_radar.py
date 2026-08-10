#!/usr/bin/env python3
"""Regression and adversarial tests for material-price-radar v2.1."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from render_radar import render
from score_radar import main, render_markdown, score_radar


AS_OF = "2026-08-10"
SKILL_DIR = Path(__file__).resolve().parents[1]
ALL_SUPPORTS = [
    "price.stage",
    "price.change_pct",
    "price.breadth",
    "price.persistence",
    "supply.inventory",
    "supply.utilization_leadtime",
    "supply.disruption",
    "supply.demand_gap",
    "forward_catalyst",
    "counterevidence.supply",
    "a_share.market_data",
]
CLASSES = [
    "regulator",
    "statistics",
    "exchange",
    "company",
    "association",
    "price_agency",
    "customs",
    "media",
]
TIERS = ["S", "A", "A", "B", "B", "C", "A", "B"]


def evidence_rows(
    count: int = 8,
    *,
    supports: list[str] | None = None,
    tier: str | None = None,
    same_event: bool = False,
    same_source: bool = False,
) -> list[dict]:
    rows = []
    for index in range(count):
        rows.append(
            {
                "evidence_id": f"e{index}",
                "event_id": "same-event" if same_event else f"event-{index}",
                "independence_group": "same-source" if same_source else f"source-{index}",
                "date": AS_OF,
                "title": f"Evidence {index}",
                "publisher": f"Publisher {index}",
                "url": f"https://example.com/{index}",
                "tier": tier or TIERS[index % len(TIERS)],
                "source_class": CLASSES[index % len(CLASSES)],
                "claim": "directly supports the tagged fact",
                "supports": list(ALL_SUPPORTS if supports is None else supports),
            }
        )
    return rows


def direct_beneficiaries(count: int = 3) -> list[dict[str, str]]:
    return [
        {
            "code": f"60000{index}",
            "name": f"Company {index}",
            "directness": "direct",
            "basis": "verified product exposure",
        }
        for index in range(count)
    ]


def max_candidate(name: str = "Maximum") -> dict:
    rows = evidence_rows()
    refs = [row["evidence_id"] for row in rows]
    return {
        "material_chain": name,
        "category": "测试材料",
        "core_catalyst": "测试催化",
        "evidence": rows,
        "price": {
            "evidence_refs": refs,
            "evidence_type": "transaction",
            "change_basis": "transaction",
            "change_pct": 10,
            "breadth_count": 3,
            "persistence_days": 8,
            "description": "成交上涨",
        },
        "supply": {
            "evidence_refs": ["e0"],
            "inventory": "tight",
            "utilization_leadtime": "tight",
            "disruption": "structural",
            "demand_gap": "quantified",
            "description": "供给紧张",
        },
        "a_share": {
            "evidence_refs": ["e1"],
            "beneficiaries": direct_beneficiaries(),
            "excess_return_pct": 10,
            "positive_breadth_pct": 85,
            "turnover_ratio": 1.8,
            "positive_excess_days": 2,
            "description": "市场确认",
        },
        "counterevidence": [],
        "invalidation_conditions": [],
    }


def payload(items: list[dict], *, strict: bool = True, window_days: int = 10) -> dict:
    result = {
        "as_of": AS_OF,
        "window_days": window_days,
        "max_items": 30,
        "candidates": items,
    }
    if strict:
        result["schema_version"] = "2.1"
    return result


def scored(item: dict, *, strict: bool = True, window_days: int = 10) -> dict:
    return score_radar(payload([item], strict=strict, window_days=window_days))["candidates"][0]


def supply_forward_candidate(name: str = "Supply Forward") -> dict:
    rows = evidence_rows(
        supports=[
            "supply.inventory",
            "supply.utilization_leadtime",
            "supply.disruption",
            "supply.demand_gap",
            "forward_catalyst",
        ]
    )
    refs = [row["evidence_id"] for row in rows]
    return {
        "material_chain": name,
        "evidence": rows,
        "price": {"evidence_refs": [], "evidence_type": "none"},
        "supply": {
            "evidence_refs": refs,
            "inventory": "tight",
            "utilization_leadtime": "tight",
            "disruption": "structural",
            "demand_gap": "quantified",
        },
        "forward_catalyst": {
            "status": "scheduled",
            "type": "maintenance",
            "timing_basis": "source_explicit",
            "start_date": "2026-09-01",
            "end_date": "2026-10-15",
            "observed_start": None,
            "description": "集中检修",
            "evidence_refs": ["e0"],
        },
        "a_share": {"evidence_refs": []},
        "counterevidence": [],
    }


class ScoreRadarV21Tests(unittest.TestCase):
    def test_t01_dimension_maxima_sum_to_100(self) -> None:
        result = scored(max_candidate())
        self.assertEqual(
            result["scores"],
            {
                "message_density": 20,
                "source_quality": 25,
                "price_validation": 25,
                "supply_constraint": 20,
                "a_share_movement": 10,
            },
        )
        self.assertEqual(result["total_score"], 100)
        self.assertEqual(result["status"], "高确定性")
        self.assertEqual(result["gate_path"], "price_confirmed")

    def test_t02_stock_only_candidate_is_excluded(self) -> None:
        item = {
            "material_chain": "Stock Only",
            "a_share": {
                "beneficiaries": direct_beneficiaries(),
                "excess_return_pct": 12,
                "positive_breadth_pct": 90,
                "turnover_ratio": 2,
                "positive_excess_days": 5,
            },
        }
        result = score_radar(payload([item]))
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("纯股票", result["excluded_candidates"][0]["reason"])

    def test_t03_d_tier_dimension_caps(self) -> None:
        item = max_candidate("D Attack")
        item["evidence"] = evidence_rows(1, tier="D")
        for dimension in ("price", "supply", "a_share"):
            item[dimension]["evidence_refs"] = ["e0"]
        result = scored(item)
        self.assertEqual(result["scores"]["price_validation"], 4)
        self.assertEqual(result["scores"]["supply_constraint"], 4)
        self.assertEqual(result["total_score"], 26)
        self.assertEqual(result["status"], "证据不足")

    def test_t04_rumor_cannot_gain_numeric_points(self) -> None:
        item = max_candidate("Rumor")
        item["price"].update({"evidence_type": "rumor", "change_basis": None})
        result = scored(item)
        details = result["score_details"]["price_validation"]
        self.assertEqual(result["scores"]["price_validation"], 1)
        self.assertEqual(details["change_points"], 0)
        self.assertEqual(details["breadth_points"], 0)
        self.assertEqual(details["persistence_points"], 0)

    def test_t05_preprice_action_stage_is_six(self) -> None:
        item = max_candidate("Preprice")
        item["price"].update(
            {
                "evidence_type": "preprice_action",
                "change_basis": "terms",
                "change_pct": 0,
                "breadth_count": 0,
                "persistence_days": 1,
            }
        )
        result = scored(item)
        self.assertEqual(result["score_details"]["price_validation"]["evidence_points"], 6)

    def test_t06_intent_stage_is_four(self) -> None:
        item = max_candidate("Intent")
        item["price"].update(
            {
                "evidence_type": "intent",
                "change_basis": "intent",
                "change_pct": 0,
                "breadth_count": 0,
                "persistence_days": 1,
            }
        )
        result = scored(item)
        self.assertEqual(result["score_details"]["price_validation"]["evidence_points"], 4)

    def test_t07_supplier_notice_can_reach_thirteen(self) -> None:
        item = max_candidate("Notice")
        item["price"].update(
            {
                "evidence_type": "supplier_notice",
                "change_basis": "notice",
                "change_pct": 8,
                "breadth_count": 1,
                "persistence_days": 1,
            }
        )
        result = scored(item)
        self.assertGreaterEqual(result["scores"]["price_validation"], 13)
        self.assertEqual(result["gate_path"], "price_confirmed")

    def test_t08_legacy_json_runs_with_gap(self) -> None:
        item = max_candidate("Legacy")
        item["price"].pop("change_basis")
        for row in item["evidence"]:
            row.pop("supports")
        result = scored(item, strict=False)
        self.assertEqual(result["gate_path"], "price_confirmed")
        self.assertTrue(any("legacy coarse binding" in gap for gap in result["data_gaps"]))

    def test_t09_supply_forward_path(self) -> None:
        result = scored(supply_forward_candidate())
        self.assertEqual(result["total_score"], 65)
        self.assertEqual(result["status"], "高确定性")
        self.assertEqual(result["gate_path"], "supply_forward")
        self.assertTrue(
            any("price_confirmed" in reason for reason in result["status_gate"]["reasons"])
        )

    def test_t10_single_maintenance_is_not_high(self) -> None:
        item = supply_forward_candidate("Single")
        item["evidence"] = item["evidence"][:1]
        item["supply"]["evidence_refs"] = ["e0"]
        result = scored(item)
        self.assertNotEqual(result["status"], "高确定性")

    def test_t11_supply_forward_requires_catalyst(self) -> None:
        item = supply_forward_candidate("No Catalyst")
        item.pop("forward_catalyst")
        result = scored(item)
        self.assertEqual(result["total_score"], 65)
        self.assertEqual(result["status"], "发酵中")
        self.assertEqual(result["gate_path"], "none")

    def test_t12_ongoing_catalyst_accepts_time_series(self) -> None:
        item = supply_forward_candidate("Ongoing")
        item["evidence"][0]["time_series"] = True
        item["forward_catalyst"] = {
            "status": "ongoing",
            "type": "inventory_tightening",
            "timing_basis": "observed_trend",
            "observed_start": "2026-07-01",
            "description": "持续去库",
            "evidence_refs": ["e0"],
        }
        result = scored(item)
        self.assertTrue(result["score_details"]["forward_catalyst"]["valid"])
        self.assertEqual(result["gate_path"], "supply_forward")

    def test_t13_blocking_counterevidence_blocks_supply_path(self) -> None:
        item = supply_forward_candidate("Blocked")
        counter = {
            "evidence_id": "counter",
            "event_id": "counter-event",
            "independence_group": "counter-source",
            "date": AS_OF,
            "title": "New capacity",
            "publisher": "Authority",
            "url": "https://example.com/counter",
            "tier": "A",
            "source_class": "company",
            "claim": "offsetting capacity",
            "supports": ["counterevidence.supply"],
        }
        item["evidence"].append(counter)
        item["counterevidence"] = [
            {
                "description": "新增产能抵消供给收缩",
                "effect": "blocking",
                "dimension": "supply",
                "evidence_refs": ["counter"],
            }
        ]
        result = scored(item)
        self.assertEqual(result["status"], "发酵中")
        self.assertEqual(result["gate_path"], "none")
        self.assertTrue(result["score_details"]["counterevidence"]["blocking"])

    def test_t14_blocking_does_not_cancel_price_path(self) -> None:
        item = max_candidate("Price Wins")
        counter = copy.deepcopy(item["evidence"][0])
        counter.update(
            {
                "evidence_id": "counter",
                "event_id": "counter-event",
                "independence_group": "counter-source",
                "supports": ["counterevidence.supply"],
            }
        )
        item["evidence"].append(counter)
        item["counterevidence"] = [
            {
                "description": "未来扩产",
                "effect": "blocking",
                "dimension": "supply",
                "evidence_refs": ["counter"],
            }
        ]
        result = scored(item)
        self.assertEqual(result["gate_path"], "price_confirmed")
        self.assertEqual(result["status"], "高确定性")

    def test_t15_intent_cannot_use_price_gate(self) -> None:
        item = max_candidate("Intent Gate")
        item["evidence"][0]["time_series"] = True
        item["price"].update(
            {"evidence_type": "intent", "change_basis": "intent", "change_pct": 10}
        )
        item.pop("forward_catalyst", None)
        result = scored(item)
        self.assertGreaterEqual(result["scores"]["price_validation"], 13)
        self.assertNotEqual(result["gate_path"], "price_confirmed")

    def test_t16_preprice_cannot_use_price_gate(self) -> None:
        item = max_candidate("Preprice Gate")
        item["price"].update(
            {"evidence_type": "preprice_action", "change_basis": "terms"}
        )
        result = scored(item)
        self.assertGreaterEqual(result["scores"]["price_validation"], 13)
        self.assertNotEqual(result["gate_path"], "price_confirmed")

    def test_t17_price_path_precedes_supply_path(self) -> None:
        item = max_candidate("Both")
        item["forward_catalyst"] = {
            "status": "scheduled",
            "type": "maintenance",
            "timing_basis": "source_explicit",
            "start_date": "2026-09-01",
            "description": "检修",
            "evidence_refs": ["e0"],
        }
        result = scored(item)
        self.assertEqual(result["gate_path"], "price_confirmed")

    def test_t18_high_total_without_gate_is_fermenting(self) -> None:
        item = supply_forward_candidate("Gate Failure")
        item.pop("forward_catalyst")
        result = scored(item)
        self.assertGreaterEqual(result["total_score"], 65)
        self.assertEqual(result["status"], "发酵中")

    def test_t19_a_share_evidence_isolated_from_fundamentals(self) -> None:
        material = evidence_rows(1, supports=["price.stage"], tier="D")[0]
        market_rows = evidence_rows(8, supports=["a_share.market_data"])
        for index, row in enumerate(market_rows):
            row["evidence_id"] = f"m{index}"
            row["event_id"] = f"market-{index}"
            row["independence_group"] = f"market-source-{index}"
        item = {
            "material_chain": "Isolation",
            "evidence": [material, *market_rows],
            "price": {
                "evidence_refs": ["e0"],
                "evidence_type": "rumor",
                "change_pct": 0,
                "breadth_count": 0,
                "persistence_days": 1,
            },
            "supply": {"evidence_refs": []},
            "a_share": {
                "evidence_refs": [f"m{i}" for i in range(8)],
                "beneficiaries": direct_beneficiaries(),
                "excess_return_pct": 10,
                "positive_breadth_pct": 85,
                "turnover_ratio": 1.8,
                "positive_excess_days": 2,
            },
        }
        result = scored(item)
        self.assertEqual(result["scores"]["message_density"], 7)
        self.assertEqual(result["scores"]["source_quality"], 1)

    def test_t20_dimension_caps_are_independent(self) -> None:
        item = max_candidate("Independent Caps")
        item["evidence"][0]["tier"] = "D"
        item["price"]["evidence_refs"] = ["e0"]
        item["supply"]["evidence_refs"] = ["e1"]
        result = scored(item)
        self.assertEqual(result["scores"]["price_validation"], 4)
        self.assertEqual(result["scores"]["supply_constraint"], 20)

    def test_unused_price_support_cannot_raise_price_cap(self) -> None:
        stage = evidence_rows(1, supports=["price.stage"], tier="D")[0]
        unused = evidence_rows(1, supports=["price.persistence"], tier="A")[0]
        unused.update(
            {
                "evidence_id": "unused",
                "event_id": "unused-event",
                "independence_group": "unused-source",
            }
        )
        item = {
            "material_chain": "Price Cap Boundary",
            "evidence": [stage, unused],
            "price": {
                "evidence_refs": ["e0", "unused"],
                "evidence_type": "preprice_action",
                "change_basis": "terms",
                "change_pct": 0,
                "breadth_count": 0,
                "persistence_days": 1,
            },
        }
        result = scored(item)
        self.assertEqual(result["scores"]["price_validation"], 4)
        self.assertEqual(
            result["score_details"]["price_validation"]["strongest_source_tier"],
            "D",
        )

    def test_disallowed_price_subitem_cannot_raise_fundamental_scores(self) -> None:
        stage = evidence_rows(1, supports=["price.stage"], tier="D")[0]
        unused = evidence_rows(1, supports=["price.change_pct"], tier="A")[0]
        unused.update(
            {
                "evidence_id": "unused",
                "event_id": "unused-event",
                "independence_group": "unused-source",
                "source_class": "association",
            }
        )
        item = {
            "material_chain": "Fundamental Boundary",
            "evidence": [stage, unused],
            "price": {
                "evidence_refs": ["e0", "unused"],
                "evidence_type": "rumor",
                "change_pct": 10,
                "breadth_count": 0,
                "persistence_days": 1,
            },
        }
        result = scored(item)
        self.assertEqual(result["scores"]["source_quality"], 1)
        self.assertEqual(
            result["score_details"]["message_density"]["independent_events"], 1
        )

    def test_unused_supply_support_cannot_raise_supply_cap(self) -> None:
        disruption = evidence_rows(1, supports=["supply.disruption"], tier="D")[0]
        unused = evidence_rows(1, supports=["supply.inventory"], tier="A")[0]
        unused.update(
            {
                "evidence_id": "unused",
                "event_id": "unused-event",
                "independence_group": "unused-source",
            }
        )
        item = {
            "material_chain": "Supply Cap Boundary",
            "evidence": [disruption, unused],
            "supply": {
                "evidence_refs": ["e0", "unused"],
                "inventory": "normal",
                "utilization_leadtime": "normal",
                "disruption": "structural",
                "demand_gap": "none",
            },
        }
        result = scored(item)
        self.assertEqual(result["scores"]["supply_constraint"], 4)
        self.assertEqual(
            result["score_details"]["supply_constraint"]["strongest_source_tier"],
            "D",
        )

    def test_strict_source_requires_independence_group(self) -> None:
        item = max_candidate("Missing Independence")
        for row in item["evidence"]:
            row.pop("independence_group")
        result = scored(item)
        self.assertEqual(result["scores"]["source_quality"], 0)
        self.assertEqual(result["scores"]["price_validation"], 0)
        self.assertEqual(result["scores"]["supply_constraint"], 0)
        self.assertEqual(
            result["score_details"]["message_density"]["source_class_count"], 0
        )
        self.assertTrue(any("independence_group" in gap for gap in result["data_gaps"]))

    def test_t21_one_a_source_does_not_unlock_supply_fields(self) -> None:
        item = max_candidate("Supply Supports")
        item["evidence"][0]["supports"] = ["supply.disruption"]
        item["supply"]["evidence_refs"] = ["e0"]
        result = scored(item)
        details = result["score_details"]["supply_constraint"]
        self.assertEqual(details["inventory_points"], 0)
        self.assertEqual(details["utilization_leadtime_points"], 0)
        self.assertEqual(details["demand_gap_points"], 0)
        self.assertEqual(details["disruption_points"], 5)

    def test_t22_one_a_source_does_not_unlock_price_fields(self) -> None:
        item = max_candidate("Price Supports")
        item["evidence"][0]["supports"] = ["price.stage"]
        item["price"]["evidence_refs"] = ["e0"]
        result = scored(item)
        details = result["score_details"]["price_validation"]
        self.assertEqual(details["evidence_points"], 15)
        self.assertEqual(details["change_points"], 0)
        self.assertEqual(details["breadth_points"], 0)
        self.assertEqual(details["persistence_points"], 0)

    def test_t23_missing_support_scores_zero_and_records_gap(self) -> None:
        item = max_candidate("Missing Supports")
        item["evidence"][0]["supports"] = []
        item["supply"]["evidence_refs"] = ["e0"]
        result = scored(item)
        self.assertEqual(result["scores"]["supply_constraint"], 0)
        self.assertTrue(any("子项支持" in gap for gap in result["data_gaps"]))

    def test_t24_change_basis_mismatch_downgrades(self) -> None:
        item = max_candidate("Mismatch")
        item["price"].update(
            {"evidence_type": "transaction", "change_basis": "notice"}
        )
        result = scored(item)
        self.assertEqual(
            result["score_details"]["price_validation"]["evidence_type"],
            "supplier_notice",
        )
        self.assertTrue(any("错配" in gap for gap in result["data_gaps"]))

    def test_t25_duplicate_evidence_id_cannot_bind(self) -> None:
        item = max_candidate("Duplicate")
        duplicate = copy.deepcopy(item["evidence"][0])
        duplicate["event_id"] = "other-event"
        item["evidence"].append(duplicate)
        item["price"]["evidence_refs"] = ["e0"]
        result = score_radar(payload([item]))
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("evidence_refs", result["excluded_candidates"][0]["reason"])

    def test_t26_source_diversity_uses_all_independent_sources(self) -> None:
        rows = evidence_rows(2, supports=["price.stage"], same_event=True)
        item = {
            "material_chain": "Diversity",
            "evidence": rows,
            "price": {
                "evidence_refs": ["e0", "e1"],
                "evidence_type": "rumor",
                "change_pct": 0,
                "breadth_count": 0,
                "persistence_days": 1,
            },
        }
        result = scored(item)
        details = result["score_details"]["message_density"]
        self.assertEqual(details["independent_events"], 1)
        self.assertEqual(details["source_class_count"], 2)

    def test_t27_scheduled_catalyst_requires_start_date(self) -> None:
        item = supply_forward_candidate("No Start")
        item["forward_catalyst"].pop("start_date")
        result = scored(item)
        self.assertFalse(result["score_details"]["forward_catalyst"]["valid"])

    def test_t28_ongoing_catalyst_requires_trend_evidence(self) -> None:
        item = supply_forward_candidate("No Trend")
        item["forward_catalyst"] = {
            "status": "ongoing",
            "type": "inventory_tightening",
            "timing_basis": "observed_trend",
            "observed_start": "2026-07-01",
            "evidence_refs": ["e0"],
        }
        result = scored(item)
        self.assertFalse(result["score_details"]["forward_catalyst"]["valid"])

    def test_t29_catalyst_must_reference_supply_evidence(self) -> None:
        item = supply_forward_candidate("Wrong Catalyst Ref")
        market = copy.deepcopy(item["evidence"][0])
        market.update(
            {
                "evidence_id": "market",
                "event_id": "market-event",
                "independence_group": "market-source",
                "supports": ["forward_catalyst"],
            }
        )
        item["evidence"].append(market)
        item["forward_catalyst"]["evidence_refs"] = ["market"]
        result = scored(item)
        self.assertFalse(result["score_details"]["forward_catalyst"]["valid"])

    def test_catalyst_b_tier_must_be_on_supported_row(self) -> None:
        item = supply_forward_candidate("Catalyst Same Row")
        item["evidence"][0]["tier"] = "D"
        item["evidence"][1]["supports"].remove("forward_catalyst")
        item["forward_catalyst"]["evidence_refs"] = ["e0", "e1"]
        result = scored(item)
        self.assertFalse(result["score_details"]["forward_catalyst"]["valid"])
        self.assertTrue(
            any("同时带支持标签" in reason for reason in result["score_details"]["forward_catalyst"]["reasons"])
        )

    def test_t30_invalid_blocking_downgrades_to_ordinary(self) -> None:
        item = supply_forward_candidate("Weak Counter")
        counter = {
            "evidence_id": "counter",
            "event_id": "counter",
            "independence_group": "counter",
            "date": AS_OF,
            "title": "Rumor",
            "publisher": "Forum",
            "url": "https://example.com/counter",
            "tier": "D",
            "source_class": "market_discussion",
            "claim": "weak counter",
            "supports": ["counterevidence.supply"],
        }
        item["evidence"].append(counter)
        item["counterevidence"] = [
            {
                "description": "弱反证",
                "effect": "blocking",
                "dimension": "supply",
                "evidence_refs": ["counter"],
            }
        ]
        result = scored(item)
        self.assertEqual(result["counterevidence"][0]["effect"], "ordinary")
        self.assertEqual(result["gate_path"], "supply_forward")

    def test_blocking_b_tier_must_be_on_supported_row(self) -> None:
        item = supply_forward_candidate("Blocking Same Row")
        weak = copy.deepcopy(item["evidence"][0])
        weak.update(
            {
                "evidence_id": "weak-counter",
                "event_id": "weak-counter-event",
                "independence_group": "weak-counter-source",
                "tier": "D",
                "supports": ["counterevidence.supply"],
            }
        )
        strong = copy.deepcopy(item["evidence"][1])
        strong.update(
            {
                "evidence_id": "strong-untagged",
                "event_id": "strong-untagged-event",
                "independence_group": "strong-untagged-source",
                "supports": [],
            }
        )
        item["evidence"].extend([weak, strong])
        item["counterevidence"] = [
            {
                "description": "强来源与支持标签错开",
                "effect": "blocking",
                "dimension": "supply",
                "evidence_refs": ["weak-counter", "strong-untagged"],
            }
        ]
        result = scored(item)
        self.assertEqual(result["counterevidence"][0]["effect"], "ordinary")
        self.assertEqual(result["gate_path"], "supply_forward")

    def test_strict_market_evidence_cannot_masquerade_as_price_signal(self) -> None:
        row = evidence_rows(1, supports=["a_share.market_data"], tier="A")[0]
        item = {
            "material_chain": "Market Masquerade",
            "evidence": [row],
            "price": {
                "evidence_refs": ["e0"],
                "evidence_type": "transaction",
                "change_basis": "transaction",
                "change_pct": 10,
                "breadth_count": 3,
                "persistence_days": 8,
            },
        }
        result = score_radar(payload([item]))
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("有效材料价格或供给信号", result["excluded_candidates"][0]["reason"])

    def test_t31_one_day_window_has_zero_acceleration(self) -> None:
        item = max_candidate("One Day")
        result = scored(item, window_days=1)
        details = result["score_details"]["message_density"]
        self.assertEqual(details["acceleration_points"], 0)
        self.assertTrue(any("窗口不足" in gap for gap in result["data_gaps"]))

    def test_t32_recent_report_can_contain_old_history(self) -> None:
        item = supply_forward_candidate("Historical Series")
        item["evidence"][0]["time_series"] = True
        item["evidence"][0]["claim"] = "最近发布，内部包含过去8周库存序列"
        result = scored(item)
        self.assertGreater(result["scores"]["supply_constraint"], 0)

    def test_t33_future_catalyst_beyond_window_is_valid(self) -> None:
        item = supply_forward_candidate("Future")
        item["forward_catalyst"]["start_date"] = "2026-12-01"
        item["forward_catalyst"]["end_date"] = "2027-01-15"
        result = scored(item)
        self.assertTrue(result["score_details"]["forward_catalyst"]["valid"])

    def test_t34_old_evidence_without_update_is_excluded(self) -> None:
        item = supply_forward_candidate("Old")
        for row in item["evidence"]:
            row["date"] = "2026-07-01"
        result = score_radar(payload([item]))
        self.assertEqual(result["candidate_count"], 0)

    def test_t35_single_day_strong_a_share_scores_nine(self) -> None:
        item = max_candidate("One Day Market")
        item["a_share"]["positive_excess_days"] = 1
        result = scored(item)
        self.assertEqual(result["scores"]["a_share_movement"], 9)

    def test_t36_markdown_contains_v21_audit_fields(self) -> None:
        result = score_radar(payload([max_candidate("Markdown")]))
        markdown = render_markdown(result)
        self.assertIn("方法版本：2.1.0", markdown)
        self.assertIn("确认路径", markdown)
        self.assertIn("price_confirmed", markdown)

    def test_t37_unsafe_url_does_not_score_or_render_link(self) -> None:
        item = max_candidate("Unsafe")
        item["evidence"][0]["url"] = "javascript:alert(1)"
        result = score_radar(payload([item]))
        markdown = render_markdown(result)
        self.assertNotIn("javascript:", markdown)

    def test_t38_invalid_numeric_ranges_record_gaps(self) -> None:
        item = max_candidate("Invalid Ranges")
        item["price"]["breadth_count"] = 1.5
        item["a_share"]["positive_breadth_pct"] = 150
        result = scored(item)
        self.assertTrue(any("非负整数" in gap for gap in result["data_gaps"]))
        self.assertTrue(any("0到100" in gap for gap in result["data_gaps"]))

    def test_t39_max_30_and_stable_sort(self) -> None:
        items = []
        for index in range(31):
            item = supply_forward_candidate(f"Material {30-index:02d}")
            for row_index, row in enumerate(item["evidence"]):
                row["evidence_id"] = f"{index}-{row_index}"
                row["event_id"] = f"{index}-event-{row_index}"
                row["independence_group"] = f"{index}-source-{row_index}"
            refs = [row["evidence_id"] for row in item["evidence"]]
            item["supply"]["evidence_refs"] = refs
            item["forward_catalyst"]["evidence_refs"] = [refs[0]]
            items.append(item)
        result = score_radar(payload(items))
        names = [row["material_chain"] for row in result["candidates"]]
        self.assertEqual(len(names), 30)
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(result["excluded_candidates"]), 1)
        self.assertIn("max_items=30", result["excluded_candidates"][0]["reason"])

    def test_t40_html_embeds_same_scored_json(self) -> None:
        result = score_radar(payload([max_candidate("HTML")]))
        html = render(
            result,
            SKILL_DIR / "assets" / "report-template.html",
            SKILL_DIR / "assets" / "report.css",
        )
        self.assertIn('"total_score": 100', html)
        self.assertIn('"gate_path": "price_confirmed"', html)
        self.assertIn('"method_version": "2.1.0"', html)

    def test_t41_html_has_v21_audit_surfaces(self) -> None:
        result = score_radar(payload([max_candidate("Audit")]))
        html = render(
            result,
            SKILL_DIR / "assets" / "report-template.html",
            SKILL_DIR / "assets" / "report.css",
        )
        for label in ("确认路径", "价格阶段", "Forward Catalyst", "A股受益公司", "门槛降级", "已排除候选"):
            self.assertIn(label, html)

    def test_structured_catalyst_and_subitems_render_in_all_formats(self) -> None:
        result = score_radar(payload([supply_forward_candidate("Audit Detail")]))
        markdown = render_markdown(result)
        html = render(
            result,
            SKILL_DIR / "assets" / "report-template.html",
            SKILL_DIR / "assets" / "report.css",
        )
        for value in ("timing_basis=source_explicit", "start=2026-09-01", "供给子项", "Gate"):
            self.assertIn(value, markdown)
        for value in ('"timing_basis": "source_explicit"', '"start_date": "2026-09-01"', "供给子项", "Gate 审计"):
            self.assertIn(value, html)

    def test_markdown_cli_renders_from_scored_json_without_rescoring(self) -> None:
        result = score_radar(payload([max_candidate("Single Source JSON")]))
        expected = render_markdown(result)
        with tempfile.TemporaryDirectory() as directory:
            scored_path = Path(directory) / "scored.json"
            markdown_path = Path(directory) / "report.md"
            scored_path.write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
            exit_code = main(
                [
                    str(scored_path),
                    "--from-scored",
                    "--format",
                    "markdown",
                    "--output",
                    str(markdown_path),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), expected)


if __name__ == "__main__":
    unittest.main()
