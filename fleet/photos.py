"""Deterministic synthetic photography for the demonstration seed.

The demo must not present invented photographs as real evidence. It therefore
generates its own images at run time and stamps every one of them ``DEMO PHOTO``
in the image itself, so a screenshot cannot quietly become a claim about a real
yard. The generator is deterministic: the same photo record always produces the
same bytes, and the report prints the SHA-256 of each image it embeds.

PNG is written by hand with zlib (no Pillow, no ImageMagick) so the demo runs from
a clean checkout with the standard library only. ``decode_png`` exists because a
PDF cannot embed PNG bytes directly: the writer needs the raw RGB pixels.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

from .model import Asset, Inspection, Photo, Registry, format_ts

WIDTH = 360
HEIGHT = 270
RGB = tuple[int, int, int]

# A 5x7 bitmap font, enough for the stamps printed into every synthetic photo.
_FONT: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00100", "00100"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}

INK: RGB = (14, 26, 43)
WALL: RGB = (46, 57, 70)
FLOOR: RGB = (112, 116, 116)
FLOOR_LINE: RGB = (92, 96, 98)
SHADOW: RGB = (28, 33, 40)
STEEL: RGB = (156, 163, 166)
STEEL_DARK: RGB = (118, 125, 130)
PLATE: RGB = (196, 201, 202)
AMBER: RGB = (196, 148, 44)
TEAL: RGB = (72, 174, 172)
RUST: RGB = (176, 72, 56)
WARN: RGB = (214, 168, 62)
WHITE: RGB = (238, 240, 240)
GREY: RGB = (120, 124, 128)

FOCUS_BOXES: dict[str, tuple[int, int, int, int]] = {
    "rear": (214, 118, 118, 78),
    "deck": (104, 148, 168, 58),
    "brakes": (232, 168, 100, 72),
    "cab": (44, 116, 116, 96),
    "front": (36, 126, 116, 96),
    "hydraulics": (126, 108, 154, 92),
}

ASSET_COLOURS: dict[str, RGB] = {
    "trailer": STEEL,
    "power_unit": PLATE,
    "equipment": AMBER,
}


class Image:
    def __init__(self, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.width = width
        self.height = height
        self.pixels = bytearray(width * height * 3)

    def set_pixel(self, x: int, y: int, colour: RGB) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset : offset + 3] = bytes(colour)

    def rect(self, x: int, y: int, w: int, h: int, colour: RGB) -> None:
        for yy in range(max(0, y), min(self.height, y + h)):
            row = (yy * self.width) * 3
            for xx in range(max(0, x), min(self.width, x + w)):
                offset = row + xx * 3
                self.pixels[offset : offset + 3] = bytes(colour)

    def frame(self, x: int, y: int, w: int, h: int, colour: RGB) -> None:
        self.rect(x, y, w, 2, colour)
        self.rect(x, y + h - 2, w, 2, colour)
        self.rect(x, y, 2, h, colour)
        self.rect(x + w - 2, y, 2, h, colour)

    def line(self, x0: int, y0: int, x1: int, y1: int, colour: RGB) -> None:
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.set_pixel(x0, y0, colour)
            if x0 == x1 and y0 == y1:
                break
            err2 = 2 * err
            if err2 >= dy:
                err += dy
                x0 += sx
            if err2 <= dx:
                err += dx
                y0 += sy

    def ellipse(self, cx: int, cy: int, rx: int, ry: int, colour: RGB, fill: bool = False) -> None:
        if fill:
            for y in range(cy - ry, cy + ry + 1):
                for x in range(cx - rx, cx + rx + 1):
                    nx = (x - cx) / max(1, rx)
                    ny = (y - cy) / max(1, ry)
                    if nx * nx + ny * ny <= 1.0:
                        self.set_pixel(x, y, colour)
            return
        steps = max(24, (rx + ry) * 4)
        for step in range(steps):
            angle = 6.283185307179586 * step / steps
            import math

            x = cx + int(round(rx * math.cos(angle)))
            y = cy + int(round(ry * math.sin(angle)))
            self.set_pixel(x, y, colour)
            self.set_pixel(x + 1, y, colour)

    def dashed_ring(self, cx: int, cy: int, radius: int, colour: RGB) -> None:
        import math

        for step in range(0, 360, 6):
            if (step // 6) % 2:
                continue
            angle = math.radians(step)
            x = cx + int(round(radius * math.cos(angle)))
            y = cy + int(round(radius * 0.72 * math.sin(angle)))
            self.set_pixel(x, y, colour)
            self.set_pixel(x, y + 1, colour)

    def text(self, x: int, y: int, text: str, colour: RGB, scale: int = 1) -> None:
        cursor = x
        for char in text.upper():
            glyph = _FONT.get(char, _FONT[" "])
            for row, bits in enumerate(glyph):
                for col, bit in enumerate(bits):
                    if bit != "1":
                        continue
                    self.rect(
                        cursor + col * scale,
                        y + row * scale,
                        scale,
                        scale,
                        colour,
                    )
            cursor += 6 * scale

    def draw(self, x: int, y: int, w: int, h: int, colour: RGB) -> None:
        self.rect(x, y, w, h, colour)


def _draw_scene(image: Image, asset_class: str) -> None:
    image.rect(0, 0, WIDTH, 150, WALL)
    image.rect(0, 150, WIDTH, HEIGHT - 150, FLOOR)
    for y in range(170, HEIGHT, 26):
        image.rect(0, y, WIDTH, 1, FLOOR_LINE)
    for x in range(24, WIDTH, 72):
        image.rect(x, 150, 2, HEIGHT - 150, FLOOR_LINE)
    # A wall line and a bay number keep the image from being a flat colour field.
    image.rect(0, 148, WIDTH, 3, SHADOW)
    image.text(12, 10, "BAY 04", GREY, 1)
    image.rect(0, HEIGHT, WIDTH, 0, SHADOW)

    if asset_class == "trailer":
        image.rect(34, 150, 296, 10, SHADOW)
        image.rect(40, 108, 286, 46, STEEL)
        image.rect(40, 108, 286, 4, STEEL_DARK)
        for x in range(54, 316, 34):
            image.rect(x, 110, 2, 42, STEEL_DARK)
        image.rect(40, 152, 286, 8, STEEL_DARK)
        for wx in (96, 248):
            image.rect(wx - 6, 152, 12, 44, SHADOW)
            image.ellipse(wx, 186, 22, 22, INK, fill=True)
            image.ellipse(wx, 186, 12, 12, STEEL_DARK, fill=True)
        image.rect(22, 128, 26, 8, STEEL_DARK)
    elif asset_class == "power_unit":
        image.rect(54, 158, 262, 10, SHADOW)
        image.rect(56, 112, 90, 48, PLATE)
        image.rect(56, 112, 90, 6, STEEL_DARK)
        image.rect(70, 120, 26, 20, (92, 112, 128))
        image.rect(146, 132, 162, 30, PLATE)
        image.rect(146, 132, 162, 4, STEEL_DARK)
        for x in range(156, 300, 30):
            image.rect(x, 136, 20, 22, PLATE)
        for wx in (96, 268):
            image.rect(wx - 6, 158, 12, 42, SHADOW)
            image.ellipse(wx, 190, 22, 22, INK, fill=True)
            image.ellipse(wx, 190, 12, 12, STEEL_DARK, fill=True)
    else:
        image.rect(60, 158, 224, 10, SHADOW)
        image.rect(52, 158, 84, 34, STEEL_DARK)
        image.rect(56, 162, 76, 26, INK)
        for x in range(60, 128, 14):
            image.rect(x, 160, 10, 30, (60, 64, 68))
        image.rect(140, 136, 92, 48, AMBER)
        image.rect(140, 136, 92, 4, STEEL_DARK)
        image.rect(150, 146, 26, 24, (92, 112, 128))
        image.line(232, 150, 292, 108, AMBER)
        image.line(238, 156, 298, 114, AMBER)
        image.line(292, 108, 274, 82, AMBER)
        image.line(298, 114, 280, 88, AMBER)
        image.line(274, 82, 302, 74, STEEL_DARK)
        image.line(280, 88, 308, 80, STEEL_DARK)
        image.rect(160, 96, 6, 40, SHADOW)
        image.ellipse(300, 190, 16, 16, INK, fill=True)


def _focus(image: Image, view: str) -> tuple[int, int, int, int]:
    box = FOCUS_BOXES.get(view, FOCUS_BOXES["rear"])
    image.frame(box[0], box[1], box[2], box[3], TEAL)
    return box


def _marker_position(asset_id: str, item_id: str, view: str, box: tuple[int, int, int, int]) -> tuple[int, int]:
    seed = hashlib.sha256(f"{asset_id}|{item_id}|{view}".encode("utf-8")).digest()
    x = box[0] + 14 + seed[0] % max(1, box[2] - 28)
    y = box[1] + 14 + seed[1] % max(1, box[3] - 28)
    return x, y


def _draw_defect(image: Image, defect: str, x: int, y: int) -> None:
    if defect == "none":
        return
    colour = WARN if defect in ("lamp_out", "inop", "worn") else RUST
    image.dashed_ring(x, y, 20, colour)
    if defect in ("crack",):
        image.line(x - 14, y - 6, x - 4, y - 1, colour)
        image.line(x - 4, y - 1, x + 1, y + 7, colour)
        image.line(x + 1, y + 7, x + 11, y + 3, colour)
        image.line(x + 11, y + 3, x + 16, y + 10, colour)
        image.line(x - 14, y - 6, x - 8, y - 11, colour)
    elif defect == "dent":
        image.ellipse(x, y, 12, 8, colour)
        image.line(x - 9, y - 4, x + 8, y + 4, colour)
        image.line(x - 8, y + 4, x + 9, y - 4, colour)
    elif defect == "leak":
        image.ellipse(x, y, 5, 4, colour, fill=True)
        image.line(x - 2, y + 5, x - 3, y + 13, colour)
        image.line(x + 3, y + 5, x + 4, y + 15, colour)
    elif defect in ("lamp_out", "inop"):
        image.ellipse(x, y, 9, 9, colour)
        image.line(x - 7, y - 7, x + 7, y + 7, colour)
        image.line(x - 7, y + 7, x + 7, y - 7, colour)
    elif defect == "worn":
        image.ellipse(x, y, 12, 8, colour)
        for dx, dy in ((-6, -3), (0, 0), (6, 3), (-3, 4), (4, -4)):
            image.rect(x + dx, y + dy, 2, 2, colour)


def _stamp(image: Image, asset: Asset, inspection: Inspection, photo: Photo) -> None:
    band_height = 52
    top = HEIGHT - band_height
    image.rect(0, top, WIDTH, band_height, INK)
    image.rect(0, top, WIDTH, 2, TEAL)
    image.rect(246, 18, 106, 26, RUST)
    image.text(254, 22, "DEMO", WHITE, 3)
    image.rect(8, top + 8, 42, 17, RUST)
    image.text(12, top + 12, "DEMO", WHITE, 1)
    image.text(58, top + 10, "SYNTHETIC PHOTO - NOT A REAL PHOTOGRAPH", WHITE, 1)
    image.text(
        8,
        top + 24,
        f"{asset.id}  {inspection.side.replace('_', ' ').upper()}  VIEW {photo.view.upper()}",
        TEAL,
        1,
    )
    image.text(8, top + 36, f"{photo.item_id}  {format_ts(photo.captured_at)}", GREY, 1)


def render_photo(photo: Photo, asset: Asset, inspection: Inspection) -> bytes:
    image = Image()
    _draw_scene(image, asset.asset_class)
    box = _focus(image, photo.view)
    x, y = _marker_position(asset.id, photo.item_id, photo.view, box)
    _draw_defect(image, photo.defect, x, y)
    _stamp(image, asset, inspection, photo)
    return encode_png(image)


# ---------------------------------------------------------------------------
# PNG codec (RGB, 8 bits per channel). Decode supports the five filter types so
# the PDF writer can read any writer's output, not just our own filter-0 output.


def encode_png(image: Image) -> bytes:
    raw = bytearray()
    stride = image.width * 3
    pixels = bytes(image.pixels)
    for y in range(image.height):
        raw.append(0)  # filter type 0 (None)
        raw.extend(pixels[y * stride : (y + 1) * stride])

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    offset = 8
    width = height = None
    idat = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, colour_type, _, _, _ = struct.unpack(">IIBBBBB", payload)
            if bit_depth != 8 or colour_type != 2:
                raise ValueError("only 8-bit RGB PNG is supported")
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            break
    if width is None or height is None:
        raise ValueError("PNG has no IHDR")
    raw = zlib.decompress(bytes(idat))
    stride = width * 3
    out = bytearray(height * stride)
    previous = bytearray(stride)
    position = 0
    for y in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position : position + stride])
        position += stride
        if filter_type == 1:  # Sub
            for i in range(3, stride):
                line[i] = (line[i] + line[i - 3]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - 3] if i >= 3 else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - 3] if i >= 3 else 0
                up = previous[i]
                up_left = previous[i - 3] if i >= 3 else 0
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                predictor = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                line[i] = (line[i] + predictor) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"unsupported PNG filter {filter_type}")
        out[y * stride : (y + 1) * stride] = line
        previous = line
    return width, height, bytes(out)


# ---------------------------------------------------------------------------
# Photo store: where generated images live and how the comparison engine sees them.


class PhotoStore:
    """Generates the seed's photos and answers evidence questions about them."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, photo: Photo) -> Path:
        return self.root / f"{photo.id}.png"

    def ensure(self, photo: Photo, asset: Asset, inspection: Inspection) -> Path:
        path = self.path_for(photo)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = render_photo(photo, asset, inspection)
        path.write_bytes(data)
        return path

    def generate_all(self, registry: Registry) -> dict[str, dict[str, str]]:
        manifest: dict[str, dict[str, str]] = {}
        for photo in sorted(registry.photos.values(), key=lambda p: p.id):
            inspection = next(i for i in registry.inspections if i.id == photo.inspection_id)
            asset = registry.asset(inspection.asset_id)
            path = self.ensure(photo, asset, inspection)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest[photo.id] = {
                "file": path.name,
                "sha256": digest,
                "item_id": photo.item_id,
                "inspection_id": photo.inspection_id,
                "view": photo.view,
                "defect": photo.defect,
                "captured_at": photo.captured_at.isoformat(),
            }
        return manifest

    def ref(self, photo: Photo):
        from .engine import PhotoRef

        path = self.path_for(photo)
        if not path.exists():
            return PhotoRef(
                photo_id=photo.id,
                view=photo.view,
                caption=photo.caption,
                captured_at=photo.captured_at,
                available=False,
                sha256=None,
                filename=path.name,
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return PhotoRef(
            photo_id=photo.id,
            view=photo.view,
            caption=photo.caption,
            captured_at=photo.captured_at,
            available=True,
            sha256=digest,
            filename=path.name,
        )
