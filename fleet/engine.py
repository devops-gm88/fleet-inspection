"""The comparison engine — the product.

One asset, one cycle, a pre-use inspection and a post-use inspection of the same
template. The engine classifies every template item, then turns the classifications
that matter into findings. A finding is only allowed to resolve if it points at a
template item *and* at a photo that exists; anything else becomes ``NEEDS REVIEW``.

The classification rule that matters most: a defect that was already recorded at
pre-use is ``existing_damage`` and is never ``new_damage``. Charging a customer for
damage they arrived with is the one error this product cannot make, so the seed and
the tests both pin it down.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .model import (
    Asset,
    Company,
    Inspection,
    Period,
    Photo,
    Registry,
    TemplateItem,
    format_date,
    format_ts,
)

# Classification of one template item across the pair.
NEW_DAMAGE = "new_damage"
CONDITION_DEGRADED = "condition_degraded"
EXISTING_DAMAGE = "existing_damage"
CLEARED = "cleared"
MISSING_ITEM = "missing_item"
MISSING_BASELINE = "missing_baseline"
UNCHANGED = "unchanged"

FINDING_CATEGORIES = (
    NEW_DAMAGE,
    CONDITION_DEGRADED,
    EXISTING_DAMAGE,
    MISSING_ITEM,
    MISSING_BASELINE,
)

CATEGORY_LABELS = {
    NEW_DAMAGE: "New damage",
    CONDITION_DEGRADED: "Condition degraded",
    EXISTING_DAMAGE: "Existing damage (pre-existing)",
    MISSING_ITEM: "No post-use result",
    MISSING_BASELINE: "No pre-use baseline",
    CLEARED: "Cleared",
    UNCHANGED: "Unchanged",
}

RESOLVED = "resolved"
NEEDS_REVIEW = "needs_review"


class PhotoEvidence(Protocol):
    """Anything that can say whether a photo exists and what its digest is."""

    def ref(self, photo: Photo) -> "PhotoRef":
        ...


@dataclass(frozen=True)
class PhotoRef:
    photo_id: str
    view: str
    caption: str
    captured_at: datetime
    available: bool
    sha256: str | None
    filename: str

    @property
    def short_digest(self) -> str:
        return (self.sha256 or "")[:12]


class MissingEvidence:
    """Evidence source that resolves every photo to 'not available'.

    Used when the engine is asked to compare without a photo store. Every finding
    that would need a photo then becomes NEEDS REVIEW instead of being dropped.
    """

    def ref(self, photo: Photo) -> PhotoRef:
        return PhotoRef(
            photo_id=photo.id,
            view=photo.view,
            caption=photo.caption,
            captured_at=photo.captured_at,
            available=False,
            sha256=None,
            filename=f"{photo.id}.png",
        )


@dataclass(frozen=True)
class ItemDiff:
    item_id: str
    label: str
    kind: str
    critical: bool
    category: str
    pre_state: str  # pass | fail | not_checked | missing
    post_state: str  # pass | fail | not_checked | missing
    pre_grade: int | None
    post_grade: int | None
    note_pre: str
    note_post: str
    photos_pre: tuple[PhotoRef, ...]
    photos_post: tuple[PhotoRef, ...]
    newly_unroadworthy: bool

    @property
    def changed(self) -> bool:
        return self.category not in (UNCHANGED,)


@dataclass(frozen=True)
class Finding:
    id: str
    asset_id: str
    cycle_ref: str
    category: str
    item_id: str
    label: str
    kind: str
    critical: bool
    pre_state: str
    post_state: str
    pre_grade: int | None
    post_grade: int | None
    note_pre: str
    note_post: str
    photos_pre: tuple[PhotoRef, ...]
    photos_post: tuple[PhotoRef, ...]
    status: str
    basis: str
    reason: str | None
    newly_unroadworthy: bool

    @property
    def needs_review(self) -> bool:
        return self.status == NEEDS_REVIEW

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]

    @property
    def evidence_photo_ids(self) -> tuple[str, ...]:
        return tuple(p.photo_id for p in self.photos_pre + self.photos_post)


@dataclass(frozen=True)
class SideSummary:
    inspection_id: str
    inspector_name: str
    inspector_role: str
    started_at: datetime
    completed_at: datetime

    @property
    def started_label(self) -> str:
        return format_ts(self.started_at)


@dataclass(frozen=True)
class AssetComparison:
    asset: Asset
    cycle_ref: str
    pre: SideSummary
    post: SideSummary
    diffs: tuple[ItemDiff, ...]
    findings: tuple[Finding, ...]
    newly_unroadworthy: tuple[str, ...]
    recommended_status: str

    @property
    def findings_resolved(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.status == RESOLVED)

    @property
    def findings_needs_review(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.status == NEEDS_REVIEW)

    @property
    def new_damage(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.category == NEW_DAMAGE)

    @property
    def existing_damage(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.category == EXISTING_DAMAGE)

    @property
    def condition_degraded(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.category == CONDITION_DEGRADED)

    @property
    def cleared(self) -> tuple[ItemDiff, ...]:
        return tuple(d for d in self.diffs if d.category == CLEARED)

    @property
    def unchanged(self) -> tuple[ItemDiff, ...]:
        return tuple(d for d in self.diffs if d.category == UNCHANGED)

    @property
    def inspected_items(self) -> int:
        return sum(
            1
            for d in self.diffs
            if d.pre_state in ("pass", "fail") and d.post_state in ("pass", "fail")
        )


@dataclass(frozen=True)
class NotInspected:
    asset: Asset
    cycle_ref: str
    present_sides: tuple[str, ...]
    reason: str
    records: tuple[Inspection, ...]
    last_inspection_id: str | None
    last_inspection_at: datetime | None


@dataclass(frozen=True)
class Stats:
    assets_total: int
    comparable_assets: int
    not_inspected_assets: int
    findings_total: int
    findings_resolved: int
    findings_needs_review: int
    new_damage: int
    existing_damage: int
    condition_degraded: int
    missing_items: int
    cleared_items: int
    newly_unroadworthy: int
    photos_used: int
    items_compared: int


@dataclass(frozen=True)
class ComparisonReport:
    company: Company
    period: Period
    generated_at: datetime
    comparisons: tuple[AssetComparison, ...]
    not_inspected: tuple[NotInspected, ...]
    stats: Stats

    def comparison_for(self, asset_id: str) -> AssetComparison | None:
        for comparison in self.comparisons:
            if comparison.asset.id == asset_id:
                return comparison
        return None

    def not_inspected_for(self, asset_id: str) -> NotInspected | None:
        for entry in self.not_inspected:
            if entry.asset.id == asset_id:
                return entry
        return None

    @property
    def not_inspected_assets(self) -> tuple[Asset, ...]:
        return tuple(entry.asset for entry in self.not_inspected)


def _side_summary(registry: Registry, inspection: Inspection) -> SideSummary:
    inspector = registry.inspector(inspection.inspector_id)
    return SideSummary(
        inspection_id=inspection.id,
        inspector_name=inspector.name,
        inspector_role=inspector.role,
        started_at=inspection.started_at,
        completed_at=inspection.completed_at,
    )


def _state_of(item: TemplateItem, result) -> str:
    if result is None:
        return "missing"
    if result.state == "not_checked":
        return "not_checked"
    if item.graded:
        return item.state_for(result.grade)
    return result.state


def _photos_of(registry: Registry, evidence: PhotoEvidence, inspection: Inspection, item_id: str) -> tuple[PhotoRef, ...]:
    return tuple(evidence.ref(photo) for photo in registry.photos_for(inspection.id, item_id))


def classify(item: TemplateItem, pre_state: str, post_state: str, pre_grade: int | None, post_grade: int | None) -> str:
    """Classify one item. Exposed as a pure function so it can be tested directly."""
    usable_pre = pre_state in ("pass", "fail")
    usable_post = post_state in ("pass", "fail")
    if not usable_post:
        return MISSING_ITEM
    if not usable_pre:
        return MISSING_BASELINE
    if pre_state == "fail" and post_state == "fail":
        return EXISTING_DAMAGE
    if pre_state == "fail" and post_state == "pass":
        return CLEARED
    if pre_state == "pass" and post_state == "fail":
        return NEW_DAMAGE
    if item.graded and pre_grade is not None and post_grade is not None:
        delta = post_grade - pre_grade
        if delta <= -item.change_threshold:
            return CONDITION_DEGRADED
    return UNCHANGED


def _resolve_finding(
    category: str,
    item: TemplateItem | None,
    item_id: str,
    evidence: PhotoEvidence,
    photos_pre: tuple[PhotoRef, ...],
    photos_post: tuple[PhotoRef, ...],
) -> tuple[str, str, str | None]:
    """Return (status, basis, reason).

    A finding resolves only when it points at a template item and at the photo
    evidence the category needs. Every failure path returns NEEDS REVIEW with a
    reason that names what is missing.
    """
    if item is None:
        return NEEDS_REVIEW, "", f"item {item_id} does not resolve to an item in the asset class template"

    if category in (MISSING_ITEM, MISSING_BASELINE):
        detail = (
            "no post-use result was recorded for this item"
            if category == MISSING_ITEM
            else "no pre-use result was recorded for this item"
        )
        return NEEDS_REVIEW, "", f"{detail}; the two inspections cannot be compared for it"

    if category == EXISTING_DAMAGE:
        if not photos_pre:
            return (
                NEEDS_REVIEW,
                "",
                "pre-existing damage has no pre-use photo, so it cannot be proved pre-existing",
            )
        if not photos_post:
            return NEEDS_REVIEW, "", "pre-existing damage has no post-use photo to confirm its condition at return"
        unusable = [p.photo_id for p in photos_pre + photos_post if not p.available]
        if unusable:
            return NEEDS_REVIEW, "", "photo file(s) missing or unreadable: " + ", ".join(unusable)
        basis = (
            f"item {item.id} + pre-use photo {photos_pre[0].photo_id} "
            f"(sha256 {photos_pre[0].short_digest}) + post-use photo {photos_post[0].photo_id} "
            f"(sha256 {photos_post[0].short_digest})"
        )
        return RESOLVED, basis, None

    if not photos_post:
        return NEEDS_REVIEW, "", f"no photo is attached to item {item.id} on the post-use inspection"
    unusable = [p.photo_id for p in photos_post if not p.available]
    if unusable:
        return NEEDS_REVIEW, "", "photo file(s) missing or unreadable: " + ", ".join(unusable)
    basis = f"item {item.id} + post-use photo {photos_post[0].photo_id} (sha256 {photos_post[0].short_digest})"
    if photos_pre:
        basis += f" against pre-use photo {photos_pre[0].photo_id} (sha256 {photos_pre[0].short_digest})"
    return RESOLVED, basis, None


def _finding_id(asset_id: str, category: str, item_id: str) -> str:
    return f"FND-{asset_id}-{category.upper().replace('_', '')}-{item_id}"


def _recommended_status(diffs: tuple[ItemDiff, ...], findings: tuple[Finding, ...]) -> str:
    critical_fail = any(
        diff.critical and diff.post_state == "fail" for diff in diffs
    )
    if critical_fail:
        return "out_of_service"
    if any(finding.needs_review for finding in findings):
        return "needs_review"
    if any(diff.category in (NEW_DAMAGE, CONDITION_DEGRADED) for diff in diffs):
        return "release_with_damage"
    return "available"


def compare_cycle(
    registry: Registry,
    evidence: PhotoEvidence,
    asset: Asset,
    pre: Inspection,
    post: Inspection,
) -> AssetComparison:
    template = registry.template_for(asset)
    diffs: list[ItemDiff] = []
    findings: list[Finding] = []
    newly_unroadworthy: list[str] = []

    for item in template.items:
        pre_result = pre.result_for(item.id)
        post_result = post.result_for(item.id)
        pre_state = _state_of(item, pre_result)
        post_state = _state_of(item, post_result)
        pre_grade = pre_result.grade if pre_result else None
        post_grade = post_result.grade if post_result else None
        category = classify(item, pre_state, post_state, pre_grade, post_grade)
        photos_pre = _photos_of(registry, evidence, pre, item.id)
        photos_post = _photos_of(registry, evidence, post, item.id)
        is_newly_unroadworthy = category == NEW_DAMAGE and item.critical
        if is_newly_unroadworthy:
            newly_unroadworthy.append(item.id)

        diff = ItemDiff(
            item_id=item.id,
            label=item.label,
            kind=item.kind,
            critical=item.critical,
            category=category,
            pre_state=pre_state,
            post_state=post_state,
            pre_grade=pre_grade,
            post_grade=post_grade,
            note_pre=pre_result.note if pre_result else "",
            note_post=post_result.note if post_result else "",
            photos_pre=photos_pre,
            photos_post=photos_post,
            newly_unroadworthy=is_newly_unroadworthy,
        )
        diffs.append(diff)

        if category in FINDING_CATEGORIES:
            status, basis, reason = _resolve_finding(
                category, item, item.id, evidence, photos_pre, photos_post
            )
            findings.append(
                Finding(
                    id=_finding_id(asset.id, category, item.id),
                    asset_id=asset.id,
                    cycle_ref=pre.cycle_ref,
                    category=category,
                    item_id=item.id,
                    label=item.label,
                    kind=item.kind,
                    critical=item.critical,
                    pre_state=pre_state,
                    post_state=post_state,
                    pre_grade=pre_grade,
                    post_grade=post_grade,
                    note_pre=diff.note_pre,
                    note_post=diff.note_post,
                    photos_pre=photos_pre,
                    photos_post=photos_post,
                    status=status,
                    basis=basis,
                    reason=reason,
                    newly_unroadworthy=is_newly_unroadworthy,
                )
            )

    diffs_tuple = tuple(diffs)
    findings_tuple = tuple(findings)
    return AssetComparison(
        asset=asset,
        cycle_ref=pre.cycle_ref,
        pre=_side_summary(registry, pre),
        post=_side_summary(registry, post),
        diffs=diffs_tuple,
        findings=findings_tuple,
        newly_unroadworthy=tuple(newly_unroadworthy),
        recommended_status=_recommended_status(diffs_tuple, findings_tuple),
    )


def _not_inspected_reason(period: Period, present_sides: tuple[str, ...], records: tuple[Inspection, ...]) -> str:
    if not records:
        return f"no inspection record at all in {period.label}"
    if present_sides == ("pre_use",):
        latest = max(records, key=lambda i: i.completed_at)
        return (
            f"pre-use inspection recorded {format_date(latest.completed_at)}; "
            "no post-use return inspection in the period, so the return condition is unknown"
        )
    if present_sides == ("post_use",):
        latest = max(records, key=lambda i: i.completed_at)
        return (
            f"post-use inspection recorded {format_date(latest.completed_at)}; "
            "no pre-use baseline in the period, so change cannot be proved"
        )
    return "no complete pre-use/post-use pair in the period"


def compare(
    registry: Registry,
    evidence: PhotoEvidence | None = None,
    generated_at: datetime | None = None,
) -> ComparisonReport:
    evidence = evidence or MissingEvidence()
    period = registry.period

    period_inspections = tuple(i for i in registry.inspections if period.contains(i.started_at))
    by_cycle: dict[tuple[str, str], list[Inspection]] = {}
    for inspection in period_inspections:
        by_cycle.setdefault((inspection.asset_id, inspection.cycle_ref), []).append(inspection)

    comparisons: list[AssetComparison] = []
    not_inspected: list[NotInspected] = []

    for asset in registry.assets.values():
        cycles = sorted(
            {
                cycle_ref
                for (asset_id, cycle_ref) in by_cycle
                if asset_id == asset.id
            }
        )
        if not cycles:
            not_inspected.append(
                NotInspected(
                    asset=asset,
                    cycle_ref="",
                    present_sides=(),
                    reason=_not_inspected_reason(period, (), ()),
                    records=(),
                    last_inspection_id=None,
                    last_inspection_at=None,
                )
            )
            continue
        for cycle_ref in cycles:
            inspections = by_cycle[(asset.id, cycle_ref)]
            pre = next((i for i in inspections if i.side == "pre_use"), None)
            post = next((i for i in inspections if i.side == "post_use"), None)
            if pre is not None and post is not None:
                comparisons.append(compare_cycle(registry, evidence, asset, pre, post))
                continue
            present = tuple(sorted(i.side for i in inspections))
            latest_record = max(inspections, key=lambda i: i.completed_at)
            not_inspected.append(
                NotInspected(
                    asset=asset,
                    cycle_ref=cycle_ref,
                    present_sides=present,
                    reason=_not_inspected_reason(period, present, tuple(inspections)),
                    records=tuple(inspections),
                    last_inspection_id=latest_record.id,
                    last_inspection_at=latest_record.completed_at,
                )
            )

    all_findings = [finding for comparison in comparisons for finding in comparison.findings]
    all_diffs = [diff for comparison in comparisons for diff in comparison.diffs]
    photo_ids = {
        photo.photo_id
        for finding in all_findings
        for photo in finding.photos_pre + finding.photos_post
    }
    stats = Stats(
        assets_total=len(registry.assets),
        comparable_assets=len(comparisons),
        not_inspected_assets=len(not_inspected),
        findings_total=len(all_findings),
        findings_resolved=sum(1 for f in all_findings if f.status == RESOLVED),
        findings_needs_review=sum(1 for f in all_findings if f.status == NEEDS_REVIEW),
        new_damage=sum(1 for f in all_findings if f.category == NEW_DAMAGE),
        existing_damage=sum(1 for f in all_findings if f.category == EXISTING_DAMAGE),
        condition_degraded=sum(1 for f in all_findings if f.category == CONDITION_DEGRADED),
        missing_items=sum(
            1 for f in all_findings if f.category in (MISSING_ITEM, MISSING_BASELINE)
        ),
        cleared_items=sum(1 for d in all_diffs if d.category == CLEARED),
        newly_unroadworthy=sum(1 for c in comparisons for _ in c.newly_unroadworthy),
        photos_used=len(photo_ids),
        items_compared=sum(1 for d in all_diffs if d.pre_state in ("pass", "fail") and d.post_state in ("pass", "fail")),
    )
    return ComparisonReport(
        company=registry.company,
        period=period,
        generated_at=generated_at or datetime.now().astimezone(),
        comparisons=tuple(comparisons),
        not_inspected=tuple(not_inspected),
        stats=stats,
    )
