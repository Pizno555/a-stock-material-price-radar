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


METHOD_VERSION = "2.1.0"
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
    "intent": 4,
    "preprice_action": 6,
    "supplier_notice": 10,
    "public_quote": 12,
    "transaction": 15,
}
PRICE_SOURCE_CAPS = {"D": 4, "C": 10, "B": 20, "A": 25, "S": 25}
SUPPLY_SOURCE_CAPS = {"D": 4, "C": 10, "B": 16, "A": 20, "S": 20}
PRICE_GATE_TYPES = {"supplier_notice", "public_quote", "transaction"}
CHANGE_BASIS_TO_TYPE = {
    "intent": "intent",
    "terms": "preprice_action",
    "notice": "supplier_notice",
    "quote": "public_quote",
    "transaction": "transaction",
}
EXPECTED_CHANGE_BASIS = {value: key for key, value in CHANGE_BASIS_TO_TYPE.items()}
SUPPORT_TAGS = {
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
}
CATALYST_STATUSES = {"scheduled", "ongoing"}
CATALYST_TIMING_BASES = {"source_explicit", "observed_trend", "research_inference"}
CATALYST_TYPES = {
    "maintenance",
    "shutdown",
    "quota_reduction",
    "export_restriction",
    "capacity_exit",
    "capacity_delay",
    "restart_delay",
    "demand_commissioning",
    "inventory_tightening",
    "leadtime_extension",
    "order_acceleration",
    "utilization_ramp",
    "supply_gap_widening",
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


def _supports(item: dict[str, Any], gaps: list[str] | None = None) -> set[str]:
    raw = item.get("supports", [])
    if raw is None:
        return set()
    if not isinstance(raw, list):
        if gaps is not None:
            gaps.append(f"证据{_text(item.get('evidence_id')) or '（无ID）'}的supports不是数组")
        return set()
    reported = {_slug(_text(value)) for value in raw if _text(value)}
    unknown = reported - SUPPORT_TAGS
    if unknown and gaps is not None:
        gaps.append(
            f"证据{_text(item.get('evidence_id')) or '（无ID）'}有{len(unknown)}个未知supports标签"
        )
    return reported & SUPPORT_TAGS


def _rows_with_support(rows: list[dict[str, Any]], tag: str) -> list[dict[str, Any]]:
    return [row for row in rows if tag in _supports(row)]


def _tier_at_least(item: dict[str, Any], minimum: str) -> bool:
    tier = _text(item.get("tier")).upper()
    return tier in TIER_ORDER and TIER_ORDER[tier] >= TIER_ORDER[minimum]


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


def _source_key(item: dict[str, Any], require_explicit: bool = False) -> str:
    explicit = _text(item.get("independence_group"))
    if explicit:
        return _slug(explicit)
    if require_explicit:
        return ""
    return _slug(
        _text(item.get("publisher"))
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
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
]:
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
    return linked, supporting, by_id


def _score_density(
    evidence: list[dict[str, Any]],
    as_of: date,
    window_days: int,
    gaps: list[str],
    strict: bool,
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

    independent_sources: dict[str, dict[str, Any]] = {}
    missing_source_keys = 0
    for item in evidence:
        key = _source_key(item, require_explicit=strict)
        if not key:
            missing_source_keys += 1
            continue
        if key and key not in independent_sources:
            independent_sources[key] = item
    if missing_source_keys:
        gaps.append(
            f"{missing_source_keys}条基本面证据缺少independence_group，未计入来源多样性"
        )
    reported_classes = {
        _slug(_text(item.get("source_class")))
        for item in independent_sources.values()
        if _text(item.get("source_class"))
    }
    source_classes = reported_classes & SOURCE_CLASSES
    diversity_points = min(4, len(source_classes))
    if count and not source_classes:
        gaps.append("证据缺少source_class，来源多样性按0分")
    unknown_classes = reported_classes - SOURCE_CLASSES
    if unknown_classes:
        gaps.append(f"{len(unknown_classes)}个未知source_class未计入来源多样性")

    if window_days == 1:
        acceleration_points = 0
        acceleration_ratio = None
        gaps.append("窗口不足，无法计算消息加速度")
    elif not events:
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
    evidence: list[dict[str, Any]], gaps: list[str], strict: bool
) -> tuple[int, dict[str, Any]]:
    sources: dict[str, str] = {}
    invalid_tiers = 0
    invalid_urls = 0
    missing_source_keys = 0
    for item in evidence:
        tier = _text(item.get("tier")).upper()
        if tier not in TIER_ORDER:
            invalid_tiers += 1
            continue
        if not _safe_http_url(item.get("url")):
            invalid_urls += 1
            continue
        key = _source_key(item, require_explicit=strict)
        if not key:
            missing_source_keys += 1
            continue
        if key not in sources or TIER_ORDER[tier] > TIER_ORDER[sources[key]]:
            sources[key] = tier
    if invalid_tiers:
        gaps.append(f"{invalid_tiers}条证据缺少合法S/A/B/C/D等级，未计入来源质量")
    if invalid_urls:
        gaps.append(f"{invalid_urls}条证据缺少合法HTTP(S)直达链接，未计入来源质量")
    if missing_source_keys:
        gaps.append(
            f"{missing_source_keys}条证据缺少independence_group，未计入来源质量"
        )
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


def _dimension_source_cap(
    evidence: list[dict[str, Any]],
    caps: dict[str, int],
    gaps: list[str],
    label: str,
    strict: bool,
) -> tuple[int, str | None]:
    tiers = [
        _text(item.get("tier")).upper()
        for item in evidence
        if _text(item.get("tier")).upper() in TIER_ORDER
        and _safe_http_url(item.get("url"))
        and (not strict or bool(_source_key(item, require_explicit=True)))
    ]
    if not tiers:
        gaps.append(f"{label}维度没有带合法直达链接和等级的有效来源，分项封顶0分")
        return 0, None
    strongest = max(tiers, key=lambda tier: TIER_ORDER[tier])
    return caps[strongest], strongest


def _dedupe_evidence(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = _slug(_text(row.get("evidence_id")))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _fundamental_evidence(
    linked: dict[str, list[dict[str, Any]]],
    price_details: dict[str, Any],
    supply_details: dict[str, Any],
    strict: bool,
) -> list[dict[str, Any]]:
    if not strict:
        return _dedupe_evidence([*linked["price"], *linked["supply"]])

    active_price_tags = set(price_details.get("scored_support_tags", []))
    active_supply_tags = set(supply_details.get("scored_support_tags", []))
    rows = [
        row for row in linked["price"] if _supports(row) & active_price_tags
    ] + [
        row for row in linked["supply"] if _supports(row) & active_supply_tags
    ]
    return _dedupe_evidence(rows)


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


def _score_price(
    price: Any,
    evidence: list[dict[str, Any]],
    strict: bool,
    gaps: list[str],
) -> tuple[int, dict[str, Any]]:
    data = price if isinstance(price, dict) else {}
    reported_type = _text(data.get("evidence_type"), "none").lower()
    if reported_type not in PRICE_TYPE_POINTS:
        gaps.append("price.evidence_type无效，按none处理")
        reported_type = "none"
    evidence_type = reported_type

    change_basis = _text(data.get("change_basis")).lower()
    expected_basis = EXPECTED_CHANGE_BASIS.get(evidence_type, "")
    inferred_basis = False
    numeric_basis_valid = True
    if evidence_type in {"none", "rumor"}:
        if change_basis:
            gaps.append(f"{evidence_type}不应填写price.change_basis")
        change_basis = ""
    elif not change_basis:
        if strict:
            numeric_basis_valid = False
            gaps.append("v2.1输入缺少price.change_basis，价格数值子项按0分")
        else:
            change_basis = expected_basis
            inferred_basis = True
            gaps.append("legacy输入缺少price.change_basis，已按evidence_type推断")
    elif change_basis not in CHANGE_BASIS_TO_TYPE:
        numeric_basis_valid = False
        gaps.append("price.change_basis无效，价格数值子项按0分")
    elif change_basis != expected_basis:
        basis_type = CHANGE_BASIS_TO_TYPE[change_basis]
        if PRICE_TYPE_POINTS[basis_type] < PRICE_TYPE_POINTS[evidence_type]:
            gaps.append(
                f"price.change_basis与evidence_type错配，价格阶段已从{evidence_type}降为{basis_type}"
            )
            evidence_type = basis_type
        else:
            numeric_basis_valid = False
            gaps.append("price.change_basis高于可验证证据阶段，价格数值子项按0分")

    stage_supported = not strict or bool(_rows_with_support(evidence, "price.stage"))
    if not stage_supported:
        evidence_points = 0
        gaps.append("缺少price.stage子项支持，价格阶段按0分")
    else:
        evidence_points = PRICE_TYPE_POINTS[evidence_type]
    if evidence_type == "none":
        gaps.append("缺少可验证的价格证据形态")

    numeric_allowed = evidence_type not in {"none", "rumor"} and numeric_basis_valid

    change_pct = _number(data.get("change_pct"))
    change_supported = not strict or bool(_rows_with_support(evidence, "price.change_pct"))
    if change_pct is None:
        change_points = 0
        gaps.append("缺少同口径明确涨幅")
    elif change_pct <= 0:
        change_points = 0
    elif not numeric_allowed:
        change_points = 0
        gaps.append(f"{evidence_type}阶段不允许涨幅子项计分")
    elif not change_supported:
        change_points = 0
        gaps.append("缺少price.change_pct子项支持，明确涨幅按0分")
    elif change_pct < 2:
        change_points = 1
    elif change_pct < 5:
        change_points = 2
    elif change_pct < 10:
        change_points = 3
    else:
        change_points = 4

    breadth = _nonnegative_integer(data.get("breadth_count"), "价格覆盖面数据", gaps)
    breadth_supported = not strict or bool(_rows_with_support(evidence, "price.breadth"))
    if breadth is None or breadth <= 0:
        breadth_points = 0
    elif not numeric_allowed:
        breadth_points = 0
        gaps.append(f"{evidence_type}阶段不允许覆盖面子项计分")
    elif not breadth_supported:
        breadth_points = 0
        gaps.append("缺少price.breadth子项支持，价格覆盖面按0分")
    elif breadth == 1:
        breadth_points = 1
    elif breadth == 2:
        breadth_points = 2
    else:
        breadth_points = 3

    persistence = _nonnegative_integer(data.get("persistence_days"), "价格持续天数", gaps)
    persistence_rows = _rows_with_support(evidence, "price.persistence")
    persistence_supported = not strict or bool(persistence_rows)
    if evidence_type == "intent" and strict and persistence_supported:
        distinct_dates = {_text(row.get("date")) for row in persistence_rows}
        has_time_series = any(
            row.get("time_series") is True and _tier_at_least(row, "B")
            for row in persistence_rows
        )
        if len(distinct_dates) < 2 and not has_time_series:
            persistence_supported = False
            gaps.append("intent持续性缺少两个日期事件或B级以上时间序列")
    if persistence is None or persistence <= 1:
        persistence_points = 0
    elif not numeric_allowed:
        persistence_points = 0
        gaps.append(f"{evidence_type}阶段不允许持续性子项计分")
    elif not persistence_supported:
        persistence_points = 0
        gaps.append("缺少price.persistence子项支持，价格持续性按0分")
    elif persistence <= 3:
        persistence_points = 1
    elif persistence <= 7:
        persistence_points = 2
    else:
        persistence_points = 3

    scored_support_tags = []
    for tag, points in (
        ("price.stage", evidence_points),
        ("price.change_pct", change_points),
        ("price.breadth", breadth_points),
        ("price.persistence", persistence_points),
    ):
        if points > 0:
            scored_support_tags.append(tag)

    raw_total = evidence_points + change_points + breadth_points + persistence_points
    if raw_total > 0:
        cap_evidence = evidence
        if strict:
            active_tags = set(scored_support_tags)
            cap_evidence = [row for row in evidence if _supports(row) & active_tags]
        cap, strongest_tier = _dimension_source_cap(
            cap_evidence, PRICE_SOURCE_CAPS, gaps, "价格", strict
        )
    else:
        cap, strongest_tier = 0, None
    total = min(raw_total, cap)
    if raw_total > cap:
        gaps.append(f"价格原始分{raw_total}受{strongest_tier or '无'}级信源封顶为{cap}分")
    return total, {
        "reported_evidence_type": reported_type,
        "evidence_type": evidence_type,
        "change_basis": change_basis or None,
        "change_basis_inferred": inferred_basis,
        "evidence_points": evidence_points,
        "change_pct": change_pct,
        "change_points": change_points,
        "breadth_count": breadth,
        "breadth_points": breadth_points,
        "persistence_days": persistence,
        "persistence_points": persistence_points,
        "scored_support_tags": scored_support_tags,
        "raw_total": raw_total,
        "source_cap": cap,
        "strongest_source_tier": strongest_tier,
    }


def _score_supply(
    supply: Any,
    evidence: list[dict[str, Any]],
    strict: bool,
    gaps: list[str],
) -> tuple[int, dict[str, Any]]:
    data = supply if isinstance(supply, dict) else {}
    inventory_points, inventory = _enum_points(data, "inventory", INVENTORY_POINTS, gaps, "库存")
    utilization_points, utilization = _enum_points(
        data, "utilization_leadtime", UTILIZATION_POINTS, gaps, "开工率/交期"
    )
    disruption_points, disruption = _enum_points(
        data, "disruption", DISRUPTION_POINTS, gaps, "供给扰动"
    )
    demand_points, demand = _enum_points(data, "demand_gap", DEMAND_POINTS, gaps, "需求/缺口")
    support_checks = {
        "inventory": ("supply.inventory", inventory_points),
        "utilization_leadtime": ("supply.utilization_leadtime", utilization_points),
        "disruption": ("supply.disruption", disruption_points),
        "demand_gap": ("supply.demand_gap", demand_points),
    }
    values = {
        "inventory": inventory_points,
        "utilization_leadtime": utilization_points,
        "disruption": disruption_points,
        "demand_gap": demand_points,
    }
    if strict:
        for field, (tag, points) in support_checks.items():
            if points and not _rows_with_support(evidence, tag):
                values[field] = 0
                gaps.append(f"缺少{tag}子项支持，{field}按0分")
    scored_support_tags = [
        support_checks[field][0] for field, points in values.items() if points > 0
    ]
    raw_total = sum(values.values())
    if raw_total > 0:
        cap_evidence = evidence
        if strict:
            active_tags = set(scored_support_tags)
            cap_evidence = [row for row in evidence if _supports(row) & active_tags]
        cap, strongest_tier = _dimension_source_cap(
            cap_evidence, SUPPLY_SOURCE_CAPS, gaps, "供给", strict
        )
    else:
        cap, strongest_tier = 0, None
    total = min(raw_total, cap)
    if raw_total > cap:
        gaps.append(f"供给原始分{raw_total}受{strongest_tier or '无'}级信源封顶为{cap}分")
    return total, {
        "inventory": inventory,
        "inventory_points": values["inventory"],
        "utilization_leadtime": utilization,
        "utilization_leadtime_points": values["utilization_leadtime"],
        "disruption": disruption,
        "disruption_points": values["disruption"],
        "demand_gap": demand,
        "demand_gap_points": values["demand_gap"],
        "scored_support_tags": scored_support_tags,
        "raw_total": raw_total,
        "source_cap": cap,
        "strongest_source_tier": strongest_tier,
    }


def _score_a_share(
    market: Any,
    evidence: list[dict[str, Any]],
    strict: bool,
    gaps: list[str],
) -> tuple[int, dict[str, Any]]:
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
    if strict and raw_total and not _rows_with_support(evidence, "a_share.market_data"):
        gaps.append("缺少a_share.market_data子项支持，A股异动按0分")
        raw_total = 0
        excess_points = 0
        breadth_points = 0
        turnover_points = 0
        persistence_points = 0
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


def _parse_optional_date(value: Any) -> date | None:
    if not _text(value):
        return None
    try:
        return _parse_date(value, "date")
    except RadarValidationError:
        return None


def _validate_forward_catalyst(
    candidate: dict[str, Any],
    supply_rows: list[dict[str, Any]],
    as_of: date,
    gaps: list[str],
) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
    raw = candidate.get("forward_catalyst")
    if not isinstance(raw, dict):
        return False, {"valid": False, "reasons": ["缺少forward_catalyst"]}, []

    reasons: list[str] = []
    status = _text(raw.get("status")).lower()
    catalyst_type = _text(raw.get("type")).lower()
    timing_basis = _text(raw.get("timing_basis")).lower()
    if status not in CATALYST_STATUSES:
        reasons.append("forward_catalyst.status无效")
    if catalyst_type not in CATALYST_TYPES:
        reasons.append("forward_catalyst.type无效")
    if timing_basis not in CATALYST_TIMING_BASES:
        reasons.append("forward_catalyst.timing_basis无效")

    raw_refs = raw.get("evidence_refs", [])
    refs = _unique_strings(raw_refs) if isinstance(raw_refs, list) else []
    if not refs:
        reasons.append("forward_catalyst缺少evidence_refs")
    supply_by_id = {
        _slug(_text(row.get("evidence_id"))): row for row in supply_rows
    }
    rows = [supply_by_id[_slug(ref)] for ref in refs if _slug(ref) in supply_by_id]
    if len(rows) != len(refs):
        reasons.append("forward_catalyst引用了非供给或窗口外证据")
    support_rows = _rows_with_support(rows, "forward_catalyst")
    if not support_rows:
        reasons.append("forward_catalyst缺少对应supports标签")
    if not any(
        _tier_at_least(row, "B") and _safe_http_url(row.get("url"))
        for row in support_rows
    ):
        reasons.append("forward_catalyst缺少同时带支持标签的B级以上直达来源")

    start_date = _parse_optional_date(raw.get("start_date"))
    end_date = _parse_optional_date(raw.get("end_date"))
    observed_start = _parse_optional_date(raw.get("observed_start"))
    if status == "scheduled":
        if start_date is None:
            reasons.append("scheduled catalyst缺少合法start_date")
        elif start_date < as_of:
            reasons.append("scheduled catalyst的start_date早于as_of")
        if end_date is not None and start_date is not None and end_date < start_date:
            reasons.append("scheduled catalyst的end_date早于start_date")
        if timing_basis not in {"source_explicit", "research_inference"}:
            reasons.append("scheduled catalyst的timing_basis不合法")
    elif status == "ongoing":
        if observed_start is None:
            reasons.append("ongoing catalyst缺少合法observed_start")
        elif observed_start > as_of:
            reasons.append("ongoing catalyst的observed_start晚于as_of")
        if timing_basis not in {"observed_trend", "research_inference"}:
            reasons.append("ongoing catalyst的timing_basis不合法")
        distinct_dates = {_text(row.get("date")) for row in support_rows}
        has_time_series = any(
            row.get("time_series") is True and _tier_at_least(row, "B")
            for row in support_rows
        )
        if len(distinct_dates) < 2 and not has_time_series:
            reasons.append("ongoing catalyst缺少两个日期观察或B级以上时间序列")

    valid = not reasons
    if not valid:
        gaps.extend(reasons)
    details = {
        "valid": valid,
        "status": status or None,
        "type": catalyst_type or None,
        "timing_basis": timing_basis or None,
        "linked_evidence_ids": [_text(row.get("evidence_id")) for row in rows],
        "reasons": reasons,
    }
    return valid, details, rows


def _normalize_counterevidence(
    candidate: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    gaps: list[str],
) -> tuple[list[Any], bool, list[dict[str, Any]]]:
    raw_items = candidate.get("counterevidence", [])
    if not isinstance(raw_items, list):
        gaps.append("counterevidence不是数组，已按空列表处理")
        return [], False, []
    normalized: list[Any] = []
    has_blocking = False
    referenced_rows: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            normalized.append(_text(raw))
            continue
        item = copy.deepcopy(raw)
        effect = _text(item.get("effect"), "ordinary").lower()
        if effect not in {"ordinary", "blocking"}:
            gaps.append("counterevidence.effect无效，已降为ordinary")
            effect = "ordinary"
        refs_raw = item.get("evidence_refs", [])
        refs = _unique_strings(refs_raw) if isinstance(refs_raw, list) else []
        rows = [by_id[_slug(ref)] for ref in refs if _slug(ref) in by_id]
        referenced_rows.extend(rows)
        reasons: list[str] = []
        if effect == "blocking":
            if _text(item.get("dimension")).lower() != "supply":
                reasons.append("blocking counterevidence必须作用于supply")
            if not refs or len(rows) != len(refs):
                reasons.append("blocking counterevidence引用缺失或无效")
            support_rows = _rows_with_support(rows, "counterevidence.supply")
            if not support_rows:
                reasons.append("blocking counterevidence缺少对应supports标签")
            if not any(
                _tier_at_least(row, "B") and _safe_http_url(row.get("url"))
                for row in support_rows
            ):
                reasons.append("blocking counterevidence缺少同时带支持标签的B级以上直达来源")
            if reasons:
                effect = "ordinary"
                gaps.extend(reasons)
                item["downgrade_reasons"] = reasons
            else:
                has_blocking = True
        item["effect"] = effect
        normalized.append(item)
    return normalized, has_blocking, _dedupe_evidence(referenced_rows)


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
    candidate: dict[str, Any], linked: dict[str, list[dict[str, Any]]], strict: bool
) -> bool:
    price = candidate.get("price") if isinstance(candidate.get("price"), dict) else {}
    price_type = _text(price.get("evidence_type"), "none").lower()
    if linked["price"] and price_type in PRICE_TYPE_POINTS and PRICE_TYPE_POINTS[price_type] > 0:
        if not strict or any(
            any(tag.startswith("price.") for tag in _supports(row))
            for row in linked["price"]
        ):
            return True
    supply = candidate.get("supply") if isinstance(candidate.get("supply"), dict) else {}
    mappings = {
        "inventory": INVENTORY_POINTS,
        "utilization_leadtime": UTILIZATION_POINTS,
        "disruption": DISRUPTION_POINTS,
        "demand_gap": DEMAND_POINTS,
    }
    has_supply_value = bool(linked["supply"]) and any(
        mapping.get(_text(supply.get(field), "missing").lower(), 0) > 0
        for field, mapping in mappings.items()
    )
    if not has_supply_value:
        return False
    return not strict or any(
        any(tag.startswith("supply.") for tag in _supports(row))
        for row in linked["supply"]
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
    candidate: dict[str, Any], as_of: date, window_days: int, strict: bool = False
) -> dict[str, Any]:
    result = copy.deepcopy(candidate)
    gaps = _unique_strings(candidate.get("data_gaps", []) if isinstance(candidate.get("data_gaps"), list) else [])
    evidence = candidate.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
        gaps.append("evidence不是数组")
    valid_evidence = _window_evidence(evidence, as_of, window_days, gaps)
    for row in valid_evidence:
        _supports(row, gaps)
    linked, _, by_id = _linked_evidence(candidate, valid_evidence, gaps)
    if not strict:
        gaps.append("legacy coarse binding：未提供schema_version=2.1子项级supports")

    scorers = {
        "price": (_score_price, candidate.get("price")),
        "supply": (_score_supply, candidate.get("supply")),
        "a_share": (_score_a_share, candidate.get("a_share")),
    }
    dimension_results: dict[str, tuple[int, dict[str, Any]]] = {}
    for dimension, (scorer, value) in scorers.items():
        rows = linked[dimension]
        if rows:
            score, details = scorer(value, rows, strict, gaps)
            details["eligible"] = True
            details["linked_evidence_ids"] = [_text(row.get("evidence_id")) for row in rows]
        else:
            score = 0
            details = {"eligible": False, "linked_evidence_ids": []}
        dimension_results[dimension] = (score, details)
    price_score, price_details = dimension_results["price"]
    supply_score, supply_details = dimension_results["supply"]
    market_score, market_details = dimension_results["a_share"]

    fundamental_evidence = _fundamental_evidence(
        linked, price_details, supply_details, strict
    )
    density_score, density_details = _score_density(
        fundamental_evidence, as_of, window_days, gaps, strict
    )
    source_score, source_details = _score_sources(fundamental_evidence, gaps, strict)

    catalyst_valid, catalyst_details, catalyst_rows = _validate_forward_catalyst(
        candidate, linked["supply"], as_of, gaps
    )
    normalized_counterevidence, has_blocking, counter_rows = _normalize_counterevidence(
        candidate, by_id, gaps
    )

    scores = {
        "message_density": density_score,
        "source_quality": source_score,
        "price_validation": price_score,
        "supply_constraint": supply_score,
        "a_share_movement": market_score,
    }
    total = sum(scores.values())
    status = _initial_status(total)
    gate_path = "none"
    gate_reasons: list[str] = []
    if total >= 65:
        price_type = _text(price_details.get("evidence_type"), "none")
        price_gate_reasons: list[str] = []
        if source_score < 15:
            price_gate_reasons.append(f"来源质量{source_score}<15")
        if price_score < 13:
            price_gate_reasons.append(f"价格验证{price_score}<13")
        if price_type not in PRICE_GATE_TYPES:
            price_gate_reasons.append(f"价格阶段{price_type}不具备price_confirmed资格")

        if not price_gate_reasons:
            gate_path = "price_confirmed"
        else:
            supply_gate_reasons: list[str] = []
            if source_score < 15:
                supply_gate_reasons.append(f"来源质量{source_score}<15")
            if density_score < 12:
                supply_gate_reasons.append(f"消息密度{density_score}<12")
            if supply_score < 14:
                supply_gate_reasons.append(f"供给约束{supply_score}<14")
            if not catalyst_valid:
                supply_gate_reasons.append("缺少有效forward_catalyst")
            if has_blocking:
                supply_gate_reasons.append("存在blocking counterevidence")
            if not supply_gate_reasons:
                gate_path = "supply_forward"
                gate_reasons = _unique_strings(
                    [f"price_confirmed：{reason}" for reason in price_gate_reasons]
                )
            else:
                gate_reasons = _unique_strings(
                    [
                        *[f"price_confirmed：{reason}" for reason in price_gate_reasons],
                        *[f"supply_forward：{reason}" for reason in supply_gate_reasons],
                    ]
                )
        if gate_path == "none":
            status = "发酵中"

    result["scores"] = scores
    result["score_details"] = {
        "message_density": density_details,
        "source_quality": source_details,
        "price_validation": price_details,
        "supply_constraint": supply_details,
        "a_share_movement": market_details,
        "forward_catalyst": catalyst_details,
        "counterevidence": {"blocking": has_blocking},
    }
    result["total_score"] = total
    result["status"] = status
    result["gate_path"] = gate_path
    result["status_gate"] = {
        "applied": total >= 65,
        "passed": gate_path != "none",
        "requirements": {
            "price_confirmed": {
                "source_quality_min": 15,
                "price_validation_min": 13,
                "eligible_evidence_types": sorted(PRICE_GATE_TYPES),
            },
            "supply_forward": {
                "source_quality_min": 15,
                "message_density_min": 12,
                "supply_constraint_min": 14,
                "requires_forward_catalyst": True,
                "blocks_on_counterevidence": True,
            },
        },
        "reasons": gate_reasons,
    }
    display_evidence = _dedupe_evidence(
        [
            *linked["price"],
            *linked["supply"],
            *linked["a_share"],
            *catalyst_rows,
            *counter_rows,
        ]
    )
    result["evidence"] = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in display_evidence
    ]
    result["counterevidence"] = normalized_counterevidence
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
    schema_version = _text(payload.get("schema_version"))
    if schema_version and schema_version != "2.1":
        raise RadarValidationError("schema_version must be 2.1 when provided")
    strict = schema_version == "2.1"

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
        linked, _, _ = _linked_evidence(candidate, valid_evidence, [])
        if not _has_linked_material_signal(candidate, linked, strict):
            excluded.append({
                "material_chain": name,
                "reason": "没有监控窗口内且通过evidence_refs绑定的有效材料价格或供给信号",
            })
            continue
        scored.append(score_candidate(candidate, as_of, window_days, strict=strict))

    scored.sort(
        key=lambda item: (
            -int(item["total_score"]),
            -int(item["scores"]["price_validation"]),
            -int(item["scores"]["source_quality"]),
            -int(item["scores"]["message_density"]),
            _text(item.get("material_chain")),
        )
    )
    overflow = scored[max_items:]
    for item in overflow:
        excluded.append(
            {
                "material_chain": _text(item.get("material_chain")),
                "reason": f"评分后排名超过max_items={max_items}，未进入榜单",
            }
        )
    scored = scored[:max_items]
    return {
        "title": _text(payload.get("title"), "材料涨价雷达"),
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "max_items": max_items,
        "schema_version": schema_version or "2.0-legacy",
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


def _md_counterevidence(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "- 无"
    lines: list[str] = []
    for value in values:
        if isinstance(value, dict):
            effect = _text(value.get("effect"), "ordinary")
            dimension = _text(value.get("dimension"), "—")
            raw_refs = value.get("evidence_refs", [])
            refs = ", ".join(_unique_strings(raw_refs if isinstance(raw_refs, list) else [])) or "—"
            downgrade = "；降级原因：" + "；".join(value.get("downgrade_reasons", [])) if value.get("downgrade_reasons") else ""
            lines.append(
                f"- [{_md_escape(effect)}] {_md_escape(value.get('description'))}"
                f"（维度：{_md_escape(dimension)}；证据：{_md_escape(refs)}{_md_escape(downgrade)}）"
            )
        else:
            lines.append(f"- {_md_escape(value)}")
    return "\n".join(lines)


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# {_md_escape(result.get('title'))}",
        "",
        f"截止日期：{result['as_of']}　监控窗口：近{result['window_days']}个自然日　覆盖：{result['candidate_count']}条材料链",
        f"Schema：{_md_escape(result.get('schema_version'))}　方法版本：{_md_escape(result.get('method_version'))}",
        "",
        "评分：消息密度20 + 来源质量25 + 价格验证25 + 供给约束20 + A股异动10。",
        "",
        "## 涨价与供需雷达总览（按评分排序）",
        "",
        "| 材料链 | 分类 | 总分 | 状态 | 确认路径 | 价格阶段 | 核心催化 |",
        "|---|---|---:|---|---|---|---|",
    ]
    for item in result["candidates"]:
        price_details = (item.get("score_details") or {}).get("price_validation", {})
        lines.append(
            "| {material} | {category} | {score} | {status} | {path} | {stage} | {catalyst} |".format(
                material=_md_escape(item.get("material_chain")),
                category=_md_escape(item.get("category")),
                score=item["total_score"],
                status=item["status"],
                path=_md_escape(item.get("gate_path")),
                stage=_md_escape(price_details.get("evidence_type")),
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
        price_details = (item.get("score_details") or {}).get("price_validation", {})
        supply_details = (item.get("score_details") or {}).get("supply_constraint", {})
        market_details = (item.get("score_details") or {}).get("a_share_movement", {})
        catalyst_details = (item.get("score_details") or {}).get("forward_catalyst", {})
        gate = item.get("status_gate", {})
        catalyst = item.get("forward_catalyst") if isinstance(item.get("forward_catalyst"), dict) else {}
        catalyst_dates = "; ".join(
            f"{label}={_text(catalyst.get(field), '—')}"
            for label, field in (
                ("start", "start_date"),
                ("end", "end_date"),
                ("observed_start", "observed_start"),
            )
        )
        raw_catalyst_refs = catalyst.get("evidence_refs", [])
        catalyst_refs = ", ".join(
            _unique_strings(raw_catalyst_refs if isinstance(raw_catalyst_refs, list) else [])
        ) or "—"
        catalyst_reasons = "；".join(catalyst_details.get("reasons", [])) or "无"
        gate_reasons = "；".join(gate.get("reasons", [])) or (
            "已通过" if gate.get("passed") else "总分未触发高确定性 Gate"
        )
        lines.extend(
            [
                f"### {index}. {_md_escape(item.get('material_chain'))} — {item['total_score']}分 · {item['status']}",
                "",
                f"- 分项：{breakdown}",
                f"- 确认路径：{_md_escape(item.get('gate_path'))}",
                f"- Gate：{'已应用' if gate.get('applied') else '未应用'}；{'通过' if gate.get('passed') else '未通过'}；原因：{_md_escape(gate_reasons)}",
                f"- 价格阶段：{_md_escape(price_details.get('evidence_type'))}；change_basis：{_md_escape(price_details.get('change_basis'))}",
                f"- 核心催化：{_md_escape(item.get('core_catalyst'))}",
                f"- 价格子项：阶段 {price_details.get('evidence_points', 0)}；涨幅 {_md_escape(price_details.get('change_pct'))}%/{price_details.get('change_points', 0)}分；覆盖 {_md_escape(price_details.get('breadth_count'))}/{price_details.get('breadth_points', 0)}分；持续 {_md_escape(price_details.get('persistence_days'))}天/{price_details.get('persistence_points', 0)}分；说明：{_md_escape((item.get('price') or {}).get('description'))}",
                f"- 供给子项：库存 {_md_escape(supply_details.get('inventory'))}/{supply_details.get('inventory_points', 0)}分；开工/交期 {_md_escape(supply_details.get('utilization_leadtime'))}/{supply_details.get('utilization_leadtime_points', 0)}分；扰动 {_md_escape(supply_details.get('disruption'))}/{supply_details.get('disruption_points', 0)}分；需求/缺口 {_md_escape(supply_details.get('demand_gap'))}/{supply_details.get('demand_gap_points', 0)}分；说明：{_md_escape((item.get('supply') or {}).get('description'))}",
                f"- A股验证：超额 {_md_escape(market_details.get('excess_return_pct'))}%；广度 {_md_escape(market_details.get('positive_breadth_pct'))}%；成交 {_md_escape(market_details.get('turnover_ratio'))}倍；正超额 {_md_escape(market_details.get('positive_excess_days'))}天；直接受益股 {market_details.get('direct_beneficiary_count', 0)}只；说明：{_md_escape((item.get('a_share') or {}).get('description'))}",
                f"- Forward Catalyst：{'有效' if catalyst_details.get('valid') else '无效/缺失'}；status={_md_escape(catalyst.get('status'))}；type={_md_escape(catalyst.get('type'))}；timing_basis={_md_escape(catalyst.get('timing_basis'))}；{_md_escape(catalyst_dates)}；证据={_md_escape(catalyst_refs)}；原因={_md_escape(catalyst_reasons)}；说明：{_md_escape(catalyst.get('description'))}",
                "",
                "| 等级 | 日期 | 来源 | 支持事实 | Supports |",
                "|---|---|---|---|---|",
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
            supports = ", ".join(row.get("supports", [])) if isinstance(row.get("supports"), list) else ""
            lines.append(
                f"| {_md_escape(row.get('tier'))} | {_md_escape(row.get('date'))} | {source} | {_md_escape(row.get('claim'))} | {_md_escape(supports)} |"
            )
        if not evidence:
            lines.append("| — | — | — | 无可展示证据 | — |")

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
                _md_counterevidence(item.get("counterevidence")),
                "",
                "失效条件：",
                _md_list(item.get("invalidation_conditions")),
                "",
                "数据缺口：",
                _md_list(item.get("data_gaps")),
            ]
        )
        if gate.get("applied") and not gate.get("passed"):
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


def _validate_scored_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise RadarValidationError("scored input must be an object with a candidates array")
    if _text(payload.get("method_version")) != METHOD_VERSION:
        raise RadarValidationError(
            f"scored input method_version must be {METHOD_VERSION}"
        )
    required_scores = {
        "message_density",
        "source_quality",
        "price_validation",
        "supply_constraint",
        "a_share_movement",
    }
    for index, item in enumerate(payload["candidates"]):
        if not isinstance(item, dict):
            raise RadarValidationError(f"scored candidates[{index}] must be an object")
        scores = item.get("scores")
        if not isinstance(scores, dict) or not required_scores.issubset(scores):
            raise RadarValidationError(f"scored candidates[{index}] is missing five scores")
        if _number(item.get("total_score")) is None:
            raise RadarValidationError(f"scored candidates[{index}] is missing total_score")
        if _text(item.get("gate_path")) not in {
            "price_confirmed",
            "supply_forward",
            "none",
        }:
            raise RadarValidationError(f"scored candidates[{index}] has invalid gate_path")
        if not _text(item.get("status")):
            raise RadarValidationError(f"scored candidates[{index}] is missing status")
    return copy.deepcopy(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="normalized UTF-8 radar JSON")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path, help="write output instead of stdout")
    parser.add_argument(
        "--from-scored",
        action="store_true",
        help="render Markdown from scored JSON without rescoring",
    )
    args = parser.parse_args(argv)
    try:
        payload = _load_json(args.input)
        if args.from_scored:
            if args.format != "markdown":
                raise RadarValidationError("--from-scored requires --format markdown")
            result = _validate_scored_payload(payload)
        else:
            result = score_radar(payload)
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
