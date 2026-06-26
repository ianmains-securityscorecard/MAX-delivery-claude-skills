# MAX API — Full Endpoint & Field Reference

> Live-validated May 2026 · 92 customers · LIFARS primary key

---

## /max/partner/managed-customers

```
GET /max/partner/managed-customers
Headers: version: beta
```

No query params required. Returns all customers in a single response (no pagination needed — `size` is null).

### Response fields

| Field | Type | Notes |
|---|---|---|
| `customer_id` | str | UUID |
| `customer_name` | str | Display name |
| `customer_domain` | str | Root domain |
| `managed_vendors` | int | Total vendors in portfolio |
| `available_slots` | int | Remaining vendor slots |
| `last_update_sent` | date str | Last report date sent |
| `engagement_active_breach` | bool | Active breach flag |
| `request_status` | str | e.g. `"active"` |

---

## /max/partner/vendors

```
GET /max/partner/vendors
Params: customer_name=<str>, page=<int 0-indexed>
Headers: version: beta
```

Page size is **100** (not returned in response — hard-code 100 for page math).
Use `managed_vendors` from `/managed-customers` to calculate total pages.

### Response fields

| Field | Type | Notes |
|---|---|---|
| `customer_domain` | str | Parent customer domain |
| `vendor_domain` | str | Vendor root domain |
| `tier` | str | `"Gold"`, `"Platinum"`, `"ZDaaS"` — title-cased |

### Pagination example
```python
managed_vendors = 350   # from managed-customers response
page_count = math.ceil(managed_vendors / 100)
for page in range(0, page_count + 1):
    r = requests.get(f"{BASE_URL}/max/partner/vendors",
                     headers=HEADERS_BETA,
                     params={"customer_name": customer_name, "page": page})
```

---

## /max/partner/findings

```
GET /max/partner/findings
Params: filters=<json>, page=<int>
Headers: version: beta
```

Page size: **50**. Filter operators: `"gte"`, `"lte"`, `"gt"`, `"lt"`.

### Filter object structure
```python
filters = {
    "first_seen":   {"operator": "gte", "value": "2026-05-01"},
    "max_severity": ["critical", "high"],   # list; options: critical, high, medium, low
    "triaged":      "true",                 # optional string "true"/"false"
}
params = {"filters": json.dumps(filters), "page": 0}
```

### Response fields (all live-confirmed)

| Field | Type | Notes |
|---|---|---|
| `vendor_id` | str | **Required for PUT triage** |
| `vendor_domain` | str | |
| `vendor_name` | str | |
| `finding_id` | str | **Required for PUT triage** |
| `customers` | list[dict] | `[{"domain": "...", "name": "..."}]` — see parsing below |
| `information` | str | Technical detail |
| `first_observed_at` | str | ISO datetime |
| `last_observed_at` | str | ISO datetime |
| `is_active_breach` | bool | |
| `issue_name` | str | Human-readable finding type |
| `issue_type` | str | Machine key |
| `category` | str | |
| `max_severity` | str | `critical`/`high`/`medium`/`low` |
| `breach_risk` | str | |
| `threat_level` | str | |
| `description` | str | |
| `hostname` | str | |
| `ip_address` | str | |
| `product_name` | str | |
| `product_version` | str | |
| `port` | int | |
| `report` | bool | Whether marked for customer reporting |
| `triaged` | bool | Whether triaged |
| `triaged_at` | str | ISO datetime |
| `edited_at` | str | ISO datetime |
| `edited_by` | str | |

### CVE sub-fields (after pd.json_normalize, dot-notation)

`cve.id`, `cve.known_exploit`, `cve.last_modified_date`, `cve.severity`, `cve.score`,
`cve.description`, `cve.is_in_cisa_kev`, `cve.cisa_exploit_add`, `cve.cisa_action_due`,
`cve.cisa_required_action`, `cve.cisa_short_description`, `cve.cisa_notes`

---

## /max/partner/breaches

```
GET /max/partner/breaches
Params: recent_only=true, triaged=true, report=true, first_seen=<json>, page=<int>
Headers: version: deprecated   ← CRITICAL — beta header returns wrong data
```

Page size: **50**.

