---
name: max-hostname-triage
description: >
  Three-phase MAX workstation triage that verifies actual asset ownership before
  bulk-clearing everything else. Phase 0: for every untriaged finding that has a
  hostname, ask Driftnet who really operates that hostname's infrastructure right
  now (ASN/cert/entity attribution) — a vendor's own exact domain string can still
  resolve to shared CDN/cloud infrastructure, so string equality alone is not
  ownership evidence. Confirmed → report=True. Mismatch, unresolvable, or no
  recent scan → report=False or held for human review; string-domain match is
  kept only as a secondary signal, never the deciding one. Findings a human has
  already edited are given the SAME scrutiny, not less — a manual attribution is
  a one-time judgment call that can go stale. Phase 2: bulk triage all remaining
  untriaged findings as report=True, triaged=True, excluding anything held in
  Phase 0. Use this skill whenever the user says "smart triage", "triage with
  hostname check", "run hostname triage", "clear the workstation", "triage
  everything", "run the full triage", or any variation of wanting to triage MAX
  findings with attribution awareness. Always prefer this skill over raw bulk
  triage when findings with hostnames may be present. Requires SSC_API_TOKEN and
  DRIFTNET_API_TOKEN loaded in environment (run vroc-session-init first) — without
  DRIFTNET_API_TOKEN, ownership cannot be verified and matching findings are held,
  not auto-approved.
version: 1.1
last_updated: 2026-08-13
owner: ian.mains@securityscorecard.io
status: active
category: triage
---

# MAX Hostname-Aware Triage Skill

Three-phase triage for the MAX partner workstation.

- **Phase 0 — Digital-footprint ownership verification (Driftnet):** For every
  hostname finding, Driftnet's current DNS + latest passive scan (ASN, TLS cert
  subject, entity) is checked against the claimed vendor. This is the
  authoritative check — a hostname that is textually the vendor's own domain
  can still fail here if it resolves to shared CDN/cloud infrastructure. Live
  examples that failed this check while passing the old string-only test:
  `verizon.com` (resolves to Akamai) and `lumen.com` (resolves to a Fastly node
  serving an unrelated customer's certificate). `CONFIRMED` → `report=True`.
  `SHARED_INFRA_MISMATCH` / `NO_DNS_RECORD` → `report=False`. `UNVERIFIABLE` /
  `AMBIGUOUS` (including a missing Driftnet token) → **held**, left
  `triaged=False`, and excluded from Phase 2 — never silently auto-approved.
  A prior manual edit (`edited_by` populated) does not exempt a finding from
  this check; it gets the same verdict, logged with an explicit warning when
  Driftnet disagrees with a standing manual attribution.
- **Phase 1 — String-domain check:** Retained as a secondary/diagnostic signal
  alongside Phase 0's verdict — mainly useful for spotting when the two
  disagree (logged as `[STRING CHECK WOULD HAVE PASSED THIS]`).
- **Phase 2 — Bulk clear remaining:** All remaining untriaged findings (no
  hostname, or left over and not held by Phase 0) are marked
  `report=True, triaged=True` in parallel batches of 100.

> **Prerequisite:** `SSC_API_TOKEN` and `DRIFTNET_API_TOKEN` must be in the
> environment. Run `vroc-session-init` first if not already loaded.

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
   findings + any not touched in Phase 0/1), then drop anything Phase 0 held.
2. Chunk into batches of 100 (API hard limit).
3. PUT all batches in parallel (8 workers) with exponential backoff on 429s.
4. Print running progress every 50 batches and a final count.

---

## Output Summary

After all phases, the script prints:

```
═══════════════════════════════════════════════════
PHASE 0/1 — OWNERSHIP VERIFICATION (Driftnet) + HOSTNAME CHECK
  Findings with hostname:        15
  Driftnet CONFIRMED:             3  → report=True
  Driftnet MISMATCH/no-DNS:      10  → report=False (hide)
  Driftnet UNVERIFIABLE/AMBIG:    2  → held, NOT auto-approved
═══════════════════════════════════════════════════
PHASE 2 — BULK CLEAR
  Excluded 2 finding(s) held by Phase 0/1 from the bulk-clear pass.
  Remaining untriaged: 32,320
  Batches: 324
  ✓ Triaged: 32,320  ✗ Failed: 0
═══════════════════════════════════════════════════
COMPLETE — 32,335 total findings processed, 2 held for review
```

---

## Key Implementation Notes

- **Ownership over string matching:** Phase 0's Driftnet check is authoritative;
  the old string-domain check (`hostname_belongs_to_vendor`) is retained only as
  a diagnostic secondary signal, logged when it disagrees with Driftnet
  (`[STRING CHECK WOULD HAVE PASSED THIS]`). String equality was never evidence
  of who runs the infrastructure — see `verizon.com`/`lumen.com` in the SKILL
  description above.
- **Manual edits get equal scrutiny, not an exemption:** a finding with
  `edited_by` populated still runs through Phase 0. If Driftnet disagrees with
  a standing manual attribution, it's overridden and logged with a `⚠` warning
  — a human confirming something once is not the same as it staying true.
- **Fail closed, not open:** if `DRIFTNET_API_TOKEN` is missing or a Driftnet
  call errors/times out, the finding is held (`UNVERIFIABLE`), never defaulted
  to `report=True`. An outage in the verification step should never widen what
  gets auto-approved.
- **Multi-TLD awareness:** handles `co.uk`, `com.au`, `gov.uk`, `com.br`, etc.
  so `example.co.uk` correctly extracts as root domain, not `co.uk`.
- **204 = success:** The MAX PUT endpoint returns HTTP 204 (No Content) on
  success — the script treats both 200 and 204 as success.
- **100-item batch hard limit:** Exceeding this returns a 400 error. The script
  always chunks at 100.
- **Parallel fetching:** Pages are fetched with `ThreadPoolExecutor` (10
  workers); PUT batches use 8 workers. Driftnet calls are deduped per unique
  hostname before firing, not per finding.

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
- Driftnet base: `https://api.driftnet.io/v1`, `Authorization: Bearer {DRIFTNET_API_TOKEN}`
  (same `.vroc_keys` file as `SSC_API_TOKEN`, loaded by `vroc-session-init`)
- Driftnet paths used here (`/dns/forward`, `/scans/protocols`) mirror the
  `driftnet_query` MCP tool's `forward_dns`/`scan_protocols` operations —
  confirm exact params against `docs/DRIFTNET_API_REFERENCE.md` if a call
  returns an unexpected shape; not independently verified via raw REST yet,
  only via the MCP wrapper (validated live 2026-08-13 against Lloyds Banking
  Group and United Airlines' real MAX triage queues — see the "Groundtruth"
  hackathon writeup).
- See `max-findings-triage` skill for breaches triage pattern
