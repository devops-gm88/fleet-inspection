"""One-page report rendering: what changed, the evidence, and what was missed.

Each asset in a period gets exactly one page. A comparable asset gets the diff:
every finding row carries the template item, the before/after state, the two
photos with their SHA-256 digests, and a status that says either RESOLVED or
NEEDS REVIEW with the reason. An asset with only one inspection side gets the
same one page, but its content is what is missing and why no comparison exists.

Every page also carries the fleet's "not inspected in this period" list, because
the record that says everything was inspected is the failure the product exists
to expose.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import pdf
from .engine import (
    CONDITION_DEGRADED,
    EXISTING_DAMAGE,
    MISSING_BASELINE,
    MISSING_ITEM,
    NEW_DAMAGE,
    RESOLVED,
    AssetComparison,
    ComparisonReport,
    Finding,
    NotInspected,
    PhotoRef,
)
from .model import Asset, Registry, format_date, format_ts
from .photos import PhotoStore, decode_png

MARGIN = 36.0
CONTENT = 612.0 - 2 * MARGIN

STATUS_LABELS = {
    "out_of_service": "OUT OF SERVICE",
    "needs_review": "HOLD - REVIEW",
    "release_with_damage": "RELEASE WITH DAMAGE",
    "available": "AVAILABLE",
}

SIDE_LABELS = {"pre_use": "PRE-USE", "post_use": "POST-USE"}


@dataclass(frozen=True)
class RenderedReport:
    asset_id: str
    filename: str
    path: Path
    pdf_bytes: bytes
    text: str
    page_count: int


def _chip(page: pdf.Page, x: float, top: float, label: str, colour, text_colour=pdf.WHITE, size: float = 6.5) -> float:
    width = pdf.text_width(label, size, bold=True) + 12
    page.rect(x, top, width, 14, colour)
    page.text(x + 6, top + 3.6, size, label, text_colour, bold=True)
    return x + width


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _state_label(state: str) -> str:
    return {"pass": "PASS", "fail": "FAIL", "not_checked": "NOT CHECKED", "missing": "NOT RECORDED"}.get(
        state, state.upper()
    )


def _change_text(finding: Finding) -> str:
    if finding.category == NEW_DAMAGE:
        if finding.kind == "graded":
            return f"PASS -> FAIL  (grade {finding.pre_grade} -> {finding.post_grade})"
        return "PASS -> FAIL"
    if finding.category == CONDITION_DEGRADED:
        return f"grade {finding.pre_grade} -> {finding.post_grade}, still passing"
    if finding.category == EXISTING_DAMAGE:
        if finding.kind == "graded" and finding.pre_grade != finding.post_grade:
            return f"FAIL -> FAIL  (pre-existing, grade {finding.pre_grade} -> {finding.post_grade})"
        return "FAIL -> FAIL  (pre-existing, recorded at pre-use)"
    if finding.category == MISSING_ITEM:
        return "no post-use result recorded"
    if finding.category == MISSING_BASELINE:
        return "no pre-use result recorded"
    return finding.category


def _asset_subtitle(asset: Asset, registry: Registry) -> str:
    return (
        f"{asset.label}  ·  class {asset.asset_class.replace('_', ' ')}  ·  {asset.ownership}  ·  "
        f"serial {asset.serial}  ·  {registry.site_name(asset.site_id)}"
    )


def _header(page: pdf.Page, report: ComparisonReport, title: str, subtitle: str) -> float:
    page.text(MARGIN, 34, 7.5, report.company.name.upper(), pdf.GREY, bold=True)
    page.text(MARGIN, 46, 17, title, pdf.INK, bold=True)
    end = page.wrapped(MARGIN, 68, CONTENT - 120, 8, subtitle, pdf.GREY)
    _chip(page, 612 - MARGIN - 64, 34, "DEMO DATA", pdf.RUST)
    page.text_right(612 - MARGIN, 52, 8, report.period.label, pdf.GREY, bold=True)
    page.text_right(612 - MARGIN, 63, 7, f"period {report.period.id}", pdf.GREY)
    rule_top = max(88, end + 4)
    page.line(MARGIN, rule_top, 612 - MARGIN, rule_top, pdf.RULE, 0.8)
    return rule_top + 8


def _inspection_panel(
    page: pdf.Page,
    registry: Registry,
    x: float,
    top: float,
    width: float,
    side: str,
    inspection_id: str,
    inspector_name: str,
    inspector_role: str,
    started_at: datetime,
    completed_at: datetime,
    recommendation: str | None = None,
) -> None:
    page.rect(x, top, width, 62, pdf.WASH)
    page.rect(x, top, 3, 62, pdf.TEAL if side == "pre_use" else pdf.INK)
    page.text(x + 10, top + 8, 7, SIDE_LABELS[side], pdf.TEAL, bold=True)
    page.text(x + 10, top + 19, 11, inspector_name, pdf.INK, bold=True)
    page.text(x + 10, top + 33, 7.5, inspector_role, pdf.GREY)
    page.text(x + 10, top + 44, 7.5, f"signed {format_ts(completed_at)}", pdf.INK)
    page.text(x + 10, top + 53, 6.5, inspection_id, pdf.GREY)
    if recommendation:
        label = STATUS_LABELS.get(recommendation, recommendation.upper())
        colour = pdf.RUST if recommendation in ("out_of_service", "needs_review") else pdf.TEAL
        _chip(page, x + width - pdf.text_width(label, 6.5, bold=True) - 18, top + 8, label, colour)


def _missing_panel(page: pdf.Page, x: float, top: float, width: float, side: str, note: str) -> None:
    page.stroke_rect(x, top, width, 62, pdf.RUST, 0.8)
    page.text(x + 10, top + 8, 7, SIDE_LABELS[side], pdf.RUST, bold=True)
    page.text(x + 10, top + 22, 10, "MISSING", pdf.RUST, bold=True)
    page.wrapped(x + 10, top + 36, width - 20, 7, note, pdf.GREY, max_lines=2)


def _photo_box(page: pdf.Page, x: float, top: float, width: float, height: float, ref: PhotoRef | None, label: str) -> None:
    if ref is not None and ref.available:
        image = _photo_image(page, ref)
        if image is not None:
            page.image(image, x, top, width, height)
            page.stroke_rect(x, top, width, height, pdf.RULE, 0.5)
            page.text(x + 2, top + height + 2, 6, label, pdf.GREY)
            page.text(x + 2, top + height + 9, 5.5, ref.short_digest, pdf.GREY)
            return
    page.rect(x, top, width, height, pdf.WASH)
    page.stroke_rect(x, top, width, height, pdf.RULE, 0.5)
    page.text(x + 3, top + height / 2 - 4, 6, "no photo captured", pdf.GREY)
    page.text(x + 2, top + height + 2, 6, label, pdf.GREY)


# Set once by render_all: the photo records and the store they were generated to.
_PHOTOS: dict[str, "object"] = {}
_STORE: PhotoStore | None = None


def _photo_image(page: pdf.Page, ref: PhotoRef) -> pdf.ImageRef | None:
    photo = _PHOTOS.get(ref.photo_id)
    if photo is None or _STORE is None:
        return None
    source = _STORE.path_for(photo)  # type: ignore[arg-type]
    if not source.exists():
        return None
    width, height, rgb = decode_png(source.read_bytes())
    return page.document.add_image(width, height, rgb)


def _finding_row(
    page: pdf.Page,
    document: pdf.Document,
    y: float,
    finding: Finding,
    max_note_lines: int,
) -> float:
    left_width = 218
    post_left = MARGIN + left_width + 8
    pre_left = post_left + 84
    status_left = pre_left + 84 + 10
    status_width = 612 - MARGIN - status_left

    page.text(MARGIN, y, 7.5, finding.item_id, pdf.INK, bold=True)
    label_top = page.wrapped(MARGIN, y + 11, left_width, 8.5, finding.label, pdf.INK, bold=True, leading=10, max_lines=2)
    detail = _change_text(finding)
    page.text(MARGIN, label_top + 2, 7, detail, pdf.RUST if finding.status != RESOLVED else pdf.AMBER, bold=True)
    if finding.critical and finding.post_state == "fail":
        page.text(MARGIN, label_top + 12, 6.5, "critical item", pdf.RUST, bold=True)
    note = finding.note_post or finding.note_pre
    note_top = label_top + 22
    if note:
        note_top = page.wrapped(MARGIN, note_top, left_width, 6.8, note, pdf.GREY, leading=8.2, max_lines=max_note_lines)

    photos_top = y + 12
    _photo_box(page, post_left, photos_top, 78, 58, (finding.photos_pre[0] if finding.photos_pre else None), f"before {format_date(finding.photos_pre[0].captured_at) if finding.photos_pre else ''}".strip())
    _photo_box(page, pre_left, photos_top, 78, 58, (finding.photos_post[0] if finding.photos_post else None), f"after {format_date(finding.photos_post[0].captured_at) if finding.photos_post else ''}".strip())

    if finding.status == RESOLVED:
        page.text(status_left, y + 10, 7, "RESOLVED", pdf.TEAL, bold=True)
        page.wrapped(status_left, y + 20, status_width, 6.5, "item + photo", pdf.GREY, leading=7.8, max_lines=1)
    else:
        page.text(status_left, y + 10, 7, "NEEDS REVIEW", pdf.RUST, bold=True)
        page.wrapped(status_left, y + 20, status_width, 6.5, finding.reason or "unresolved", pdf.RUST, leading=7.8, max_lines=3)

    bottom = max(note_top, y + 86)
    page.line(MARGIN, bottom + 4, 612 - MARGIN, bottom + 4, pdf.RULE, 0.4)
    return bottom + 10


def _not_inspected_block(page: pdf.Page, report: ComparisonReport, top: float, dark: bool = False) -> float:
    entries = report.not_inspected
    colour = pdf.RUST if dark else pdf.INK
    page.text(MARGIN, top, 10, f"Not inspected this period — {_plural(len(entries), 'asset')}", colour, bold=True)
    top += 14
    page.wrapped(
        MARGIN,
        top,
        CONTENT,
        6.8,
        "An asset with only one side, or neither, cannot be compared. It is listed here rather than assumed to have returned in the condition it left in.",
        pdf.GREY,
        leading=8.2,
    )
    top += 20
    for entry in entries:
        page.text(MARGIN, top, 7.5, entry.asset.id, pdf.INK, bold=True)
        page.text(MARGIN + 56, top, 7.5, f"{entry.asset.asset_class.replace('_', ' ')} · {report_asset_site(entry)}", pdf.GREY)
        page.wrapped(MARGIN + 176, top, CONTENT - 176, 6.8, entry.reason, pdf.GREY, leading=7.8, max_lines=2)
        top += 15
    return top


def report_asset_site(entry: NotInspected) -> str:
    return ASSET_SITE_NAMES.get(entry.asset.site_id, entry.asset.site_id)


ASSET_SITE_NAMES: dict[str, str] = {}


def _method_box(page: pdf.Page, top: float = 664) -> None:
    page.rect(MARGIN, top, CONTENT, 64, pdf.WASH)
    page.text(MARGIN + 8, top + 6, 7, "HOW THIS COMPARISON IS BUILT", pdf.TEAL, bold=True)
    lines = [
        "Same template and version on both sides, so every item is compared item for item, not as two unrelated forms.",
        "A change is a finding only when it resolves to a template item id and a photo with a printed SHA-256; otherwise it says NEEDS REVIEW.",
        "Damage recorded at pre-use is pre-existing: it is named on the page and never counted as new damage.",
        "An asset with only one inspection side is listed as not inspected. The record never assumes a return condition.",
    ]
    for index, line in enumerate(lines):
        page.text(MARGIN + 8, top + 17 + index * 11, 6.6, line, pdf.INK)


def _fit(y: float, where: str) -> None:
    """A one-page report is a hard constraint, not an aspiration."""
    if y > 734:
        raise RuntimeError(
            f"report page overflow for {where}: content ends at {y:.0f}pt but the footer starts at 744pt"
        )


def _footer(page: pdf.Page, report: ComparisonReport, report_id: str) -> None:
    page.line(MARGIN, 744, 612 - MARGIN, 744, pdf.RULE, 0.6)
    page.text(MARGIN, 750, 6.5, "Demonstration build — fictional assets, synthetic photos. No compliance or regulatory claim is made.", pdf.GREY)
    page.text(MARGIN, 759, 6.5, f"{report_id}  ·  generated {format_ts(report.generated_at)}  ·  page 1 of 1", pdf.GREY)


def _render_comparable(
    registry: Registry,
    report: ComparisonReport,
    comparison: AssetComparison,
    store: PhotoStore,
    report_id: str,
) -> RenderedReport:
    document = pdf.Document()
    page = document.add_page()
    y = _header(
        page,
        report,
        f"Handover comparison — {comparison.asset.id}",
        _asset_subtitle(comparison.asset, registry),
    )
    _inspection_panel(
        page,
        registry,
        MARGIN,
        y,
        CONTENT / 2 - 8,
        "pre_use",
        comparison.pre.inspection_id,
        comparison.pre.inspector_name,
        comparison.pre.inspector_role,
        comparison.pre.started_at,
        comparison.pre.completed_at,
    )
    _inspection_panel(
        page,
        registry,
        MARGIN + CONTENT / 2 + 8,
        y,
        CONTENT / 2 - 8,
        "post_use",
        comparison.post.inspection_id,
        comparison.post.inspector_name,
        comparison.post.inspector_role,
        comparison.post.started_at,
        comparison.post.completed_at,
        comparison.recommended_status,
    )
    y += 76

    findings = comparison.findings
    page.text(MARGIN, y, 11, f"What changed — {_plural(len(findings), 'finding')}", pdf.INK, bold=True)
    y += 15
    page.wrapped(
        MARGIN,
        y,
        CONTENT,
        7,
        "Each finding resolves to a template item and at least one post-use photo. A finding that cannot resolve says NEEDS REVIEW instead of being dropped.",
        pdf.GREY,
        leading=8.4,
    )
    y += 18

    if findings:
        for finding in findings:
            y = _finding_row(page, document, y, finding, max_note_lines=3)
    else:
        page.text(MARGIN, y, 8, "No item changed state in this cycle; nothing to report.", pdf.TEAL)
        y += 14

    if comparison.existing_damage:
        y = page.wrapped(
            MARGIN,
            y + 2,
            CONTENT,
            7,
            "Pre-existing damage is carried forward, with its pre-use photo, and is never counted as new damage. "
            "It does not appear in the new-damage total.",
            pdf.AMBER,
            leading=8.4,
        )
        y += 8

    if comparison.cleared:
        cleared = ", ".join(f"{diff.item_id} ({diff.label})" for diff in comparison.cleared)
        y = page.wrapped(MARGIN, y + 2, CONTENT, 7, f"Cleared at return: {cleared}.", pdf.TEAL, leading=8.4)
        y += 4

    unchanged = len(comparison.unchanged)
    page.wrapped(
        MARGIN,
        y + 2,
        CONTENT,
        7,
        f"{_plural(comparison.inspected_items, 'template item')} compared, {_plural(len(findings), 'finding')}, {unchanged} unchanged. "
        f"Recommended asset status: {STATUS_LABELS.get(comparison.recommended_status, comparison.recommended_status)}.",
        pdf.GREY,
        leading=8.4,
    )
    y += 22

    pre_fail = sum(1 for diff in comparison.diffs if diff.pre_state == "fail")
    post_fail = sum(1 for diff in comparison.diffs if diff.post_state == "fail")
    page.rect(MARGIN, y, CONTENT, 44, pdf.WASH)
    page.text(MARGIN + 8, y + 7, 7.5, "EVIDENCE SUMMARY", pdf.TEAL, bold=True)
    page.text(
        MARGIN + 8,
        y + 18,
        6.8,
        f"Pre-use: {len(comparison.diffs)} items recorded, {pre_fail} failing — "
        f"{comparison.pre.inspector_name}, {format_ts(comparison.pre.completed_at)}.",
        pdf.INK,
    )
    page.text(
        MARGIN + 8,
        y + 28,
        6.8,
        f"Post-use: {post_fail} failing — {comparison.post.inspector_name}, {format_ts(comparison.post.completed_at)}. "
        f"{_plural(len(comparison.findings_resolved), 'finding')} resolved with a photo digest; "
        f"{len(comparison.findings_needs_review)} NEEDS REVIEW.",
        pdf.INK,
    )
    page.text(
        MARGIN + 8,
        y + 38,
        6.8,
        f"{comparison.inspected_items} items compared · {len(comparison.existing_damage)} pre-existing carried forward "
        f"(never charged as new) · {len(comparison.cleared)} cleared at return · "
        f"recommended status {STATUS_LABELS.get(comparison.recommended_status, comparison.recommended_status)}.",
        pdf.INK,
    )
    y += 54

    y = _not_inspected_block(page, report, y)
    method_top = max(664.0, y + 6)
    _fit(method_top + 64, comparison.asset.id)
    _method_box(page, method_top)
    _footer(page, report, report_id)
    data = document.build()
    filename = f"{report.period.id}-{comparison.asset.id}.pdf"
    return RenderedReport(
        asset_id=comparison.asset.id,
        filename=filename,
        path=Path(filename),
        pdf_bytes=data,
        text=pdf.extract_text(data),
        page_count=pdf.count_pages(data),
    )


def _recorded_items_table(page: pdf.Page, registry: Registry, entry: NotInspected, top: float) -> float:
    """Show item by item what the recorded side actually says, and what is absent."""
    template = registry.template_for(entry.asset)
    record = entry.records[0] if entry.records else None
    title = (
        f"What the {SIDE_LABELS[record.side].lower()} record shows ({len(template.items)} template items)"
        if record
        else f"What the template would have recorded ({len(template.items)} items) — nothing was recorded"
    )
    y = top
    page.text(MARGIN, y, 9, title, pdf.INK, bold=True)
    y += 14
    columns = [62, 212, 96, 170]
    page.rect(MARGIN, y, CONTENT, 14, pdf.INK)
    cursor = MARGIN
    for label, width in zip(["ITEM", "TEMPLATE ITEM", "RESULT", "INSPECTOR NOTE"], columns):
        page.text(cursor + 4, y + 3.5, 6, label, pdf.WHITE, bold=True)
        cursor += width
    y += 14
    for index, item in enumerate(template.items):
        result = record.result_for(item.id) if record else None
        if result is None:
            result_text = "not recorded"
            note = "no result exists for this item"
        elif item.graded:
            result_text = f"{_state_label(result.state).lower()} · grade {result.grade}"
            note = result.note
        else:
            result_text = _state_label(result.state).lower()
            note = result.note
        page.rect(MARGIN, y, CONTENT, 13, pdf.WASH if index % 2 == 0 else pdf.PAPER)
        page.text(MARGIN + 4, y + 3, 6.5, item.id, pdf.INK, bold=True)
        page.wrapped(MARGIN + columns[0] + 4, y + 3, columns[1] - 8, 6.3, item.label, pdf.INK, leading=7, max_lines=1)
        colour = pdf.RUST if result is None else (pdf.RUST if result.state == "fail" else pdf.GREY)
        page.text(MARGIN + columns[0] + columns[1] + 4, y + 3, 6.3, result_text, colour)
        page.wrapped(
            MARGIN + columns[0] + columns[1] + columns[2] + 4,
            y + 3,
            columns[3] - 8,
            6.3,
            note,
            pdf.GREY,
            leading=7,
            max_lines=1,
        )
        y += 13
    return y + 6


def _render_not_inspected(
    registry: Registry,
    report: ComparisonReport,
    entry: NotInspected,
    store: PhotoStore,
    report_id: str,
) -> RenderedReport:
    document = pdf.Document()
    page = document.add_page()
    y = _header(
        page,
        report,
        f"No comparison possible — {entry.asset.id}",
        _asset_subtitle(entry.asset, registry),
    )
    page.text(MARGIN, y, 11, "This asset cannot be compared this period", pdf.RUST, bold=True)
    y += 16
    y = page.wrapped(MARGIN, y, CONTENT, 8.5, entry.reason[0].upper() + entry.reason[1:] + ".", pdf.INK, leading=11)
    y += 10

    if entry.records:
        half = CONTENT / 2 - 8
        if entry.present_sides == ("pre_use",):
            record = entry.records[0]
            inspector = registry.inspector(record.inspector_id)
            _inspection_panel(
                page,
                registry,
                MARGIN,
                y,
                half,
                "pre_use",
                record.id,
                inspector.name,
                inspector.role,
                record.started_at,
                record.completed_at,
            )
            _missing_panel(
                page,
                MARGIN + CONTENT / 2 + 8,
                y,
                half,
                "post_use",
                "No return inspection exists, so loss or damage cannot be attributed to this hire.",
            )
        elif entry.present_sides == ("post_use",):
            record = entry.records[0]
            inspector = registry.inspector(record.inspector_id)
            _missing_panel(
                page,
                MARGIN,
                y,
                half,
                "pre_use",
                "No pre-use baseline exists, so the recorded condition cannot be called new damage.",
            )
            _inspection_panel(
                page,
                registry,
                MARGIN + CONTENT / 2 + 8,
                y,
                half,
                "post_use",
                record.id,
                inspector.name,
                inspector.role,
                record.started_at,
                record.completed_at,
            )
    else:
        page.rect(MARGIN, y, CONTENT, 62, pdf.WASH)
        page.text(MARGIN + 10, y + 12, 10, "NO INSPECTION RECORD", pdf.RUST, bold=True)
        page.wrapped(
            MARGIN + 10,
            y + 30,
            CONTENT - 20,
            7,
            "No inspection of either side was recorded in the period. This asset is invisible to a handover dispute.",
            pdf.GREY,
            max_lines=2,
        )
    y += 80
    y = _recorded_items_table(page, registry, entry, y)
    y += 8

    page.text(MARGIN, y, 9.5, "What is needed to close this", pdf.INK, bold=True)
    y += 14
    required = (
        "A post-use (return) inspection on the same template and cycle."
        if entry.present_sides == ("pre_use",)
        else "A pre-use inspection on the same template and cycle, recorded before the asset goes out."
        if entry.present_sides == ("post_use",)
        else "One pre-use inspection before dispatch and one post-use inspection at return."
    )
    page.wrapped(MARGIN, y, CONTENT, 8, required, pdf.INK, leading=10)
    y += 26
    y = _not_inspected_block(page, report, y, dark=True)
    method_top = max(664.0, y + 6)
    _fit(method_top + 64, entry.asset.id)
    _method_box(page, method_top)
    _footer(page, report, report_id)
    data = document.build()
    filename = f"{report.period.id}-{entry.asset.id}.pdf"
    return RenderedReport(
        asset_id=entry.asset.id,
        filename=filename,
        path=Path(filename),
        pdf_bytes=data,
        text=pdf.extract_text(data),
        page_count=pdf.count_pages(data),
    )


def render_fleet_report(registry: Registry, report: ComparisonReport, store: PhotoStore, report_id: str) -> RenderedReport:
    document = pdf.Document()
    page = document.add_page()
    y = _header(
        page,
        report,
        "Fleet handover summary",
        f"{len(registry.assets)} assets · {report.period.label} · every asset either compared or named as not inspected",
    )
    stats = report.stats
    boxes = [
        ("ASSETS", str(stats.assets_total)),
        ("COMPARED", str(stats.comparable_assets)),
        ("NOT INSPECTED", str(stats.not_inspected_assets)),
        ("FINDINGS", str(stats.findings_total)),
        ("NEEDS REVIEW", str(stats.findings_needs_review)),
        ("NEW DAMAGE", str(stats.new_damage)),
    ]
    box_width = CONTENT / len(boxes)
    for index, (label, value) in enumerate(boxes):
        x = MARGIN + index * box_width
        page.rect(x, y, box_width - 6, 42, pdf.WASH)
        page.text(x + 8, y + 8, 6.5, label, pdf.GREY, bold=True)
        page.text(x + 8, y + 19, 16, value, pdf.INK, bold=True)
    y += 54

    headers = ["ASSET", "CLASS", "SITE", "PAIR", "FINDINGS", "NEW / EXISTING", "EXIT STATUS"]
    columns = [72, 70, 96, 42, 60, 86, 114]
    page.rect(MARGIN, y, CONTENT, 16, pdf.INK)
    cursor = MARGIN
    for label, width in zip(headers, columns):
        page.text(cursor + 4, y + 4.5, 6.2, label, pdf.WHITE, bold=True)
        cursor += width
    y += 16

    for row_index, asset in enumerate(registry.assets.values()):
        comparison = report.comparison_for(asset.id)
        entry = report.not_inspected_for(asset.id)
        pair = "yes" if comparison else "no"
        findings = str(len(comparison.findings)) if comparison else "-"
        if comparison:
            new = sum(1 for f in comparison.findings if f.category == NEW_DAMAGE)
            existing = sum(1 for f in comparison.findings if f.category == EXISTING_DAMAGE)
            damage = f"{new} / {existing}"
            status = STATUS_LABELS.get(comparison.recommended_status, comparison.recommended_status)
            status_colour = pdf.RUST if comparison.recommended_status in ("out_of_service", "needs_review") else pdf.TEAL
        else:
            damage = "-"
            status = "NOT INSPECTED"
            status_colour = pdf.RUST
        row_height = 18
        page.rect(MARGIN, y, CONTENT, row_height, pdf.WASH if row_index % 2 else pdf.PAPER)
        values = [
            asset.id,
            asset.asset_class.replace("_", " "),
            registry.site_name(asset.site_id),
            pair,
            findings,
            damage,
            status,
        ]
        cursor = MARGIN
        for index, (value, width) in enumerate(zip(values, columns)):
            colour = status_colour if index == 6 else pdf.INK
            page.text(cursor + 4, y + 4.5, 7, value, colour, bold=index in (0, 6))
            cursor += width
        y += row_height
    y += 8

    page.text(MARGIN, y, 9.5, "Sharp findings", pdf.INK, bold=True)
    y += 13
    sharp = [f for comparison in report.comparisons for f in comparison.new_damage]
    for finding in sharp:
        asset_comparison = report.comparison_for(finding.asset_id)
        post = asset_comparison.post if asset_comparison else None
        resolved = finding.status == RESOLVED
        page.text(MARGIN, y, 7, finding.item_id, pdf.INK, bold=True)
        page.wrapped(MARGIN + 70, y, 210, 7, finding.label, pdf.INK, leading=8, max_lines=1)
        page.text(MARGIN + 288, y, 7, f"{finding.asset_id} · new damage", pdf.RUST)
        page.text(MARGIN + 402, y, 7, "RESOLVED" if resolved else "NEEDS REVIEW", pdf.TEAL if resolved else pdf.RUST, bold=True)
        page.text(
            MARGIN + 70,
            y + 9,
            6.3,
            f"inspector {post.inspector_name} · {format_ts(post.completed_at)}" if post else "",
            pdf.GREY,
        )
        y += 21
    if not sharp:
        page.text(MARGIN, y, 7, "No new damage found in the period.", pdf.GREY)
        y += 13
    y += 10

    page.text(MARGIN, y, 9.5, "Reading this page", pdf.INK, bold=True)
    y += 13
    y = page.wrapped(
        MARGIN,
        y,
        CONTENT,
        6.8,
        "PAIR means a complete pre-use/post-use pair exists. NEW / EXISTING counts new damage against damage "
        "recorded at pre-use. EXIT STATUS is the comparison engine's recommendation: OUT OF SERVICE when a critical "
        "item fails, HOLD - REVIEW when a finding is unresolved, RELEASE WITH DAMAGE when changes are evidenced but "
        "nothing critical failed. Every sharp finding above carries its inspector and signature time.",
        pdf.GREY,
        leading=8.4,
    )
    y += 10

    y = _not_inspected_block(page, report, y)
    method_top = max(664.0, y + 6)
    _fit(method_top + 64, "FLEET")
    _method_box(page, method_top)
    _footer(page, report, report_id)
    data = document.build()
    filename = f"{report.period.id}-FLEET.pdf"
    return RenderedReport(
        asset_id="FLEET",
        filename=filename,
        path=Path(filename),
        pdf_bytes=data,
        text=pdf.extract_text(data),
        page_count=pdf.count_pages(data),
    )


def render_all(registry: Registry, report: ComparisonReport, store: PhotoStore, site_names: dict[str, str] | None = None) -> tuple[RenderedReport, ...]:
    global _STORE
    ASSET_SITE_NAMES.update(site_names or {site_id: site.name for site_id, site in registry.sites.items()})
    _STORE = store
    _PHOTOS.clear()
    _PHOTOS.update(registry.photos)
    rendered: list[RenderedReport] = []
    for asset in registry.assets.values():
        report_id = f"RPT-{report.period.id}-{asset.id}"
        comparison = report.comparison_for(asset.id)
        if comparison is not None:
            rendered.append(_render_comparable(registry, report, comparison, store, report_id))
            continue
        entry = report.not_inspected_for(asset.id)
        if entry is None:  # defensive: an asset must be one or the other
            continue
        rendered.append(_render_not_inspected(registry, report, entry, store, report_id))
    rendered.append(render_fleet_report(registry, report, store, f"RPT-{report.period.id}-FLEET"))
    return tuple(rendered)
