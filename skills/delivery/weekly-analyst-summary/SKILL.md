---
name: weekly-analyst-summary
description: >
  Produce the weekly MAX Vendor Report analyst content for SecurityScorecard Managed
  Services. Handles Drive file retrieval, historical trending, all analytical sections,
  and Outreach Status Update. Outputs reviewed analyst text ready for slide build.
  Trigger on "analyst summary", "weekly summary", "run the weekly", "write the weekly
  for [client]", or any Weekly_Vendor_Report file upload. Default run context is a
  Cowork session with GDrive mapped. Do NOT use for Monthly ROI, QBR, ZDaaS
  notifications, reactive breach reports, or slide building (separate skill).
---

# Weekly Analyst Summary & PPTX Rebuild

## Purpose

Produce the weekly analyst content for the MAX Weekly Vendor Report.
Outputs reviewed analyst text ready to hand off to the slide build skill.
Does not build or modify any PPTX files.

**Canonical section order (analyst output):**
```
1.  New Concerning Findings   ← always; rebuilt every run
2.  Breaches                  ← omit + delete if none this period
3.  ZDaaS Reports             ← omit + delete if none this period
4.  KEV Tracking              ← omit + delete if no KEV findings
5.  Sub-C Vendors             ← omit + delete if no D/F vendors
6.  Fourth-Party Risk         ← omit + delete if no concentration story
7.  Score Movements           ← vendors with ≥±15 pt 30-day change only; omit if none
8.  Recommended Next Actions  ← MAX Team actions + Your Team actions; no scorecard table
last. Outreach Status Update  ← Platinum only; always last; email-sourced
```

Script-generated slides from Colab are **never modified** — this skill inserts/rebuilds
only the analyst-facing slides listed above.

---

## Run Modes

Detected automatically — do not ask the user.

### Mode A — API Mode *(preferred)*
Requires `SSC_API_TOKEN` in environment (via `vroc-session-init` or `.vroc_keys`).
All vendor, tier, score, and delta data pulled live from MAX + SSC APIs.
Driftnet enrichment available if `DRIFTNET_API_TOKEN` also loaded.

### Mode B — Export Mode *(fallback)*
Requires MAX workstation CSV export in session or working directory.
**Export CSV schema:** `Vendor, Domain, Customer, Tier, Grade, Risk Status, Business Impact,
Incident Likelihood, Assessment Trend, Initial Assessment, Previous Assessment, Custom Tags, Date Added`
`Tier` is lowercase in export — normalize to title case on load.

**Export Mode limitations (surface to user on start):**
- No live scores or 30-day deltas — grade used as proxy; Score Movements slide omitted
- No Driftnet enrichment
- Contact sheet + ZDaaS Drive lookups still run (Drive MCP, no token needed)
- Outreach Status Update still runs (Gmail MCP, no token needed)

### Mode detection
```python
import os, glob

SSC_TOKEN   = os.environ.get("SSC_API_TOKEN", "").strip()
DRIFT_TOKEN = os.environ.get("DRIFTNET_API_TOKEN", "").strip()

def _is_max_export(path):
    try:
        with open(path) as f:
            h = f.readline().lower()
            return "vendor" in h and "domain" in h and "tier" in h
    except Exception:
        return False

_export_candidates = (
    glob.glob("/mnt/user-data/uploads/MAX_Export*.csv") +
    glob.glob("/mnt/user-data/uploads/*.csv") +
    glob.glob(os.path.join(os.getcwd(), "MAX_Export*.csv"))
)
max_export_path = next((p for p in _export_candidates if _is_max_export(p)), None)

if SSC_TOKEN:
    RUN_MODE = "api"
    print(f"[MODE] API — SSC token present. Driftnet: {'✓' if DRIFT_TOKEN else '✗'}")
elif max_export_path:
    RUN_MODE = "export"
    print(f"[MODE] Export — using: {os.path.basename(max_export_path)}")
    print("[WARN] Export Mode: no live scores, no deltas, Score Movements slide omitted.")
else:
    raise RuntimeError(
        "Cannot proceed: no SSC_API_TOKEN and no MAX export CSV found.\n"
        "Either:\n"
        "  1. Upload .vroc_keys and run vroc-session-init, OR\n"
        "  2. Export vendors from MAX Workstation and upload the CSV."
    )
```

---

## Prerequisites

- **Always run inside a named Claude project.** The project name must match Column A
  of `MS_Customer_Contact_Details-Prod.xlsx` exactly (e.g. `State of New Mexico`).
  This is how the skill knows which customer it is running for — no file upload needed
  to identify the customer.
- **API Mode:** Upload `.vroc_keys`, run `vroc-session-init`.
- **Export Mode:** Export CSV from MAX Workstation UI, upload to session.
- **Both modes:** Cowork session with GDrive mapped (preferred). If GDrive is mapped,
  the skill finds and downloads all required files automatically from the customer's
  `Weekly Reporting` folder. If not mapped, skill will prompt for manual file uploads.

---

## Inputs

**Datasheet filename pattern (authoritative):**
```
<CustomerFolderName>-Weekly_Vendor_Report-<Scope>-Generated_on-YYYY-MM-DD.xlsx
```
- `<CustomerFolderName>` — matches Column A of `MS_Customer_Contact_Details-Prod.xlsx`
- `<Scope>` — `All` or a specific tier name; never use to infer tier (always from MAX API/export)
- `Generated_on-YYYY-MM-DD` — report date

---

## Workflow

### Step 0 — Session startup and file resolution

**This is the first step every run.** Resolves all required files before any analysis.
Default path is GDrive lookup; fallback is manual upload prompt.

**Reads the project manifest first.** If a manifest exists in the project instructions,
values are loaded directly — skipping the corresponding runtime lookups. If the manifest
is missing, a staleness warning is shown and the skill falls back to full discovery.

```python
import os, glob, re as _re, io
from datetime import datetime, timedelta

TODAY        = datetime.today()
LOOKBACK_MIN = TODAY - timedelta(days=35)   # 4 weeks + 3-day buffer
LOOKBACK_MAX = TODAY + timedelta(days=3)    # allow minor clock skew

CUSTOMER_CONTACT_FILE_ID = "1DH4ZRA5yprnhJfMh544Hsk-5OP_LNGnt"

# ── 0a: Read project manifest from project instructions ───────────────────────
# The manifest is a markdown table wrapped in ```project-manifest ... ```
# Parse it into a dict; use values to skip runtime lookups where available.
# If manifest is missing or any required field is absent → fall through to discovery.
#
# Claude reads the project instructions directly from context — no MCP call needed.
# The manifest block is identified by the ```project-manifest fence tag.

_manifest = {}   # populated below if manifest found

# In a Cowork/Claude session: project instructions are available in context.
# Claude extracts the project-manifest block and parses it here.
# Assign _manifest_raw to the raw content of the ```project-manifest block
# before running this section.

if "_manifest_raw" in dir() and _manifest_raw:
    try:
        for line in _manifest_raw.strip().splitlines():
            # Parse markdown table rows: | `field` | value |
            _m = _re.match(r"\|\s*`?([a-z_]+)`?\s*\|\s*(.+?)\s*\|", line)
            if _m:
                _key = _m.group(1).strip()
                _val = _m.group(2).strip()
                if _val and _val != "⚠️ fill manually":
                    _manifest[_key] = _val
        print(f"[MANIFEST] Loaded {len(_manifest)} fields from project manifest.")
    except Exception as e:
        print(f"[WARN] Manifest parse error: {e}. Falling back to full discovery.")
        _manifest = {}
else:
    print("[WARN] No project manifest found in project instructions.")
    print("       Run the 'project-setup' skill to generate one.")
    print("       Continuing with full runtime discovery — this will take longer.")
    print("       ⚠️  First run without a manifest: expect slower startup and")
    print("       possible missing fields. Run project-setup after this run.")

