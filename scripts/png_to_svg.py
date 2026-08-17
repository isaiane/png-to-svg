#!/usr/bin/env python3
"""Convert raster artwork to editable, path-only SVG.

Requires Pillow and NumPy. The tracer quantizes color, creates binary masks,
extracts boundary loops on the pixel grid, and simplifies those loops.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

try:
    import numpy as np
    from PIL import Image, ImageFilter
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency. Install with: python3 -m pip install Pillow numpy"
    ) from exc


Point = tuple[float, float]


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    values = rgb.astype(np.float64) / 255.0
    linear = np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)
    xyz = linear @ np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    ).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    delta = 6 / 29
    f = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29)
    l = 116 * f[:, 1] - 16
    a = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    return np.column_stack((l, a, b))


def hex_color(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise argparse.ArgumentTypeError(f"invalid color: {value!r}")
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid color: {value!r}") from exc


def to_hex(rgb: Sequence[int]) -> str:
    return "#" + "".join(f"{int(c):02X}" for c in rgb)


def parse_palette(value: str) -> list[tuple[int, int, int]]:
    colors = [hex_color(item) for item in value.split(",") if item.strip()]
    if not colors:
        raise argparse.ArgumentTypeError("palette must contain at least one color")
    if len(colors) > 64:
        raise argparse.ArgumentTypeError("palette supports at most 64 colors")
    return colors


def resize_for_trace(image: Image.Image, max_dimension: int) -> tuple[Image.Image, float, float]:
    width, height = image.size
    if max_dimension <= 0 or max(width, height) <= max_dimension:
        return image, 1.0, 1.0
    factor = max_dimension / max(width, height)
    new_size = (max(1, round(width * factor)), max(1, round(height * factor)))
    resized = image.resize(new_size, Image.Resampling.LANCZOS)
    return resized, width / new_size[0], height / new_size[1]


def inferred_background(rgb: np.ndarray) -> tuple[int, int, int]:
    h, w, _ = rgb.shape
    patch = max(1, min(h, w, 24) // 6)
    samples = np.concatenate(
        (
            rgb[:patch, :patch].reshape(-1, 3),
            rgb[:patch, -patch:].reshape(-1, 3),
            rgb[-patch:, :patch].reshape(-1, 3),
            rgb[-patch:, -patch:].reshape(-1, 3),
        )
    )
    return tuple(int(x) for x in np.median(samples, axis=0))  # type: ignore[return-value]


def remove_background(
    rgb: np.ndarray,
    alpha: np.ndarray,
    background: str,
    tolerance: float,
) -> tuple[np.ndarray, tuple[int, int, int] | None]:
    if background == "none":
        return alpha, None
    target = inferred_background(rgb) if background == "auto" else hex_color(background)
    distance = np.linalg.norm(rgb.astype(np.int16) - np.array(target, dtype=np.int16), axis=2)
    result = alpha.copy()
    result[distance <= tolerance] = 0
    return result, target


def quantized_palette(pixels: np.ndarray, count: int) -> list[tuple[int, int, int]]:
    if len(pixels) == 0:
        return []
    unique = np.unique(pixels, axis=0)
    if len(unique) <= count:
        return [tuple(int(x) for x in row) for row in unique]

    # Quantize source pixels into coarse RGB bins first. The square-rooted bin
    # weights prevent broad textured areas from consuming most of the palette.
    bins = (pixels.astype(np.uint16) >> 3).astype(np.uint16)
    keys, inverse, counts = np.unique(
        bins[:, 0] * 1024 + bins[:, 1] * 32 + bins[:, 2],
        return_inverse=True,
        return_counts=True,
    )
    sums = np.zeros((len(keys), 3), dtype=np.float64)
    np.add.at(sums, inverse, pixels.astype(np.float64))
    candidates = sums / counts[:, None]
    lab = srgb_to_lab(candidates)
    weights = np.sqrt(counts.astype(np.float64))
    k = min(count, len(candidates))

    centers = [int(np.argmax(counts))]
    min_distances = np.sum((lab - lab[centers[0]]) ** 2, axis=1)
    while len(centers) < k:
        score = min_distances * weights
        score[centers] = -1
        next_center = int(np.argmax(score))
        if score[next_center] <= 0:
            break
        centers.append(next_center)
        distances = np.sum((lab - lab[next_center]) ** 2, axis=1)
        min_distances = np.minimum(min_distances, distances)

    center_lab = lab[centers].copy()
    center_rgb = candidates[centers].copy()
    for _ in range(14):
        distances = np.sum((lab[:, None, :] - center_lab[None, :, :]) ** 2, axis=2)
        labels = np.argmin(distances, axis=1)
        changed = False
        for index in range(len(center_lab)):
            member = labels == index
            if not np.any(member):
                continue
            total = np.sum(weights[member])
            new_lab = np.sum(lab[member] * weights[member, None], axis=0) / total
            new_rgb = np.sum(candidates[member] * weights[member, None], axis=0) / total
            changed = changed or np.linalg.norm(new_lab - center_lab[index]) > 0.01
            center_lab[index] = new_lab
            center_rgb[index] = new_rgb
        if not changed:
            break

    distances = np.sum((lab[:, None, :] - center_lab[None, :, :]) ** 2, axis=2)
    labels = np.argmin(distances, axis=1)
    order = sorted(
        range(len(center_rgb)),
        key=lambda index: int(np.sum(counts[labels == index])),
        reverse=True,
    )
    return [
        tuple(int(x) for x in np.clip(np.rint(center_rgb[index]), 0, 255))
        for index in order
    ]


def nearest_palette(rgb: np.ndarray, palette: Sequence[Sequence[int]]) -> np.ndarray:
    result = np.empty(rgb.shape[:2], dtype=np.int16)
    flat = rgb.reshape(-1, 3).astype(np.int32)
    pal = np.asarray(palette, dtype=np.int32)
    out = result.reshape(-1)
    chunk_size = 200_000
    for start in range(0, len(flat), chunk_size):
        chunk = flat[start : start + chunk_size]
        distances = np.sum((chunk[:, None, :] - pal[None, :, :]) ** 2, axis=2, dtype=np.int32)
        out[start : start + len(chunk)] = np.argmin(distances, axis=1)
    return result


def mask_edges(mask: np.ndarray) -> dict[tuple[int, int], list[tuple[int, int]]]:
    h, w = mask.shape
    edges: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if y == 0 or not mask[y - 1, x]:
            edges[(x, y)].append((x + 1, y))
        if x == w - 1 or not mask[y, x + 1]:
            edges[(x + 1, y)].append((x + 1, y + 1))
        if y == h - 1 or not mask[y + 1, x]:
            edges[(x + 1, y + 1)].append((x, y + 1))
        if x == 0 or not mask[y, x - 1]:
            edges[(x, y + 1)].append((x, y))
    return edges


def turn_priority(prev: tuple[int, int], current: tuple[int, int], nxt: tuple[int, int]) -> int:
    ax, ay = current[0] - prev[0], current[1] - prev[1]
    bx, by = nxt[0] - current[0], nxt[1] - current[1]
    cross = ax * by - ay * bx
    dot = ax * bx + ay * by
    if cross > 0:
        return 0
    if dot > 0:
        return 1
    if cross < 0:
        return 2
    return 3


def trace_loops(mask: np.ndarray) -> list[list[Point]]:
    edges = mask_edges(mask)
    loops: list[list[Point]] = []
    while edges:
        start = next(iter(edges))
        first = edges[start].pop()
        if not edges[start]:
            del edges[start]
        loop: list[Point] = [start, first]
        previous, current = start, first
        while current != start:
            choices = edges.get(current)
            if not choices:
                raise RuntimeError("open boundary encountered while tracing")
            index = min(range(len(choices)), key=lambda i: turn_priority(previous, current, choices[i]))
            nxt = choices.pop(index)
            if not choices:
                del edges[current]
            loop.append(nxt)
            previous, current = current, nxt
        loops.append(loop[:-1])
    return loops


def polygon_area(points: Sequence[Point]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )


def point_line_distance(point: Point, start: Point, end: Point) -> float:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    return abs(dy * px - dx * py + x2 * y1 - y2 * x1) / math.hypot(dx, dy)


def rdp(points: Sequence[Point], epsilon: float) -> list[Point]:
    if len(points) <= 2:
        return list(points)
    keep = {0, len(points) - 1}
    spans = [(0, len(points) - 1)]
    while spans:
        start, end = spans.pop()
        best_index = -1
        best_distance = epsilon
        for index in range(start + 1, end):
            distance = point_line_distance(points[index], points[start], points[end])
            if distance > best_distance:
                best_index, best_distance = index, distance
        if best_index >= 0:
            keep.add(best_index)
            spans.append((start, best_index))
            spans.append((best_index, end))
    return [points[index] for index in sorted(keep)]


def simplify_closed(points: list[Point], epsilon: float) -> list[Point]:
    if epsilon <= 0 or len(points) < 5:
        return points
    p0 = 0
    p1 = max(
        range(1, len(points)),
        key=lambda i: (points[i][0] - points[p0][0]) ** 2 + (points[i][1] - points[p0][1]) ** 2,
    )
    arc1 = rdp(points[p0 : p1 + 1], epsilon)
    arc2 = rdp(points[p1:] + points[:1], epsilon)
    simplified = arc1[:-1] + arc2[:-1]
    return simplified if len(simplified) >= 3 else points


def format_number(value: float, precision: int) -> str:
    text = f"{value:.{precision}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def path_data(loops: Iterable[Sequence[Point]], sx: float, sy: float, precision: int) -> str:
    pieces: list[str] = []
    for loop in loops:
        if len(loop) < 3:
            continue
        first, *rest = loop
        pieces.append(
            f"M{format_number(first[0] * sx, precision)} {format_number(first[1] * sy, precision)}"
        )
        pieces.extend(
            f"L{format_number(x * sx, precision)} {format_number(y * sy, precision)}"
            for x, y in rest
        )
        pieces.append("Z")
    return " ".join(pieces)


def build_svg(
    width: int,
    height: int,
    layers: Sequence[tuple[tuple[int, int, int], list[list[Point]]]],
    sx: float,
    sy: float,
    precision: int,
) -> tuple[str, int]:
    root = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
        },
    )
    path_count = 0
    for color, loops in layers:
        data = path_data(loops, sx, sy, precision)
        if not data:
            continue
        ET.SubElement(
            root,
            "path",
            {"fill": to_hex(color), "fill-rule": "evenodd", "d": data},
        )
        path_count += 1
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n", path_count


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("input", type=Path)
    result.add_argument("output", type=Path)
    result.add_argument("--mode", choices=("color", "bw"), default="color")
    result.add_argument("--colors", type=int, default=8)
    result.add_argument("--palette", type=parse_palette)
    result.add_argument("--threshold", type=int, default=128)
    result.add_argument("--invert", action="store_true")
    result.add_argument("--foreground", type=hex_color, default=(0, 0, 0))
    result.add_argument("--alpha-threshold", type=int, default=8)
    result.add_argument("--background", default="none", help="none, auto, or #RRGGBB")
    result.add_argument("--bg-tolerance", type=float, default=24.0)
    result.add_argument("--blur", type=float, default=0.0)
    result.add_argument("--simplify", type=float, default=1.0)
    result.add_argument("--min-area", type=float, default=4.0)
    result.add_argument("--max-dimension", type=int, default=0)
    result.add_argument("--precision", type=int, default=2)
    result.add_argument("--report", type=Path)
    return result


def validate_args(args: argparse.Namespace) -> None:
    if not 2 <= args.colors <= 64:
        raise SystemExit("--colors must be between 2 and 64")
    if not 0 <= args.threshold <= 255 or not 0 <= args.alpha_threshold <= 255:
        raise SystemExit("thresholds must be between 0 and 255")
    if args.bg_tolerance < 0 or args.blur < 0 or args.simplify < 0 or args.min_area < 0:
        raise SystemExit("tolerances, blur, simplify, and min-area must be non-negative")
    if not 0 <= args.precision <= 4:
        raise SystemExit("--precision must be between 0 and 4")
    if args.background not in ("none", "auto"):
        hex_color(args.background)


def main() -> int:
    args = parser().parse_args()
    validate_args(args)
    if not args.input.is_file():
        raise SystemExit(f"input does not exist: {args.input}")
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("input and output paths must differ")

    source = Image.open(args.input).convert("RGBA")
    original_width, original_height = source.size
    image, sx, sy = resize_for_trace(source, args.max_dimension)
    if args.blur > 0:
        alpha_channel = image.getchannel("A")
        image = image.filter(ImageFilter.GaussianBlur(args.blur))
        image.putalpha(alpha_channel)
    array = np.asarray(image)
    rgb = array[:, :, :3]
    alpha = array[:, :, 3].copy()
    alpha, removed_background = remove_background(rgb, alpha, args.background, args.bg_tolerance)
    opaque = alpha >= args.alpha_threshold
    if not np.any(opaque):
        raise SystemExit("no opaque pixels remain after transparency/background filtering")

    if args.mode == "bw":
        luminance = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
        selected = luminance >= args.threshold if args.invert else luminance < args.threshold
        masks_and_colors = [(selected & opaque, args.foreground)]
    else:
        palette = args.palette or quantized_palette(rgb[opaque], args.colors)
        labels = nearest_palette(rgb, palette)
        masks_and_colors = [((labels == index) & opaque, color) for index, color in enumerate(palette)]

    layers: list[tuple[tuple[int, int, int], list[list[Point]]]] = []
    region_count = 0
    node_count = 0
    scale_area = sx * sy
    for mask, color in masks_and_colors:
        if not np.any(mask):
            continue
        loops = []
        for loop in trace_loops(mask):
            area = abs(polygon_area(loop)) * scale_area
            if area < args.min_area:
                continue
            simplified = simplify_closed(loop, args.simplify / max(sx, sy))
            loops.append(simplified)
            node_count += len(simplified)
        if loops:
            layers.append((color, loops))
            region_count += len(loops)

    svg, path_count = build_svg(
        original_width, original_height, layers, sx, sy, args.precision
    )
    if path_count == 0:
        raise SystemExit("trace produced no paths; lower --min-area or change threshold settings")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    ET.parse(args.output)

    summary = {
        "input": str(args.input),
        "output": str(args.output),
        "width": original_width,
        "height": original_height,
        "mode": args.mode,
        "requested_colors": 1 if args.mode == "bw" else len(args.palette or []) or args.colors,
        "actual_colors": path_count,
        "palette": [to_hex(color) for color, _ in layers],
        "path_count": path_count,
        "region_count": region_count,
        "node_count": node_count,
        "background_removed": to_hex(removed_background) if removed_background else None,
        "embedded_raster": False,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
