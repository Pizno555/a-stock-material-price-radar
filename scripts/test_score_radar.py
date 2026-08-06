#!/usr/bin/env python3
"""Regression tests for the deterministic radar score."""

from __future__ import annotations

import unittest

from score_radar import render_markdown, score_radar


AS_OF = "2026-08-06"


def evidence_for_density(source_group: str | None = None) -> list[dict[str, str]]:
    rows = []
    for index, (day, tier, source_class) in enumerate(
        [
            ("2026-08-06", "S", "company"),
            ("2026-08-05", "A", "company"),
            ("2026-08-02", "A", "company"),
            ("2026-08-01", "B", "company"),
        ]
    ):
        rows.append(
            {
                "evidence_id": f"evidence-{index}",
                "event_id": f"event-{index}",
                "independence_group": source_group or f"source-{index}",
                "date": day,
                "title": f"Evidence {index}",
                "publisher": f"Publisher {index}",
                "url": f"https://example.com/{index}",
                "tier": tier,
                "source_class": source_class,
                "claim": "verified claim",
            }
        )
    return rows


def direct_beneficiaries(count: int = 3) -> list[dict[str, str]]:
    return [
        {"code": f"60000{i}", "name": f"Company {i}", "directness": "direct", "basis": "product exposure"}
        for i in range(count)
    ]


def candidate(name: str, disruption: str = "structural") -> dict:
    return {
        "material_chain": name,
        "category": "测试材料",
        "core_catalyst": "测试催化",
        "evidence": evidence_for_density(),
        "price": {
            "evidence_refs": ["evidence-0"],
            "evidence_type": "transaction",
            "change_pct": 1,
            "breadth_count": 0,
            "persistence_days": 1,
            "description": "成交证据",
        },
        "supply": {
            "evidence_refs": ["evidence-1", "evidence-2"],
            "inventory": "tight",
            "utilization_leadtime": "tight",
            "disruption": disruption,
            "demand_gap": "none",
            "description": "供给紧张",
        },
        "a_share": {
            "evidence_refs": ["evidence-3"],
            "beneficiaries": direct_beneficiaries(),
            "excess_return_pct": 0,
            "positive_breadth_pct": 0,
            "turnover_ratio": 1,
            "positive_excess_days": 1,
            "description": "无异动加分",
        },
    }


def payload(items: list[dict], max_items: int = 30) -> dict:
    return {"as_of": AS_OF, "window_days": 10, "max_items": max_items, "candidates": items}