# ── Convenience accessors — use manifest values where present ─────────────────
_m_customer_name     = _manifest.get("customer_name")
_m_customer_domain   = _manifest.get("customer_domain")
_m_max_customer_id   = _manifest.get("max_customer_id")
_m_wr_folder_id      = _manifest.get("weekly_reporting_folder_id")
_m_tier_composition  = [t.strip() for t in _manifest.get("tier_composition","").split(",") if t.strip()]
_m_vendor_count      = int(_manifest.get("vendor_count","0") or 0)
_m_zdaas_active      = _manifest.get("zdaas_active","").lower() == "true"
_m_contacts_on_file  = _manifest.get("contacts_on_file","").lower() == "true"
_m_scope             = _manifest.get("scope", "All")
_m_manifest_version  = _manifest.get("manifest_version")

# ── Staleness check ───────────────────────────────────────────────────────────
# Run after Step 2 loads live vendor data.
# Flags are set here; actual comparison happens after vendor data is loaded.
_manifest_stale      = False
_stale_reasons       = []

# If manifest is present but has no version, it predates versioning — flag it
if _manifest and not _m_manifest_version:
    _manifest_stale = True
    _stale_reasons.append("manifest_version missing — manifest may be outdated")

# If manifest version is older than 90 days — prompt for refresh
if _m_manifest_version:
    try:
        _mv_dt = datetime.strptime(_m_manifest_version, "%Y-%m-%dT%H:%M:%SZ")
        _age_days = (TODAY - _mv_dt).days
        if _age_days > 90:
            _manifest_stale = True
            _stale_reasons.append(
                f"manifest is {_age_days} days old (last run: {_m_manifest_version[:10]})"
            )
    except Exception:
        pass

# NOTE: tier_composition and vendor_count staleness checks run in Step 2
# after live vendor data is loaded — see Step 2 staleness validation block.
```
# Col A = Drive folder name (exact), Col B = customer domain
# Used for: ZDaaS folder lookup, Weekly Reporting folder lookup, PPTX/datasheet lookup
#
# Drive MCP call: Google Drive:download_file_content
#   fileId = CUSTOMER_CONTACT_FILE_ID
#   returns base64-encoded xlsx bytes
# Decode with base64.b64decode() before passing to openpyxl.

import base64, openpyxl

domain_to_folder = {}
folder_to_domain = {}

try:
    # ── Download contact sheet via Drive MCP ──────────────────────────────────
    # In a Cowork/Claude session this is a direct MCP tool call.
    # In a standalone Colab notebook, replace with:
    #   from googleapiclient.discovery import build
    #   drive = build('drive', 'v3', credentials=creds)
    #   resp = drive.files().get_media(fileId=CUSTOMER_CONTACT_FILE_ID).execute()
    #   contact_file_bytes = resp

    # MCP call returns: {"content": "<base64string>", ...}
    # Assign result of: Google Drive:download_file_content(fileId=CUSTOMER_CONTACT_FILE_ID)
    # to _contact_dl before running this block.

    if '_contact_dl' not in dir():
        raise RuntimeError("_contact_dl not set — run Drive MCP download first")

    contact_file_bytes = base64.b64decode(_contact_dl["content"])
    contact_wb = openpyxl.load_workbook(io.BytesIO(contact_file_bytes), read_only=True)

    if "Sheet1" not in contact_wb.sheetnames:
        raise RuntimeError(f"Sheet1 not found. Sheets: {contact_wb.sheetnames}")

    contact_ws = contact_wb["Sheet1"]
    for row in contact_ws.iter_rows(min_row=2, values_only=True):
        folder = str(row[0]).strip() if row[0] else None
        domain = str(row[1]).strip().lower() if row[1] else None
        if folder and domain and folder != "None" and domain != "none":
            domain_to_folder[domain] = folder
            folder_to_domain[folder.lower()] = folder

    if not domain_to_folder:
        raise RuntimeError("Contact sheet parsed but no domain→folder rows found. Check Sheet1 Col A/B.")

    print(f"[INFO] Contact sheet loaded: {len(domain_to_folder)} customers")

except Exception as e:
    domain_to_folder = {}
    folder_to_domain = {}
    print(f"[WARN] Could not load contact sheet: {e}")
    print("       Drive folder lookups will use filename-based matching as fallback.")

# ── 0b: Resolve PPTX and datasheets ───────────────────────────────────────────
# Priority order:
#   1. GDrive: <CustomerFolderName>/Weekly Reporting/ — pull current PPTX + last 4 datasheets
#   2. Local: /mnt/user-data/uploads/ or os.getcwd()
#   3. Prompt user to upload if neither found

def _parse_report_date(filename):
    """Extract Generated_on date from filename. Returns datetime or None."""
    m = _re.search(r"Generated_on-(\d{4}-\d{2}-\d{2})", filename)
    return datetime.strptime(m.group(1), "%Y-%m-%d") if m else None

def _within_lookback(filename):
    """True if file's Generated_on date falls within the 35-day lookback window."""
    dt = _parse_report_date(filename)
    return dt is not None and LOOKBACK_MIN <= dt <= LOOKBACK_MAX

def _find_local_files():
    """Find PPTX and datasheets in uploads or working dir."""
    search_dirs = ["/mnt/user-data/uploads", os.getcwd()]
    xlsx_files = []
    for d in search_dirs:
        if os.path.isdir(d):
            xlsx_files += glob.glob(os.path.join(d, "*Weekly_Vendor_Report*.xlsx"))
    return xlsx_files

# ── Attempt GDrive lookup ──────────────────────────────────────────────────────
# Drive MCP calls used in this block:
#   Google Drive:search_files(query=...) — find folders and files by name/parent
#   Google Drive:download_file_content(fileId=...) — download file as base64 string
#
# Resolution order:
#   1. GDrive: <CustomerFolderName>/Weekly Reporting/ folder
#   2. Local: /mnt/user-data/uploads/ or os.getcwd()

gdrive_available           = False
datasheet_path             = None
prior_datasheet_paths      = []
weekly_reporting_folder_id = _m_wr_folder_id or None   # use manifest value if present

if weekly_reporting_folder_id:
    print(f"[MANIFEST] Using Weekly Reporting folder ID from manifest: {weekly_reporting_folder_id}")
    print("           Skipping Drive folder search.")

# ── Step 1: Derive customer folder name ───────────────────────────────────────
# Priority order:
#   1. Project context — skill always runs inside a named customer project.
#      The project name IS the customer name. Read it from the system prompt.
#   2. Filename seed — parse from any locally present Weekly_Vendor_Report file.
#   3. Raise clearly if neither source resolves a name.
#
# Project context is the authoritative source. When Claude is running inside
# a project, the project name is available in the conversation context as
# the value of the <project_name> tag or equivalent system prompt field.
# The skill reads this directly — no file upload needed to identify the customer.

# Read project name from context (Cowork/Claude session)
# In a Claude project session, this is the project name set when the project was created.
# The project name should exactly match Col A of MS_Customer_Contact_Details-Prod.xlsx.

_project_customer = None
try:
    # Claude makes the project name available as a context variable.
    # In a Cowork session: read from the PROJECT_NAME environment variable if set,
    # or from the Claude project system prompt context directly.
    _project_customer = (
        os.environ.get("PROJECT_NAME")          # Cowork may inject this
        or os.environ.get("CLAUDE_PROJECT_NAME") # alternate env var name
        or None
    )
    # If not in env, the skill reads it from the conversation context naturally —
    # Claude knows which project it is running in and uses that name directly.
    # The variable _project_customer is set to that project name by Claude before
    # running this code block.
    if _project_customer:
        print(f"[INFO] Project context: '{_project_customer}'")
except Exception:
    pass

# Filename seed fallback (if project context unavailable)
_local_xlsx_raw = _find_local_files()
_seed_file = next(iter(
    sorted(_local_xlsx_raw,
           key=lambda p: _parse_report_date(os.path.basename(p)) or datetime.min,
           reverse=True)
), None)

_filename_customer = None
if _seed_file:
    _m = _re.match(r"^(.+?)-Weekly_Vendor_Report-", os.path.basename(_seed_file))
    if _m:
        _filename_customer = _m.group(1).strip()

# Resolve final seed — project context wins
_seed_customer = _project_customer or _filename_customer

if not _seed_customer:
    raise RuntimeError(
        "Cannot determine customer name. This skill must be run inside a Claude project "
        "named after the customer (e.g. 'State of New Mexico').\n"
        "Alternatively, upload a Weekly_Vendor_Report file to the session to seed the name."
    )

print(f"[INFO] Customer resolved from: "
      f"{'project context' if _project_customer else 'filename seed'} "
      f"→ '{_seed_customer}'")

# Resolve exact Drive folder name via contact sheet (Col A lookup)
_exact_folder_name = None
if folder_to_domain:
    _exact_folder_name = next(
        (v for k, v in folder_to_domain.items()
         if k == _seed_customer.lower()),
        _seed_customer   # fall back to seed name as-is if not in contact sheet
    )
else:
    _exact_folder_name = _seed_customer

print(f"[INFO] Drive folder name: '{_exact_folder_name}'")

# ── Step 2: Find Weekly Reporting folder in Drive ─────────────────────────────
# MCP call: Google Drive:search_files
#   query = "title = '<_exact_folder_name>' and mimeType = 'application/vnd.google-apps.folder'"
# Then within that result, find the Weekly Reporting subfolder:
#   query = "title = 'Weekly Reporting' and '<customer_folder_id>' in parents
#            and mimeType = 'application/vnd.google-apps.folder'"
# Assign weekly_reporting_folder_id from the result.
# If not found: log [WARN] and skip to local fallback.

# ── Step 3: List Weekly_Vendor_Report files in folder ─────────────────────────
# MCP call: Google Drive:search_files
#   query = "'<weekly_reporting_folder_id>' in parents
#            and title contains 'Weekly_Vendor_Report'"
# Filter results:
#   xlsx files: keep only those with Generated_on date within LOOKBACK_MIN–LOOKBACK_MAX
#   pptx files: keep all (take most recent by Generated_on date)
# Sort each list by Generated_on date descending.

# ── Step 4: Download files ────────────────────────────────────────────────────
# For each file to download:
#   MCP call: Google Drive:download_file_content(fileId=<file_id>)
#   Returns: {"content": "<base64string>", ...}
#   Decode: file_bytes = base64.b64decode(result["content"])
#   Write:  open(<local_dest>, "wb").write(file_bytes)
#
# Download targets:
#   PPTX  → most recent pptx; store file_id as target_drive_file_id
#   XLSX  → most recent xlsx in window → datasheet_path
#   XLSX  → next 3 most recent xlsx in window → prior_datasheet_paths
#
# On any download failure: log [WARN] and fall through to local fallback.

_dl_dir = os.path.join(os.getcwd(), "_gdrive_dl")
os.makedirs(_dl_dir, exist_ok=True)

# ── Local fallback ────────────────────────────────────────────────────────────
# Run when Drive lookup is unavailable or partially failed.
if not datasheet_path:
    print("[INFO] Using local file resolution.")
    _local_xlsx = _find_local_files()

    _local_xlsx_dated = sorted(
        [(p, _parse_report_date(os.path.basename(p)))
         for p in _local_xlsx if _parse_report_date(os.path.basename(p))],
        key=lambda x: x[1], reverse=True
    )
    _local_xlsx_in_window = [
        p for p, dt in _local_xlsx_dated
        if LOOKBACK_MIN <= dt <= LOOKBACK_MAX
    ]

    if not datasheet_path and _local_xlsx_in_window:
        datasheet_path        = _local_xlsx_in_window[0]
    if not prior_datasheet_paths:
        prior_datasheet_paths = _local_xlsx_in_window[1:4]

# ── Validate what we have; prompt if insufficient ──────────────────────────────
missing = []
if not datasheet_path:
    missing.append("current week datasheet  (Weekly_Vendor_Report...xlsx)")
if len(prior_datasheet_paths) < 3:
    have = len(prior_datasheet_paths)
    missing.append(f"prior week datasheets for trending ({have}/3 found within last 35 days)")

if missing:
    print("\n[INPUT NEEDED] The following files are required and were not found in Drive or uploads:")
    for item in missing:
        print(f"  • {item}")
    print("\nPlease upload them to this session, or ensure your GDrive mapping includes")
    print(f"  <CustomerName>/Weekly Reporting/")
    print("Then re-run Step 0.")
    raise FileNotFoundError("Required files missing — see prompt above.")

# ── Parse customer identity from datasheet filename ───────────────────────────
datasheet_filename = os.path.basename(datasheet_path)
_fn_match = _re.match(
    r"^(.+?)-Weekly_Vendor_Report-(.+?)-Generated_on-(\d{4}-\d{2}-\d{2})",
    datasheet_filename,
)
if not _fn_match:
    raise RuntimeError(
        f"Filename '{datasheet_filename}' does not match expected pattern:\n"
        "  <CustomerFolderName>-Weekly_Vendor_Report-<Scope>-Generated_on-YYYY-MM-DD.xlsx"
    )
filename_customer = _fn_match.group(1).strip()   # matches Col A of contact sheet
filename_scope    = _fn_match.group(2).strip()
report_date_str   = _fn_match.group(3).strip()
report_date       = datetime.strptime(report_date_str, "%Y-%m-%d")

# Resolve customer domain from contact sheet (Col A → Col B)
customer_domain = next(
    (dom for folder, dom in {v: k for k, v in domain_to_folder.items()}.items()
     if folder.lower() == filename_customer.lower()),
    ""
)

print(f"[INFO] Customer : {filename_customer}")
print(f"[INFO] Domain   : {customer_domain or '(not resolved — ZDaaS will use name match)'}")
print(f"[INFO] Report   : {report_date_str}  |  Scope: {filename_scope}")
print(f"[INFO] Current  : {datasheet_filename}")
for p in prior_datasheet_paths:
    dt = _parse_report_date(os.path.basename(p))
    print(f"[INFO] Prior    : {os.path.basename(p)}  ({dt.strftime('%Y-%m-%d') if dt else 'date unknown'})")
```

