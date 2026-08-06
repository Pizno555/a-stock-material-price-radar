#!/usr/bin/env python3
"""Deterministically score normalized material price-radar evidence."""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


METHOD_VERSION = "2.0.0"
TIER_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
SOURCE_CLASSES = {
    "regulator",
    "statistics",
    "exchange",
    "company",
    "association",
    "price_agency",
    "customs",
    "media",
    "research",
    "market_discussion",
}
BEST_SOURCE_POINTS = {"S": 16, "A": 13, "B": 9, "C": 5, "D": 1}
SECOND_SOURCE_POINTS = {"S": 6, "A": 6, "B": 4, "C": 2, "D": 0}
THIRD_SOURCE_POINTS = {"S": 3, "A": 3, "B": 2, "C": 1, "D": 0}
PRICE_TYPE_POINTS = {
    "none": 0,
    "rumor": 1,
    "intent": 3,
    "supplier_notice": 6,
    "public_quote": 9,
    "transaction": 12,
}
INVENTORY_POINTS = {"missing": 0, "normal": 0, "declining": 3, "tight": 5}
UTILIZATION_POINTS = {"missing": 0, "normal": 0, "rising": 3, "tight": 5}
DISRUPTION_POINTS = {"missing": 0, "none": 0, "isolated": 1, "multiple": 4, "structural": 5}
DEMAND_POINTS = {"missing": 0, "none": 0, "anecdotal": 1, "confirmed": 3, "quantified": 5}


