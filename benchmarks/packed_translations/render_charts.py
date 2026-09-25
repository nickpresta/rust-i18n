#!/usr/bin/env python3
"""Render the benchmark JSONL as SVG and PNG charts."""

import argparse
from collections import defaultdict
from html import escape
import json
import math
from pathlib import Path
import statistics
import subprocess


MAIN = "#64748b"
PR = "#089a98"
GUARD = "#c76b20"
TEXT = "#172b3a"
MUTED = "#60717d"
GRID = "#dce6ea"
BG = "#f5f8fa"
TIERS = ("small", "medium", "large", "xlarge")
LABELS = {"small": "8,000", "medium": "16,000", "large": "32,000", "xlarge": "863,870"}


class Svg:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.items = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            f'<rect width="{width}" height="{height}" fill="{BG}"/>',
            '<style>text {font-family: Arial, Helvetica, sans-serif}</style>',
        ]

    def rect(self, x, y, width, height, fill, radius=0, stroke=None, sw=1):
        extra = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        self.items.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="{radius}" fill="{fill}"{extra}/>')

    def line(self, x1, y1, x2, y2, color, width=1, dash=None):
        extra = f' stroke-dasharray="{dash}"' if dash else ""
        self.items.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}"{extra}/>')

    def text(self, x, y, value, size=22, color=TEXT, weight=400, anchor="start"):
        self.items.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>')

    def write(self, path):
        path.write_text("\n".join(self.items + ["</svg>"]))


def summarize(rows, phase, field):
    grouped = defaultdict(list)
    stopped = defaultdict(list)
    for row in rows:
        if row["phase"] != phase:
            continue
        key = (row["tier"], row["variant"])
        if row["status"] != "success":
            stopped[key].append(row)
            continue
        value = row
        for component in field.split("."):
            value = value[component]
        if value is not None:
            grouped[key].append(value)
    output = {}
    for tier in TIERS:
        for variant in ("main", "candidate"):
            key = (tier, variant)
            values = grouped[key]
            output[key] = {
                "median": statistics.median(values) if values else None,
                "low": min(values) if values else None,
                "high": max(values) if values else None,
                "n": len(values),
                "stopped": stopped[key][0] if stopped[key] else None,
            }
    return output


def plot_panel(svg, x, y, w, h, title, subhead, summary, formatter, minimum, maximum, ticks, logarithmic=False, show_guard=False):
    svg.rect(x, y, w, h, "#ffffff", 20, GRID, 1)
    svg.text(x + 34, y + 52, title, 27, TEXT, 700)
    svg.text(x + 34, y + 83, subhead, 17, MUTED)
    px = x + 168
    pw = w - 290
    top = y + 156
    bottom = y + h - 78

    def position(value):
        if logarithmic:
            return px + pw * (math.log(value) - math.log(minimum)) / (math.log(maximum) - math.log(minimum))
        return px + pw * (value - minimum) / (maximum - minimum)

    for tick in ticks:
        at = position(tick)
        svg.line(at, top - 26, at, bottom, GRID, 1)
        svg.text(at, bottom + 29, formatter(tick), 14, MUTED, anchor="middle")

    row_step = (bottom - top) / len(TIERS)
    for index, tier in enumerate(TIERS):
        cy = top + row_step * (index + .5)
        svg.text(x + 34, cy + 17, LABELS[tier], 20, TEXT, 700)
        for variant, offset, color in (("main", -12, MAIN), ("candidate", 17, PR)):
            item = summary[(tier, variant)]
            bar_y = cy + offset - 10
            if item["median"] is not None:
                median = position(item["median"])
                svg.rect(px, bar_y, max(2, median - px), 17, color, 5)
                if item["low"] != item["high"]:
                    lo, hi = position(item["low"]), position(item["high"])
                    svg.line(lo, bar_y + 8.5, hi, bar_y + 8.5, TEXT, 1.5)
                    svg.line(lo, bar_y + 3, lo, bar_y + 14, TEXT, 1.5)
                    svg.line(hi, bar_y + 3, hi, bar_y + 14, TEXT, 1.5)
                svg.text(x + w - 28, bar_y + 15, formatter(item["median"]), 16, color, 700, "end")
            elif show_guard and item["stopped"]:
                reason = item["stopped"]["status"].replace("_", " ").upper()
                svg.text(px + 8, bar_y + 14, f"Stopped: {reason}; no completed build", 15, GUARD, 700)
            else:
                svg.text(px + 8, bar_y + 14, "No completed build" if tier == "xlarge" else "—", 15, MUTED)


def legend(svg, y):
    svg.rect(62, y - 16, 26, 16, MAIN, 4)
    svg.text(98, y - 2, "Upstream main", 18, MUTED)
    svg.rect(298, y - 16, 26, 16, PR, 4)
    svg.text(334, y - 2, "PR #148", 18, MUTED)
    svg.text(564, y - 2, "Thin whiskers show observed range", 17, MUTED)