**Trending window:** datasheets must have a `Generated_on` date within 35 days of today.
Files outside this window are excluded from trend analysis even if present.
If fewer than 3 prior-week files are found, warn and continue — trend arrows will be
shorter but analysis is not blocked.

---

## ⛔ Gate 1 — File Confirmation

**STOP HERE. Confirm files are correct and fresh before running any API calls or analysis.**

### Freshness check

A file is **fresh** if its `Generated_on` date (from filename) or Drive `modifiedTime`
(if pulled from Drive) falls within **3 business days** of today.

Business days exclude Saturday and Sunday. Do not count public holidays.

```python
def _business_days_ago(file_date: datetime, today: datetime) -> int:
    """Count business days between file_date and today (exclusive of today)."""
    count = 0
    current = file_date.date()
    end     = today.date()
    if current >= end:
        return 0
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:   # Monday=0 … Friday=4
            count += 1
    return count

# Determine file dates
# Priority: Generated_on date from filename → Drive modifiedTime → local file mtime
def _file_date(path, drive_modified_time=None):
    """Return the best available date for a file."""
    # Try Generated_on from filename first
    dt = _parse_report_date(os.path.basename(path))
    if dt:
        return dt, "filename (Generated_on)"
    # Try Drive modifiedTime if available
    if drive_modified_time:
        try:
            return datetime.strptime(drive_modified_time[:10], "%Y-%m-%d"), "Drive modifiedTime"
        except Exception:
            pass
    # Fall back to local file mtime
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    return mtime, "local file mtime"

# Evaluate each file
_ds_date, _ds_date_src = _file_date(datasheet_path)
_ds_age   = _business_days_ago(_ds_date, TODAY)
_ds_fresh = _ds_age <= 3
```

### Gate prompt to show the user

Present this exactly, substituting real values:

```
─────────────────────────────────────────────────────────────
GATE 1 — FILE CONFIRMATION
─────────────────────────────────────────────────────────────
Customer:  [customer_name]
Run mode:  [API Mode / Export Mode]

Files resolved:

  Current datasheet: [filename]
           Date: [_ds_date.strftime('%Y-%m-%d')] (via [_ds_date_src])
           Age:  [_ds_age] business day(s) ago  [✓ Fresh / ⚠️ STALE]

  Prior datasheets for trending:
    [for each prior: filename | date | age | ✓/⚠️]
    [if none found: "None found — trend analysis will be limited to current week only"]

[If any file is stale, add:]
  ⚠️  One or more files are outside the 3-business-day freshness window.
      These may not reflect the current reporting period.

─────────────────────────────────────────────────────────────
Reply with one of:
  • "proceed" — continue with the files listed above
  • "upload" — pause here; upload the correct file(s) then re-run
─────────────────────────────────────────────────────────────
```

