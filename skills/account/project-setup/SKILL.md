---
name: project-setup
description: >
  One-time setup skill that builds a project manifest for a MAX Managed Services
  customer project. Run this once when creating a new customer project — it pulls
  what it can from live sources (MAX API, Drive, contact sheet) and asks for the
  rest, then produces a ready-to-paste markdown table for the project instructions.
  Trigger on "set up this project", "create the manifest", "initialize this project",
  "run project setup", or "first time setup for [client]". Also trigger automatically
  when weekly-analyst-summary detects a missing manifest.
---

# Project Setup — Customer Manifest Builder

## Purpose

Builds the project manifest that `weekly-analyst-summary` reads at the start of every
run. Run once per customer project. Output is a markdown table to paste directly into
the project instructions.

The manifest eliminates runtime Drive lookups, MAX API customer searches, and contact
sheet queries — the weekly skill reads values directly from project instructions and
skips the corresponding discovery steps.

---

## Manifest Schema

The manifest is a markdown table with two columns: `Field` and `Value`.
It is wrapped in a fenced code block with the tag `project-manifest` so the weekly
skill can locate it reliably even if other content exists in the project instructions.

**Canonical fields:**

| Field | Description | Source |
|---|---|---|
| `customer_name` | Exact name — must match Col A of contact sheet | MAX API / practitioner |
| `customer_domain` | Primary domain — MAX API join key | MAX API |
| `max_customer_id` | MAX workstation customer ID | MAX API |
| `weekly_reporting_folder_id` | Drive folder ID for Weekly Reporting subfolder | Drive search |
| `tier_composition` | Comma-separated tiers present (e.g. `Platinum, Gold`) | MAX API |
| `vendor_count` | Total vendor count at time of setup | MAX API |
| `zdaas_active` | `true` / `false` — whether ZDaaS folder exists for this customer | Drive search |
| `contacts_on_file` | `true` / `false` — whether vendor contacts exist in contact sheet | Contact sheet |
| `scope` | `All` or specific tier | Practitioner |
| `poc_name` | Primary contact name at customer org | Practitioner |
| `delivery_notes` | Account-specific voice, framing, or delivery preferences | Practitioner |
| `manifest_version` | Timestamp of last setup run — used for staleness detection | Auto-generated |

**Staleness triggers** (weekly skill flags these for re-run):
- `tier_composition` differs from live MAX API data
- `vendor_count` differs by more than 10% from live MAX API data
- `weekly_reporting_folder_id` no longer resolves in Drive

---

## Workflow

### Step 0 — Confirm customer identity

```python
import os, requests, base64, io, re as _re
from datetime import datetime

BASE_URL = "https://api.securityscorecard.io"
CUSTOMER_CONTACT_FILE_ID = "1DH4ZRA5yprnhJfMh544Hsk-5OP_LNGnt"
ZDAAS_ROOT_ID            = "1ct04IJBZ8qVe3bzX2pA_lD5aBw4u6Z_f"

# Read customer name from project context (project name = customer name)
# Claude sets this from the project it is running in.
_project_name = (
    os.environ.get("PROJECT_NAME") or
    os.environ.get("CLAUDE_PROJECT_NAME") or
    None
)
# If not in env, Claude reads it from context directly and assigns _project_name.

if not _project_name:
    raise RuntimeError(
        "Run this skill inside the customer's Claude project. "
        "The project name is used as the customer name."
    )

print(f"[SETUP] Customer project: '{_project_name}'")
print(f"[SETUP] Starting manifest build — this will take about 30 seconds.")
```

---

### Step 1 — Pull customer data from MAX API