### Filter params
```python
params = {
    "recent_only": "true",
    "triaged":     "true",       # optional
    "report":      "true",       # optional
    "first_seen":  json.dumps({"operator": "gt", "value": DATE_FROM}),
    "page":        0,
}
```

### Response fields (all live-confirmed)

| Field | Type | Notes |
|---|---|---|
| `vendor_id` | str | **Required for PUT triage** |
| `vendor_domain` | str | |
| `vendor_name` | str | |
| `breach_id` | str | **Required for PUT triage** |
| `description` | str | Breach description |
| `link` | str | External reference URL |
| `published_date` | str | ISO datetime |
| `customers` | list[dict] | Same structure as findings |
| `report` | bool | |
| `triaged` | bool | |
| `is_active_breach` | bool | |
| `edited_at` | str | ISO datetime |
| `triaged_at` | str | ISO datetime |
| `edited_by` | str | |

---

## /max/partner/indicators/exclusion

```
GET /max/partner/indicators/exclusion
Headers: version: beta
```

No params required. Returns all exclusions (44 on this account as of May 2026).

### Response fields

| Field | Type | Values |
|---|---|---|
| `scope` | str | `"customer"` or `"vendor"` |
| `issue_type_name` | str | Issue type to exclude |
| `vendor_domain` | str | Vendor this exclusion applies to |
| `customer_domain` | str | Customer this exclusion applies to |

### Applying exclusions to findings
```python
# Customer-level exclusions (by customer_domain + issue_name)
df_ex_cust   = df_ex[df_ex['scope'] == 'customer'][['issue_type_name', 'customer_domain']]
df_ex_vendor = df_ex[df_ex['scope'] == 'vendor'][['issue_type_name', 'vendor_domain']]

df_ex_cust.rename(columns={'issue_type_name': 'issue_name'}, inplace=True)
df_ex_vendor.rename(columns={'issue_type_name': 'issue_name'}, inplace=True)

df_ex_cust['_remove']   = True
df_ex_vendor['_remove'] = True

df = pd.merge(df, df_ex_cust, how='left', on=['customer_domain', 'issue_name'])
df = df[df['_remove'] != True].drop(columns='_remove')

df = pd.merge(df, df_ex_vendor, how='left', on=['vendor_domain', 'issue_name'])
df = df[df['_remove'] != True].drop(columns='_remove')
```

---

## Parsing the `customers` Nested Field

Both findings and breaches have a `customers` field that is a list of `{domain, name}` dicts.
After `pd.json_normalize` it comes through as a stringified list and needs manual expansion:

```python
import re

def expand_customers(df: pd.DataFrame) -> pd.DataFrame:
    """Explode the customers field into one row per (finding, customer)."""
    df['customers'] = df['customers'].astype(str)
    s = df['customers'].str.split("},").apply(pd.Series, 1).stack()
    s.index = s.index.droplevel(-1)
    del df['customers']
    s.name = 'customers'
    df = df.join(s)

    def extract_domain(text):
        m = re.search(r"'domain': '(.*?)'", str(text))
        return m.group(1) if m else ''

    df['customer_domain'] = df['customers'].apply(extract_domain)
    return df.reset_index(drop=True)
```

---

## Date Filtering Business Logic

| Variable | Default | Use |
|---|---|---|
| `DATE_FROM` | yesterday | Filter `first_seen`, `triaged_at` |
| `DATE_FROM_72` | 3 days ago | Filter `first_observed_at`, `published_date` |
| `DATE_TODAY` | today | Output file naming |

### Standard filter pipeline for daily reports
```python
# Parse dates
df['triaged_at']       = pd.to_datetime(df['triaged_at']).dt.strftime('%Y-%m-%d')
df['first_observed_at']= pd.to_datetime(df['first_observed_at']).dt.strftime('%Y-%m-%d')

# Apply date windows
df = df[df['triaged_at'] >= DATE_FROM]
df = df[df['first_observed_at'] >= DATE_FROM_72]

# Only report=True rows
df['report'] = df['report'].astype(str)
df = df[df['report'] == 'True']
```

---

## /companies/{domain}/history/events/breaches

```
GET /companies/{domain}/history/events/breaches
Headers: standard (no version header needed)
```