### Handling the response

- **"proceed"** — continue to Step 1 immediately, even if files are stale
  (practitioner has acknowledged and accepted the risk)
- **"upload"** — stop here completely. Do not continue.
  Tell the user: "Upload the correct datasheet(s) to this session,
  then re-run the skill from the beginning."

**Never skip this gate.** Even if all files are fresh, the practitioner must
confirm before the skill spends time on API calls.

---

### Step 1 — Read analysis framework and establish client context

**Read `references/analysis_framework.md` before looking at any data.**
Read `references/output_template.md` for bullet structure and phrasings.

**Client context priority:**
- Inside a named client project → project instructions are authoritative for voice rules
- Outside a project → read `references/client_profiles.md`
- Tier is always live from MAX API / export — never infer from filename

---

### Step 2 — Load customer and vendor data

Branches on `RUN_MODE`. Both paths produce: `customer_name`, `customer_domain`,
`df_vendors`, `has_platinum`, `has_gold`, `has_silver`, `platinum_vendors`,
`gold_vendors`, `silver_vendors`.

```python
import csv, math, time, requests
import pandas as pd

BASE_URL = "https://api.securityscorecard.io"

# ── PATH A: API Mode ──────────────────────────────────────────────────────────
if RUN_MODE == "api":
    TOKEN        = os.environ["SSC_API_TOKEN"]
    HEADERS_BETA = {"accept": "application/json",
                    "Authorization": f"Token {TOKEN}", "version": "beta"}

    resp = requests.get(f"{BASE_URL}/max/partner/managed-customers", headers=HEADERS_BETA)
    if resp.status_code != 200:
        raise RuntimeError(
            f"MAX API unavailable ({resp.status_code}). "
            "Tip: export from MAX Workstation to run in Export Mode."
        )
    customers = resp.json().get("entries", [])

    # If manifest has customer ID, match directly — skip fuzzy search
    if _m_max_customer_id:
        matched = next(
            (c for c in customers if c.get("customer_id") == _m_max_customer_id),
            None
        )
        if matched:
            print(f"[MANIFEST] Customer matched by ID: '{matched.get('customer_name')}'")
        else:
            print(f"[WARN] Manifest customer_id '{_m_max_customer_id}' not found in MAX. "
                  f"Falling back to name match.")
    if not matched if _m_max_customer_id else True:
        matched = next(
            (c for c in customers
             if filename_customer.lower() in c.get("customer_name", "").lower()
             or c.get("customer_name", "").lower() in filename_customer.lower()), None
        )
    if not matched:
        raise RuntimeError(
            f"No MAX customer matched '{filename_customer}'.\n"
            f"Available: {[c.get('customer_name') for c in customers]}"
        )
    customer_id     = matched["customer_id"]
    customer_name   = matched["customer_name"]
    customer_domain = matched.get("customer_domain", customer_domain)

    vendor_resp = requests.get(f"{BASE_URL}/max/partner/vendors", headers=HEADERS_BETA,
                               params={"customer_name": customer_name, "page": 0})
    if vendor_resp.status_code != 200:
        raise RuntimeError(f"Vendor list unavailable ({vendor_resp.status_code}).")

    vdata       = vendor_resp.json()
    total       = vdata.get("total", 0)
    page_size   = vdata.get("size") or 100
    all_vendors = list(vdata.get("entries", []))
    if total > page_size:
        for page in range(1, math.ceil(total / page_size)):
            r = requests.get(f"{BASE_URL}/max/partner/vendors", headers=HEADERS_BETA,
                             params={"customer_name": customer_name, "page": page})
            if r.status_code == 200:
                all_vendors.extend(r.json().get("entries", []))

    df_vendors = pd.json_normalize(all_vendors)
    if df_vendors.empty:
        raise RuntimeError(f"Vendor list for {customer_name} is empty.")
    if "tier" in df_vendors.columns:
        df_vendors["tier"] = df_vendors["tier"].str.strip().str.title()

# ── PATH B: Export Mode ───────────────────────────────────────────────────────
else:
    with open(max_export_path, newline="", encoding="utf-8-sig") as f:
        export_rows = list(csv.DictReader(f))
    if not export_rows:
        raise RuntimeError(f"MAX export CSV is empty: {max_export_path}")
    for row in export_rows:
        row["Tier"] = row.get("Tier", "").strip().title()

    names = list({r["Customer"] for r in export_rows if r.get("Customer")})
    customer_name   = names[0] if len(names) == 1 else filename_customer
    customer_id     = None
    if len(names) != 1:
        print(f"[WARN] Multiple Customer values in export; using '{customer_name}'")

    df_vendors = pd.DataFrame([{
        "vendor_name": r.get("Vendor",""), "domain": r.get("Domain","").lower().strip(),
        "tier": r.get("Tier","Gold"), "grade": r.get("Grade",""),
        "risk_status": r.get("Risk Status",""), "date_added": r.get("Date Added",""),
        "score": None, "delta30": None, "likelihood_score": None,
    } for r in export_rows])

# ── Tier segmentation (both modes) ───────────────────────────────────────────
tier_col = "tier"
if tier_col in df_vendors.columns:
    platinum_vendors = df_vendors[df_vendors[tier_col] == "Platinum"].copy()
    gold_vendors     = df_vendors[df_vendors[tier_col] == "Gold"].copy()
    silver_vendors   = df_vendors[df_vendors[tier_col] == "Silver"].copy()
else:
    print("[WARN] No tier column — defaulting all vendors to Gold.")
    platinum_vendors, gold_vendors, silver_vendors = pd.DataFrame(), df_vendors.copy(), pd.DataFrame()

has_platinum = not platinum_vendors.empty
has_gold     = not gold_vendors.empty
has_silver   = not silver_vendors.empty
print(f"Tier composition: Platinum={len(platinum_vendors)} | Gold={len(gold_vendors)} | Silver={len(silver_vendors)}")

# ── Manifest staleness validation (runs after live vendor data loaded) ─────────
if _manifest:
    # Check tier composition drift
    _live_tiers = sorted(
        set(df_vendors[tier_col].dropna().unique().tolist()) if tier_col in df_vendors.columns else [],
        key=lambda t: {"Platinum":0,"Gold":1,"Silver":2}.get(t,9)
    )
    if _m_tier_composition and _live_tiers and set(_live_tiers) != set(_m_tier_composition):
        _manifest_stale = True
        _stale_reasons.append(
            f"tier_composition changed: manifest={_m_tier_composition} "
            f"vs live={_live_tiers}"
        )

    # Check vendor count drift (>10% change)
    if _m_vendor_count and len(df_vendors) > 0:
        _drift_pct = abs(len(df_vendors) - _m_vendor_count) / _m_vendor_count
        if _drift_pct > 0.10:
            _manifest_stale = True
            _stale_reasons.append(
                f"vendor_count changed: manifest={_m_vendor_count} "
                f"vs live={len(df_vendors)} ({round(_drift_pct*100)}% drift)"
            )

    # Surface staleness warnings
    if _manifest_stale:
        print("\n⚠️  PROJECT MANIFEST MAY BE STALE")
        for reason in _stale_reasons:
            print(f"   • {reason}")
        print("   Run the 'project-setup' skill to refresh the manifest.")
        print("   Continuing with live data for this run.\n")
    else:
        print(f"[MANIFEST] ✅ Manifest current (version: {_m_manifest_version or 'unversioned'})")
```

---

## ⛔ Gate 2 — Vendor List Confirmation *(stale or missing manifest only)*

**Only show this gate if `_manifest_stale = True` OR `_manifest = {}` (no manifest).**

If the manifest is current and matched live data exactly — skip this gate silently
and continue to Step 2.5 without interrupting the user.

When the gate fires, present this:

