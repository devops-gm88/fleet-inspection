# Fleet & Asset Inspection (DVIR)

A fleet handover is a comparison, not a checklist. This demo builds the comparison:
the same asset, the same template, a **pre-use** inspection before it goes out and a
**post-use** inspection when it comes back — diffed item by item, with the photo
evidence, the inspector and the timestamp a dispute would need, and an explicit list
of everything that was **not inspected**.

It is aimed at operators who hand over vehicles, trailers and equipment: construction,
transport, equipment hire and field-service fleets. The buyer is the operations or
compliance manager who has to answer "was this damage already there?" — not a software
team.

**This is a demonstration build.** The seven assets, four inspectors, ten inspections,
the operator and the photos are invented. Nothing here is a real yard's data, and the
photos are generated and stamped `DEMO` by the code that ships with the repo.

## One command

```bash
./demo.sh          # or: make demo
```

Python 3.10+, standard library only, no network, no install step. It needs a clean
checkout and nothing else. Time the run yourself with `time ./demo.sh`; it is quick
enough to repeat on every change.

It writes `out/`:

| Artefact | What it is |
|---|---|
| `reports/<period>-<asset>.pdf` | one page per asset — the comparison, or why no comparison exists |
| `reports/<period>-FLEET.pdf` | one-page fleet summary with the "not inspected" list |
| `comparison.json` | the full machine-readable diff, finding by finding |
| `photos/` + `photos_manifest.json` | the generated photo evidence and its SHA-256 digests |
| `SUMMARY.txt` | the run summary printed by the command |

Then prove the acceptance criteria:

```bash
make verify        # python3 -m fleet verify
```

## What the demo shows

Seed period `2026-W46`. Seven assets, four comparable pairs, three assets that cannot
be compared:

- **TRL-1042 / Wilson flatbed trailer** — passes all 8 items at pre-use. At post-use it
  fails three named items: `TRL-BRK-02` air brake leak-down (a critical item that was
  fine before), `TRL-LGT-04` right rear marker lamp, and `TRL-BED-07` two cracked deck
  boards (grade 4 → 2). All three are new damage, all three resolve to a template item
  and a photo, and the page names **Marcus Hale** and **12 Nov 2026 17:24 (-05:00)**.
  The trailer is recommended out of service.
- **TRL-2087 / Fontaine step-deck** — the asset arrives with a dented deck and a
  cracked lamp lens already recorded at pre-use. At return the dent is still there and
  the lamp has been replaced. The dent is **existing damage**, carries both photos, and
  is explicitly **not** counted as new; the lamp shows as cleared. No false recharge.
- **PU-3312 / Ford service truck** — the cab grade drops 5 → 3 and the washer pump
  fails. Both are evidenced; the recommendation is release with damage.
- **EQ-5510 / Bobcat excavator** — a critical hydraulic finding was recorded but the
  photo did not save, and the pins item got no result at all. Both become **NEEDS
  REVIEW** with the reason named; nothing is guessed. The asset is held.
- **EQ-6604** — pre-use recorded, no return inspection: in the not-inspected list and
  its own report page lists every template item as `not recorded`.
- **EQ-7711** — post-use recorded with no pre-use baseline: the same list, because a
  return condition with no "before" cannot be used to prove change.
- **TRL-3120** — no record at all. The asset the record claims was inspected is the
  one that never was. This is the failure the product exists to expose.

## The comparison rules

The template per asset class is the shared denominator. Every item is boolean or
graded (1–5, with a fail threshold and a change threshold), and both sides use the same
template **and version**, so the comparison is item for item.

| Classification | Rule | Finding? |
|---|---|---|
| `new_damage` | passed at pre-use, fails at post-use | yes — chargeable |
| `condition_degraded` | graded item drops by the template's threshold, still passing | yes — monitored |
| `existing_damage` | failed at both ends | yes — carried forward, **never** new |
| `cleared` | failed at pre-use, passes at post-use | no |
| `missing_item` | no usable post-use result | yes — **NEEDS REVIEW** |
| `missing_baseline` | no usable pre-use result | yes — **NEEDS REVIEW** |
| `unchanged` | same state and grade | no |

The single rule that carries the product: **existing damage can never be reported as
new damage.** A false "new damage" is a billing dispute, so both the seed and the test
suite pin it down.

## Findings discipline

A finding resolves only when it points at a template item id **and** at a photo whose
SHA-256 the report prints. Anything else says **NEEDS REVIEW** with the reason:

- a critical item with no photo → "no photo is attached to item EQ-HYD-01 on the post-use inspection";
- a pre-existing defect with no pre-use photo → cannot be proved pre-existing;
- a missing result on either side → the two inspections cannot be compared for that item;
- a photo record whose file is missing → named as missing evidence.

Every finding also carries its inspector and completed timestamp. The report shows the
digest under each photo, so the image in the page can be checked against the file.

## Not inspected is a first-class result

