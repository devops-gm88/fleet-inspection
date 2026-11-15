"""A small dependency-free PDF writer: text, rules, filled boxes and RGB images.

The demo has to emit a report from a clean checkout with no network and no PDF
library, so the file is written directly: base-14 Helvetica fonts, uncompressed
content streams (which keeps the text selectable and greppable), one xref table,
and Flate-compressed RGB image XObjects for the photo evidence. Only the features
the one-page report uses are implemented; there is no attempt at a general PDF
library.
"""
from __future__ import annotations

import hashlib
import zlib

# Helvetica and Helvetica-Bold advance widths (1/1000 em) for ASCII 32..126.
_HELV = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]
_HELVB = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
]

# Colours used by the report. One ink, one accent, semantic status colours.
INK = (0.055, 0.102, 0.169)
TEAL = (0.180, 0.420, 0.420)
RUST = (0.620, 0.240, 0.190)
AMBER = (0.600, 0.450, 0.140)
GREY = (0.380, 0.410, 0.440)
RULE = (0.820, 0.850, 0.870)
PAPER = (1.0, 1.0, 1.0)
WASH = (0.945, 0.955, 0.960)
WHITE = (1.0, 1.0, 1.0)

# characters WinAnsi cannot carry are folded to ASCII
_FOLD = {
    "\u2212": "-", "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u00b7": "-", "\u2264": "<=", "\u2265": ">=",
    "\u00d7": "x", "\u2026": "...", "\u00b0": "o", "\u2192": "->",
}


def _fold(text: str) -> str:
    return "".join(_FOLD.get(char, char) for char in text)


def _escape_text(text: str) -> str:
    text = _fold(text)
    return (
        text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        .replace("\r", " ").replace("\n", " ")
    )


def text_width(text: str, size: float, bold: bool = False) -> float:
    widths = _HELVB if bold else _HELV
    total = 0
    for char in _fold(text):
        code = ord(char)
        total += widths[code - 32] if 32 <= code <= 126 else 556
    return total * size / 1000.0


def wrap_text(text: str, width: float, size: float, bold: bool = False) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if text_width(candidate, size, bold) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


class ImageRef:
    def __init__(self, name: str, width: int, height: int) -> None:
        self.name = name
        self.width = width
        self.height = height


class Page:
    def __init__(self, document: "Document") -> None:
        self.document = document
        self.ops: list[str] = []
        self.images: list[ImageRef] = []

    @property
    def width(self) -> float:
        return self.document.width

    @property
    def height(self) -> float:
        return self.document.height

    def _y(self, top: float, height: float = 0.0) -> float:
        return self.height - top - height

    def rect(self, x: float, top: float, w: float, h: float, colour=INK) -> None:
        self.ops.append(
            f"{colour[0]:.4f} {colour[1]:.4f} {colour[2]:.4f} rg "
            f"{x:.2f} {self._y(top, h):.2f} {w:.2f} {h:.2f} re f"
        )

    def stroke_rect(self, x: float, top: float, w: float, h: float, colour=RULE, line_width: float = 0.6) -> None:
        self.ops.append(
            f"{colour[0]:.4f} {colour[1]:.4f} {colour[2]:.4f} RG {line_width:.2f} w "
            f"{x:.2f} {self._y(top, h):.2f} {w:.2f} {h:.2f} re S"
        )

    def line(self, x0: float, top0: float, x1: float, top1: float, colour=RULE, line_width: float = 0.6) -> None:
        self.ops.append(
            f"{colour[0]:.4f} {colour[1]:.4f} {colour[2]:.4f} RG {line_width:.2f} w "
            f"{x0:.2f} {self._y(top0):.2f} m {x1:.2f} {self._y(top1):.2f} l S"
        )

    def text(
        self,
        x: float,
        top: float,
        size: float,
        content: str,
        colour=INK,
        bold: bool = False,
    ) -> None:
        text = _escape_text(content)
        font = "F2" if bold else "F1"
        baseline = self.height - top - size * 0.80
        self.ops.append(
            f"BT {colour[0]:.4f} {colour[1]:.4f} {colour[2]:.4f} rg /{font} {size:.2f} Tf "
            f"{x:.2f} {baseline:.2f} Td ({text}) Tj ET"
        )

    def text_right(self, right: float, top: float, size: float, content: str, colour=INK, bold: bool = False) -> None:
        self.text(right - text_width(content, size, bold), top, size, content, colour, bold)

    def wrapped(
        self,
        x: float,
        top: float,
        width: float,
        size: float,
        content: str,
        colour=INK,
        bold: bool = False,
        leading: float | None = None,
        max_lines: int | None = None,
    ) -> float:
        leading = leading or size * 1.25
        lines = wrap_text(content, width, size, bold)
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip() + "..."
        for index, line in enumerate(lines):
            self.text(x, top + index * leading, size, line, colour, bold)
        return top + len(lines) * leading

    def image(self, ref: ImageRef, x: float, top: float, w: float, h: float) -> None:
        self.images.append(ref)
        self.ops.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {self._y(top, h):.2f} cm /{ref.name} Do Q")

    def content(self) -> bytes:
        return ("\n".join(self.ops) + "\n").encode("latin-1", "replace")


