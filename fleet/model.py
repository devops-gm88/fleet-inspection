"""Typed records and seed loading for the Fleet & Asset Inspection demo.

Everything the product knows comes from three JSON files in ``data/``. This
module turns them into immutable records and refuses a seed that breaks its own
rules: duplicate ids, dangling references, an inspection whose template does not
match its asset class, a graded item without a grade, a photo attached to an
inspection that does not exist. The comparison engine downstream is allowed to
assume the seed is consistent because this module proves it first.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SIDES = ("pre_use", "post_use")
ASSET_CLASSES = ("power_unit", "trailer", "equipment")
OWNERSHIP = ("owned", "leased", "rented")
BOOLEAN_STATES = ("pass", "fail", "not_checked")


class SeedError(ValueError):
    """The seed data contradicts itself. Raised with a path to the bad record."""


def parse_ts(value: str, where: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SeedError(f"{where}: {value!r} is not an ISO 8601 timestamp ({exc})") from exc
    if ts.tzinfo is None:
        raise SeedError(f"{where}: {value!r} has no UTC offset")
    return ts


def format_ts(ts: datetime) -> str:
    """Render a timestamp the way both the PDF and the JSON evidence use it.

    The numeric offset is printed instead of a zone abbreviation because an
    abbreviation can lie about which offset was actually recorded.
    """
    offset = ts.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"{ts.strftime('%d %b %Y %H:%M')} ({offset})"


def format_date(ts: datetime) -> str:
    return ts.strftime("%d %b %Y")


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    kind: str


@dataclass(frozen=True)
class Site:
    id: str
    name: str


@dataclass(frozen=True)
class Inspector:
    id: str
    name: str
    role: str


@dataclass(frozen=True)
class Period:
    id: str
    label: str
    starts_at: datetime
    ends_at: datetime

    def contains(self, ts: datetime) -> bool:
        return self.starts_at <= ts < self.ends_at


@dataclass(frozen=True)
class Asset:
    id: str
    asset_class: str
    serial: str
    make: str
    model: str
    year: int
    ownership: str
    status: str
    site_id: str
    assigned_to: str
    note: str

    @property
    def label(self) -> str:
        return f"{self.make} {self.model} ({self.year})"


@dataclass(frozen=True)
class TemplateItem:
    id: str
    label: str
    kind: str  # "boolean" | "graded"
    critical: bool
    guidance: str
    scale_min: int | None = None
    scale_max: int | None = None
    fail_at_or_below: int | None = None
    change_threshold: int | None = None

    @property
    def graded(self) -> bool:
        return self.kind == "graded"

    def state_for(self, grade: int | None) -> str:
        if not self.graded:
            raise ValueError(f"{self.id} is not a graded item")
        if grade is None:
            return "not_checked"
        return "fail" if grade <= (self.fail_at_or_below or 0) else "pass"


@dataclass(frozen=True)
class Template:
    id: str
    asset_class: str
    name: str
    version: int
    items: tuple[TemplateItem, ...]

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.items)


@dataclass(frozen=True)
class Result:
    item_id: str
    state: str  # pass | fail | not_checked (graded items derive state from grade)
    grade: int | None
    note: str


@dataclass(frozen=True)
class Photo:
    id: str
    inspection_id: str
    item_id: str
    view: str
    defect: str
    captured_at: datetime
    caption: str


@dataclass(frozen=True)
class Inspection:
    id: str
    asset_id: str
    template_id: str
    template_version: int
    side: str
    cycle_ref: str
    inspector_id: str
    started_at: datetime
    completed_at: datetime
    results: tuple[Result, ...]

    def result_for(self, item_id: str) -> Result | None:
        for result in self.results:
            if result.item_id == item_id:
                return result
        return None


@dataclass(frozen=True)
class Registry:
    company: Company
    sites: dict[str, Site]
    inspectors: dict[str, Inspector]
    period: Period
    assets: dict[str, Asset]
    templates: dict[str, Template]  # keyed by class
    inspections: tuple[Inspection, ...]
    photos: dict[str, Photo]

    def asset(self, asset_id: str) -> Asset:
        return self.assets[asset_id]

    def template_for(self, asset: Asset) -> Template:
        return self.templates[asset.asset_class]

    def inspector(self, inspector_id: str) -> Inspector:
        return self.inspectors[inspector_id]

    def site_name(self, site_id: str) -> str:
        return self.sites[site_id].name

    def photos_for(self, inspection_id: str, item_id: str | None = None) -> tuple[Photo, ...]:
        found = [
            photo
            for photo in self.photos.values()
            if photo.inspection_id == inspection_id
            and (item_id is None or photo.item_id == item_id)
        ]
        return tuple(sorted(found, key=lambda p: (p.captured_at, p.id)))

    def inspections_for(self, asset_id: str) -> tuple[Inspection, ...]:
        return tuple(i for i in self.inspections if i.asset_id == asset_id)


def _require(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise SeedError(f"{where}: missing required field {key!r}")
    return mapping[key]


def _unique(records: Iterable[Any], attr: str, where: str) -> None:
    seen: dict[str, int] = {}
    for index, record in enumerate(records):
        value = getattr(record, attr)
        if value in seen:
            raise SeedError(f"{where}: duplicate {attr} {value!r} (records {seen[value]} and {index})")
        seen[value] = index


def _check_asset(record: dict[str, Any], index: int, sites: set[str], inspectors: set[str]) -> Asset:
    where = f"assets[{index}]"
    asset = Asset(
        id=str(_require(record, "id", where)),
        asset_class=str(_require(record, "class", where)),
        serial=str(_require(record, "serial", where)),
        make=str(_require(record, "make", where)),
        model=str(_require(record, "model", where)),
        year=int(_require(record, "year", where)),
        ownership=str(_require(record, "ownership", where)),
        status=str(_require(record, "status", where)),
        site_id=str(_require(record, "site_id", where)),
        assigned_to=str(_require(record, "assigned_to", where)),
        note=str(record.get("note", "")),
    )
    if asset.asset_class not in ASSET_CLASSES:
        raise SeedError(f"{where}: class {asset.asset_class!r} is not one of {ASSET_CLASSES}")
    if asset.ownership not in OWNERSHIP:
        raise SeedError(f"{where}: ownership {asset.ownership!r} is not one of {OWNERSHIP}")
    if asset.site_id not in sites:
        raise SeedError(f"{where}: site_id {asset.site_id!r} is not a seeded site")
    if asset.assigned_to not in inspectors:
        raise SeedError(f"{where}: assigned_to {asset.assigned_to!r} is not a seeded inspector")
    if not asset.serial:
        raise SeedError(f"{where}: serial is empty")
    return asset


def _check_template_item(record: dict[str, Any], where: str) -> TemplateItem:
    item = TemplateItem(
        id=str(_require(record, "id", where)),
        label=str(_require(record, "label", where)),
        kind=str(_require(record, "kind", where)),
        critical=bool(record.get("critical", False)),
        guidance=str(record.get("guidance", "")),
        scale_min=record.get("scale_min"),
        scale_max=record.get("scale_max"),
        fail_at_or_below=record.get("fail_at_or_below"),
        change_threshold=record.get("change_threshold"),
    )
    if item.kind not in ("boolean", "graded"):
        raise SeedError(f"{where}: kind {item.kind!r} is not 'boolean' or 'graded'")
    if item.graded:
        if not isinstance(item.scale_min, int) or not isinstance(item.scale_max, int):
            raise SeedError(f"{where}: graded item needs integer scale_min and scale_max")
        if item.scale_min >= item.scale_max:
            raise SeedError(f"{where}: scale_min must be below scale_max")
        if not isinstance(item.fail_at_or_below, int):
            raise SeedError(f"{where}: graded item needs fail_at_or_below")
        if not item.scale_min <= item.fail_at_or_below < item.scale_max:
            raise SeedError(f"{where}: fail_at_or_below is outside the grade scale")
        if not isinstance(item.change_threshold, int) or item.change_threshold < 1:
            raise SeedError(f"{where}: graded item needs change_threshold >= 1")
    if not item.label.strip():
        raise SeedError(f"{where}: label is empty")
    return item


def _check_template(record: dict[str, Any], index: int) -> Template:
    where = f"templates[{index}]"
    raw_items = _require(record, "items", where)
    if not isinstance(raw_items, list) or not raw_items:
        raise SeedError(f"{where}: items must be a non-empty list")
    items = tuple(_check_template_item(raw, f"{where}.items[{n}]") for n, raw in enumerate(raw_items))
    _unique(items, "id", f"{where}.items")
    template = Template(
        id=str(_require(record, "id", where)),
        asset_class=str(_require(record, "class", where)),
        name=str(_require(record, "name", where)),
        version=int(_require(record, "version", where)),
        items=items,
    )
    if template.asset_class not in ASSET_CLASSES:
        raise SeedError(f"{where}: class {template.asset_class!r} is not one of {ASSET_CLASSES}")
    if template.version < 1:
        raise SeedError(f"{where}: version must be >= 1")
    return template


def _check_inspection(
    record: dict[str, Any],
    index: int,
    assets: dict[str, Asset],
    templates_by_id: dict[str, Template],
    inspectors: set[str],
) -> Inspection:
    where = f"inspections[{index}]"
    inspection = Inspection(
        id=str(_require(record, "id", where)),
        asset_id=str(_require(record, "asset_id", where)),
        template_id=str(_require(record, "template_id", where)),
        template_version=int(_require(record, "template_version", where)),
        side=str(_require(record, "side", where)),
        cycle_ref=str(_require(record, "cycle_ref", where)),
        inspector_id=str(_require(record, "inspector_id", where)),
        started_at=parse_ts(str(_require(record, "started_at", where)), where + ".started_at"),
        completed_at=parse_ts(str(_require(record, "completed_at", where)), where + ".completed_at"),
        results=(),
    )
    if inspection.asset_id not in assets:
        raise SeedError(f"{where}: asset_id {inspection.asset_id!r} does not exist")
    if inspection.template_id not in templates_by_id:
        raise SeedError(f"{where}: template_id {inspection.template_id!r} does not exist")
    template = templates_by_id[inspection.template_id]
    if template.asset_class != assets[inspection.asset_id].asset_class:
        raise SeedError(
            f"{where}: template {template.id!r} is for class {template.asset_class!r} "
            f"but asset has class {assets[inspection.asset_id].asset_class!r}"
        )
    if inspection.template_version != template.version:
        raise SeedError(
            f"{where}: template_version {inspection.template_version} does not match "
            f"seeded template {template.id} version {template.version}"
        )
    if inspection.side not in SIDES:
        raise SeedError(f"{where}: side {inspection.side!r} is not one of {SIDES}")
    if not inspection.cycle_ref.strip():
        raise SeedError(f"{where}: cycle_ref is empty")
    if inspection.inspector_id not in inspectors:
        raise SeedError(f"{where}: inspector_id {inspection.inspector_id!r} is not seeded")
    if inspection.completed_at < inspection.started_at:
        raise SeedError(f"{where}: completed_at is before started_at")

    raw_results = record.get("results", [])
    if not isinstance(raw_results, list) or not raw_results:
        raise SeedError(f"{where}: results must be a non-empty list")
    results: list[Result] = []
    for n, raw in enumerate(raw_results):
        rwhere = f"{where}.results[{n}]"
        item_id = str(_require(raw, "item_id", rwhere))
        if item_id not in template.item_ids:
            raise SeedError(f"{rwhere}: item_id {item_id!r} is not in template {template.id}")
        item = next(i for i in template.items if i.id == item_id)
        state = str(_require(raw, "state", rwhere))
        grade = raw.get("grade")
        note = str(raw.get("note", ""))
        if state not in BOOLEAN_STATES:
            raise SeedError(f"{rwhere}: state {state!r} is not one of {BOOLEAN_STATES}")
        if item.graded:
            if state == "not_checked":
                if grade is not None:
                    raise SeedError(f"{rwhere}: not_checked graded item must not carry a grade")
            else:
                if not isinstance(grade, int):
                    raise SeedError(f"{rwhere}: graded item {item_id} needs an integer grade")
                if not item.scale_min <= grade <= item.scale_max:
                    raise SeedError(f"{rwhere}: grade {grade} is outside {item.scale_min}-{item.scale_max}")
                if state != item.state_for(grade):
                    raise SeedError(
                        f"{rwhere}: state {state!r} contradicts grade {grade} for graded item {item_id}"
                    )
        else:
            if grade is not None:
                raise SeedError(f"{rwhere}: boolean item {item_id} must not carry a grade")
        results.append(Result(item_id=item_id, state=state, grade=grade, note=note))
    _unique(results, "item_id", f"{where}.results")
    return replace(inspection, results=tuple(results))


def _check_photo(record: dict[str, Any], index: int, inspections: dict[str, Inspection], templates_by_id: dict[str, Template]) -> Photo:
    where = f"photos[{index}]"
    photo = Photo(
        id=str(_require(record, "id", where)),
        inspection_id=str(_require(record, "inspection_id", where)),
        item_id=str(_require(record, "item_id", where)),
        view=str(_require(record, "view", where)),
        defect=str(record.get("defect", "none")),
        captured_at=parse_ts(str(_require(record, "captured_at", where)), where + ".captured_at"),
        caption=str(record.get("caption", "")),
    )
    if photo.inspection_id not in inspections:
        raise SeedError(f"{where}: inspection_id {photo.inspection_id!r} does not exist")
    inspection = inspections[photo.inspection_id]
    template = templates_by_id[inspection.template_id]
    if photo.item_id not in template.item_ids:
        raise SeedError(f"{where}: item_id {photo.item_id!r} is not in template {template.id}")
    if not inspection.started_at <= photo.captured_at <= inspection.completed_at:
        raise SeedError(
            f"{where}: captured_at is outside inspection {inspection.id} "
            f"({inspection.started_at.isoformat()} .. {inspection.completed_at.isoformat()})"
        )
    return photo


def load_registry(data_dir: Path) -> Registry:
    assets_path = data_dir / "assets.json"
    templates_path = data_dir / "templates.json"
    inspections_path = data_dir / "inspections.json"
    for path in (assets_path, templates_path, inspections_path):
        if not path.exists():
            raise SeedError(f"seed file not found: {path}")

    assets_doc = json.loads(assets_path.read_text(encoding="utf-8"))
    templates_doc = json.loads(templates_path.read_text(encoding="utf-8"))
    inspections_doc = json.loads(inspections_path.read_text(encoding="utf-8"))

    company = Company(
        id=str(_require(assets_doc, "company", "assets.json").get("id", "company")),
        name=str(_require(assets_doc["company"], "name", "assets.json.company")),
        kind=str(assets_doc["company"].get("kind", "operator")),
    )
    sites = {
        str(_require(raw, "id", f"sites[{n}]")): Site(str(raw["id"]), str(_require(raw, "name", f"sites[{n}]")))
        for n, raw in enumerate(assets_doc.get("sites", []))
    }
    if not sites:
        raise SeedError("assets.json: at least one site is required")
    inspectors = {}
    for n, raw in enumerate(assets_doc.get("inspectors", [])):
        where = f"inspectors[{n}]"
        inspector = Inspector(
            id=str(_require(raw, "id", where)),
            name=str(_require(raw, "name", where)),
            role=str(_require(raw, "role", where)),
        )
        if inspector.id in inspectors:
            raise SeedError(f"{where}: duplicate inspector id {inspector.id!r}")
        inspectors[inspector.id] = inspector
    if not inspectors:
        raise SeedError("assets.json: at least one inspector is required")

    period_raw = _require(assets_doc, "period", "assets.json")
    period = Period(
        id=str(_require(period_raw, "id", "period")),
        label=str(_require(period_raw, "label", "period")),
        starts_at=parse_ts(str(_require(period_raw, "starts_at", "period")), "period.starts_at"),
        ends_at=parse_ts(str(_require(period_raw, "ends_at", "period")), "period.ends_at"),
    )
    if period.ends_at <= period.starts_at:
        raise SeedError("period.ends_at must be after period.starts_at")

    raw_assets = _require(assets_doc, "assets", "assets.json")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise SeedError("assets.json: assets must be a non-empty list")
    assets = [
        _check_asset(raw, n, set(sites), set(inspectors))
        for n, raw in enumerate(raw_assets)
    ]
    _unique(assets, "id", "assets.json.assets")

    raw_templates = _require(templates_doc, "templates", "templates.json")
    if not isinstance(raw_templates, list) or not raw_templates:
        raise SeedError("templates.json: templates must be a non-empty list")
    templates = [_check_template(raw, n) for n, raw in enumerate(raw_templates)]
    _unique(templates, "id", "templates.json.templates")
    templates_by_class: dict[str, Template] = {}
    for template in templates:
        if template.asset_class in templates_by_class:
            raise SeedError(
                f"templates.json: class {template.asset_class!r} has more than one template "
                f"({templates_by_class[template.asset_class].id} and {template.id})"
            )
        templates_by_class[template.asset_class] = template
    templates_by_id = {template.id: template for template in templates}
    for asset in assets:
        if asset.asset_class not in templates_by_class:
            raise SeedError(f"assets.json: asset {asset.id} has class {asset.asset_class!r} with no template")

    raw_inspections = _require(inspections_doc, "inspections", "inspections.json")
    if not isinstance(raw_inspections, list) or not raw_inspections:
        raise SeedError("inspections.json: inspections must be a non-empty list")
    inspections = [
        _check_inspection(raw, n, {a.id: a for a in assets}, templates_by_id, set(inspectors))
        for n, raw in enumerate(raw_inspections)
    ]
    _unique(inspections, "id", "inspections.json.inspections")
    inspections_by_id = {inspection.id: inspection for inspection in inspections}

    raw_photos = inspections_doc.get("photos", [])
    if not isinstance(raw_photos, list):
        raise SeedError("inspections.json: photos must be a list")
    photos = [_check_photo(raw, n, inspections_by_id, templates_by_id) for n, raw in enumerate(raw_photos)]
    _unique(photos, "id", "inspections.json.photos")

    # A cycle is the pairing key. It may not contain two records of one side:
    # that would make "the" pre-use record ambiguous, and the safe answer is to
    # refuse the seed rather than silently pick one.
    cycle_sides: dict[tuple[str, str], set[str]] = {}
    for inspection in inspections:
        key = (inspection.asset_id, inspection.cycle_ref)
        sides = cycle_sides.setdefault(key, set())
        if inspection.side in sides:
            raise SeedError(
                f"inspections.json: cycle {inspection.cycle_ref!r} for asset {inspection.asset_id!r} "
                f"has more than one {inspection.side} record"
            )
        sides.add(inspection.side)

    return Registry(
        company=company,
        sites=sites,
        inspectors=inspectors,
        period=period,
        assets={asset.id: asset for asset in assets},
        templates=templates_by_class,
        inspections=tuple(inspections),
        photos={photo.id: photo for photo in photos},
    )


def photo_view_is_comparable(pre: Photo | None, post: Photo | None) -> bool:
    """Before/after photos are only a fair pair when they share a view."""
    return bool(pre and post and pre.view == post.view)


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