```python
TOKEN        = os.environ.get("SSC_API_TOKEN", "")
HEADERS_BETA = {
    "accept":        "application/json",
    "Authorization": f"Token {TOKEN}",
    "version":       "beta",
}

max_customer_id    = None
customer_name      = _project_name   # confirmed or corrected below
customer_domain    = None
tier_composition   = []
vendor_count       = 0

if not TOKEN:
    print("[WARN] No SSC_API_TOKEN — skipping MAX API lookup. "
          "Run vroc-session-init first for best results, or fill fields manually.")
else:
    try:
        # Fetch managed customer list
        resp = requests.get(
            f"{BASE_URL}/max/partner/managed-customers",
            headers=HEADERS_BETA
        )
        resp.raise_for_status()
        customers = resp.json().get("entries", [])

        # Match on project name (partial, case-insensitive)
        matched = next(
            (c for c in customers
             if _project_name.lower() in c.get("customer_name","").lower()
             or c.get("customer_name","").lower() in _project_name.lower()),
            None
        )

        if not matched:
            print(f"[WARN] No MAX customer matched '{_project_name}'.")
            print(f"       Available: {[c.get('customer_name') for c in customers]}")
            print("       Proceeding with project name as customer_name. "
                  "Correct manually if needed.")
        else:
            max_customer_id = matched.get("customer_id")
            customer_name   = matched.get("customer_name", _project_name)
            customer_domain = matched.get("customer_domain", "")
            print(f"[SETUP] MAX match: '{customer_name}' (ID: {max_customer_id})")

        # Fetch vendor list for tier composition
        if max_customer_id:
            import math
            vresp = requests.get(
                f"{BASE_URL}/max/partner/vendors",
                headers=HEADERS_BETA,
                params={"customer_name": customer_name, "page": 0}
            )
            if vresp.status_code == 200:
                vdata       = vresp.json()
                total       = vdata.get("total", 0)
                page_size   = vdata.get("size") or 100
                all_vendors = list(vdata.get("entries", []))

                if total > page_size:
                    for page in range(1, math.ceil(total / page_size)):
                        r = requests.get(
                            f"{BASE_URL}/max/partner/vendors",
                            headers=HEADERS_BETA,
                            params={"customer_name": customer_name, "page": page}
                        )
                        if r.status_code == 200:
                            all_vendors.extend(r.json().get("entries", []))

                vendor_count = len(all_vendors)
                _tiers_raw   = [
                    str(v.get("tier","")).strip().title()
                    for v in all_vendors
                    if v.get("tier")
                ]
                tier_composition = sorted(
                    set(_tiers_raw),
                    key=lambda t: {"Platinum":0,"Gold":1,"Silver":2}.get(t, 9)
                )
                print(f"[SETUP] Vendors: {vendor_count} | Tiers: {tier_composition}")

    except Exception as e:
        print(f"[WARN] MAX API error: {e}. Fill fields manually.")
```

---

### Step 2 — Load contact sheet and resolve folder name

```python
import openpyxl

domain_to_folder = {}
contacts_on_file = False

try:
    # MCP call: Google Drive:download_file_content(fileId=CUSTOMER_CONTACT_FILE_ID)
    # _contact_dl = <MCP result>
    contact_bytes = base64.b64decode(_contact_dl["content"])
    contact_wb    = openpyxl.load_workbook(io.BytesIO(contact_bytes), read_only=True)
    contact_ws    = contact_wb["Sheet1"]

    for row in contact_ws.iter_rows(min_row=2, values_only=True):
        folder = str(row[0]).strip() if row[0] else None
        domain = str(row[1]).strip().lower() if row[1] else None
        if folder and domain:
            domain_to_folder[domain] = folder

    # Resolve folder name: domain match first, then name match
    _folder_name = None
    if customer_domain:
        _folder_name = domain_to_folder.get(customer_domain.lower())
    if not _folder_name:
        _folder_name = next(
            (v for k, v in domain_to_folder.items()
             if customer_name.lower() in v.lower()
             or v.lower() in customer_name.lower()),
            customer_name   # fall back to customer_name as-is
        )

    print(f"[SETUP] Contact sheet folder name: '{_folder_name}'")

    # Check if vendor contacts exist for this customer
    # MCP call: Google Drive:search_files(
    #   query = "title contains 'MS_Vendor_Contact_Details-Prod'",
    #   pageSize = 5
    # )
    # Sort by modifiedTime desc, take first result
    # MCP call: Google Drive:download_file_content(fileId=<result id>)
    # Parse Sheet1 — columns: name, first_name, email, domain, send, customer, customer_cc
    # contacts_on_file = True if any row where customer column contains customer_name

    # In a Cowork session Claude executes the above MCP calls directly and sets:
    #   contacts_on_file = True/False based on whether rows match customer_name
    # Log result:
    print(f"[SETUP] Contacts on file: {contacts_on_file}")

except Exception as e:
    _folder_name = customer_name
    print(f"[WARN] Contact sheet unavailable ({e}). Using customer name as folder name.")
```

