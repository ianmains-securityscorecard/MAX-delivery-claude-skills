---
name: max-findings-triage
description: >
  Bulk triage SecurityScorecard MAX partner findings and breaches — marking them as
  report=true and/or triaged=true via the MAX partner API. Findings that carry a
  hostname are ownership-verified against Driftnet before being marked report=true
  (see Step 0) — a finding is never bulk-approved on attribution alone, whether that
  attribution was automatic or a prior manual edit. Use this skill whenever the user
  asks to triage findings, mark findings as report, bulk update findings, clear the
  untriaged backlog, or push findings/breaches to report in the MAX workstation.
  Also trigger when the user asks to triage breaches, mark breaches as report, or
  update any MAX workstation items in bulk. Always use this skill — do not attempt
  MAX bulk triage without it; the correct endpoint and headers are non-obvious and
  differ from the standard MAX API conventions. This is the fallback path for
  findings with no hostname (nothing to ownership-check); for the full
  hostname-aware two-tier flow prefer `max-hostname-triage` when it applies.
version: 1.1
last_updated: 2026-08-13
owner: ian.mains@securityscorecard.io
status: active
category: triage
---

# MAX Findings & Breaches Triage Skill

Bulk-updates MAX partner findings and/or breaches to `report=true / triaged=true`
using the correct BETA endpoint and header — with a Driftnet ownership check gating
`report=true` for any finding that carries a hostname, so bulk triage can't silently
rubber-stamp a mis-attributed finding just because it's sitting in the untriaged pile.

> **Prerequisites:** `SSC_API_TOKEN` and `DRIFTNET_API_TOKEN` must be loaded in the
> environment. Run the `vroc-session-init` skill first if keys are not yet loaded.
> Without `DRIFTNET_API_TOKEN`, hostname-bearing findings are held rather than
> auto-approved — see Step 0.

---

## Critical API Facts

These differ from the main SSC API and from `/max/v1/` conventions:

| Property | Value |
|----------|-------|
| Findings URL | `https://api.securityscorecard.io/max/partner/findings` |
| Breaches URL | `https://api.securityscorecard.io/max/partner/breaches` |
| Required extra header | `"version": "beta"` |
| Max items per PUT | **100** (400 error if exceeded) |
| PUT payload (findings) | `{"findings": [{...}, ...]}` |
| PUT payload (breaches) | `{"breaches": [{...}, ...]}` |
| Required fields per finding | `finding_id`, `vendor_id`, `report`, `triaged` |
| Required fields per breach | `breach_id`, `vendor_id`, `report`, `triaged` |

**Common failure modes:**
- Using `/max/v1/findings` → 404 (wrong path)
- Omitting `"version": "beta"` header → 400
- Chunks > 100 items → `"You can only edit up to 100 findings in one request."`

---

## Headers

```python
TOKEN = os.environ['SSC_API_TOKEN']

HEADERS_READ = {
    "accept": "application/json",
    "Authorization": f"Token {TOKEN}",
    "version": "beta",
}
HEADERS_WRITE = {
    **HEADERS_READ,
    "content-type": "application/json",
}
```

---

## Step 1 — Fetch All Untriaged Findings (Parallel)

```python
import os, requests, math, time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://api.securityscorecard.io"
PAGE_SIZE = 50

# Get total count
resp = requests.get(f"{BASE}/max/partner/findings", headers=HEADERS_READ,
    params={"triaged": "false", "page_size": 1}, timeout=15)
total = resp.json().get("total", 0)
total_pages = math.ceil(total / PAGE_SIZE)
print(f"Untriaged findings: {total:,} across {total_pages} pages")

def fetch_page(page):
    r = requests.get(f"{BASE}/max/partner/findings", headers=HEADERS_READ,
        params={"triaged": "false", "page_size": PAGE_SIZE, "page": page}, timeout=20)
    return r.json().get("entries", []) if r.status_code == 200 else []

all_findings = []
with ThreadPoolExecutor(max_workers=10) as ex:
    futures = {ex.submit(fetch_page, p): p for p in range(1, total_pages + 1)}
    for fut in as_completed(futures):
        all_findings.extend(fut.result())

print(f"Fetched {len(all_findings):,} findings")
```

**Optional filters** (add to `params` dict):
- `customer_domain=example.com` — scope to one customer
- `partner_domain=lifars.com` — scope to partner (default: all)
- `triaged=false` — untriaged only (standard use case)

---

## Step 0 — Digital-Footprint Ownership Check (Driftnet)

Before anything gets `report=true`, every fetched finding that carries a `hostname`
is checked against Driftnet: does the ASN / TLS cert / entity currently observed at
that hostname actually match the vendor it's flagged against, right now? String
equality between the hostname and the vendor's domain is **not** ownership evidence
— a vendor's own exact domain can resolve to shared CDN/cloud infrastructure. This
was confirmed live (2026-08-13) against real MAX findings: `verizon.com` resolves to
Akamai; `lumen.com` resolves to a Fastly node serving a *different* Fastly
customer's TLS certificate. Both are the vendor's own domain string — neither is
the vendor's own infrastructure.