Returns historical breach events for a single vendor domain.

### Response fields

| Field | Type | Notes |
|---|---|---|
| `date` | str | ISO datetime — parse with `errors='coerce'` |
| `event_type` | str | e.g. `"breach"` |
| `breach_data.description` | str | Breach narrative |
| `breach_data.link` | str | External reference URL |

### Usage pattern
```python
# Always fetch in parallel across vendor list
def fetch_breaches(domain: str) -> pd.DataFrame:
    r = ssc_request("get", f"{BASE_URL}/companies/{domain}/history/events/breaches", headers=HEADERS)
    if r.status_code != 200: return pd.DataFrame()
    df = pd.json_normalize(r.json().get("entries", []))
    df["vendor"] = domain
    return df
```

---

## /companies/{domain}/history/events/

```
GET /companies/{domain}/history/events/
Params: date_from=<ISO>, date_to=<ISO>   (URL-encoded T00:00:00.000Z suffix)
Headers: standard
```

Returns all security events for a vendor in a date window. Use for weekly findings collection.

```python
url = (f"{BASE_URL}/companies/{domain}/history/events/"
       f"?date_from={DATE_FROM}T00%3A00%3A00.000Z"
       f"&date_to={DATE_TO}T00%3A00%3A00.000Z")
```

Filter to `group_status == "active"` after fetch.

---

## /companies/{domain}/issues/{issue_type}

```
GET /companies/{domain}/issues/{issue_type}
Headers: standard
```

Returns detailed finding records for a single (domain, issue_type) pair.
Always fetch in parallel — never loop sequentially over findings pairs.

### Key fields

| Field | Type | Notes |
|---|---|---|
| `issue_id` | str | Unique finding ID |
| `parent_domain` | str | Vendor domain |
| `first_seen_time` | str | ISO8601 — use `format="ISO8601"` |
| `last_seen_time`  | str | ISO8601 |
| `effective_date`  | str | ISO8601 |
| `vulnerability_id` | str | CVE ID if applicable |
| `vulnerability_description` | str | CVE description |
| `connection_attributes.dst_ip` | str | Target IP |
| `connection_attributes.dst_port` | str | Target port |
| `product_name` | str | Affected product |

---

## /vendor-detection/{domain}/products

```
GET /vendor-detection/{domain}/products
Headers: standard
```

Returns products detected in use by a vendor domain. Fetch in parallel.

### Key fields: `name` (product name), `vendor_domain` (echoed back)

---

## /vendor-detection/{domain}/third-party

```
GET /vendor-detection/{domain}/third-party
Params: limit=10000
Headers: standard
```

Returns third-party vendors detected communicating with this domain.

### Key fields: `domain` (third-party domain), `score` (SSC score)

---

## /companies/{domain}/summary/factors

```
GET /companies/{domain}/summary/factors
Headers: standard
```

Returns current factor scores (snapshot). **Prefer this over** `/history/factors/score`
for weekly reporting — same data, no pagination, no dedup needed.

### Key fields: `name` (factor key), `score` (0–100)

```python
# Factor name mapping
FACTOR_LABELS = {
    "application_security": "Application Security",
    "cubit_score":          "Cubit Score",
    "dns_health":           "DNS Health",
    "endpoint_security":    "Endpoint Security",
    "hacker_chatter":       "Hacker Chatter",
    "ip_reputation":        "IP Reputation",
    "leaked_information":   "Leaked Information",
    "network_security":     "Network Security",
    "patching_cadence":     "Patching Cadence",
    "social_engineering":   "Social Engineering",
}
```

---

## /portfolios (CRUD)

```
POST /portfolios          — create: json={"name": "<name>"}
GET  /portfolios          — list all
GET  /portfolios/{id}/companies — list members (returns domain, score, grade, last30days_score_change)
PUT  /portfolios/{id}/companies/{domain} — add vendor
DELETE /portfolios/{id}/companies/all    — clear all vendors
DELETE /portfolios/{id}                  — delete portfolio
```

### Portfolio usage notes
- After POST, poll GET list for up to 60s until the portfolio appears — do not `time.sleep(30)`.
- Use `params=` for customer_name and page args — never manually URL-encode with `.replace()`.
- `last30days_score_change` field name on portfolio company records (note: different from MAX API spelling `last30day_score_change`).

