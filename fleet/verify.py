"""The acceptance checks. ``python3 -m fleet verify`` proves the demo, not the prose.

Every check is named after the claim it protects. They are deliberately written
against the generated artefacts (the PDF bytes and the comparison objects), so a
change that quietly stops producing evidence fails the demo instead of passing a
unit test that was updated to match.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .build import DemoResult, build
from .engine import (
    EXISTING_DAMAGE,
    MISSING_ITEM,
    NEEDS_REVIEW,
    NEW_DAMAGE,
    RESOLVED,
)
from .model import format_ts

TRL1042_PRE = "INS-2026-11-11-TRL1042-PRE"
TRL1042_POST = "INS-2026-11-12-TRL1042-POST"
EXPECTED_FAILURES = ("TRL-BRK-02", "TRL-BED-07", "TRL-LGT-04")
EXPECTED_NOT_INSPECTED = ("EQ-6604", "EQ-7711", "TRL-3120")
REGULATORY = re.compile(
    r"\b(regulat\w*|complian(?:ce\s+(?:with|to)|t)|certified|compliant|FMCSA|49\s*CFR|"
    r"required by (?:law|regulation))\b",
    re.IGNORECASE,
)
CITATION = re.compile(
    r"\[source:\s*https?://|no compliance or regulatory claim|makes no claim about what any regulator requires",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _norm(text: str) -> str:
    return " ".join(text.split())


def _check(name: str, ok: bool, detail: str) -> Check:
    return Check(name=name, ok=bool(ok), detail=detail)


def run_checks(result: DemoResult) -> list[Check]:
    registry = result.registry
    report = result.report
    checks: list[Check] = []

    # 1. The seed's pre-use inspection passes every item.
    pre = next(i for i in registry.inspections if i.id == TRL1042_PRE)
    post = next(i for i in registry.inspections if i.id == TRL1042_POST)
    failed_pre = [r.item_id for r in pre.results if r.state != "pass"]
    checks.append(
        _check(
            "seed.pre_use_passes",
            not failed_pre,
            f"{len(pre.results)} items inspected at pre-use, {len(failed_pre)} not passing",
        )
    )

    # 2. The post-use inspection fails exactly the three named items.
    failed_post = sorted(r.item_id for r in post.results if r.state == "fail")
    checks.append(
        _check(
            "seed.post_use_fails_three_items",
            failed_post == sorted(EXPECTED_FAILURES),
            f"failed: {', '.join(failed_post) if failed_post else 'none'}",
        )
    )

    # 3. The comparison names those three as new damage, all resolved.
    comparison = report.comparison_for("TRL-1042")
    assert comparison is not None, "TRL-1042 should have a complete pre/post pair"
    new_items = sorted(f.item_id for f in comparison.new_damage)
    resolved = all(f.status == RESOLVED for f in comparison.new_damage)
    checks.append(
        _check(
            "engine.three_items_reported_as_new_damage",
            new_items == sorted(EXPECTED_FAILURES) and resolved,
            f"new damage: {', '.join(new_items)} — {'all resolved' if resolved else 'NOT all resolved'}",
        )
    )

    checks.append(
        _check(
            "engine.newly_unroadworthy_names_critical_item",
            comparison.newly_unroadworthy == ("TRL-BRK-02",)
            and comparison.recommended_status == "out_of_service",
            f"newly unroadworthy: {', '.join(comparison.newly_unroadworthy) or 'none'}; "
            f"status {comparison.recommended_status}",
        )
    )

    # 4. Existing damage is never reported as new.
    existing_comparison = report.comparison_for("TRL-2087")
    assert existing_comparison is not None
    existing = [f for f in existing_comparison.findings if f.category == EXISTING_DAMAGE]
    new_for_existing_asset = [
        f for f in existing_comparison.findings if f.category == NEW_DAMAGE and f.item_id == "TRL-BED-07"
    ]
    checks.append(
        _check(
            "engine.existing_damage_is_never_new",
            len(existing) == 1
            and existing[0].item_id == "TRL-BED-07"
            and not new_for_existing_asset
            and "sha256" in existing[0].basis,
            f"TRL-BED-07 classified {existing[0].category if existing else 'missing'}, "
            f"{len(new_for_existing_asset)} new-damage claims for it",
        )
    )

    # 5. Every finding resolves to an item id and a photo, or says NEEDS REVIEW.
    bad_resolution: list[str] = []
    for asset_comparison in report.comparisons:
        for finding in asset_comparison.findings:
            if not finding.item_id:
                bad_resolution.append(f"{finding.id}: no item id")
                continue
            if finding.status == RESOLVED:
                photos = [p for p in finding.photos_post + finding.photos_pre if p.available and p.sha256]
                digests_ok = all(re.fullmatch(r"[0-9a-f]{64}", p.sha256 or "") for p in photos)
                if not photos or not digests_ok:
                    bad_resolution.append(f"{finding.id}: resolved without usable photo evidence")
            elif finding.status != NEEDS_REVIEW or not finding.reason:
                bad_resolution.append(f"{finding.id}: unresolved without a reason")
    checks.append(
        _check(
            "findings.resolve_to_item_plus_photo_or_needs_review",
            not bad_resolution,
            f"{report.stats.findings_resolved} resolved with photo hashes, "
            f"{report.stats.findings_needs_review} NEEDS REVIEW"
            + (f" — problems: {'; '.join(bad_resolution)}" if bad_resolution else ""),
        )
    )

    # 6. The two unresolved cases are named and explained.
    eq = report.comparison_for("EQ-5510")
    assert eq is not None
    by_item = {f.item_id: f for f in eq.findings}
    hyd = by_item.get("EQ-HYD-01")
    pin = by_item.get("EQ-PIN-03")
    checks.append(
        _check(
            "findings.missing_evidence_becomes_needs_review",
            hyd is not None
            and hyd.status == NEEDS_REVIEW
            and "no photo" in (hyd.reason or "").lower()
            and pin is not None
            and pin.status == NEEDS_REVIEW
            and pin.category == MISSING_ITEM,
            "EQ-HYD-01: " + (hyd.reason if hyd else "finding missing")
            + f"; EQ-PIN-03: {'MISSING_ITEM' if pin else 'finding missing'}",
        )
    )

    # 7. The asset with no post-use inspection is in the not-inspected list.
    not_inspected_ids = tuple(entry.asset.id for entry in report.not_inspected)
    reasons = {entry.asset.id: entry.reason for entry in report.not_inspected}
    checks.append(
        _check(
            "engine.one_sided_assets_are_not_inspected",
            sorted(not_inspected_ids) == sorted(EXPECTED_NOT_INSPECTED)
            and "no post-use return inspection" in reasons.get("EQ-6604", "")
            and "no pre-use baseline" in reasons.get("EQ-7711", "")
            and "no inspection record" in reasons.get("TRL-3120", ""),
            f"not inspected: {', '.join(not_inspected_ids)}",
        )
    )

    # 8. Every report is one page.
    wrong_pages = [
        f"{rendered.filename}={rendered.page_count}"
        for rendered in result.rendered
        if rendered.page_count != 1
    ]
    checks.append(
        _check(
            "pdf.every_report_is_one_page",
            not wrong_pages,
            f"{len(result.rendered)} reports, all exactly 1 page"
            if not wrong_pages
            else "; ".join(wrong_pages),
        )
    )

    # 9. The TRL-1042 PDF names every item, the inspector and the timestamp.
    rendered = result.report_for("TRL-1042")
    text = _norm(rendered.text)
    missing_bits: list[str] = []
    for item_id, label in (
        ("TRL-BRK-02", "Air brake system leak-down within limit"),
        ("TRL-LGT-04", "Rear marker lamps, brake lights and turn signals"),
        ("TRL-BED-07", "Deck boards, cross-members and fasteners"),
    ):
        if item_id not in text:
            missing_bits.append(item_id)
        if label not in text:
            missing_bits.append(f"label {item_id}")
    for required in (
        "Marcus Hale",
        format_ts(post.completed_at),
        "RESOLVED",
        "item + photo",
    ):
        if required not in text:
            missing_bits.append(required)
    checks.append(
        _check(
            "pdf.trl_1042_names_items_inspector_timestamp",
            not missing_bits,
            "all three items, labels, inspector Marcus Hale, timestamp "
            + format_ts(post.completed_at)
            if not missing_bits
            else "missing: " + ", ".join(missing_bits),
        )
    )

    # 10. The not-inspected list appears in the PDFs, including the missing post-use asset.
    fleet = result.report_for("FLEET")
    per_asset = result.report_for("TRL-1042")
    for label, rendered_report in (("fleet", fleet), ("TRL-1042", per_asset)):
        body = _norm(rendered_report.text)
        missing = [asset_id for asset_id in EXPECTED_NOT_INSPECTED if asset_id not in body]
        checks.append(
            _check(
                f"pdf.{label}_carries_not_inspected_list",
                not missing and "Not inspected this period" in body if label == "fleet" else not missing,
                f"{len(EXPECTED_NOT_INSPECTED) - len(missing)}/3 assets listed"
                + (f" — missing {', '.join(missing)}" if missing else ""),
            )
        )

    # 11. The seeded status for every comparable asset agrees with the engine.
    mismatched = [
        f"{comparison.asset.id}: seed {registry.asset(comparison.asset.id).status} vs engine {comparison.recommended_status}"
        for comparison in report.comparisons
        if registry.asset(comparison.asset.id).status != comparison.recommended_status
    ]
    checks.append(
        _check(
            "seed.status_matches_recommendation",
            not mismatched,
            "all comparable assets agree" if not mismatched else "; ".join(mismatched),
        )
    )

    # 12. The same inputs produce byte-identical evidence.
    with tempfile.TemporaryDirectory(prefix="fleet-determinism-") as temp:
        rebuilt = build(out_dir=Path(temp), now=report.generated_at, write=False)
        same_pdf = rebuilt.report_for("TRL-1042").pdf_bytes == rendered.pdf_bytes
        same_manifest = rebuilt.manifest == result.manifest
    checks.append(
        _check(
            "evidence.deterministic",
            same_pdf and same_manifest,
            "TRL-1042 PDF and all photo hashes identical on a second run",
        )
    )

    # 13. No uncited regulatory claim in the copy or the rendered reports.
    readme = Path(__file__).resolve().parent.parent / "README.md"
    lines: list[str] = []
    if readme.exists():
        lines.extend(f"README: {line}" for line in readme.read_text(encoding="utf-8").splitlines())
    for rendered_report in result.rendered:
        lines.extend(f"{rendered_report.filename}: {chunk}" for chunk in rendered_report.text.splitlines())
    illegal = [
        line
        for line in lines
        if REGULATORY.search(line) and not CITATION.search(line)
    ]
    checks.append(
        _check(
            "copy.no_uncited_regulatory_claim",
            not illegal,
            "no uncited regulatory claim found"
            if not illegal
            else "uncited: " + " | ".join(illegal[:3]),
        )
    )

    return checks


def format_checks(checks: list[Check]) -> str:
    width = max(len(check.name) for check in checks)
    lines = []
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        detail = check.detail
        if len(detail) > 96:
            detail = detail[:93] + "..."
        lines.append(f"{status}  {check.name.ljust(width)}  {detail}")
    passed = sum(1 for check in checks if check.ok)
    lines.append("")
    lines.append(f"verification: {passed}/{len(checks)} checks pass")
    return "\n".join(lines)