```
─────────────────────────────────────────────────────────────
GATE 2 — VENDOR LIST CONFIRMATION
─────────────────────────────────────────────────────────────
[If manifest stale:]
  ⚠️  Project manifest is stale:
      [list each _stale_reason]

[If no manifest:]
  ⚠️  No project manifest found in project instructions.
      Run 'project-setup' after this run to create one.

Live data pulled from MAX API / export:

  Customer:  [customer_name]  ([customer_domain])
  Vendors:   [len(df_vendors)] total
  Tiers:     Platinum=[len(platinum_vendors)] | Gold=[len(gold_vendors)] | Silver=[len(silver_vendors)]

[If manifest had vendor_count and it drifted:]
  ⚠️  Vendor count changed: expected [_m_vendor_count], found [len(df_vendors)]

[If manifest had tier_composition and it changed:]
  ⚠️  Tier composition changed:
      Was: [_m_tier_composition]
      Now: [_live_tiers]

─────────────────────────────────────────────────────────────
Does this look correct for this week's report?
  • "yes" — continue with the vendor data above
  • "no"  — stop here; describe what's wrong and we'll investigate
─────────────────────────────────────────────────────────────
```

### Handling the response

- **"yes"** — continue to Step 2.5 immediately
- **"no"** — stop. Ask the user what looks wrong.
  Common issues: wrong customer matched, vendor count unexpectedly low (MAX API
  pagination issue), tier changed since last run (contact account manager).
  Do not proceed until the user confirms the vendor data is correct.
```

---

### Step 2.5 — Fetch Platinum vendor contacts *(Platinum + Drive MCP)*

**⛔ HARD GATE — file must be reachable. Zero contacts is valid.**

- If the file **cannot be found or read**: stop, report the Drive error, do not proceed
- If the file is found but **zero rows match** this customer: continue with empty contacts,
  all Platinum vendors get `⚠️ CONTACT NEEDED` on slides — this is expected for new accounts
- If rows are found: load them and continue

**What to report on failure:**
- Which file was found (or that none was found)
- The actual column names in Sheet1
- The unique values in the `customer` column (first 10)
- What `customer_name` the skill was trying to match

```python
# Drive MCP call: Google Drive:search_files
#   query = "title contains 'MS_Vendor_Contact_Details-Prod'"
#   pageSize = 5, sort by modifiedTime desc
# Then: Google Drive:download_file_content(fileId=<most recent result id>)
# Decode base64 → openpyxl → load Sheet1

import pandas as pd

vendor_contacts_df = pd.DataFrame()   # populated below; empty = gate fires

try:
    # ── Find the contact details file ─────────────────────────────────────────
    # MCP call: Google Drive:search_files(
    #   query="title contains 'MS_Vendor_Contact_Details-Prod'",
    #   pageSize=5
    # )
    # Sort results by modifiedTime descending and take results[0]["id"]
    # _contact_file_id = <MCP result files>[0]["id"]

    # ── Download and parse ────────────────────────────────────────────────────
    # MCP call: Google Drive:download_file_content(fileId=_contact_file_id)
    # _vendor_contact_dl = <MCP result>
    _vendor_contact_bytes = base64.b64decode(_vendor_contact_dl["content"])
    _vcwb = openpyxl.load_workbook(io.BytesIO(_vendor_contact_bytes), read_only=True)
    _vcws = _vcwb["Sheet1"]

    # Build DataFrame from sheet
    # Confirmed column schema (2026-06-09):
    #   name, first_name, email, domain, send, customer, customer_cc
    _vc_headers = None
    _vc_rows    = []
    for i, row in enumerate(_vcws.iter_rows(values_only=True)):
        if i == 0:
            _vc_headers = [str(c).strip().lower() if c else f"col_{i}" for c in row]
        else:
            if any(v is not None for v in row):
                _vc_rows.append(dict(zip(_vc_headers, row)))

    full_df = pd.DataFrame(_vc_rows)

    # Diagnostic — always print so mismatches are visible in run log
    print(f"[CONTACTS] Sheet1 columns: {list(full_df.columns)}")
    print(f"[CONTACTS] Total rows: {len(full_df)}")
    if "customer" in full_df.columns:
        _sample_customers = full_df["customer"].dropna().unique().tolist()[:10]
        print(f"[CONTACTS] Sample customer values: {_sample_customers}")

    # Normalize columns for matching
    for col in ["domain", "customer"]:
        if col in full_df.columns:
            full_df[col] = full_df[col].astype(str).str.strip()

    # Filter to rows for this customer
    # customer column contains customer_name (partial, case-insensitive)
    # Matches both "University of Denver" and "University of Denver Silver"
    if "customer" in full_df.columns:
        vendor_contacts_df = full_df[
            full_df["customer"].str.lower().str.contains(
                customer_name.lower(), na=False
            )
        ].copy()
    else:
        print(f"[WARN] No 'customer' column found. Available: {list(full_df.columns)}")
        vendor_contacts_df = pd.DataFrame()

    if vendor_contacts_df.empty:
        print(f"[INFO] No vendor contacts found for '{customer_name}' "
              f"in MS_Vendor_Contact_Details-Prod.")
        print(f"       All Platinum vendors will show ⚠️ CONTACT NEEDED.")
        print(f"       To add contacts: open MS_Vendor_Contact_Details-Prod, "
              f"add rows with customer='{customer_name}' and the vendor domain.")
        # This is not a hard gate — zero contacts is a valid state for a new account.
        # Continue with empty vendor_contacts_df; lookup_contact() returns None for all.
    else:
        print(f"[INFO] Vendor contacts loaded: {len(vendor_contacts_df)} rows for {customer_name}")

except Exception as e:
    vendor_contacts_df = pd.DataFrame()
    # Hard gate only if the sheet itself could not be reached or parsed
    print(f"[ERROR] Vendor contact sheet could not be read: {e}")
    if has_platinum:
        print("[WARN] Proceeding without vendor contacts — all Platinum vendors "
              "will show ⚠️ CONTACT NEEDED.")
        print("       To resolve: check Drive access and that "
              "MS_Vendor_Contact_Details-Prod exists and is readable.")

def lookup_contact(vendor_domain):
    if vendor_contacts_df.empty:
        return None
    match = vendor_contacts_df[
        vendor_contacts_df["domain"].str.lower() == vendor_domain.lower().strip()
    ]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "name":       str(row.get("name", "") or ""),
        "first_name": str(row.get("first_name", "") or ""),
        "email":      str(row.get("email", "") or ""),
    }