**This check runs regardless of whether the finding was previously edited by a
human** (`edited_by` populated). A manual edit is a one-time judgment call, not
standing proof the infrastructure hasn't since moved onto shared infra — see the
`feedback_manual_attribution_scrutiny` note if you're extending this further.
Findings with no `hostname` at all have nothing to ownership-check and proceed
through the normal bulk path unchanged.

```python
DRIFTNET_BASE_URL = "https://api.driftnet.io/v1"   # confirm exact paths against
                                                     # docs/DRIFTNET_API_REFERENCE.md
DRIFTNET_TOKEN = os.environ.get("DRIFTNET_API_TOKEN")

SHARED_INFRA_MARKERS = [
    "akamai", "cloudflare", "fastly", "amazon", "aws", "microsoft azure",
    "google cloud", "digitalocean", "ovh", "securityscorecard",
]

def _driftnet_get(path, params):
    if not DRIFTNET_TOKEN:
        return None
    try:
        r = requests.get(f"{DRIFTNET_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {DRIFTNET_TOKEN}"}, params=params, timeout=15)
        return r.json() if r.status_code == 200 else None
    except requests.RequestException:
        return None

def root_domain(d):
    parts = d.lower().strip().rstrip(".").split(".")
    return parts[-2] if len(parts) >= 2 else parts[0]

def driftnet_ownership_verdict(hostname, vendor_domain):
    """Returns one of: CONFIRMED, SHARED_INFRA_MISMATCH, NO_DNS_RECORD,
    UNVERIFIABLE, AMBIGUOUS. Only CONFIRMED should ever justify report=True —
    everything else, fail closed (hold), never fail open (auto-approve)."""
    owner = root_domain(vendor_domain)

    dns = _driftnet_get("/dns/forward", {"expression": f"host={hostname}"})
    if dns is None:
        return "UNVERIFIABLE"
    records = dns.get("reports") or dns.get("records") or []
    if not records:
        return "NO_DNS_RECORD"

    ips = [i["value"] for i in records[0].get("items", [])
           if i.get("type") == "ip" and i.get("context", "").startswith("dns-a")]
    if not ips:
        return "AMBIGUOUS"

    scan = _driftnet_get("/scans/protocols", {"ip": ips[0], "most_recent": "true"})
    scan_records = (scan or {}).get("reports") or (scan or {}).get("records") or []
    if not scan_records:
        return "UNVERIFIABLE"

    hay = " ".join(
        str(i["value"]) for rep in scan_records for i in rep.get("items", [])
        if i.get("type") in ("entity", "asn", "subject", "host") and i.get("value")
    ).lower()

    owner_present = owner in hay
    shared_hit = next((m for m in SHARED_INFRA_MARKERS if m in hay and m not in owner), None)
    if shared_hit and not owner_present:
        return "SHARED_INFRA_MISMATCH"
    if owner_present:
        return "CONFIRMED"
    return "AMBIGUOUS"


def _has_hostname(f):
    return str(f.get("hostname", "")).strip() not in ("", "nan", "None")

with_hostname    = [f for f in all_findings if _has_hostname(f)]
with_hostname_ids = {f["finding_id"] for f in with_hostname}
without_hostname = [f for f in all_findings if f["finding_id"] not in with_hostname_ids]

print(f"\nFindings with hostname (ownership-checked): {len(with_hostname):,}")
print(f"Findings with no hostname (bulk path, unchanged): {len(without_hostname):,}")

verdict_cache = {}
verified_ok, verified_mismatch, held = [], [], []
for f in with_hostname:
    key = (f["hostname"], f["vendor_domain"])
    if key not in verdict_cache:
        verdict_cache[key] = driftnet_ownership_verdict(*key)
    v = verdict_cache[key]
    if f.get("edited_by") and v != "CONFIRMED":
        print(f"  ⚠ manually-edited finding overridden: {f['vendor_name']} / {f['hostname']} → {v}")
    if v == "CONFIRMED":
        verified_ok.append(f)
    elif v in ("SHARED_INFRA_MISMATCH", "NO_DNS_RECORD"):
        verified_mismatch.append(f)
    else:  # UNVERIFIABLE, AMBIGUOUS
        held.append(f)

print(f"  Driftnet CONFIRMED:          {len(verified_ok):,}  → report=True")
print(f"  Driftnet MISMATCH/no-DNS:    {len(verified_mismatch):,}  → report=False")
print(f"  Driftnet UNVERIFIABLE/AMBIG: {len(held):,}  → held, left untriaged")
```

---

## Step 2 — Batch PUT Findings (Chunks of 100)