def host_label(metadata):
    return metadata["platform"].split("-", 1)[0] + " " + metadata["machine"]


def render_build(rows, metadata, out):
    elapsed = summarize(rows, "build", "command_seconds")
    memory = summarize(rows, "build", "peak_time_rss_bytes")
    for value in memory.values():
        for field in ("median", "low", "high"):
            if value[field] is not None:
                value[field] /= 1e9
    svg = Svg(1600, 930)
    svg.text(62, 70, "Consumer release build cost", 43, TEXT, 700)
    host = metadata["rustc"].splitlines()[0] + " · " + host_label(metadata)
    svg.text(62, 108, f"{host} · warm dependencies · one Cargo job", 21, MUTED)
    legend(svg, 152)
    plot_panel(svg, 55, 190, 730, 640, "Build time", "Seconds from /usr/bin/time · logarithmic scale", elapsed,
               lambda x: f"{x:.1f}s" if x < 10 else f"{x:.0f}s", .3, 300,
               (.5, 1, 2, 5, 10, 20, 50, 100, 300), True, True)
    plot_panel(svg, 815, 190, 730, 640, "Peak resident memory", "GB · completed builds · logarithmic scale", memory,
               lambda x: f"{x:.2f}" if x < 1 else f"{x:.1f}", .1, 20,
               (.1, .2, .5, 1, 2, 5, 10, 20), True)
    svg.text(62, 867, "Translations per corpus shown at left. Bars are medians; repeats were 3 at 8k/16k, 2 at 32k, 1 at 863,870.", 18, MUTED)
    if elapsed[("xlarge", "main")]["median"] is None:
        svg.text(62, 896, "The upstream 863,870 build was stopped by a host resource guard; its completion time and peak memory are unknown.", 18, GUARD)
    svg.write(out)


def render_runtime(rows, metadata, out):
    first = summarize(rows, "runtime", "measurement.first_ns")
    lookup = summarize(rows, "runtime", "measurement.lookup_ns")
    binary = summarize(rows, "runtime", "binary_bytes")
    runtime_rss = summarize(rows, "runtime", "peak_time_rss_bytes")
    for value in first.values():
        for field in ("median", "low", "high"):
            if value[field] is not None:
                value[field] /= 1e6
    for value in binary.values():
        for field in ("median", "low", "high"):
            if value[field] is not None:
                value[field] /= 1e6
    for value in runtime_rss.values():
        for field in ("median", "low", "high"):
            if value[field] is not None:
                value[field] /= 1e6
    svg = Svg(1600, 1300)
    svg.text(62, 70, "Runtime and binary size", 43, TEXT, 700)
    host = metadata["rustc"].splitlines()[0] + " · " + host_label(metadata)
    svg.text(62, 108, f"{host} · fresh processes · one million lookups per process", 21, MUTED)
    legend(svg, 152)
    plot_panel(svg, 55, 190, 730, 480, "First translation", "Milliseconds · includes lazy table initialization", first,
               lambda x: f"{x:.1f}" if x < 10 else f"{x:.0f}", .3, 200,
               (.5, 1, 2, 5, 10, 20, 50, 100, 200), True)
    plot_panel(svg, 815, 190, 730, 480, "Steady lookup", "Nanoseconds per lookup · median", lookup,
               lambda x: f"{x:.0f}", 0, 80, (0, 20, 40, 60, 80))
    plot_panel(svg, 55, 700, 730, 480, "Consumer executable size", "MB · release binary", binary,
               lambda x: f"{x:.1f}" if x < 10 else f"{x:.0f}", 1, 200,
               (1, 2, 5, 10, 20, 50, 100, 200), True)
    plot_panel(svg, 815, 700, 730, 480, "Runtime resident memory", "MB · peak fresh-process RSS", runtime_rss,
               lambda x: f"{x:.1f}" if x < 10 else f"{x:.0f}", 1, 300,
               (1, 2, 5, 10, 20, 50, 100, 300), True)
    svg.text(62, 1226, "Runtime points pool five fresh-process trials per completed build. Whiskers show the full observed range.", 18, MUTED)
    if first[("xlarge", "main")]["median"] is None:
        svg.text(62, 1255, "No upstream runtime result exists at 863,870 because its consumer build did not complete.", 18, GUARD)
    svg.write(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line.strip()]
    metadata = json.loads(args.metadata.read_text())
    for name, draw in (("build", render_build), ("runtime", render_runtime)):
        svg = args.out_dir / f"{name}.svg"
        draw(rows, metadata, svg)
        subprocess.run(["rsvg-convert", "-o", str(args.out_dir / f"{name}.png"), str(svg)], check=True)


if __name__ == "__main__":
    main()