```

---

### Step 2.6 — Check ZDaaS Drive folder

**Root folder ID:** `1ct04IJBZ8qVe3bzX2pA_lD5aBw4u6Z_f`
**Folder name source:** `MS_Customer_Contact_Details-Prod.xlsx` Col A, joined on Col B (domain).

```python
ZDAAS_ROOT_ID = "1ct04IJBZ8qVe3bzX2pA_lD5aBw4u6Z_f"
DATE_FROM_ISO = (report_date - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
DATE_FROM_STR = (report_date - timedelta(days=7)).strftime("%Y-%m-%d")

zdaas_reports = []   # [{cve_id, report_date, pdf_title, affected_vendors}]

# ── 2.6a: Resolve customer folder name ───────────────────────────────────────
# Use domain_to_folder loaded in Step 0a (Col A of contact sheet).
# Fall back to filename_customer if domain not in sheet.
zdaas_folder_name = (
    domain_to_folder.get(customer_domain.lower())
    if customer_domain and domain_to_folder
    else None
) or filename_customer

print(f"[ZDAAS] Looking for folder: '{zdaas_folder_name}' under root {ZDAAS_ROOT_ID}")

# ── 2.6b: Find customer subfolder in ZDaaS root ───────────────────────────────
# Primary: exact name match
# MCP call: Google Drive:search_files(
#   query = "title = '<zdaas_folder_name>' and '<ZDAAS_ROOT_ID>' in parents
#            and mimeType = 'application/vnd.google-apps.folder'"
# )
# Fallback if zero results: fuzzy — list all subfolders, match any significant token
# MCP call: Google Drive:search_files(
#   query = "'{ZDAAS_ROOT_ID}' in parents
#            and mimeType = 'application/vnd.google-apps.folder'"
# )
# Then filter locally: any word from zdaas_folder_name appears in folder name (case-insensitive)
# Tiebreaker: most recently modified
# Zero after fallback: log [WARN] and jump to MAX workstation fallback below

customer_zdaas_folder_id = None   # set by search above

# ── 2.6c: Find CVE subfolders created in report window ────────────────────────
# MCP call: Google Drive:search_files(
#   query = "'<customer_zdaas_folder_id>' in parents
#            and mimeType = 'application/vnd.google-apps.folder'
#            and createdTime >= '<DATE_FROM_ISO>'"
# )
# Each folder name IS the CVE ID (e.g. "CVE-2026-20127")

cve_folders = []   # [{id, name, createdTime}] — set by search above

# ── 2.6d: Confirm PDF present in each CVE folder ─────────────────────────────
# For each CVE folder:
# MCP call: Google Drive:search_files(
#   query = "'<cve_folder_id>' in parents and mimeType = 'application/pdf'"
# )
# A PDF present = confirmed delivered ZDaaS report
# Extract: cve_id (folder name), report_date (createdTime), pdf_title (file name)

for cve_folder in cve_folders:
    cve_id = cve_folder["name"]   # folder name = CVE ID
    # MCP call: Google Drive:search_files(
    #   query = "'<cve_folder['id']>' in parents and mimeType = 'application/pdf'"
    # )
    # If PDF found:
    #   zdaas_reports.append({
    #       "cve_id":           cve_id,
    #       "report_date":      cve_folder["createdTime"][:10],
    #       "pdf_title":        pdf_files[0]["name"],
    #       "affected_vendors": [],   # populated from xlsx investigation results if present
    #   })

# ── MAX workstation fallback ──────────────────────────────────────────────────
# Triggered if customer_zdaas_folder_id is None OR Drive search raised an exception.
# MCP call via SSC API (API Mode only):
#   GET platform-api.securityscorecard.io/max/partner/documents
#   params: customer_id=customer_id, type=zdaas
# Map results to same zdaas_reports schema above.

if not zdaas_reports and RUN_MODE == "api" and customer_id:
    try:
        _docs_resp = requests.get(
            "https://platform-api.securityscorecard.io/max/partner/documents",
            headers=HEADERS_BETA,
            params={"customer_id": customer_id, "type": "zdaas",
                    "created_after": DATE_FROM_STR}
        )
        if _docs_resp.status_code == 200:
            for doc in _docs_resp.json().get("entries", []):
                zdaas_reports.append({
                    "cve_id":           doc.get("cve_id", doc.get("title", "Unknown")),
                    "report_date":      doc.get("created_at", "")[:10],
                    "pdf_title":        doc.get("title", ""),
                    "affected_vendors": doc.get("affected_vendors", []),
                    "source":           "max_api",
                })
            print(f"[ZDAAS] MAX workstation fallback: {len(zdaas_reports)} reports")
        else:
            print(f"[ZDAAS] MAX documents endpoint returned {_docs_resp.status_code}")
    except Exception as e:
        print(f"[ZDAAS] MAX workstation fallback failed ({e})")

if not zdaas_reports:
    print("[ZDAAS] No reports found via Drive or MAX API — ZDaaS slide will be omitted.")
else:
    print(f"[ZDAAS] {len(zdaas_reports)} report(s) found: {[r['cve_id'] for r in zdaas_reports]}")
```

Fallback: MAX workstation documents endpoint (`platform-api.securityscorecard.io/max/partner/documents`).
If both unavailable: set `zdaas_reports = []`, note "ZDaaS status unavailable — verify manually."

---

### Step 2.75 — Pull Outreach email context *(Platinum only)*

Search **both** `managedservices@securityscorecard.io` and the user's own inbox.
Deduplicate threads where one inbox CC'd the other (match on Message-ID or subject+date+recipients).
Token not required — uses Gmail MCP directly.

```python
# Gmail MCP calls used:
#   Gmail:search_threads(query=...) — search one inbox at a time
#   Gmail:get_thread(threadId=...) — get full thread content for classification
#
# Both inboxes searched: managedservices@securityscorecard.io + user's own inbox
# Deduplication: threads sharing the same Message-ID, or same subject+date+domain
# are collapsed to one entry (the managedservices version preferred if both match)

window_start = (report_date - timedelta(days=7)).strftime("%Y/%m/%d")
window_end   = (report_date + timedelta(days=1)).strftime("%Y/%m/%d")

# Build domain search terms for Platinum vendors only
_pt_domains = platinum_vendors["domain"].dropna().str.lower().unique().tolist()
_domain_terms = " OR ".join(_pt_domains) if _pt_domains else ""

outreach_threads = []   # [{vendor_domain, subject, date, classification, snippet}]

if not _domain_terms:
    print("[OUTREACH] No Platinum vendor domains — skipping outreach search.")
else:
    _seen_dedup_keys = set()   # (normalized_subject, date_str, vendor_domain)

    for _inbox_label, _inbox_query_prefix in [
        ("managedservices", f"to:managedservices@securityscorecard.io OR from:managedservices@securityscorecard.io"),
        ("user_inbox",      ""),   # no inbox filter = user's own inbox
    ]:
        _query = (
            f"({_domain_terms}) "
            f"after:{window_start} before:{window_end}"
        )
        if _inbox_query_prefix:
            _query = f"({_inbox_query_prefix}) {_query}"

        try:
            # MCP call: Gmail:search_threads(query=_query)
            # Returns: {"threads": [{"id": ..., "snippet": ...}, ...]}
            # Then for each thread:
            #   MCP call: Gmail:get_thread(threadId=thread["id"])
            #   Returns: {"messages": [{headers, body, ...}]}

            # For each message in thread:
            #   Extract subject from headers["Subject"]
            #   Extract date from headers["Date"] → parse to YYYY-MM-DD
            #   Extract to/from addresses → match against _pt_domains
            #   Extract snippet for classification

            # Classification logic:
            #   "commit" / "will patch" / "by <date>" → "Commitment made"
            #   "remediat" / "fixed" / "resolved"     → "Commitment made"
            #   re-check / follow.?up / no response   → "No response"
            #   escalat / executive / urgent           → "Escalation needed"
            #   default                               → "Response received"

            # Dedup key: (subject.lower().strip(), date_str, matched_domain)
            # If key already in _seen_dedup_keys: skip (other inbox already captured it)
            # Else: add to _seen_dedup_keys and append to outreach_threads

            # _threads_result = <MCP result>
            # for thread in _threads_result.get("threads", []):
            #     ...

            print(f"[OUTREACH] Searched {_inbox_label} inbox")

        except Exception as e:
            print(f"[OUTREACH] {_inbox_label} search failed ({e}) — continuing.")

    print(f"[OUTREACH] {len(outreach_threads)} unique threads found across both inboxes")

# outreach_threads feeds:
#   → Outreach Status Update slide (always last for Platinum)
#   → Next Actions (prior commitments may need follow-up flag)
```

---

### Step 3 — Pull live scores from SSC API *(API Mode, Platinum required)*

**Export Mode:** Skip — use `grade` from df_vendors as proxy. Score Movements slide omitted.

```python
def _get_vendor_scores(domain_or_id, label=None):
    endpoints = {
        "score":   f"{BASE_URL}/companies/{domain_or_id}/score",
        "summary": f"{BASE_URL}/companies/{domain_or_id}/summary",
        "factors": f"{BASE_URL}/companies/{domain_or_id}/summary/factors",
    }
    results = {}
    for key, url in endpoints.items():
        for attempt in range(4):
            r = requests.get(url, headers=HEADERS_BETA)
            if r.status_code == 200:
                results[key] = r.json(); break
            elif r.status_code == 429:
                time.sleep(0.5 * (2 ** attempt))
            elif r.status_code == 404:
                return None
            else:
                break
        time.sleep(0.3)
    if "score" not in results:
        return None
    sd, sm, ff = results.get("score",{}), results.get("summary",{}), results.get("factors",{})
    return {"score": sd.get("score"), "grade": sd.get("grade"),
            "delta30": sm.get("last30day_score_change"),
            "factors": {e["name"]: {"score": e["score"], "grade": e["grade"]}
                        for e in ff.get("entries", [])}}

# Custom scorecards — vendors that use a UUID-based identifier instead of a standard domain.
# These are rare. Add entries here only when a vendor's scorecard_identifier field from
# the MAX API contains a UUID hostname rather than a standard domain.
# Format: "lookup_key": "uuid.custom.securityscorecard.io"
# Ellucian confirmed via MAX API (2026-06-08): standard domain ellucian.com — no custom scorecard.
CUSTOM_SCORECARDS = {
    # Add entries here if discovered — e.g.:
    # "vendor_key": "uuid-placeholder.custom.securityscorecard.io",
}

vendor_scores = {}
for _, row in platinum_vendors.iterrows():
    domain = row.get("vendor_domain") or row.get("domain")
    if not domain: continue
    result = _get_vendor_scores(domain, label=row.get("vendor_name", domain))
    if result:
        vendor_scores[domain] = result
# Key factors: ip_reputation, network_security, application_security, patching_cadence
# LA scores NOT available via this token
```

---

### Step 4 — Extract structured data from datasheets

```python
import openpyxl

def read_sheet(wb, name):
    if name not in wb.sheetnames:
        print(f"[WARN] Sheet '{name}' not found in workbook — skipping.")
        return [], []
    ws = wb[name]
    rows, headers = [], None
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0: headers = row
        elif any(v is not None for v in row):
            rows.append(dict(zip(headers, row)))
    return headers, rows

wb = openpyxl.load_workbook(datasheet_path)
_, breaches   = read_sheet(wb, "Breaches")
_, critical   = read_sheet(wb, "Critical Indicators")
_, high       = read_sheet(wb, "High Indicators")
_, cves       = read_sheet(wb, "CVEs")
_, tp_vendors = read_sheet(wb, "Third Party Vendors")
_, products   = read_sheet(wb, "Products")
_, ind_count  = read_sheet(wb, "Indicator Count")

# Load prior weeks for trending (within lookback window only)
prior_data = {}  # {date_str: {'critical': [...], 'high': [...], 'cves': [...]}}
for p in prior_datasheet_paths:
    dt = _parse_report_date(os.path.basename(p))
    if dt and LOOKBACK_MIN <= dt <= LOOKBACK_MAX:
        pwb = openpyxl.load_workbook(p)
        _, pc = read_sheet(pwb, "Critical Indicators")
        _, ph = read_sheet(pwb, "High Indicators")
        _, pv = read_sheet(pwb, "CVEs")
        prior_data[dt.strftime("%Y-%m-%d")] = {"critical": pc, "high": ph, "cves": pv}
    else:
        print(f"[WARN] Skipping {os.path.basename(p)} — outside 35-day lookback window.")

sorted_dates = sorted(prior_data.keys())  # oldest → newest
print(f"Trend window: {sorted_dates[0] if sorted_dates else 'none'} → {report_date_str} ({len(sorted_dates)} prior weeks)")
```

**Persistent indicator detection:**
```python
# Build a per-week lookup: weekly_findings[date] = {(domain, issue_type)}
# A finding is persistent if the same (domain, issue_type) key appears in
# >= 2 consecutive weekly datasheets ending with the current week.

weekly_findings = {}   # {date_str: set of (domain, issue_type)}

# Current week
_cur_set = set()
for row in critical + high:
    _dom = str(row.get("Domain") or row.get("domain") or "").lower().strip()
    _typ = str(row.get("Issue Type") or row.get("issue_type") or row.get("Type") or "").strip()
    if _dom and _typ:
        _cur_set.add((_dom, _typ))
weekly_findings[report_date_str] = _cur_set

# Prior weeks
for _d in sorted_dates:
    _p_set = set()
    for row in prior_data[_d].get("critical", []) + prior_data[_d].get("high", []):
        _dom = str(row.get("Domain") or row.get("domain") or "").lower().strip()
        _typ = str(row.get("Issue Type") or row.get("issue_type") or row.get("Type") or "").strip()
        if _dom and _typ:
            _p_set.add((_dom, _typ))
    weekly_findings[_d] = _p_set

# Identify persistent findings: present in current week AND at least one prior week
all_dates_ordered = sorted(weekly_findings.keys())   # oldest → newest (current last)
persistent_findings = {}   # {(domain, issue_type): [dates_present]}

for key in weekly_findings.get(report_date_str, set()):
    dates_present = [d for d in all_dates_ordered if key in weekly_findings.get(d, set())]
    if len(dates_present) >= 2 and report_date_str in dates_present:
        persistent_findings[key] = dates_present

# Build trend strings: "domain: issue_type (3 consecutive weeks)"
persistent_summary = []   # [{domain, issue_type, weeks, trend_str}]
for (dom, typ), dates in sorted(persistent_findings.items(), key=lambda x: -len(x[1])):
    persistent_summary.append({
        "domain":     dom,
        "issue_type": typ,
        "weeks":      len(dates),
        "trend_str":  " -> ".join(d[5:] for d in dates),   # "06-01 -> 06-08"
    })

print(f"Persistent findings: {len(persistent_summary)} "
      f"({len(set(p['domain'] for p in persistent_summary))} vendors)")
```

**KEV column resolution (defensive):**
```python
def _resolve_col(rows, candidates):
    if not rows: return None
    keys = set(rows[0].keys())
    keys_lower = {k.lower(): k for k in keys}
    for c in candidates:
        if c in keys: return c
        if c.lower() in keys_lower: return keys_lower[c.lower()]
    return None

cve_id_col = _resolve_col(cves, ["CVE", "CVE ID", "cve_id", "CVE_ID"])
kev_col    = _resolve_col(cves, ["Known Exploited Vulnerability", "KEV", "Is KEV", "kev_flag"])

current_kevs = set()
if cve_id_col and kev_col:
    current_kevs = {r[cve_id_col] for r in cves
                    if r.get(cve_id_col)
                    and str(r.get(kev_col,"")).strip().lower() in ("yes","true","1","y")}

prior_kevs = set()
for d in sorted_dates:
    pc = prior_data[d].get("cves", [])
    if cve_id_col and kev_col:
        prior_kevs |= {r[cve_id_col] for r in pc
                       if r.get(cve_id_col) and str(r.get(kev_col,"")).strip().lower() in ("yes","true","1","y")}
new_kevs = current_kevs - prior_kevs
```

**Fourth-party concentration analysis:**
```python
# Source: Third Party Vendors sheet + Products sheet
# Flag if a single fourth-party provider appears across >= 30% of monitored vendors
# Do not manufacture a story — if data is ambiguous, skip this section
from collections import Counter
fourth_party_counts = Counter()
for row in tp_vendors:
    provider = row.get("Third Party") or row.get("Provider") or row.get("Domain")
    if provider:
        fourth_party_counts[provider.lower().strip()] += 1

total_vendors = len(df_vendors)
concentration_threshold = max(3, round(total_vendors * 0.30))
concentration_risks = [
    (provider, count) for provider, count in fourth_party_counts.most_common(5)
    if count >= concentration_threshold
]
# Non-empty → include Fourth-Party Risk slide; empty → delete slide
```

---

### Step 5 — Driftnet enrichment *(API Mode + Platinum + DRIFTNET_API_TOKEN only)*

For sub-C vendors (D or F grade). Skip for Export Mode, Gold, Silver, or missing token.

```python
import urllib3; urllib3.disable_warnings()
DRIFT_BASE = "https://api.driftnet.io/v1"
DH = {"Authorization": f"Bearer {os.environ['DRIFTNET_API_TOKEN']}"}

def driftnet_summarize(domain, summarize_field):
    r = requests.get(f"{DRIFT_BASE}/scan/protocols", headers=DH, verify=False,
                     params={"expression": f"host:{domain}", "summarize": summarize_field,
                             "most_recent": "true"}, timeout=30)
    return r.json().get("summary", {}).get("values", {}) if r.ok and r.content else {}

driftnet_data = {}  # {domain: {'ports': {...}, 'products': {...}}}
sub_c_domains = [
    row.get("domain") for _, row in platinum_vendors.iterrows()
    if vendor_scores.get(row.get("domain"), {}).get("grade") in ("D", "F")
]
for domain in sub_c_domains:
    driftnet_data[domain] = {
        "ports":    driftnet_summarize(domain, "port-tcp"),
        "products": driftnet_summarize(domain, "product-tag"),
    }
# Notable ports: 21 FTP, 3306 MySQL, 1433 MSSQL, 5432 PostgreSQL, 3389 RDP
# High-signal product tags: cobalt-strike, vmware-esxi, cisco-expressway, f5-big-ip
```

---

### Step 6 — Score movements *(API Mode only; ≥±15 pt threshold)*

```python
score_movements = []  # [{domain, vendor_name, score, grade, delta30, direction}]

if RUN_MODE == "api":
    for domain, scores in vendor_scores.items():
        delta = scores.get("delta30")
        if delta is not None and abs(delta) >= 15:
            score_movements.append({
                "domain":      domain,
                "vendor_name": next((r.get("vendor_name", domain)
                                     for _, r in df_vendors.iterrows()
                                     if r.get("domain") == domain), domain),
                "score":       scores.get("score"),
                "grade":       scores.get("grade"),
                "delta30":     delta,
                "direction":   "gain" if delta > 0 else "drop",
            })
    score_movements.sort(key=lambda x: abs(x["delta30"]), reverse=True)
    print(f"Score movements >=15 pts: {len(score_movements)} vendors")
# Empty → omit Score Movements slide entirely
```

---

### Step 7 — Write the analysis

Follow `references/output_template.md` for bullet structure and section headers.
Follow `references/analysis_framework.md` for analytical discipline and delivery cautions.

**Export Mode — omit or modify:**
- Score Movements slide: omit entirely — no delta data
- Vendor label: `Score: N/A (Grade: X)` since only letter grade available
- All other sections run normally

**Mandatory vendor label — first mention of each vendor:**
```
<VendorName> (<domain>) | <Tier> | LA: <score|N/A> | Score: <score> (<grade>) | Contact: <name> <email>
```
Gold/Silver: omit Contact field. Export Mode: `Score: N/A (Grade: X)`.

**Section order (markdown output — feeds slide content):**
```
### 🆕 New Concerning Findings       ← NEVER omitted; always has breach + ZDaaS callouts
### 🔴 Breaches                      ← omit section + slide if none
### 🔴 ZDaaS Reports                 ← omit section + slide if none
### 🟡 KEV Tracking                  ← new entrants this period vs prior weeks
### 🟠 Sub-C Vendors                 ← D/F grade; score, delta, Driftnet, outreach rec
### 🔵 Fourth-Party Risk             ← only if concentration_risks is non-empty
### 🟡 Score Movements (≥±15 pts)    ← API Mode only; omit if none qualify
### ➡️ Recommended Next Actions      ← MAX Team | action | timing; Your Team | action | timing
### 📧 Outreach Status Update        ← Platinum only; always last
```

**Recommended Next Actions format:**
```
MAX Team   | [specific action]     | [timing]
Your Team  | [specific action]     | [timing]
```
No scorecard table. No indicator details. Pure action items only — who does what by when.
"Your Team" actions written in second person: "Review...", "Confirm...", "Follow up with..."
Never use "the customer", "your customer", or "Customer" as a label anywhere in output.
Platinum MAX Team actions: include named vendor POC.
Gold/Silver: vendor name only.

**Outreach Status Update format (Platinum only):**
```
Vendor — Contact Name <email>
  Outreach sent: YYYY-MM-DD
  Status: Response received / No response / Commitment made / Escalation needed
  Detail: one sentence
```
If no threads found: "No outreach correspondence found — confirm status verbally at touchpoint."
Never infer or fabricate outreach status.

---

---

## ⛔ Human Review Gate

**STOP HERE. Present the full analysis output for review before delivering.**

Slide generation is not part of this skill — the analysis text is the deliverable.
Present it for review and allow the practitioner to request edits before closing out.

---

### What to show the user

Display the full analyst output from Step 7 — all sections, all findings, all next
actions — exactly as written.

Then immediately below it, show this:

```
─────────────────────────────────────────────────────────────
REVIEW — ANALYST OUTPUT FOR [customer_name]
Report date: [report_date_str]
─────────────────────────────────────────────────────────────

Sections produced:
  ✓ New Concerning Findings  ([N] items)
  [✓ or —] Breaches          ([N] found  /  none this period)
  [✓ or —] ZDaaS             ([N] found  /  none this period)
  [✓ or —] KEV Tracking      ([N] new, [N] persistent  /  none)
  [✓ or —] Sub-C Vendors     ([N] vendors  /  none)
  [✓ or —] Fourth-Party Risk ([N] providers  /  none)
  [✓ or —] Score Movements   ([N] vendors  /  none)
  ✓ Recommended Next Actions ([N] MAX Team, [N] Your Team)
  [✓ or —] Outreach Status   ([N] threads found  /  Platinum only)

⚠️  FLAGS FOR REVIEW:
  [list any concerns — see below]

─────────────────────────────────────────────────────────────
Reply with one of:
  • "looks good" — output is approved, skill complete
  • "change [section]: [what to change]" — edit before finalizing
  • "remove [section]" — drop that section from the output
  • "stop" — discard output, do not finalize
─────────────────────────────────────────────────────────────
```

### Flags to surface automatically

Populate the ⚠️ FLAGS section with any of the following that apply:

- Any Platinum vendor showing `⚠️ CONTACT NEEDED` — list by name
- Any vendor with a 30-day score drop ≥ 25 pts — call out specifically
- Any KEV with CVSS ≥ 9.0 — call out by CVE ID
- Any vendor sub-C for 3+ consecutive weeks — call out by name
- Any ZDaaS report affecting 5+ vendors — call out by CVE ID
- `analyst_output` not set or empty — flag that Next Actions will be empty
- Fewer than 3 prior datasheets found — flag that trend data may be incomplete
- Manifest stale (`_manifest_stale = True`) — flag that manifest needs refreshing

If none apply: `No flags — output looks clean.`

### Handling the response

- **"looks good"** — output is final. Save the analyst text to the session and
  confirm to the user: "Analysis complete. When you're ready to build slides,
  run the slide build skill with this output."
- **"change [section]: [what]"** — make the requested edit, show the updated
  section, re-present this gate. Never auto-proceed after an edit.
- **"remove [section]"** — remove that section from the output, confirm,
  re-present this gate.
- **"stop"** — discard the output. Confirm cancellation.

**Never finalize the output without an explicit "looks good" response.**


## Hard Rules

- **Steps run in order — no skipping.** Steps 0 → 2 → 2.5 → 2.6 → 2.75 → 3 → 4 → 5 → 6 → 7 → Human Review Gate. If a step fails, stop and report the failure. Do not proceed to the next step.
- **Hard gates block slide generation.** Step 2.5 (Platinum contacts) failure = no slides. Surface the exact error before stopping.
- **Report diagnostic detail on every failure.** Column names found, values seen, file names found — give the user what they need to fix it. Every claim maps to a datasheet row, API response, Drive file, or email thread.
- **30-day data is 30-day data.** Never say "this week" for delta data. Always "over the past 30 days."
- **Score Movements threshold is ≥±15 pts.** Do not surface smaller movements.
- **Trending window is 35 days.** Files outside this window are excluded from trend analysis.
- **Vendor contacts are Platinum only.** Never appear in Gold or Silver output.
- **Tier from MAX API / export only.** Never derive from filename.
- **No outreach credit without documented commitment-then-improvement chain.**
- **No NVD text verbatim.** Paraphrase all CVE descriptions.
- **Recommended Next Actions contains actions only.** No scorecard data, no indicator tables.
- **Outreach Status Update sourced from email only.** Both inboxes, deduplicated. Never inferred.
- **Outreach Status Update is always last.** Token not required — Gmail MCP only.
- **NCF slide always present.** Always carries at least breach + ZDaaS status callouts.
- **Identify slides by title content, not hardcoded index.** Re-identify after every structural op.

---

## References

- `references/analysis_framework.md` — Analytical checklist and delivery cautions. Read first.
- `references/output_template.md` — Bullet format, section headers, example phrasings.
- `references/client_profiles.md` — Per-client voice rules (non-project accounts only).
