---
name: max-hostname-triage
description: >
  Two-phase MAX workstation triage that intelligently handles findings with
  hostnames before bulk-clearing everything else. Phase 1: for every untriaged
  finding that has a hostname, check whether the hostname actually belongs to
  the vendor domain it was flagged against — if it matches, mark report=True
  (surface to customer); if it doesn't match (attribution mismatch / shared
  infrastructure), mark report=False (hide). Phase 2: bulk triage all remaining
  untriaged findings as report=True, triaged=True. Use this skill whenever the
  user says "smart triage", "triage with hostname check", "run hostname triage",
  "clear the workstation", "triage everything", "run the full triage", or any
  variation of wanting to triage MAX findings with attribution awareness. Always
  prefer this skill over raw bulk triage when findings with hostnames may be
  present — it prevents mis-attributed findings from being reported to the wrong
  customer. Requires SSC_API_TOKEN loaded in environment (run vroc-session-init first).
---

# MAX Hostname-Aware Triage Skill

Two-phase triage for the MAX partner workstation.

- **Phase 1 — Hostname domain verification:** Findings with a hostname are
  checked to see if the hostname actually belongs to the vendor it was flagged
  against. Matches → `report=True`. Mismatches (shared infra / attribution
  artifacts) → `report=False`. Both are marked `triaged=True`.
- **Phase 2 — Bulk clear remaining:** All remaining untriaged findings (no
  hostname, or any left over) are marked `report=True, triaged=True` in parallel
  batches of 100.

> **Prerequisite:** `SSC_API_TOKEN` must be in the environment.
> Run `vroc-session-init` first if not already loaded.

---

## When to Use This Skill vs. max-findings-triage

| Scenario | Use |
|---|---|
| Want full attribution-aware triage (hostname check + bulk clear) | **This skill** |
| Want to bulk-triage everything without hostname logic | `max-findings-triage` |
| Want to triage only breaches | `max-findings-triage` |
| Want to triage a single customer's findings | `max-findings-triage` |

---

## Quick Usage

Simply tell Claude:
- *"Run the hostname triage"*
- *"Smart triage the workstation"*
- *"Triage everything — do the hostname check first"*
- *"Clear the backlog with hostname verification"*

Claude will run the script below and summarise results.

---

## Execution

Run the bundled script directly:

```python
exec(open("/mnt/skills/user/max-hostname-triage/scripts/hostname_triage.py").read())
```

Or invoke via bash_tool:

```bash
python3 /mnt/skills/user/max-hostname-triage/scripts/hostname_triage.py
```

The script self-loads credentials from the uploaded `_vroc_keys` / `.vroc_keys`
file — no extra setup required.

---

## What the Script Does (Step-by-Step)

### Phase 1 — Hostname Verification

1. Fetch all untriaged findings where `hostname` is populated.
2. For each finding, extract the root domain from the hostname
   (e.g. `ciam-tika.straumann.com` → `straumann.com`) using a multi-TLD-aware
   parser that handles `co.uk`, `com.au`, `gov.uk`, etc.
3. Compare against `vendor_domain` — a finding matches if the hostname IS the
   vendor domain or is a strict subdomain of it.
4. **Matches** → `PUT report=True, triaged=True`
5. **Mismatches** → `PUT report=False, triaged=True`
6. Print a summary table of each decision.

### Phase 2 — Bulk Clear

1. Re-fetch all findings still marked `triaged=false` (covers no-hostname
   findings + any not touched in Phase 1).
2. Chunk into batches of 100 (API hard limit).
3. PUT all batches in parallel (8 workers) with exponential backoff on 429s.
4. Print running progress every 50 batches and a final count.

---

## Output Summary

After both phases, the script prints:

```
═══════════════════════════════════════════════════
PHASE 1 — HOSTNAME TRIAGE
  Findings with hostname:  15
  ✓ Marked REPORT (match): 3
  ✗ Marked HIDE (mismatch): 12
═══════════════════════════════════════════════════
PHASE 2 — BULK CLEAR
  Remaining untriaged: 32,322
  Batches: 324
  ✓ Triaged: 32,322  ✗ Failed: 0
═══════════════════════════════════════════════════
COMPLETE — 32,337 total findings processed
```

---

## Key Implementation Notes

- **Multi-TLD awareness:** handles `co.uk`, `com.au`, `gov.uk`, `com.br`, etc.
  so `example.co.uk` correctly extracts as root domain, not `co.uk`.
- **Strict subdomain check:** `apollo.perficient.com` matches `perficient.com`;
  `fakeperficient.com` does not.
- **204 = success:** The MAX PUT endpoint returns HTTP 204 (No Content) on
  success — the script treats both 200 and 204 as success.
- **100-item batch hard limit:** Exceeding this returns a 400 error. The script
  always chunks at 100.
- **Parallel fetching:** Pages are fetched with `ThreadPoolExecutor` (10
  workers); PUT batches use 8 workers.

---

## Scope Options

The script defaults to **all untriaged findings partner-wide**. To scope to a
specific customer, set `CUSTOMER_DOMAIN` at the top of the script:

```python
CUSTOMER_DOMAIN = "example.com"   # or None for all
```

---

## Reference

- Triage endpoint: `PUT /max/partner/findings`
- Required headers: `version: beta`, `content-type: application/json`
- Findings GET filter: `triaged=false`
- See `max-findings-triage` skill for breaches triage pattern