class Document:
    def __init__(self, width: float = 612, height: float = 792) -> None:
        self.width = width
        self.height = height
        self.pages: list[Page] = []
        self._images: dict[str, tuple[str, int, int, bytes]] = {}

    def add_page(self) -> Page:
        page = Page(self)
        self.pages.append(page)
        return page

    def add_image(self, width: int, height: int, rgb: bytes) -> ImageRef:
        digest = hashlib.sha256(rgb).hexdigest()
        if digest in self._images:
            name, w, h, _ = self._images[digest]
            return ImageRef(name, w, h)
        name = f"Im{len(self._images) + 1}"
        self._images[digest] = (name, width, height, rgb)
        return ImageRef(name, width, height)

    def build(self) -> bytes:
        objects: dict[int, bytes] = {}

        page_base = 5  # 1 catalog, 2 pages, 3 F1, 4 F2
        image_base = page_base + 2 * len(self.pages)
        image_objects: dict[str, int] = {}
        for index, (digest, (name, width, height, rgb)) in enumerate(self._images.items()):
            object_number = image_base + index
            image_objects[name] = object_number
            compressed = zlib.compress(rgb, 6)
            stream = (
                f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(compressed)} >>\nstream\n"
            ).encode("latin-1") + compressed + b"\nendstream"
            objects[object_number] = stream

        kids = []
        for index, page in enumerate(self.pages):
            page_object = page_base + 2 * index
            content_object = page_object + 1
            kids.append(f"{page_object} 0 R")
            content = page.content()
            objects[content_object] = (
                f"<< /Length {len(content)} >>\nstream\n".encode("latin-1")
                + content
                + b"\nendstream"
            )
            used = {ref.name: image_objects[ref.name] for ref in page.images}
            xobjects = " ".join(f"/{name} {number} 0 R" for name, number in used.items())
            resources = f"<< /Font << /F1 3 0 R /F2 4 0 R >>"
            if xobjects:
                resources += f" /XObject << {xobjects} >>"
            resources += " >>"
            media = f"[0 0 {self.width:g} {self.height:g}]"
            objects[page_object] = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox {media} "
                f"/Resources {resources} /Contents {content_object} 0 R >>"
            ).encode("latin-1")

        objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objects[2] = (
            f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(self.pages)} >>"
        ).encode("latin-1")
        objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"

        highest = max(objects)
        out = bytearray(b"%PDF-1.4\n%\xc7\xec\x8f\xa2\n")
        offsets: dict[int, int] = {}
        for number in range(1, highest + 1):
            if number not in objects:
                raise ValueError(f"PDF object {number} was never written")
            offsets[number] = len(out)
            out.extend(f"{number} 0 obj\n".encode("latin-1"))
            out.extend(objects[number])
            out.extend(b"\nendobj\n")
        xref_offset = len(out)
        out.extend(f"xref\n0 {highest + 1}\n".encode("latin-1"))
        out.extend(b"0000000000 65535 f \n")
        for number in range(1, highest + 1):
            out.extend(f"{offsets[number]:010d} 00000 n \n".encode("latin-1"))
        out.extend(
            f"trailer\n<< /Size {highest + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
        )
        return bytes(out)


def extract_text(pdf_bytes: bytes) -> str:
    """Pull the text operators out of an uncompressed content stream.

    Used by the acceptance checks to prove the PDF itself carries the item names,
    inspector and timestamp, rather than trusting the layout code that drew it.
    """
    chunks: list[str] = []
    position = 0
    raw = pdf_bytes
    marker = b" Tj"
    while True:
        start = raw.find(b"(", position)
        if start == -1:
            break
        end = raw.find(marker, start)
        if end == -1:
            break
        literal = raw[start + 1 : end]
        text = bytearray()
        index = 0
        while index < len(literal):
            byte = literal[index]
            if byte == 0x5C and index + 1 < len(literal):  # backslash escape
                nxt = literal[index + 1]
                text.append({0x6E: 10, 0x72: 13, 0x74: 9}.get(nxt, nxt))
                index += 2
            else:
                text.append(byte)
                index += 1
        chunks.append(text.decode("latin-1", "replace"))
        position = end + len(marker)
    return "\n".join(chunks)


def count_pages(pdf_bytes: bytes) -> int:
    import re

    match = re.search(rb"/Type\s*/Pages\s*/Kids\s*\[([^\]]*)\]", pdf_bytes)
    if match:
        return len(re.findall(rb"\d+\s+0\s+R", match.group(1)))
    return pdf_bytes.count(b"/Type /Page ")