The one-page report does not stop at the assets that were compared. Every page carries
the period's "not inspected" list with the reason: pre-use only, post-use only, or no
record at all. An asset with one side is never treated as having returned in the
condition it left in.

## What is deliberately not built

This demo is the comparison engine and the evidence report, not the whole SaaS. Not
included: multi-tenancy and RBAC, offline capture and sync, a mobile capture UI,
photo annotation, digital signatures, template editing, a database, and integrations.
The seed is JSON because the diff is the point; replacing the loader with a database
does not change the engine. The [limitations](#limitations) section below is the honest
boundary.

## Numbers

Every count below is regenerated, not typed: run `make stats` (`python3 -m fleet stats`)
and `make verify`.

**Seed:** 7 assets · 3 templates · 10 inspections · 12 photos.
**Comparison:** 4 comparable pairs · 3 not inspected · 31 template items compared.
**Findings:** 8 total — 5 new damage, 1 existing damage, 1 condition degraded,
1 missing item; 6 resolved with photo evidence, 2 NEEDS REVIEW; 2 newly unroadworthy;
1 item cleared at return.
**Output:** 8 one-page PDFs (7 assets + fleet summary).

`make verify` prints 15 named checks; the acceptance mapping:

| Acceptance criterion | Check |
|---|---|
| Seed pre-use passes | `seed.pre_use_passes` |
| Post-use fails three named items | `seed.post_use_fails_three_items` |
| The three are reported as new damage | `engine.three_items_reported_as_new_damage` |
| A critical item that was fine before is flagged | `engine.newly_unroadworthy_names_critical_item` |
| Existing damage is never new | `engine.existing_damage_is_never_new` |
| Every flag resolves to item + photo or NEEDS REVIEW | `findings.resolve_to_item_plus_photo_or_needs_review` |
| Missing evidence becomes NEEDS REVIEW | `findings.missing_evidence_becomes_needs_review` |
| One-sided assets are in the not-inspected list | `engine.one_sided_assets_are_not_inspected` |
| One page per asset/period | `pdf.every_report_is_one_page` |
| PDF names each item, the inspector and the timestamp | `pdf.trl_1042_names_items_inspector_timestamp` |
| The not-inspected list is in the PDFs | `pdf.fleet_carries_not_inspected_list`, `pdf.TRL-1042_carries_not_inspected_list` |
| Seed status agrees with the engine | `seed.status_matches_recommendation` |
| The evidence is reproducible | `evidence.deterministic` |
| No compliance or regulatory claim without a source | `copy.no_uncited_regulatory_claim` |

## Commands

| Command | Job |
|---|---|
| `./demo.sh` / `make demo` | generate photos, comparison, PDFs and evidence into `out/` |
| `make verify` | run the acceptance checks against the generated artefacts |
| `make test` | 54 unit tests (`python3 -m unittest discover -s tests`) |
| `make stats` | print the counts cited above |
| `python3 -m fleet compare --json` | the full comparison JSON on stdout |
| `python3 -m fleet photos` | regenerate only the synthetic photo evidence |
| `--now 2026-11-15T12:00:00-05:00` | fix the report clock; output is then byte-identical |

## Layout

```
data/            seeded assets, templates and inspections (JSON)
fleet/model.py   typed records and seed validation (refuses inconsistent data)
fleet/engine.py  the comparison engine and findings resolution
fleet/photos.py  deterministic synthetic photos (PNG written by hand, no Pillow)
fleet/pdf.py     dependency-free PDF writer (Helvetica, RGB image XObjects)
fleet/report.py  the one-page reports
fleet/build.py   demo orchestration + comparison.json
fleet/verify.py  the acceptance checks
tests/           54 tests, including existing-vs-new and evidence failure paths
```

## Demonstration data and the copy

The seed is fictional by design: invented assets, invented people, invented
inspections, synthetic photos. Every photo is generated by this repo and stamped
`DEMO`; the report header carries `DEMO DATA`. Do not present the output as a real
outcome.

**This build makes no compliance or regulatory claim.** It does not state who must
inspect what, quote any rule text, or assert that following its workflow satisfies an
obligation. `DVIR` is used in the directory and handoff only as the market's shorthand
for a pre-use/post-use vehicle inspection. If a buyer needs a jurisdictional
requirement, that has to be read from the source and quoted with a citation; the copy
The copy checker (`copy.no_uncited_regulatory_claim`) fails the build if a regulatory word appears without either a citation or a plain statement that this build makes no compliance or regulatory claim.

## IP and provenance

Clean implementation against invented seed data. It was built after studying the
*patterns* in the Sync Suite / SyncCrew work referenced in the build note (offline
mutation queue, photo capture, RBAC, PDF reports); no client code, schema, copy or
screenshot is used or published here. The product boundary in this repo — pre vs post
comparison as the sellable feature — came from two public Upwork postings summarised in
`MARK/Projects/Build_Ideas/fleet_asset_inspection.md`, not from client material.

MIT licensed. See `LICENSE`.