Three separate batches now, instead of one blanket `report=True`: confirmed +
no-hostname findings go `report=True`; Driftnet mismatches go `report=False`; held
findings are skipped entirely (left `triaged=false` for a human to look at — do
NOT fold them into either batch).

```python
def put_batch(items, report_value):
    if not items:
        return 0, 0
    batch = [{"finding_id": f["finding_id"], "vendor_id": f["vendor_id"],
              "report": report_value, "triaged": True} for f in items]
    chunks = [batch[i:i+100] for i in range(0, len(batch), 100)]  # hard API limit
    success, failed = 0, 0
    for i, chunk in enumerate(chunks):
        r = requests.put(f"{BASE}/max/partner/findings",
            headers=HEADERS_WRITE, json={"findings": chunk}, timeout=30)
        if r.status_code in (200, 204):
            success += len(chunk)
        else:
            failed += len(chunk)
            print(f"[ERROR] Chunk {i+1}: {r.status_code} — {r.text[:200]}")
        time.sleep(0.05)
    return success, failed

report_true_batch = verified_ok + without_hostname   # confirmed + nothing to check

ok_true,  fail_true  = put_batch(report_true_batch, True)
ok_false, fail_false = put_batch(verified_mismatch,  False)

print(f"✓ report=True:  {ok_true:,}  ✗ failed: {fail_true:,}")
print(f"✓ report=False: {ok_false:,}  ✗ failed: {fail_false:,}")
print(f"  Held (untouched, left for review): {len(held):,}")
```

---

## Step 3 — Verify

```python
resp = requests.get(f"{BASE}/max/partner/findings", headers=HEADERS_READ,
    params={"triaged": "false", "page_size": 1}, timeout=15)
remaining = resp.json().get("total", "?")
print(f"Remaining untriaged: {remaining}")
```

If `remaining > 0`, run Steps 1–3 again. A small residual (typically < 50) can
occur when new findings arrive during the fetch window — a second pass clears them.

---

## Breaches — Same Pattern, Different Keys

> **No Step 0 equivalent here.** Breach records don't carry a `hostname` — a
> breach is reported against a vendor as a whole, not a specific asset, so
> there's no per-host infrastructure to ownership-check against Driftnet.
> This bulk-approve path is unchanged and intentionally so; it isn't a gap
> that was overlooked.

Replace `findings` with `breaches` throughout, and use `breach_id` instead of
`finding_id`:

```python
BASE_URL_BREACHES = f"{BASE}/max/partner/breaches"

# Fetch untriaged breaches
resp = requests.get(BASE_URL_BREACHES, headers=HEADERS_READ,
    params={"triaged": "false", "page_size": 100}, timeout=15)
entries = resp.json().get("entries", [])

# Build and send batch (same 100-item limit)
batch = [
    {
        "breach_id": b["breach_id"],
        "vendor_id": b["vendor_id"],
        "report":    True,
        "triaged":   True,
    }
    for b in entries
]

if batch:
    r = requests.put(BASE_URL_BREACHES,
        headers=HEADERS_WRITE, json={"breaches": batch}, timeout=30)
    print(f"Breaches PUT → {r.status_code}")
```

---

## Count / Audit (Read-Only)

To check untriaged counts without modifying anything:

```python
# Partner-wide
resp = requests.get(f"{BASE}/max/partner/findings", headers=HEADERS_READ,
    params={"triaged": "false", "page_size": 1}, timeout=15)
print(f"Partner untriaged: {resp.json().get('total'):,}")

# Per customer
resp = requests.get(f"{BASE}/max/partner/findings", headers=HEADERS_READ,
    params={"triaged": "false", "customer_domain": "example.com", "page_size": 1}, timeout=15)
print(f"Customer untriaged: {resp.json().get('total'):,}")
```

---

## Notes

- The `version: beta` header is required. Without it the endpoint returns 400.
- The path `/max/v1/findings` is a different (non-functional) route — always use
  `/max/partner/findings`.
- `report=True` and `triaged=True` can be set independently if needed.
- This skill applies to the **partner** perspective. Customer-scoped updates use
  `/max/customer/findings` (different entitlements required).
- After a large bulk triage, allow ~60 seconds before the workstation UI reflects
  the changes.
- **Ownership check fails closed, not open.** If `DRIFTNET_API_TOKEN` is missing
  or a Driftnet call errors/times out, affected findings land in `held`
  (`UNVERIFIABLE`), not in the `report=True` batch. A verification outage should
  never silently widen what gets auto-approved.
- **A prior manual edit is not an exemption.** `edited_by` being set on a finding
  does not skip Step 0 — Driftnet can and does override a standing manual
  attribution when current infrastructure disagrees with it (logged with `⚠`).
- **String domain equality ≠ ownership.** A finding's hostname matching the
  vendor's domain textually says nothing about who operates the infrastructure
  behind it today — see Step 0 for the `verizon.com`/`lumen.com` cases that
  motivated this.
