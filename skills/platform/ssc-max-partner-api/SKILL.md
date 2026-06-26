---
name: ssc-max-partner-api
description: >
  Query the SecurityScorecard MAX API (Managed Assessment eXchange) for customer and partner
  assessment data. Use this skill whenever the user asks about MAX, managed assessments,
  SSC MAX customer data, MAX partner data, assessment exchange, managed security assessments,
  or MSSP/partner workflows in SecurityScorecard. Trigger on "MAX API", "managed assessment",
  "MAX customer", "MAX partner", "assessment exchange", "MSSP assessment", or any MAX-related
  query in SecurityScorecard. Also trigger when the user asks to fetch findings, triage
  findings or breaches, pull vendor lists, check customer lists, or build any automation
  against the MAX partner endpoints. Note: MAX API is currently in BETA — always use the
  correct version header for each endpoint (breaches require "deprecated"; all others use "beta").
---

# SSC MAX Partner API Skill

Provides auth patterns, endpoint reference, pagination helpers, triage write operations,
and all field schemas for the SecurityScorecard MAX Partner API. Validated live against
production (92 customers, 40k+ findings, 4.6k breaches as of May 2026).

> **Before calling any MAX API endpoint**, ensure the session is initialized via
> `vroc-session-init` so `SSC_API_TOKEN` is loaded into the environment.

> ⚠️ **Two base URLs — do not mix them:**
> - `https://api.securityscorecard.io` — all MAX endpoints **except** documents
> - `https://platform-api.securityscorecard.io` — **documents only** (`/max/partner/documents`)
>
> Using `api.securityscorecard.io` for the documents endpoint will fail silently or return 404.
> The Quick-Start template below uses the standard base — swap to `platform-api` only for documents.

---

## Quick-Start Template

```python
import os, json, math, time, requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

BASE_URL = "https://api.securityscorecard.io"
TOKEN    = os.environ["SSC_API_TOKEN"]   # loaded by vroc-session-init

HEADERS_BETA = {
    "accept":        "application/json",
    "Authorization": f"Token {TOKEN}",
    "version":       "beta",
}
HEADERS_BETA_WRITE       = {**HEADERS_BETA, "content-type": "application/json"}
HEADERS_DEPRECATED       = {**HEADERS_BETA, "version": "deprecated"}   # breaches GET
HEADERS_DEPRECATED_WRITE = {**HEADERS_DEPRECATED, "content-type": "application/json"}  # breaches PUT

DATE_FROM    = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
DATE_FROM_72 = (datetime.today() - timedelta(days=3)).strftime('%Y-%m-%d')
DATE_TODAY   = datetime.today().strftime('%Y-%m-%d')
```

---

## Endpoint Reference

| Endpoint | Method | Version Header | Live Count |
|---|---|---|---|
| `/max/partner/managed-customers` | GET | `beta` | 92 customers |
| `/max/partner/vendors` | GET | `beta` | Varies per customer |
| `/max/partner/findings` | GET / PUT | `beta` | ~40k findings |
| `/max/partner/breaches` | GET / PUT | **`deprecated`** | ~4.6k breaches |
| `/max/partner/indicators/exclusion` | GET | `beta` | 44 exclusions |

> ⚠️ **Breaches MUST use `version: deprecated`** — using `beta` returns wrong/no data.

For full field schemas, filter syntax, and pagination details, see:
→ `references/endpoints.md`

For triage write operation patterns, see:
→ `references/triage.md`

---

## Pagination Helper

Most endpoints paginate at **page size 50**, 0-indexed. Use this parallel fetcher:

```python
def fetch_all(endpoint: str, params: dict, headers: dict, workers: int = 10) -> pd.DataFrame:
    """Fetch all pages of a MAX endpoint in parallel. Returns a DataFrame."""
    resp = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=params)
    if resp.status_code != 200:
        print(f"✗ {resp.status_code}: {resp.text[:200]}")
        return pd.DataFrame()

    meta       = resp.json()
    total      = meta.get("total", 0)
    size       = meta.get("size") or 50   # size can be null — default 50
    if total == 0:
        return pd.DataFrame()

    page_count  = math.ceil(total / size)
    all_entries = list(meta.get("entries", []))

    def _get_page(page):
        r = requests.get(f"{BASE_URL}{endpoint}", headers={**headers},
                         params={**params, "page": page})
        return r.json().get("entries", []) if r.status_code == 200 else []

    if page_count > 1:
        with ThreadPoolExecutor(max_workers=min(workers, page_count - 1)) as pool:
            for entries in pool.map(_get_page, range(1, page_count)):
                all_entries.extend(entries)

    return pd.json_normalize(all_entries).reset_index(drop=True)
```