---

### Step 3 — Find Weekly Reporting folder ID in Drive

```python
weekly_reporting_folder_id = None

try:
    # Step 3a: Find customer top-level folder
    # MCP call: Google Drive:search_files(
    #   query = "title = '<_folder_name>'
    #            and mimeType = 'application/vnd.google-apps.folder'"
    # )
    # _customer_folders = <MCP result>.get("files", [])
    # _customer_folder  = next(
    #     (f for f in _customer_folders
    #      if f["name"].lower() == _folder_name.lower()),
    #     _customer_folders[0] if _customer_folders else None
    # )

    # Step 3b: Find Weekly Reporting subfolder inside customer folder
    # MCP call: Google Drive:search_files(
    #   query = "title = 'Weekly Reporting'
    #            and '<_customer_folder[\"id\"]>' in parents
    #            and mimeType = 'application/vnd.google-apps.folder'"
    # )
    # _wr_folders = <MCP result>.get("files", [])
    # weekly_reporting_folder_id = _wr_folders[0]["id"] if _wr_folders else None

    # In a Cowork session Claude executes both MCP calls above and sets
    # weekly_reporting_folder_id to the resolved folder ID.

    if weekly_reporting_folder_id:
        print(f"[SETUP] Weekly Reporting folder ID: {weekly_reporting_folder_id}")
    else:
        print("[WARN] Weekly Reporting folder not found in Drive.")
        print("       Check that the folder exists at:")
        print(f"       My Drive / {_folder_name} / Weekly Reporting")
        print("       Create it if missing, then re-run setup.")

except Exception as e:
    print(f"[WARN] Drive folder search failed ({e}). Fill weekly_reporting_folder_id manually.")
```

---

### Step 4 — Check ZDaaS folder

```python
zdaas_active = False

try:
    # MCP call: Google Drive:search_files(
    #   query = "title = '<_folder_name>'
    #            and '<ZDAAS_ROOT_ID>' in parents
    #            and mimeType = 'application/vnd.google-apps.folder'"
    # )
    # _zdaas_folders = <MCP result>.get("files", [])
    # zdaas_active   = len(_zdaas_folders) > 0

    # In a Cowork session Claude executes the MCP call above and sets
    # zdaas_active = True if a matching subfolder is found under ZDAAS_ROOT_ID.

    print(f"[SETUP] ZDaaS active: {zdaas_active}")
    if not zdaas_active:
        print(f"       No ZDaaS folder found for '{_folder_name}' under ZDaaS root.")
        print(f"       If ZDaaS is active for this account, check that the folder name")
        print(f"       matches exactly: '{_folder_name}'")

except Exception as e:
    print(f"[WARN] ZDaaS folder check failed ({e}). Set zdaas_active manually.")
```

---

### Step 5 — Ask practitioner for fields that can't be auto-populated

Present these questions one at a time and collect answers before building the manifest.

```
Questions for practitioner:

1. Scope — does this customer receive All-tier reports, or a specific tier?
   Options: All | Platinum | Gold | Silver
   Default: All

2. Primary contact name at the customer org (the person who reads the report)?
   Enter name or press Enter to leave blank.

3. Delivery notes — any account-specific voice, framing, or preferences?
   Examples:
     - "Compliance framing preferred. Reference NIST/FISMA."
     - "Executive audience — keep bullet points very short."
     - "Technical POC only — include indicator detail."
   Enter notes or press Enter to leave blank.
```