class ScoreRadarTests(unittest.TestCase):
    def test_v2_dimension_maxima_sum_to_100(self) -> None:
        classes = [
            "regulator", "statistics", "exchange", "company",
            "association", "price_agency", "customs", "media",
        ]
        tiers = ["S", "A", "A", "B", "B", "C", "C", "D"]
        evidence = []
        for index, (source_class, tier) in enumerate(zip(classes, tiers)):
            evidence.append({
                "evidence_id": f"max-{index}",
                "event_id": f"max-event-{index}",
                "independence_group": f"max-source-{index}",
                "date": AS_OF,
                "title": f"Max evidence {index}",
                "publisher": f"Max publisher {index}",
                "url": f"https://example.com/max/{index}",
                "tier": tier,
                "source_class": source_class,
                "claim": "directly supports the scored facts",
            })
        refs = [row["evidence_id"] for row in evidence]
        item = {
            "material_chain": "Maximum",
            "evidence": evidence,
            "price": {
                "evidence_refs": refs,
                "evidence_type": "transaction",
                "change_pct": 10,
                "breadth_count": 3,
                "persistence_days": 8,
            },
            "supply": {
                "evidence_refs": [refs[0]],
                "inventory": "tight",
                "utilization_leadtime": "tight",
                "disruption": "structural",
                "demand_gap": "quantified",
            },
            "a_share": {
                "evidence_refs": [refs[1]],
                "beneficiaries": direct_beneficiaries(3),
                "excess_return_pct": 10,
                "positive_breadth_pct": 85,
                "turnover_ratio": 1.8,
                "positive_excess_days": 2,
            },
        }
        result = score_radar(payload([item]))["candidates"][0]
        self.assertEqual(result["scores"], {
            "message_density": 20,
            "source_quality": 25,
            "price_validation": 25,
            "supply_constraint": 20,
            "a_share_movement": 10,
        })
        self.assertEqual(result["total_score"], 100)

    def test_65_is_high_when_gate_passes(self) -> None:
        result = score_radar(payload([candidate("Score 65")]))["candidates"][0]
        self.assertEqual(result["scores"], {
            "message_density": 12,
            "source_quality": 25,
            "price_validation": 13,
            "supply_constraint": 15,
            "a_share_movement": 0,
        })
        self.assertEqual(result["total_score"], 65)
        self.assertEqual(result["status"], "高确定性")
        self.assertFalse(result["status_gate"]["applied"])
        self.assertEqual(result["status_gate"]["requirements"]["price_validation_min"], 13)

    def test_64_is_fermenting(self) -> None:
        result = score_radar(payload([candidate("Score 64", disruption="multiple")]))["candidates"][0]
        self.assertEqual(result["total_score"], 64)
        self.assertEqual(result["status"], "发酵中")

    def test_high_total_is_downgraded_by_evidence_gate(self) -> None:
        item = candidate("Gate")
        item["evidence"] = evidence_for_density(source_group="one-source")
        for row in item["evidence"]:
            row["tier"] = "A"
        item["price"] = {
            "evidence_refs": ["evidence-0"],
            "evidence_type": "public_quote",
            "change_pct": 3,
            "breadth_count": 0,
            "persistence_days": 1,
        }
        item["a_share"] = {
            "evidence_refs": ["evidence-3"],
            "beneficiaries": direct_beneficiaries(),
            "excess_return_pct": 10,
            "positive_breadth_pct": 85,
            "turnover_ratio": 1.8,
            "positive_excess_days": 4,
        }
        item["supply"]["demand_gap"] = "quantified"
        result = score_radar(payload([item]))["candidates"][0]
        self.assertGreaterEqual(result["total_score"], 65)
        self.assertLess(result["scores"]["source_quality"], 15)
        self.assertLess(result["scores"]["price_validation"], 13)
        self.assertEqual(result["status"], "发酵中")
        self.assertTrue(result["status_gate"]["applied"])

    def test_stock_only_candidate_is_excluded(self) -> None:
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
        self.assertEqual(result["excluded_candidates"][0]["material_chain"], "Stock Only")

    def test_out_of_window_material_signal_is_excluded(self) -> None:
        item = candidate("Old Signal")
        item["evidence"][0]["date"] = "2026-07-01"
        item["evidence"][1]["date"] = "2026-07-02"
        item["evidence"][2]["date"] = "2026-07-03"
        item["evidence"][3]["date"] = "2026-07-04"
        result = score_radar(payload([item]))
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("监控窗口内", result["excluded_candidates"][0]["reason"])

    def test_low_stock_coverage_caps_market_score(self) -> None:
        item = candidate("Low Coverage")
        item["a_share"] = {
            "evidence_refs": ["evidence-3"],
            "beneficiaries": direct_beneficiaries(2),
            "excess_return_pct": 10,
            "positive_breadth_pct": 85,
            "turnover_ratio": 1.8,
            "positive_excess_days": 4,
        }
        result = score_radar(payload([item]))["candidates"][0]
        self.assertEqual(result["scores"]["a_share_movement"], 5)
        self.assertTrue(result["score_details"]["a_share_movement"]["low_coverage"])

    def test_unlinked_material_fields_are_excluded(self) -> None:
        item = candidate("Unlinked")
        item["price"].pop("evidence_refs")
        item["supply"].pop("evidence_refs")
        result = score_radar(payload([item]))
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("evidence_refs", result["excluded_candidates"][0]["reason"])

    def test_old_price_plus_current_normal_supply_is_excluded(self) -> None:
        item = candidate("Mixed Window")
        item["evidence"][0]["evidence_id"] = "old-price"
        item["evidence"][0]["date"] = "2026-07-01"
        item["price"]["evidence_refs"] = ["old-price"]
        item["supply"] = {
            "evidence_refs": ["evidence-1"],
            "inventory": "normal",
            "utilization_leadtime": "normal",
            "disruption": "none",
            "demand_gap": "none",
        }
        result = score_radar(payload([item]))
        self.assertEqual(result["candidate_count"], 0)

    def test_duplicate_direct_beneficiaries_do_not_bypass_cap(self) -> None:
        item = candidate("Duplicate Stocks")
        repeated = direct_beneficiaries(1)[0]
        item["a_share"] = {
            "evidence_refs": ["evidence-3"],
            "beneficiaries": [repeated, repeated.copy(), repeated.copy()],
            "excess_return_pct": 10,
            "positive_breadth_pct": 85,
            "turnover_ratio": 1.8,
            "positive_excess_days": 4,
        }
        result = score_radar(payload([item]))["candidates"][0]
        details = result["score_details"]["a_share_movement"]
        self.assertEqual(details["direct_beneficiary_count"], 1)
        self.assertTrue(details["low_coverage"])
        self.assertEqual(result["scores"]["a_share_movement"], 5)

    def test_invalid_numeric_ranges_score_zero_and_record_gap(self) -> None:
        item = candidate("Invalid Ranges")
        item["price"]["breadth_count"] = 1.5
        item["a_share"]["positive_breadth_pct"] = 150
        result = score_radar(payload([item]))["candidates"][0]
        self.assertEqual(result["score_details"]["price_validation"]["breadth_points"], 0)
        self.assertEqual(result["score_details"]["a_share_movement"]["breadth_points"], 0)
        self.assertTrue(any("非负整数" in gap for gap in result["data_gaps"]))
        self.assertTrue(any("0到100" in gap for gap in result["data_gaps"]))

    def test_unknown_source_class_and_unsafe_url_do_not_score_or_link(self) -> None:
        item = candidate("Unsafe")
        for row in item["evidence"]:
            row["source_class"] = "invented"
        item["evidence"][0]["url"] = "javascript:alert(1)"
        result = score_radar(payload([item]))
        scored = result["candidates"][0]
        self.assertEqual(scored["score_details"]["message_density"]["source_class_count"], 0)
        markdown = render_markdown(result)
        self.assertNotIn("javascript:", markdown)

    def test_max_30_and_stable_name_sort(self) -> None:
        items = []
        for index in range(31):
            items.append({
                "material_chain": f"Material {30 - index:02d}",
                "evidence": [{
                    "evidence_id": f"evidence-{index}",
                    "event_id": f"event-{index}",
                    "independence_group": f"source-{index}",
                    "date": AS_OF,
                    "title": "Rumor",
                    "publisher": "Market",
                    "url": "https://example.com/rumor",
                    "tier": "D",
                    "source_class": "market_discussion",
                    "claim": "unverified price signal",
                }],
                "price": {"evidence_refs": [f"evidence-{index}"], "evidence_type": "rumor", "change_pct": 0, "breadth_count": 0, "persistence_days": 1},
                "supply": {"evidence_refs": [], "inventory": "normal", "utilization_leadtime": "normal", "disruption": "none", "demand_gap": "none"},
            })
        result = score_radar(payload(items))
        names = [row["material_chain"] for row in result["candidates"]]
        self.assertEqual(len(names), 30)
        self.assertEqual(names, sorted(names))
        self.assertNotIn("Material 30", names)


if __name__ == "__main__":
    unittest.main()