class RadarValidationError(ValueError):
    pass


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise RadarValidationError(f"{field} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RadarValidationError(f"{field} must be YYYY-MM-DD: {value!r}") from exc


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def _slug(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _safe_http_url(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return raw


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = _text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _event_key(item: dict[str, Any]) -> str:
    return _slug(
        _text(item.get("event_id"))
        or _text(item.get("independence_group"))
        or f"{_text(item.get('publisher'))}|{_text(item.get('title'))}"
    )


def _source_key(item: dict[str, Any]) -> str:
    return _slug(
        _text(item.get("independence_group"))
        or _text(item.get("publisher"))
        or _text(item.get("url"))
        or _text(item.get("title"))
    )


def _window_evidence(
    evidence: list[Any], as_of: date, window_days: int, gaps: list[str]
) -> list[dict[str, Any]]:
    start = as_of - timedelta(days=window_days - 1)
    valid: list[dict[str, Any]] = []
    invalid_dates = 0
    for item in evidence:
        if not isinstance(item, dict):
            gaps.append("存在非对象证据，已忽略")
            continue
        try:
            item_date = _parse_date(item.get("date"), "evidence.date")
        except RadarValidationError:
            invalid_dates += 1
            continue
        if start <= item_date <= as_of:
            normalized = copy.deepcopy(item)
            normalized["_parsed_date"] = item_date
            valid.append(normalized)
    if invalid_dates:
        gaps.append(f"{invalid_dates}条证据缺少可解析日期，未计分")
    if evidence and not valid:
        gaps.append("没有位于监控窗口内的可定日证据")
    return valid


def _linked_evidence(
    candidate: dict[str, Any], evidence: list[dict[str, Any]], gaps: list[str]
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[str] = set()
    missing_ids = 0
    for item in evidence:
        evidence_id = _slug(_text(item.get("evidence_id")))
        if not evidence_id:
            missing_ids += 1
            continue
        if evidence_id in by_id:
            duplicate_ids.add(evidence_id)
            continue
        by_id[evidence_id] = item
    for evidence_id in duplicate_ids:
        by_id.pop(evidence_id, None)
    if missing_ids:
        gaps.append(f"{missing_ids}条窗口内证据缺少evidence_id，不能绑定评分字段")
    if duplicate_ids:
        gaps.append(f"{len(duplicate_ids)}个evidence_id重复，相关证据不能绑定评分字段")

    labels = {"price": "价格", "supply": "供给", "a_share": "A股"}
    linked: dict[str, list[dict[str, Any]]] = {}
    linked_ids: set[str] = set()
    for dimension, label in labels.items():
        data = candidate.get(dimension) if isinstance(candidate.get(dimension), dict) else {}
        raw_refs = data.get("evidence_refs", [])
        if not isinstance(raw_refs, list):
            gaps.append(f"{dimension}.evidence_refs不是数组，{label}维度按0分")
            refs: list[str] = []
        else:
            refs = _unique_strings(raw_refs)
        if not refs:
            gaps.append(f"缺少{dimension}.evidence_refs，{label}维度按0分")
        rows: list[dict[str, Any]] = []
        unknown = 0
        for ref in refs:
            evidence_id = _slug(ref)
            item = by_id.get(evidence_id)
            if item is None:
                unknown += 1
                continue
            rows.append(item)
            linked_ids.add(evidence_id)
        if unknown:
            gaps.append(f"{dimension}.evidence_refs有{unknown}项未指向窗口内唯一证据")
        linked[dimension] = rows

    supporting = [
        item
        for item in evidence
        if _slug(_text(item.get("evidence_id"))) in linked_ids
        and _slug(_text(item.get("evidence_id"))) not in duplicate_ids
    ]
    return linked, supporting


def _score_density(
    evidence: list[dict[str, Any]], as_of: date, window_days: int, gaps: list[str]
) -> tuple[int, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for item in evidence:
        key = _event_key(item)
        if not key:
            continue
        existing = events.get(key)
        if existing is None or item["_parsed_date"] < existing["_parsed_date"]:
            events[key] = item

    count = len(events)
    if count == 0:
        count_points = 0
    elif count == 1:
        count_points = 2
    elif count == 2:
        count_points = 5
    elif count <= 4:
        count_points = 7
    elif count <= 7:
        count_points = 10
    else:
        count_points = 12

    reported_classes = {
        _slug(_text(item.get("source_class")))
        for item in events.values()
        if _text(item.get("source_class"))
    }
    source_classes = reported_classes & SOURCE_CLASSES
    diversity_points = min(4, len(source_classes))
    if count and not source_classes:
        gaps.append("证据缺少source_class，来源多样性按0分")
    unknown_classes = reported_classes - SOURCE_CLASSES
    if unknown_classes:
        gaps.append(f"{len(unknown_classes)}个未知source_class未计入来源多样性")

    if not events:
        acceleration_points = 0
        acceleration_ratio: float | None = None
    else:
        recent_days = max(1, int(round(window_days * 0.3)))
        if recent_days >= window_days and window_days > 1:
            recent_days = window_days - 1
        prior_days = window_days - recent_days
        recent_count = 0
        prior_count = 0
        for item in events.values():
            age = (as_of - item["_parsed_date"]).days
            if age < recent_days:
                recent_count += 1
            else:
                prior_count += 1
        recent_rate = recent_count / recent_days
        prior_rate = prior_count / prior_days if prior_days > 0 else 0.0
        if prior_rate == 0:
            acceleration_ratio = None if recent_rate == 0 else math.inf
        else:
            acceleration_ratio = recent_rate / prior_rate
        ratio = acceleration_ratio
        if ratio is None:
            acceleration_points = 0
        elif math.isinf(ratio):
            acceleration_points = 4
        elif ratio < 0.8:
            acceleration_points = 0
        elif ratio < 1.2:
            acceleration_points = 1
        elif ratio < 1.5:
            acceleration_points = 2
        elif ratio < 2:
            acceleration_points = 3
        else:
            acceleration_points = 4

    total = count_points + diversity_points + acceleration_points
    return total, {
        "independent_events": count,
        "event_count_points": count_points,
        "source_class_count": len(source_classes),
        "source_diversity_points": diversity_points,
        "acceleration_ratio": None
        if acceleration_ratio is None or math.isinf(acceleration_ratio)
        else round(acceleration_ratio, 3),
        "acceleration_is_infinite": bool(
            acceleration_ratio is not None and math.isinf(acceleration_ratio)
        ),
        "acceleration_points": acceleration_points,
    }


def _score_sources(
    evidence: list[dict[str, Any]], gaps: list[str]
) -> tuple[int, dict[str, Any]]:
    sources: dict[str, str] = {}
    invalid_tiers = 0
    invalid_urls = 0
    for item in evidence:
        tier = _text(item.get("tier")).upper()
        if tier not in TIER_ORDER:
            invalid_tiers += 1
            continue
        if not _safe_http_url(item.get("url")):
            invalid_urls += 1
            continue
        key = _source_key(item)
        if not key:
            continue
        if key not in sources or TIER_ORDER[tier] > TIER_ORDER[sources[key]]:
            sources[key] = tier
    if invalid_tiers:
        gaps.append(f"{invalid_tiers}条证据缺少合法S/A/B/C/D等级，未计入来源质量")
    if invalid_urls:
        gaps.append(f"{invalid_urls}条证据缺少合法HTTP(S)直达链接，未计入来源质量")
    tiers = sorted(sources.values(), key=lambda tier: TIER_ORDER[tier], reverse=True)
    if not tiers:
        gaps.append("没有可计分的独立信源")
        return 0, {"independent_sources": 0, "top_tiers": [], "components": [0, 0, 0]}
    components = [BEST_SOURCE_POINTS[tiers[0]], 0, 0]
    if len(tiers) > 1:
        components[1] = SECOND_SOURCE_POINTS[tiers[1]]
    if len(tiers) > 2:
        components[2] = THIRD_SOURCE_POINTS[tiers[2]]
    return min(25, sum(components)), {
        "independent_sources": len(tiers),
        "top_tiers": tiers[:3],
        "components": components,
    }


def _enum_points(
    data: dict[str, Any], field: str, mapping: dict[str, int], gaps: list[str], label: str
) -> tuple[int, str]:
    value = _text(data.get(field), "missing").lower()
    if value not in mapping:
        gaps.append(f"{label}枚举值无效，按0分")
        return 0, value
    if value == "missing":
        gaps.append(f"缺少{label}数据")
    return mapping[value], value


def _nonnegative_integer(value: Any, field: str, gaps: list[str]) -> int | None:
    number = _number(value)
    if number is None:
        gaps.append(f"缺少{field}")
        return None
    if number < 0 or not number.is_integer():
        gaps.append(f"{field}必须是非负整数，按0分")
        return None
    return int(number)


def _bounded_percentage(value: Any, field: str, gaps: list[str]) -> float | None:
    number = _number(value)
    if number is None:
        gaps.append(f"缺少{field}")
        return None
    if not 0 <= number <= 100:
        gaps.append(f"{field}必须位于0到100之间，按0分")
        return None
    return number


def _nonnegative_number(value: Any, field: str, gaps: list[str]) -> float | None:
    number = _number(value)
    if number is None:
        gaps.append(f"缺少{field}")
        return None
    if number < 0:
        gaps.append(f"{field}必须是非负数，按0分")
        return None
    return number


def _score_price(price: Any, gaps: list[str]) -> tuple[int, dict[str, Any]]:
    data = price if isinstance(price, dict) else {}
    evidence_type = _text(data.get("evidence_type"), "none").lower()
    if evidence_type not in PRICE_TYPE_POINTS:
        gaps.append("price.evidence_type无效，按none处理")
        evidence_type = "none"
    evidence_points = PRICE_TYPE_POINTS[evidence_type]
    if evidence_type == "none":
        gaps.append("缺少可验证的价格证据形态")

    change_pct = _number(data.get("change_pct"))
    if change_pct is None:
        change_points = 0
        gaps.append("缺少可比口径的已验证涨幅")
    elif change_pct <= 0:
        change_points = 0
    elif change_pct < 2:
        change_points = 1
    elif change_pct < 5:
        change_points = 3
    elif change_pct < 10:
        change_points = 4
    else:
        change_points = 5

    breadth = _nonnegative_integer(data.get("breadth_count"), "价格覆盖面数据", gaps)
    if breadth is None:
        breadth_points = 0
    elif breadth <= 0:
        breadth_points = 0
    elif breadth < 2:
        breadth_points = 1
    elif breadth < 3:
        breadth_points = 3
    else:
        breadth_points = 4

    persistence = _nonnegative_integer(data.get("persistence_days"), "价格持续天数", gaps)
    if persistence is None:
        persistence_points = 0
    elif persistence <= 1:
        persistence_points = 0
    elif persistence <= 3:
        persistence_points = 1
    elif persistence <= 7:
        persistence_points = 3
    else:
        persistence_points = 4

    total = evidence_points + change_points + breadth_points + persistence_points
    return total, {
        "evidence_type": evidence_type,
        "evidence_points": evidence_points,
        "change_pct": change_pct,
        "change_points": change_points,
        "breadth_count": breadth,
        "breadth_points": breadth_points,
        "persistence_days": persistence,
        "persistence_points": persistence_points,
    }


def _score_supply(supply: Any, gaps: list[str]) -> tuple[int, dict[str, Any]]:
    data = supply if isinstance(supply, dict) else {}
    inventory_points, inventory = _enum_points(data, "inventory", INVENTORY_POINTS, gaps, "库存")
    utilization_points, utilization = _enum_points(
        data, "utilization_leadtime", UTILIZATION_POINTS, gaps, "开工率/交期"
    )
    disruption_points, disruption = _enum_points(
        data, "disruption", DISRUPTION_POINTS, gaps, "供给扰动"
    )
    demand_points, demand = _enum_points(data, "demand_gap", DEMAND_POINTS, gaps, "需求/缺口")
    total = inventory_points + utilization_points + disruption_points + demand_points
    return total, {
        "inventory": inventory,
        "inventory_points": inventory_points,
        "utilization_leadtime": utilization,
        "utilization_leadtime_points": utilization_points,
        "disruption": disruption,
        "disruption_points": disruption_points,
        "demand_gap": demand,
        "demand_gap_points": demand_points,
    }


def _score_a_share(market: Any, gaps: list[str]) -> tuple[int, dict[str, Any]]:
    data = market if isinstance(market, dict) else {}
    beneficiaries = data.get("beneficiaries", [])
    if not isinstance(beneficiaries, list):
        beneficiaries = []
        gaps.append("a_share.beneficiaries不是数组")
    direct_keys: set[str] = set()
    duplicate_direct = 0
    for item in beneficiaries:
        if not isinstance(item, dict) or _text(item.get("directness")).lower() != "direct":
            continue
        key = _slug(_text(item.get("code")) or _text(item.get("name")))
        if not key:
            continue
        if key in direct_keys:
            duplicate_direct += 1
        direct_keys.add(key)
    direct_count = len(direct_keys)
    if duplicate_direct:
        gaps.append(f"A股直接受益股有{duplicate_direct}条重复记录，覆盖数已去重")

    excess = _number(data.get("excess_return_pct"))
    if excess is None:
        excess_points = 0
        gaps.append("缺少直接受益股篮子超额收益")
    elif excess <= 0:
        excess_points = 0
    elif excess < 3:
        excess_points = 1
    elif excess < 6:
        excess_points = 2
    elif excess < 10:
        excess_points = 3
    else:
        excess_points = 4

    breadth = _bounded_percentage(data.get("positive_breadth_pct"), "A股正超额收益广度", gaps)
    if breadth is None:
        breadth_points = 0
    elif breadth < 40:
        breadth_points = 0
    elif breadth < 60:
        breadth_points = 1
    elif breadth < 75:
        breadth_points = 2
    elif breadth < 85:
        breadth_points = 2
    else:
        breadth_points = 3

    turnover = _nonnegative_number(data.get("turnover_ratio"), "A股成交额放大倍数", gaps)
    if turnover is None:
        turnover_points = 0
    elif turnover <= 1:
        turnover_points = 0
    elif turnover < 1.3:
        turnover_points = 1
    elif turnover < 1.8:
        turnover_points = 1
    else:
        turnover_points = 2

    positive_days = _nonnegative_integer(data.get("positive_excess_days"), "A股正超额收益持续天数", gaps)
    if positive_days is None:
        persistence_points = 0
    elif positive_days <= 1:
        persistence_points = 0
    elif positive_days <= 3:
        persistence_points = 1
    else:
        persistence_points = 1

    raw_total = excess_points + breadth_points + turnover_points + persistence_points
    low_coverage = direct_count < 3
    if low_coverage:
        gaps.append(f"直接受益股仅{direct_count}只，A股异动分封顶5分")
    total = min(5, raw_total) if low_coverage else raw_total
    return total, {
        "direct_beneficiary_count": direct_count,
        "low_coverage": low_coverage,
        "excess_return_pct": excess,
        "excess_return_points": excess_points,
        "positive_breadth_pct": breadth,
        "breadth_points": breadth_points,
        "turnover_ratio": turnover,
        "turnover_points": turnover_points,
        "positive_excess_days": positive_days,
        "persistence_points": persistence_points,
        "raw_total": raw_total,
        "applied_cap": 5 if low_coverage else 10,
    }


def _has_material_signal(candidate: dict[str, Any]) -> bool:
    price = candidate.get("price") if isinstance(candidate.get("price"), dict) else {}
    price_type = _text(price.get("evidence_type"), "none").lower()
    if price_type in PRICE_TYPE_POINTS and PRICE_TYPE_POINTS[price_type] > 0:
        return True
    supply = candidate.get("supply") if isinstance(candidate.get("supply"), dict) else {}
    mappings = {
        "inventory": INVENTORY_POINTS,
        "utilization_leadtime": UTILIZATION_POINTS,
        "disruption": DISRUPTION_POINTS,
        "demand_gap": DEMAND_POINTS,
    }
    return any(mapping.get(_text(supply.get(field), "missing").lower(), 0) > 0 for field, mapping in mappings.items())


def _has_linked_material_signal(
    candidate: dict[str, Any], linked: dict[str, list[dict[str, Any]]]
) -> bool:
    price = candidate.get("price") if isinstance(candidate.get("price"), dict) else {}
    price_type = _text(price.get("evidence_type"), "none").lower()
    if linked["price"] and price_type in PRICE_TYPE_POINTS and PRICE_TYPE_POINTS[price_type] > 0:
        return True
    supply = candidate.get("supply") if isinstance(candidate.get("supply"), dict) else {}
    mappings = {
        "inventory": INVENTORY_POINTS,
        "utilization_leadtime": UTILIZATION_POINTS,
        "disruption": DISRUPTION_POINTS,
        "demand_gap": DEMAND_POINTS,
    }
    return bool(linked["supply"]) and any(
        mapping.get(_text(supply.get(field), "missing").lower(), 0) > 0
        for field, mapping in mappings.items()
    )


def _initial_status(total: int) -> str:
    if total >= 65:
        return "高确定性"
    if total >= 50:
        return "发酵中"
    if total >= 35:
        return "观察"
    return "证据不足"


def score_candidate(
    candidate: dict[str, Any], as_of: date, window_days: int
) -> dict[str, Any]:
    result = copy.deepcopy(candidate)
    gaps = _unique_strings(candidate.get("data_gaps", []) if isinstance(candidate.get("data_gaps"), list) else [])
    evidence = candidate.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
        gaps.append("evidence不是数组")
    valid_evidence = _window_evidence(evidence, as_of, window_days, gaps)
    linked, supporting_evidence = _linked_evidence(candidate, valid_evidence, gaps)

    density_score, density_details = _score_density(supporting_evidence, as_of, window_days, gaps)
    source_score, source_details = _score_sources(supporting_evidence, gaps)

    scorers = {
        "price": (_score_price, candidate.get("price")),
        "supply": (_score_supply, candidate.get("supply")),
        "a_share": (_score_a_share, candidate.get("a_share")),
    }
    dimension_results: dict[str, tuple[int, dict[str, Any]]] = {}
    for dimension, (scorer, value) in scorers.items():
        rows = linked[dimension]
        if rows:
            score, details = scorer(value, gaps)
            details["eligible"] = True
            details["linked_evidence_ids"] = [_text(row.get("evidence_id")) for row in rows]
        else:
            score = 0
            details = {"eligible": False, "linked_evidence_ids": []}
        dimension_results[dimension] = (score, details)
    price_score, price_details = dimension_results["price"]
    supply_score, supply_details = dimension_results["supply"]
    market_score, market_details = dimension_results["a_share"]

    scores = {
        "message_density": density_score,
        "source_quality": source_score,
        "price_validation": price_score,
        "supply_constraint": supply_score,
        "a_share_movement": market_score,
    }
    total = sum(scores.values())
    status = _initial_status(total)
    gate_reasons: list[str] = []
    if total >= 65:
        if source_score < 15:
            gate_reasons.append(f"来源质量{source_score}<15")
        if price_score < 13:
            gate_reasons.append(f"价格验证{price_score}<13")
        if gate_reasons:
            status = "发酵中"

    result["scores"] = scores
    result["score_details"] = {
        "message_density": density_details,
        "source_quality": source_details,
        "price_validation": price_details,
        "supply_constraint": supply_details,
        "a_share_movement": market_details,
    }
    result["total_score"] = total
    result["status"] = status
    result["status_gate"] = {
        "applied": bool(gate_reasons),
        "requirements": {"source_quality_min": 15, "price_validation_min": 13},
        "reasons": gate_reasons,
    }
    result["evidence"] = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in supporting_evidence
    ]
    result["data_gaps"] = _unique_strings(gaps)
    return result


def score_radar(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RadarValidationError("input root must be an object")
    as_of = _parse_date(payload.get("as_of"), "as_of")
    window_days = payload.get("window_days", 10)
    max_items = payload.get("max_items", 30)
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 1:
        raise RadarValidationError("window_days must be an integer >= 1")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 30:
        raise RadarValidationError("max_items must be an integer between 1 and 30")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise RadarValidationError("candidates must be an array")

    names: set[str] = set()
    scored: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise RadarValidationError(f"candidates[{index}] must be an object")
        name = _text(candidate.get("material_chain"))
        if not name:
            raise RadarValidationError(f"candidates[{index}].material_chain is required")
        normalized_name = _slug(name)
        if normalized_name in names:
            raise RadarValidationError(f"duplicate material_chain: {name}")
        names.add(normalized_name)
        if not _has_material_signal(candidate):
            excluded.append({
                "material_chain": name,
                "reason": "缺少材料价格或供给信号；纯股票异动/概念讨论不纳入榜单",
            })
            continue
        evidence = candidate.get("evidence", [])
        valid_evidence = (
            _window_evidence(evidence, as_of, window_days, [])
            if isinstance(evidence, list)
            else []
        )
        if not valid_evidence:
            excluded.append({
                "material_chain": name,
                "reason": "没有监控窗口内可定日的材料价格或供给证据",
            })
            continue
        linked, _ = _linked_evidence(candidate, valid_evidence, [])
        if not _has_linked_material_signal(candidate, linked):
            excluded.append({
                "material_chain": name,
                "reason": "没有监控窗口内且通过evidence_refs绑定的有效材料价格或供给信号",
            })
            continue
        scored.append(score_candidate(candidate, as_of, window_days))

    scored.sort(
        key=lambda item: (
            -int(item["total_score"]),
            -int(item["scores"]["price_validation"]),
            -int(item["scores"]["source_quality"]),
            -int(item["scores"]["message_density"]),
            _text(item.get("material_chain")),
        )
    )
    scored = scored[:max_items]
    return {
        "title": _text(payload.get("title"), "材料涨价雷达"),
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "max_items": max_items,
        "method_version": METHOD_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(scored),
        "excluded_candidates": excluded,
        "candidates": scored,
    }


def _md_escape(value: Any) -> str:
    text = _text(value, "—")
    for token in ("\\", "|", "[", "]"):
        text = text.replace(token, f"\\{token}")
    return text.replace("\n", " ") or "—"


def _md_list(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "- 无"
    return "\n".join(f"- {_md_escape(value)}" for value in values)


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# {_md_escape(result.get('title'))}",
        "",
        f"截止日期：{result['as_of']}　监控窗口：近{result['window_days']}个自然日　覆盖：{result['candidate_count']}条材料链",
        "",
        "评分：消息密度20 + 来源质量25 + 价格验证25 + 供给约束20 + A股异动10。",
        "",
        "## 发酵雷达总览（按评分排序）",
        "",
        "| 材料链 | 分类 | 发酵分 | 状态 | 核心催化 |",
        "|---|---|---:|---|---|",
    ]
    for item in result["candidates"]:
        lines.append(
            "| {material} | {category} | {score} | {status} | {catalyst} |".format(
                material=_md_escape(item.get("material_chain")),
                category=_md_escape(item.get("category")),
                score=item["total_score"],
                status=item["status"],
                catalyst=_md_escape(item.get("core_catalyst")),
            )
        )

    lines.extend(["", "## 证据附录", ""])
    score_labels = {
        "message_density": "消息密度",
        "source_quality": "来源质量",
        "price_validation": "价格验证",
        "supply_constraint": "供给约束",
        "a_share_movement": "A股异动",
    }
    for index, item in enumerate(result["candidates"], 1):
        scores = item["scores"]
        breakdown = " / ".join(f"{label} {scores[key]}" for key, label in score_labels.items())
        lines.extend(
            [
                f"### {index}. {_md_escape(item.get('material_chain'))} — {item['total_score']}分 · {item['status']}",
                "",
                f"- 分项：{breakdown}",
                f"- 核心催化：{_md_escape(item.get('core_catalyst'))}",
                f"- 价格验证：{_md_escape((item.get('price') or {}).get('description'))}",
                f"- 供给约束：{_md_escape((item.get('supply') or {}).get('description'))}",
                f"- A股验证：{_md_escape((item.get('a_share') or {}).get('description'))}",
                "",
                "| 等级 | 日期 | 来源 | 支持事实 |",
                "|---|---|---|---|",
            ]
        )
        evidence = item.get("evidence", [])
        if isinstance(evidence, list):
            evidence = sorted(
                [row for row in evidence if isinstance(row, dict)],
                key=lambda row: _text(row.get("date")),
                reverse=True,
            )
        for row in evidence:
            publisher = _md_escape(row.get("publisher") or row.get("title"))
            url = _safe_http_url(row.get("url"))
            source = f"[{publisher}]({url})" if url else publisher
            lines.append(
                f"| {_md_escape(row.get('tier'))} | {_md_escape(row.get('date'))} | {source} | {_md_escape(row.get('claim'))} |"
            )
        if not evidence:
            lines.append("| — | — | — | 无可展示证据 |")

        lines.extend(["", "A股映射：", "", "| 代码 | 公司 | 直接性 | 依据 |", "|---|---|---|---|"])
        beneficiaries = (item.get("a_share") or {}).get("beneficiaries", [])
        if not isinstance(beneficiaries, list):
            beneficiaries = []
        for stock in beneficiaries:
            if not isinstance(stock, dict):
                continue
            directness = "直接" if _text(stock.get("directness")).lower() == "direct" else "间接"
            lines.append(
                f"| {_md_escape(stock.get('code'))} | {_md_escape(stock.get('name'))} | {directness} | {_md_escape(stock.get('basis'))} |"
            )
        if not beneficiaries:
            lines.append("| — | — | — | 未完成映射 |")

        lines.extend(
            [
                "",
                "反证：",
                _md_list(item.get("counterevidence")),
                "",
                "失效条件：",
                _md_list(item.get("invalidation_conditions")),
                "",
                "数据缺口：",
                _md_list(item.get("data_gaps")),
            ]
        )
        gate = item.get("status_gate", {})
        if gate.get("applied"):
            lines.extend(["", f"门槛降级：{'；'.join(gate.get('reasons', []))}"])
        lines.append("")

    if result.get("excluded_candidates"):
        lines.extend(["## 已排除候选", ""])
        for item in result["excluded_candidates"]:
            lines.append(f"- {_md_escape(item.get('material_chain'))}：{_md_escape(item.get('reason'))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RadarValidationError(f"input file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RadarValidationError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="normalized UTF-8 radar JSON")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path, help="write output instead of stdout")
    args = parser.parse_args(argv)
    try:
        result = score_radar(_load_json(args.input))
        if args.format == "json":
            rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        else:
            rendered = render_markdown(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except RadarValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
