---
name: max-findings-triage
description: >
  Bulk triage SecurityScorecard MAX partner findings and breaches — marking them as
  report=true and/or triaged=true via the MAX partner API. Use this skill whenever
  the user asks to triage findings, mark findings as report, bulk update findings,
  clear the untriaged backlog, or push findings/breaches to report in the MAX
  workstation. Also trigger when the user asks to triage breaches, mark breaches as
  report, or update any MAX workstation items in bulk. Always use this skill — do
  not attempt MAX bulk triage without it; the correct endpoint and headers are
  non-obvious and differ from the standard MAX API conventions.
version: 1.0
last_updated: 2026-06-25
owner: ian.mains@securityscorecard.io
status: active
category: triage
---

# MAX Findings & Breaches Triage Skill

Bulk-updates MAX partner findings and/or breaches to `report=true / triaged=true`
using the correct BETA endpoint and header.

> **Prerequisites:** SSC_API_TOKEN must be loaded in the environment.
> Run the `vroc-session-init` skill first if keys are not yet loaded.

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

## Step 2 — Batch PUT Findings (Chunks of 100)

```python
batch = [
    {
        "finding_id": f["finding_id"],
        "vendor_id":  f["vendor_id"],
        "report":     True,
        "triaged":    True,
    }
    for f in all_findings
]

CHUNK_SIZE = 100  # Hard API limit — do not exceed
chunks = [batch[i:i+CHUNK_SIZE] for i in range(0, len(batch), CHUNK_SIZE)]
print(f"Sending {len(chunks)} PUT requests...")

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

print(f"✓ Updated: {success:,}  ✗ Failed: {failed:,}")
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
