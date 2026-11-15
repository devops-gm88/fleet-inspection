"""Orchestration: seed -> photos -> comparison -> one-page reports -> evidence.

``python3 -m fleet demo`` calls this once and writes everything into ``out/``.
The output is the demonstration: a machine-readable comparison, the generated
photo evidence with digests, and one PDF page per asset plus the fleet summary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .engine import ComparisonReport, Finding, compare
from .model import Registry, load_registry
from .photos import PhotoStore
from .report import RenderedReport, render_all

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class DemoResult:
    registry: Registry
    report: ComparisonReport
    rendered: tuple[RenderedReport, ...]
    manifest: dict[str, dict[str, str]]
    out_dir: Path

    def report_for(self, asset_id: str) -> RenderedReport:
        for rendered in self.rendered:
            if rendered.asset_id == asset_id:
                return rendered
        raise KeyError(asset_id)


def load(data_dir: Path | None = None) -> Registry:
    return load_registry(Path(data_dir or DEFAULT_DATA))


def build(data_dir: Path | None = None, out_dir: Path | None = None, now: datetime | None = None, write: bool = True) -> DemoResult:
    registry = load(data_dir)
    out = Path(out_dir or (Path.cwd() / "out"))
    store = PhotoStore(out / "photos")
    manifest = store.generate_all(registry)
    report = compare(registry, store, generated_at=now)
    rendered = render_all(registry, report, store)
    result = DemoResult(registry=registry, report=report, rendered=rendered, manifest=manifest, out_dir=out)
    if write:
        write_outputs(result)
    return result


def _finding_payload(finding: Finding) -> dict:
    return {
        "id": finding.id,
        "asset_id": finding.asset_id,
        "cycle_ref": finding.cycle_ref,
        "category": finding.category,
        "item_id": finding.item_id,
        "item_label": finding.label,
        "kind": finding.kind,
        "critical": finding.critical,
        "pre_state": finding.pre_state,
        "post_state": finding.post_state,
        "pre_grade": finding.pre_grade,
        "post_grade": finding.post_grade,
        "newly_unroadworthy": finding.newly_unroadworthy,
        "status": finding.status,
        "basis": finding.basis,
        "reason": finding.reason,
        "note_pre": finding.note_pre,
        "note_post": finding.note_post,
        "evidence": _finding_photos(finding),
    }


def _finding_photos(finding: Finding) -> list[dict]:
    photos = []
    for ref in finding.photos_pre + finding.photos_post:
        photos.append(
            {
                "photo_id": ref.photo_id,
                "file": f"photos/{ref.filename}",
                "sha256": ref.sha256,
                "view": ref.view,
                "captured_at": ref.captured_at.isoformat(),
                "available": ref.available,
                "caption": ref.caption,
            }
        )
    return photos


def _diff_payload(diff) -> dict:
    return {
        "item_id": diff.item_id,
        "item_label": diff.label,
        "kind": diff.kind,
        "critical": diff.critical,
        "category": diff.category,
        "pre_state": diff.pre_state,
        "post_state": diff.post_state,
        "pre_grade": diff.pre_grade,
        "post_grade": diff.post_grade,
        "newly_unroadworthy": diff.newly_unroadworthy,
        "photo_ids_pre": [p.photo_id for p in diff.photos_pre],
        "photo_ids_post": [p.photo_id for p in diff.photos_post],
    }


def _side_payload(side) -> dict:
    return {
        "inspection_id": side.inspection_id,
        "inspector": side.inspector_name,
        "inspector_role": side.inspector_role,
        "started_at": side.started_at.isoformat(),
        "completed_at": side.completed_at.isoformat(),
    }


def report_payload(result: DemoResult) -> dict:
    report = result.report
    registry = result.registry
    assets: list[dict] = []
    for asset in registry.assets.values():
        comparison = report.comparison_for(asset.id)
        entry = report.not_inspected_for(asset.id)
        payload: dict = {
            "id": asset.id,
            "class": asset.asset_class,
            "make": asset.make,
            "model": asset.model,
            "year": asset.year,
            "serial": asset.serial,
            "ownership": asset.ownership,
            "site": registry.site_name(asset.site_id),
            "assigned_to": registry.inspector(asset.assigned_to).name,
            "seeded_status": asset.status,
        }
        if comparison is not None:
            payload.update(
                {
                    "comparable": True,
                    "cycle_ref": comparison.cycle_ref,
                    "pre": _side_payload(comparison.pre),
                    "post": _side_payload(comparison.post),
                    "recommended_status": comparison.recommended_status,
                    "newly_unroadworthy": list(comparison.newly_unroadworthy),
                    "findings": [_finding_payload(f) for f in comparison.findings],
                    "diffs": [_diff_payload(d) for d in comparison.diffs],
                    "cleared_items": [d.item_id for d in comparison.cleared],
                    "report": f"reports/{result.report_for(asset.id).filename}",
                }
            )
        else:
            payload.update(
                {
                    "comparable": False,
                    "not_inspected": {
                        "reason": entry.reason if entry else "no record",
                        "present_sides": list(entry.present_sides) if entry else [],
                        "last_inspection_id": entry.last_inspection_id if entry else None,
                        "last_inspection_at": entry.last_inspection_at.isoformat() if entry and entry.last_inspection_at else None,
                    },
                    "report": f"reports/{result.report_for(asset.id).filename}",
                }
            )
        assets.append(payload)

    stats = report.stats
    return {
        "demonstration": True,
        "note": "Fictional assets, fictional inspectors and synthetic photos. No real yard data is present.",
        "command": "python3 -m fleet demo",
        "company": {"id": report.company.id, "name": report.company.name},
        "period": {
            "id": report.period.id,
            "label": report.period.label,
            "starts_at": report.period.starts_at.isoformat(),
            "ends_at": report.period.ends_at.isoformat(),
        },
        "generated_at": report.generated_at.isoformat(),
        "stats": {
            "assets_total": stats.assets_total,
            "comparable_assets": stats.comparable_assets,
            "not_inspected_assets": stats.not_inspected_assets,
            "findings_total": stats.findings_total,
            "findings_resolved": stats.findings_resolved,
            "findings_needs_review": stats.findings_needs_review,
            "new_damage": stats.new_damage,
            "existing_damage": stats.existing_damage,
            "condition_degraded": stats.condition_degraded,
            "missing_items": stats.missing_items,
            "cleared_items": stats.cleared_items,
            "newly_unroadworthy": stats.newly_unroadworthy,
            "photos_used": stats.photos_used,
            "items_compared": stats.items_compared,
        },
        "not_inspected": [
            {
                "asset_id": entry.asset.id,
                "reason": entry.reason,
                "present_sides": list(entry.present_sides),
                "last_inspection_id": entry.last_inspection_id,
            }
            for entry in report.not_inspected
        ],
        "assets": assets,
        "reports": [f"reports/{rendered.filename}" for rendered in result.rendered],
        "verification": "run `python3 -m fleet verify` for the acceptance checks",
    }


def write_outputs(result: DemoResult) -> None:
    out = result.out_dir
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for rendered in result.rendered:
        (reports_dir / rendered.filename).write_bytes(rendered.pdf_bytes)
    (out / "comparison.json").write_text(
        json.dumps(report_payload(result), indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    (out / "photos_manifest.json").write_text(
        json.dumps(result.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "Fleet & Asset Inspection — demonstration run",
        "",
        f"period: {result.report.period.id} — {result.report.period.label}",
        f"assets: {result.report.stats.assets_total}   compared: {result.report.stats.comparable_assets}   not inspected: {result.report.stats.not_inspected_assets}",
        f"findings: {result.report.stats.findings_total}   resolved: {result.report.stats.findings_resolved}   needs review: {result.report.stats.findings_needs_review}",
        f"new damage: {result.report.stats.new_damage}   existing damage: {result.report.stats.existing_damage}   newly unroadworthy: {result.report.stats.newly_unroadworthy}",
        "",
        "reports:",
    ]
    lines.extend(f"  reports/{rendered.filename}  ({rendered.page_count} page)" for rendered in result.rendered)
    lines.extend(
        [
            "",
            "not inspected:",
            *[f"  {entry.asset.id}: {entry.reason}" for entry in result.report.not_inspected],
            "",
            "verification: python3 -m fleet verify",
        ]
    )
    (out / "SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