---

## Common Patterns

### Fetch all managed customers
```python
df_customers = fetch_all("/max/partner/managed-customers", {}, HEADERS_BETA)
# Fields: customer_id, customer_name, customer_domain, managed_vendors,
#         available_slots, last_update_sent, engagement_active_breach, request_status
```

### Fetch findings (Critical + High, last 24h)
```python
params = {
    "filters": json.dumps({
        "first_seen":   {"operator": "gte", "value": DATE_FROM},
        "max_severity": ["critical", "high"],
    })
}
df_findings = fetch_all("/max/partner/findings", params, HEADERS_BETA)
```

### Fetch breaches (last 24h, recent only)
```python
params = {
    "recent_only": "true",
    "first_seen":  json.dumps({"operator": "gt", "value": DATE_FROM}),
}
df_breaches = fetch_all("/max/partner/breaches", params, HEADERS_DEPRECATED)
```

### Fetch vendors for a specific customer
```python
params = {"customer_name": "Pepsi", "page": 0}
# Note: managed-customers returns managed_vendors count; use that for page math
# Vendor page size = 100
df_vendors = fetch_all("/max/partner/vendors", params, HEADERS_BETA)
# Filter by tier: df_vendors[df_vendors['tier'] == 'Gold']
```

### Fetch exclusions
```python
df_exclusions = fetch_all("/max/partner/indicators/exclusion", {}, HEADERS_BETA)
# scope = 'customer' or 'vendor'; issue_type_name, vendor_domain, customer_domain
```

---

## Triage Operations (PUT)

See `references/triage.md` for the full batch triage function. Quick summary:

```python
# Findings triage
payload = {
    "findings": [
        {"finding_id": row["finding_id"], "vendor_id": row["vendor_id"],
         "report": True, "triaged": True}
        for row in untriaged_df.to_dict("records")
    ]
}
requests.put(f"{BASE_URL}/max/partner/findings", json=payload, headers=HEADERS_BETA_WRITE)

# Breaches triage (same pattern, different key — MUST use deprecated header)
payload = {
    "breaches": [
        {"breach_id": row["breach_id"], "vendor_id": row["vendor_id"],
         "report": True, "triaged": True}
        for row in untriaged_df.to_dict("records")
    ]
}
requests.put(f"{BASE_URL}/max/partner/breaches", json=payload, headers=HEADERS_DEPRECATED_WRITE)
```

---

## Gotchas

| Gotcha | Fix |
|---|---|
| Breaches return empty with `version: beta` | Always use `version: deprecated` for `/breaches` |
| `size` is `null` for some endpoints | `size = meta.get("size") or 50` |
| `vendor_id` required for PUT | Always fetched from GET response; never skip it |
| `triaged`/`report` are booleans in GET | Use `.ne(True)` to find untriaged; cast to str before `== 'True'` filters |
| `customers` field is a nested list | Requires string parsing — see `references/endpoints.md` |
| Page numbering is 0-indexed | `fetch_all` fetches page 0 in the initial call, then parallel-fetches `range(1, page_count)`. For manual loops: start at `page=0`, increment until entries are empty |
| 429 rate limiting | Exponential backoff: sleep `2**attempt` seconds before retry |

---

## Secret Naming (GCP Secret Manager)

- **Primary LIFARS key:** `max-lifars-primary`
- **Per-customer bot key:** `{CustomerName}-BOT` (hyphenated alphanumeric)
  - Pattern: strip suffixes (Inc., Corp., LLC, Ltd., Group, N.A., A.G.), join words, append `-BOT`
  - Examples: `Pepsi-BOT`, `JPMorganChase-BOT`, `StateofNewMexico-BOT`
- **GCP Project ID:** `618101963447`