```python
# Collect answers interactively in the Cowork session
# scope         = input or "All"
# poc_name      = input or ""
# delivery_notes = input or ""

# Defaults if not provided
scope          = scope          if "scope"          in dir() else "All"
poc_name       = poc_name       if "poc_name"       in dir() else ""
delivery_notes = delivery_notes if "delivery_notes" in dir() else ""
```

---

### Step 6 — Build and display the manifest

```python
manifest_version = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

# Format tier_composition as comma-separated string
_tier_str = ", ".join(tier_composition) if tier_composition else "Gold"

# Build markdown table rows
_fields = [
    ("customer_name",               customer_name or _project_name),
    ("customer_domain",             customer_domain or "⚠️ fill manually"),
    ("max_customer_id",             max_customer_id or "⚠️ fill manually"),
    ("weekly_reporting_folder_id",  weekly_reporting_folder_id or "⚠️ fill manually"),
    ("tier_composition",            _tier_str),
    ("vendor_count",                str(vendor_count) if vendor_count else "⚠️ fill manually"),
    ("zdaas_active",                str(zdaas_active).lower()),
    ("contacts_on_file",            str(contacts_on_file).lower()),
    ("scope",                       scope),
    ("poc_name",                    poc_name or ""),
    ("delivery_notes",              delivery_notes or ""),
    ("manifest_version",            manifest_version),
]

# Any field with ⚠️ needs manual completion before use
_needs_manual = [f for f, v in _fields if "⚠️" in str(v)]

# Render markdown table
_rows = "\n".join(f"| `{f}` | {v} |" for f, v in _fields)

manifest_output = f"""```project-manifest
| Field | Value |
|---|---|
{_rows}
```"""

print("\n" + "="*60)
print("MANIFEST — copy everything between the lines below")
print("="*60)
print(manifest_output)
print("="*60)

if _needs_manual:
    print(f"\n⚠️  {len(_needs_manual)} field(s) need manual completion:")
    for f in _needs_manual:
        print(f"   • {f}")
    print("\nEdit those values in the table before pasting into project instructions.")
else:
    print("\n✅ All fields resolved automatically. Ready to paste.")

print(f"\nManifest version: {manifest_version}")
print("Paste into: Project Instructions (above or below existing content)")
```

---

### Step 7 — Validation summary

After building the manifest, print a clear summary of what was confirmed vs. what needs attention:

```python
_confirmed  = [(f, v) for f, v in _fields if "⚠️" not in str(v) and v]
_manual     = [(f, v) for f, v in _fields if "⚠️" in str(v)]
_optional   = [(f, v) for f, v in _fields if not v and "⚠️" not in str(v)]

print("\n📋 SETUP SUMMARY")
print(f"   ✅ Confirmed automatically : {len(_confirmed)} fields")
for f, v in _confirmed:
    print(f"      {f}: {v}")

if _manual:
    print(f"\n   ⚠️  Needs manual input     : {len(_manual)} fields")
    for f, _ in _manual:
        print(f"      {f}")

if _optional:
    print(f"\n   ℹ️  Left blank (optional)  : {len(_optional)} fields")
    for f, _ in _optional:
        print(f"      {f}")

print("\nNext steps:")
print("  1. Copy the manifest block above")
print("  2. Paste into this project's Instructions")
print("  3. Fill in any ⚠️ fields manually")
print("  4. Run weekly-analyst-summary — it will read the manifest automatically")
```

---

## Hard Rules

- **Never fabricate values.** If a field can't be resolved, mark it `⚠️ fill manually`.
- **Never overwrite an existing manifest** without confirming with the practitioner first.
  If a manifest already exists in project instructions, show a diff of what changed
  and ask before replacing.
- **Manifest version is always UTC ISO 8601.** Do not use local time.
- **The project name is the customer name.** Do not prompt for customer name —
  read it from project context.
- **delivery_notes are practitioner-supplied only.** Do not infer them from data.
