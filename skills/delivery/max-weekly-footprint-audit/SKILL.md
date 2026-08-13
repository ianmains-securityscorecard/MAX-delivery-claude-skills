---
name: max-weekly-footprint-audit
description: >
  Post-processes a generated MAX Weekly_Vendor_Report datasheet.xlsx with a
  Driftnet digital-footprint ownership check, independent of live workstation
  triage. Weekly report generation is not gated by triage state, so a
  mis-attributed finding (shared CDN/cloud infrastructure fronting the vendor's
  own domain, or a finding pinned to an opaque *.custom.securityscorecard.io
  placeholder with no real DNS presence) can and does reach a datasheet even
  after max-hostname-triage / max-findings-triage have run. Use this skill
  whenever a weekly report/datasheet needs a footprint sanity check before
  going to a customer — "audit this datasheet", "check footprint on this
  report", "clean up the datasheet", "verify this week's findings before we
  send it", or any Weekly_Vendor_Report*.xlsx that hasn't been through this
  check yet. Adds a "Digital Footprint Verdicts" summary sheet (mode=annotate)
  and/or moves unverifiable rows out of the customer-facing finding sheets into
  an "Excluded — Unverified Footprint" sheet (mode=clean, the default). Chains
  naturally into max-weekly-delivery (Phase 1b.5) and weekly-analyst-summary
  (Step 4.5) — see Pipeline Integration below — but also runs standalone on any
  datasheet that shows up outside the full delivery flow.
version: 1.0
last_updated: 2026-08-13
owner: ian.mains@securityscorecard.io
status: active
category: delivery
---

# MAX Weekly Datasheet Footprint Audit

Runs the same Driftnet ownership verdict engine used by `max-hostname-triage` /
`max-findings-triage` against a generated datasheet, instead of against live
findings — because report generation is a separate pipeline from triage, and a
finding that was never triaged (or was triaged correctly at the time, before its
underlying infrastructure moved onto shared/CDN hosting) can still land in a
customer-facing report.

> **Prerequisite:** `DRIFTNET_API_TOKEN` in the environment (`vroc-session-init`).
> Without it, every domain comes back `UNVERIFIABLE` and is held, not cleared —
> the audit fails closed, it never silently waves a datasheet through.

---

## What it checks

Three sheets in the standard datasheet layout carry one row per (domain, issue)
and become customer-facing finding tables in the generated deck:

| Sheet | Domain column | Vendor column |
|---|---|---|
| `Critical Indicators` | `DOMAIN` | — |
| `High Indicators` | `DOMAIN` | — |
| `New Findings (7d)` | `DOMAIN` | `VENDOR` |

Every unique domain across these sheets gets one deduped Driftnet ownership
check (current DNS + latest scan's ASN/TLS-cert/entity attribution vs. the
claimed vendor). `CVEs` and `Indicator Count` carry multi-domain, semicolon-
delimited aggregate lists per row — this version reads them for cross-reference
context only; it does not attempt to split and clean them (higher risk of
mangling a cell that also drives other report math). Treat that as a known gap,
not a silent one.

---

## Quick Usage

```bash
python3 scripts/audit_datasheet.py --in datasheet.xlsx --out datasheet_audited.xlsx
```

- `--mode annotate` — add the summary sheet only, touch nothing else (safest;
  use this first time on an account until you trust the output).
- `--mode clean` — also move mismatched/unresolvable rows out of the primary
  sheets (default paired with annotate, i.e. `--mode both` is the default).
- Nothing is ever hard-deleted. Excluded rows land in a new sheet with the
  verdict and reason attached, not the trash.

---

## Verdicts and what they mean here

| Verdict | Primary-sheet action | Meaning |
|---|---|---|
| `CONFIRMED` | kept, untouched | Latest scan attribution matches the claimed vendor |
| `SHARED_INFRA_MISMATCH` | moved to Excluded | Latest scan names a known shared-infra provider (Akamai/Fastly/AWS/etc.), not the vendor |
| `NO_DNS_RECORD` | moved to Excluded | Domain has zero DNS presence — not a verifiable internet asset (the `*.custom.securityscorecard.io` placeholder pattern) |
| `STALE_NO_SCAN` | kept, highlighted | DNS looks fine but no recent scan corroborates the finding — held, not cleared |
| `UNVERIFIABLE` | kept, highlighted | Driftnet call failed or token missing — fails closed |
| `AMBIGUOUS` | kept, highlighted | No clear match either way — needs a human |

Only `CONFIRMED` should ever be treated as "safe to ship as-is." Everything else
either gets pulled before a customer sees it, or gets a visible flag so an
analyst can't miss it while skimming the sheet.

---

## Pipeline Integration

This is designed to slot into the standard pipeline with a one-line addition,
not to run as a separate thing analysts have to remember:

- **`max-weekly-delivery`, between Phase 1b and Phase 1c:** run this audit on
  the just-read `xlsx_path` before invoking `weekly-analyst-summary`. If the
  audit's `Excluded` sheet is non-empty, surface it at Gate-equivalent visibility
  — the analyst should see what got pulled before the blurb is written from data
  that no longer matches what's in the deck.
- **`weekly-analyst-summary`, right after Step 4 (data extraction):** the
  `critical`/`high` row lists that feed "New Concerning Findings" should be the
  *post-audit* versions, not the raw sheet reads — otherwise the audit runs but
  the analyst text still cites a finding that was just excluded.
- **Standalone:** for datasheets that arrive outside a full delivery run (ad
  hoc customer requests, a report pulled for QBR prep, spot-checking an
  account) — just run it directly, no other skill required.

---

## Why a separate skill instead of folding this into triage

`max-hostname-triage` / `max-findings-triage` gate whether a *live finding* ever
becomes eligible to `report=true` via the partner API. Weekly report generation
is a distinct pipeline that reads from wherever the report-generation agent
pulls its data — it is not itself gated by workstation triage state, so a
finding can reach a datasheet without ever having passed (or failed) triage.
This is the last-mile check before a human or a customer sees the file, and it
needs to run regardless of what happened upstream — treat a "clean" triage
history as encouraging, not as a reason to skip this.

---

## Notes

- **Fails closed.** No `DRIFTNET_API_TOKEN` → every domain `UNVERIFIABLE` → held,
  not cleared. Missing verification should never look like a passing check.
- **Nothing is silently dropped.** Excluded rows carry the original row contents
  into the `Excluded — Unverified Footprint` sheet, with source sheet and
  verdict, so a disagreement can be reviewed and reversed.
- **Driftnet calls are deduped per unique domain**, not per row — a domain
  appearing in both `Critical Indicators` and `New Findings (7d)` is checked once.
- Driftnet REST paths (`/dns/forward`, `/scans/protocols`) mirror the
  `driftnet_query` MCP tool's `forward_dns`/`scan_protocols` operations — confirm
  against `docs/DRIFTNET_API_REFERENCE.md` if a call returns an unexpected shape.
