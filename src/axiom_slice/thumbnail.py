from __future__ import annotations

import base64
import math
import re
import struct
import zlib
from collections.abc import Iterable
from pathlib import Path

THUMBNAIL_RE = re.compile(
    r"; thumbnail begin (?P<w>\d+)x(?P<h>\d+) \d+\s*\n(?P<data>(?:; [A-Za-z0-9+/=]+\s*\n)+); thumbnail end",
    re.MULTILINE,
)


def extract_gcode_thumbnails(gcode: Path, output_dir: Path) -> dict[str, Path]:
    text = gcode.read_text(encoding="utf-8", errors="replace")
    artifacts: dict[str, Path] = {}
    for match in THUMBNAIL_RE.finditer(text):
        encoded = "".join(line.removeprefix("; ").strip() for line in match["data"].splitlines())
        try:
            payload = base64.b64decode(encoded, validate=True)
        except ValueError:
            continue
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            continue
        name = f"thumbnail-{match['w']}x{match['h']}.png"
        path = output_dir / name
        path.write_bytes(payload)
        artifacts[name] = path
    return artifacts


def _triangles(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    raw = path.read_bytes()
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    if len(raw) >= 84:
        count = struct.unpack_from("<I", raw, 80)[0]
        if 84 + count * 50 == len(raw):
            for offset in range(84, len(raw), 50):
                values = struct.unpack_from("<12f", raw, offset)
                triangles.append((values[3:6], values[6:9], values[9:12]))
            return triangles
    vertices: list[tuple[float, float, float]] = []
    for line in raw.decode("utf-8", errors="ignore").splitlines():
        parts = line.strip().split()
        if len(parts) == 4 and parts[0].lower() == "vertex":
            try:
                vertices.append(tuple(float(value) for value in parts[1:4]))
            except ValueError:
                continue
    for index in range(0, len(vertices) - 2, 3):
        triangles.append((vertices[index], vertices[index + 1], vertices[index + 2]))
    return triangles


def _png(width: int, height: int, pixels: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    rows = b"".join(b"\0" + pixels[row * width * 4 : (row + 1) * width * 4] for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def _render(
    triangles: Iterable[tuple[tuple[float, float, float], ...]], width: int, height: int, color: tuple[int, int, int]
) -> bytes:
    cos_x, sin_x = math.cos(math.radians(-60)), math.sin(math.radians(-60))
    cos_z, sin_z = math.cos(math.radians(45)), math.sin(math.radians(45))

    def rotate(vertex: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = vertex
        x1, y1 = x * cos_z - y * sin_z, x * sin_z + y * cos_z
        return x1, y1 * cos_x - z * sin_x, y1 * sin_x + z * cos_x

    visible = []
    xs: list[float] = []
    ys: list[float] = []
    for triangle in triangles:
        points = tuple(rotate(vertex) for vertex in triangle)
        a, b, c = points
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        normal = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        length = math.sqrt(sum(value * value for value in normal))
        if not length or normal[2] <= 0:
            continue
        unit = tuple(value / length for value in normal)
        visible.append((unit, points))
        xs.extend(point[0] for point in points)
        ys.extend(point[1] for point in points)
    if not visible:
        raise ValueError("model has no renderable triangles")
    model_width = max(xs) - min(xs) or 1
    model_height = max(ys) - min(ys) or 1
    scale = min(width * 0.84 / model_width, height * 0.84 / model_height)
    offset_x = width / 2 - scale * (min(xs) + max(xs)) / 2
    offset_y = height / 2 - scale * (min(ys) + max(ys)) / 2
    z_buffer = [float("-inf")] * (width * height)
    pixels = bytearray(width * height * 4)
    light_length = math.sqrt(6)
    light = (1 / light_length, 1 / light_length, 2 / light_length)
    for normal, points in visible:
        projected = [(x * scale + offset_x, height - 1 - (y * scale + offset_y), z) for x, y, z in points]
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = projected
        area = (x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)
        if not area:
            continue
        intensity = max(0.2, min(1.0, sum(normal[i] * light[i] for i in range(3))))
        shaded = tuple(int(channel * intensity) for channel in color)
        for px in range(max(0, math.floor(min(x1, x2, x3))), min(width - 1, math.ceil(max(x1, x2, x3))) + 1):
            for py in range(max(0, math.floor(min(y1, y2, y3))), min(height - 1, math.ceil(max(y1, y2, y3))) + 1):
                cx, cy = px + 0.5, py + 0.5
                w1 = ((cx - x2) * (y3 - y2) - (cy - y2) * (x3 - x2)) / area
                w2 = ((cx - x3) * (y1 - y3) - (cy - y3) * (x1 - x3)) / area
                w3 = 1 - w1 - w2
                if min(w1, w2, w3) < -1e-5:
                    continue
                depth = w1 * z1 + w2 * z2 + w3 * z3
                index = py * width + px
                if depth > z_buffer[index]:
                    z_buffer[index] = depth
                    pixels[index * 4 : index * 4 + 4] = bytes((*shaded, 255))
    return _png(width, height, pixels)


def render_stl_thumbnails(stl: Path, output_dir: Path, color: str = "#8080c0") -> dict[str, Path]:
    rgb = tuple(int(color.lstrip("#")[index : index + 2], 16) for index in (0, 2, 4))
    triangles = _triangles(stl)
    if not triangles:
        raise ValueError("STL contains no triangles")
    result: dict[str, Path] = {}
    for width, height in ((32, 32), (96, 96), (300, 300)):
        name = f"thumbnail-{width}x{height}.png"
        path = output_dir / name
        path.write_bytes(_render(triangles, width, height, rgb))
        result[name] = path
    return result
