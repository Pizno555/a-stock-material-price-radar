#!/usr/bin/env python3
"""Render scored material-radar JSON as a self-contained local HTML report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class RenderError(ValueError):
    pass


def load_scored(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RenderError(f"input file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RenderError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise RenderError("input must be scored radar JSON with a candidates array")
    for index, item in enumerate(data["candidates"]):
        if not isinstance(item, dict) or "scores" not in item or "total_score" not in item:
            raise RenderError(f"candidates[{index}] is not scored; run score_radar.py first")
    return data


def render(data: dict[str, Any], template_path: Path, css_path: Path) -> str:
    template = template_path.read_text(encoding="utf-8")
    css = css_path.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    if "{{STYLES}}" not in template or "{{DATA_JSON}}" not in template:
        raise RenderError("HTML template is missing required placeholders")
    return template.replace("{{STYLES}}", css).replace("{{DATA_JSON}}", payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="scored JSON from score_radar.py")
    parser.add_argument("--output", type=Path, required=True, help="local HTML output path")
    args = parser.parse_args(argv)
    skill_dir = Path(__file__).resolve().parents[1]
    try:
        data = load_scored(args.input)
        html = render(
            data,
            skill_dir / "assets" / "report-template.html",
            skill_dir / "assets" / "report.css",
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(html, encoding="utf-8")
        print(args.output.resolve())
        return 0
    except (OSError, RenderError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
