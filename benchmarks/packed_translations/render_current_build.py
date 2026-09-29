#!/usr/bin/env python3
"""Render one pinned, compile-only benchmark run as SVG and PNG."""

import argparse
import json
import math
from pathlib import Path
import statistics
import subprocess

from render_charts import GRID, GUARD, MAIN, MUTED, PR, TEXT, Svg


LABELS = {"small": "8,000", "medium": "16,000", "large": "32,000"}


def summarize(rows, tier, variant, field):
    builds = [row for row in rows if row["phase"] == "build"
              and row["tier"] == tier and row["variant"] == variant]
    values = [row[field] for row in builds
              if row["status"] == "success" and row[field] is not None]
    return {
        "median": statistics.median(values) if values else None,
        "low": min(values) if values else None,
        "high": max(values) if values else None,
        "count": len(values),
        "stop": next((row["status"] for row in builds if row["status"] != "success"), None),
    }


def panel(svg, x, y, title, subtitle, tiers, rows, field, minimum, maximum, ticks, unit):
    width, height = 730, 485
    svg.rect(x, y, width, height, "#ffffff", 20, GRID)
    svg.text(x + 34, y + 51, title, 27, TEXT, 700)
    svg.text(x + 34, y + 81, subtitle, 17, MUTED)
    axis_x, axis_width = x + 145, 445
    plot_top, plot_bottom = y + 135, y + height - 70

    def position(value):
        return axis_x + axis_width * (math.log(value) - math.log(minimum)) / (
            math.log(maximum) - math.log(minimum)
        )

    for tick in ticks:
        px = position(tick)
        svg.line(px, plot_top - 17, px, plot_bottom, GRID)
        svg.text(px, plot_bottom + 27, f"{tick:g}", 14, MUTED, anchor="middle")

    row_height = (plot_bottom - plot_top) / len(tiers)
    for index, tier in enumerate(tiers):
        center = plot_top + row_height * (index + .5)
        svg.text(x + 34, center + 17, LABELS[tier], 20, TEXT, 700)
        for variant, offset, color in (("main", -12, MAIN), ("candidate", 16, PR)):
            item = summarize(rows, tier, variant, field)
            bar_y = center + offset - 10
            if item["median"] is None:
                label = f"Stopped: {item['stop'].replace('_', ' ')}" if item["stop"] else "No result"
                svg.text(axis_x + 8, bar_y + 15, label, 15, GUARD)
                continue
            median = item["median"] / unit
            svg.rect(axis_x, bar_y, max(2, position(median) - axis_x), 17, color, 5)
            if item["low"] != item["high"]:
                low, high = position(item["low"] / unit), position(item["high"] / unit)
                svg.line(low, bar_y + 8, high, bar_y + 8, TEXT, 1.5)
                svg.line(low, bar_y + 3, low, bar_y + 14, TEXT, 1.5)
                svg.line(high, bar_y + 3, high, bar_y + 14, TEXT, 1.5)
            value = f"{median:.2f}" if median < 10 else f"{median:.1f}"
            svg.text(x + width - 27, bar_y + 15, value, 16, color, 700, "end")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--comparison-note", default="Compare exact commit ancestry before attributing differences.")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
    metadata = json.loads(args.metadata.read_text())
    tiers = [tier for tier in LABELS if any(row["tier"] == tier for row in rows)]
    if not tiers:
        raise SystemExit("No measured compile tiers")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    svg = Svg(1600, 825)
    svg.text(62, 68, "Release consumer build · latest main vs PR #148", 40, TEXT, 700)
    svg.text(62, 105, f"Linux x86_64 · {metadata['rustc'].splitlines()[0]} · warm dependencies · one Cargo job", 20, MUTED)
    svg.text(62, 135, f"Main {metadata['main_sha'][:8]} · PR {metadata['candidate_sha'][:8]}", 18, MUTED)
    svg.rect(62, 167, 26, 16, MAIN, 4)
    svg.text(99, 181, "Upstream main", 18, MUTED)
    svg.rect(307, 167, 26, 16, PR, 4)
    svg.text(344, 181, "PR #148", 18, MUTED)
    svg.text(565, 181, "Bars show medians; thin whiskers show observed range", 17, MUTED)
    panel(svg, 55, 215, "Build time", "Seconds from /usr/bin/time · log scale", tiers,
          rows, "command_seconds", .3, 300, (.5, 1, 2, 5, 10, 20, 50, 100, 300), 1)
    panel(svg, 815, 215, "Peak resident memory", "GB for completed builds · log scale", tiers,
          rows, "peak_time_rss_bytes", .1, 20, (.1, .2, .5, 1, 2, 5, 10, 20), 1e9)
    counts = [summarize(rows, tier, "main", "command_seconds")["count"] for tier in tiers]
    svg.text(62, 744, f"Synthetic YAML: 1,000 keys per locale. Completed upstream builds per tier: {', '.join(map(str, counts))}.", 17, MUTED)
    svg.text(62, 775, args.comparison_note, 17, GUARD)
    out = args.out_dir / "build.svg"
    svg.write(out)
    subprocess.run(["rsvg-convert", "-o", str(args.out_dir / "build.png"), str(out)], check=True)


if __name__ == "__main__":
    main()