---

## /max/partner/documents

> **Base URL for this endpoint is `platform-api.securityscorecard.io`** — different from
> the standard `api.securityscorecard.io` used by all other MAX endpoints.

```
POST https://platform-api.securityscorecard.io/max/partner/documents   ← upload
GET  https://platform-api.securityscorecard.io/max/partner/documents   ← retrieve (inferred)
Headers: Authorization: Token {key}, version: beta, accept: application/json
```

ZDaaS reports are automatically uploaded here after generation. Each report is associated
with a `customerId` and optionally with specific `vendor_id`s.

### Upload (POST) — confirmed from PROD_ZDaaS_Report_Generation notebook

```python
PLATFORM_BASE = "https://platform-api.securityscorecard.io"
UPLOAD_HEADERS = {
    "accept":        "application/json",
    "Authorization": f"Token {API_KEY}",
    "version":       "beta",
    # Note: do NOT set Content-Type — requests sets multipart boundary automatically
}

def upload_to_max_dashboard(file_path, customer_id, description, vendor_ids=None):
    mime_type = (
        "application/pdf"
        if file_path.endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    files       = {"file": (os.path.basename(file_path), open(file_path, "rb"), mime_type)}
    payload     = {"customerId": customer_id, "description": description}
    if vendor_ids:
        payload["associatedVendorIds"] = json.dumps(vendor_ids)  # JSON string, not list

    resp = requests.post(
        f"{PLATFORM_BASE}/max/partner/documents",
        headers=UPLOAD_HEADERS,
        files=files,
        data=payload,
    )
    return resp.status_code in (200, 201, 202)
```

**Description format used in production:**
```
"{CVE_ID} ZDaaS Investigation Report - Generated {Month DD, YYYY}"
```
Filter GET results on `description.contains("ZDaaS")` to identify ZDaaS reports.

### Retrieve (GET) — inferred, validate on first run

```python
PLATFORM_BASE = "https://platform-api.securityscorecard.io"

r = requests.get(
    f"{PLATFORM_BASE}/max/partner/documents",
    headers=UPLOAD_HEADERS,
    params={"customerId": customer_id},
)
if r.status_code == 200:
    print("[VALIDATE] Raw response:", r.text[:500])   # confirm field names
    docs = r.json().get("entries", r.json().get("documents", []))
```

**Expected response fields (validate and update):**

| Field | Notes |
|---|---|
| `customerId` | UUID of the customer this document belongs to |
| `description` | Free-text; ZDaaS format: `"{CVE} ZDaaS Investigation Report - Generated {date}"` |
| `associatedVendorIds` | List of vendor UUIDs the report is associated with |
| `filename` / `name` | Original filename — PDF or XLSX |
| `created_at` / `uploadedAt` | Upload timestamp — use to filter by report window |

### ZDaaS lookup pattern

```python
from datetime import datetime, timedelta

# Find ZDaaS documents uploaded in the report window
r = requests.get(
    f"{PLATFORM_BASE}/max/partner/documents",
    headers=UPLOAD_HEADERS,
    params={"customerId": customer_id},
)
zdaas_from_max = []
if r.status_code == 200:
    for doc in r.json().get("entries", r.json().get("documents", [])):
        desc    = str(doc.get("description", ""))
        created = str(doc.get("created_at", doc.get("uploadedAt", "")))[:10]
        if "zdaas" in desc.lower() and created >= DATE_FROM:
            cve_match = re.search(r"CVE-\d{4}-\d+", desc, re.IGNORECASE)
            zdaas_from_max.append({
                "cve_id":      cve_match.group(0) if cve_match else "Unknown CVE",
                "report_date": created,
                "description": desc,
                "source":      "MAX workstation",
            })
```

### Critical notes
- `associatedVendorIds` is uploaded as a **JSON string** (`json.dumps(list)`), not a raw list
- The `customerId` is the UUID from `/max/partner/managed-customers` (`customer_id` field)
- `vendor_id` values come from the `/max/partner/vendors` response
- Successful upload returns HTTP 200, 201, or 202
