---
name: account-brief
description: >
  Build or refresh the Account Brief for a MAX Managed Services customer. Run
  inside the customer's Claude project. Optionally sets up ~/.max_api_keys
  (SSC + Driftnet) before pulling live data. Sources data from MAX API,
  coverage tracker, and vendor contact sheet, then interviews the analyst for
  qualitative context. Checks for an existing internal Slack channel and offers
  setup steps if none exists. Produces: (1) Account Brief Google Doc in the
  customer's Drive folder, (2) master index row for the Account Brief Sheet,
  (3) Project Instructions block ready to paste into the Claude project so
  context persists across sessions. Trigger on: "build the account brief",
  "create the brief for [client]", "run account brief", "initialize account
  context", "set up the brief". Do NOT use for project manifest setup
  (use project-setup) or weekly delivery (use max-weekly-delivery).
version: 1.0
last_updated: 2026-06-25
owner: ian.mains@securityscorecard.io
status: active
category: account
---

# Account Brief Skill

## ⚠️ PROJECT REQUIREMENT — READ FIRST

**This skill must be run inside the customer's Claude project.**

Before doing anything else, check whether Claude is running inside a named
project. If not, stop immediately and display this message:

```
⚠️  STOP — Wrong context.

This skill should be run inside the customer's Claude project, not in a
general conversation. Running it here means the Project Instructions output
has nowhere to live, and Claude will have to re-derive all account context
on every future run.

To proceed correctly:
  1. Open or create a Claude project named after this customer
     (e.g. "BMO", "Denny's", "HCA Healthcare")
  2. Start a new conversation inside that project
  3. Run this skill again from there

You can continue here if you want to generate the outputs manually and
paste them into the project yourself — but the recommended path is above.

Continue anyway? (yes / no)
```

If the analyst says yes, continue with a warning logged to the summary.
If no, stop cleanly.

---

## Purpose

Creates and populates a living Account Brief for a MAX Managed Services
customer. The brief is the single source of truth for anyone delivering to
or picking up the account — FTE or contractor. It replaces tribal knowledge
and eliminates the "flying blind" problem when an analyst is new to an account.

The Project Instructions output is the most operationally important output —
it means every future Claude run inside this project starts with full account
context already loaded, with no re-derivation needed.

Does NOT include ARR or revenue data.

---

## Source Priority

Pull data in this order, preferring automated sources:

1. **MAX API** — customer ID, domain, vendor count, tier composition
2. **Coverage tracker** (`MAX Resourcing Status-20260518.xlsx`, ID: `1Yg4iABE2FMyJWE6JhVpElwqFcpK0nXX2`) — FTE owner, contractor analyst, delivery cadence, call schedule
3. **Vendor contact sheet** (`MS_Vendor_Contact_Details-Prod.xlsx`, ID: `1DH4ZRA5yprnhJfMh544Hsk-5OP_LNGnt`) — POC names, emails, roles
4. **Slack MCP** — search for existing internal channel for this account
5. **Drive** — existing call notes, prior reports, ZDaaS folder presence
6. **Analyst interview** — qualitative fields that no API can provide

If a source is unavailable, mark the field `⚠️ fill manually` and continue.
Never block on a missing source.

---

## Workflow

### Step 0 — Project check & customer identity

```python
import os, requests, base64, io, re
from datetime import datetime

BASE_URL             = "https://api.securityscorecard.io"
COVERAGE_TRACKER_ID  = "1Yg4iABE2FMyJWE6JhVpElwqFcpK0nXX2"
CONTACT_SHEET_ID     = "1DH4ZRA5yprnhJfMh544Hsk-5OP_LNGnt"

# Check for project context
customer_name = os.environ.get("PROJECT_NAME") or os.environ.get("CLAUDE_PROJECT_NAME")

if not customer_name:
    # Warn analyst — not running inside a project
    print("""
⚠️  STOP — Wrong context.

This skill should be run inside the customer's Claude project, not in a
general conversation. Running it here means the Project Instructions output
has nowhere to live, and Claude will have to re-derive all account context
on every future run.

To proceed correctly:
  1. Open or create a Claude project named after this customer
  2. Start a new conversation inside that project
  3. Run this skill again from there

Continue anyway? (yes / no)
""")
    _continue = input().strip().lower()
    if _continue != "yes":
        raise SystemExit("Stopped. Re-run inside the customer's Claude project.")
    # If continuing, ask for customer name manually
    customer_name = input("Customer name: ").strip()
    _project_warning = True
else:
    _project_warning = False

print(f"[BRIEF] Building account brief for: '{customer_name}'")
```

---

### Step 0.5 — API keys setup (optional)

Check whether SSC and Driftnet tokens are already loaded in the environment.
If not, offer to walk the analyst through creating a keys file before proceeding.
This step is never a hard requirement — the analyst can skip it and fill
API-sourced fields manually at the end.

```python
_ssc_token      = os.environ.get("SSC_API_TOKEN", "")
_driftnet_token = os.environ.get("DRIFTNET_API_TOKEN", "")
_keys_loaded    = bool(_ssc_token)
_keys_file_path = os.path.expanduser("~/.max_api_keys")
```

**If tokens are already loaded**, print a brief confirmation and move on:

```
✅ API tokens detected in session — skipping keys setup.
   SSC:      loaded
   Driftnet: [loaded / not set]
```

**If no tokens are detected**, present this offer:

```
🔑 No API tokens detected in this session.

The account brief can pull data automatically from MAX and SSC APIs if
you have tokens available. This is optional — you can skip it and fill
any API-sourced fields manually after the brief is built.

Would you like to set up your API keys now?
  1. Yes — walk me through it and save a keys file for future sessions
  2. Yes — I'll paste my tokens directly (no file saved)
  3. No — skip it, I'll fill API fields manually
```

**If option 1 — guided keys file setup:**

```
We'll create a file at ~/.max_api_keys on your machine.
This file stores your API tokens so you can load them at the start
of any session without re-entering them.

⚠️  Keep this file private. Do not commit it to git or share it.
    Add ~/.max_api_keys to your .gitignore if you use a local repo.

Let's collect your tokens. Press Enter to skip any you don't have yet.
```

Collect each token interactively, then validate live:

```python
import requests

_new_keys = {}

# SSC token
print("\n── SSC API Token ───────────────────────────────────────")
print("Get yours at: https://platform.securityscorecard.io/")
print("  My Account → API → Create Token")
_ssc_input = input("SSC_API_TOKEN (or Enter to skip): ").strip()

if _ssc_input:
    # Validate immediately
    _resp = requests.get(
        "https://api.securityscorecard.io/portfolios",
        headers={"Authorization": f"Token {_ssc_input}"},
        timeout=10
    )
    if _resp.status_code == 200:
        _count = len(_resp.json().get("entries", []))
        print(f"✅ SSC token valid — {_count} portfolio(s) visible")
        _new_keys["SSC_API_TOKEN"] = _ssc_input
        os.environ["SSC_API_TOKEN"] = _ssc_input
    else:
        print(f"⚠️  SSC token returned {_resp.status_code} — double-check the value.")
        print("    You can re-enter it or skip and add it to the file manually later.")
        _retry = input("Re-enter token (or Enter to skip): ").strip()
        if _retry:
            _new_keys["SSC_API_TOKEN"] = _retry
            os.environ["SSC_API_TOKEN"] = _retry

# Driftnet token
print("\n── Driftnet API Token ──────────────────────────────────")
print("Get yours at: https://driftnet.io/ → Account → API Keys")
print("Note: Driftnet uses a shared team account.")
print("  Check with your team lead for the current token.")
_dn_input = input("DRIFTNET_API_TOKEN (or Enter to skip): ").strip()

if _dn_input:
    _resp = requests.get(
        "https://api.driftnet.io/v1/admin/user",
        headers={"Authorization": f"Bearer {_dn_input}"},
        timeout=10
    )
    if _resp.status_code == 200:
        _data  = _resp.json()
        _email = _data.get("email", "unknown")
        _usage = _data.get("quota", {}).get("api_usage", "?")
        _limit = _data.get("quota", {}).get("api_limit", "?")
        print(f"✅ Driftnet valid — logged in as {_email} (quota: {_usage}/{_limit})")
        _new_keys["DRIFTNET_API_TOKEN"] = _dn_input
        os.environ["DRIFTNET_API_TOKEN"] = _dn_input
    else:
        print(f"⚠️  Driftnet token returned {_resp.status_code} — check the value.")
        _retry = input("Re-enter token (or Enter to skip): ").strip()
        if _retry:
            _new_keys["DRIFTNET_API_TOKEN"] = _retry
            os.environ["DRIFTNET_API_TOKEN"] = _retry

# Write keys file
if _new_keys:
    _lines = [
        "# MAX Managed Services API Keys",
        "# Generated by account-brief skill",
        f"# Created: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "# Keep this file private. Do not commit to git.",
        "#",
        "# To load in a new Claude session:",
        "#   Upload this file, then say 'load my keys'",
        "#   or run the vroc-session-init skill.",
        "",
    ]
    for k, v in _new_keys.items():
        _lines.append(f"{k}={v}")

    with open(_keys_file_path, "w") as f:
        f.write("\n".join(_lines) + "\n")

    print(f"""
✅ Keys file saved to: {_keys_file_path}

To use in future sessions:
  1. Upload {_keys_file_path} to Claude at the start of a new conversation
  2. Say "load my keys" — Claude will parse and validate them automatically

For this session, tokens are already active and the brief will use them now.
""")
    _keys_loaded = True
else:
    print("\nNo tokens collected — continuing without API access.")
    print("API-sourced fields will be marked ⚠️ fill manually in the output.")
    _keys_loaded = False
```

**If option 2 — paste tokens directly (no file):**

```python
# Same collection and validation as option 1, but skip the file write step.
# Tokens are loaded into os.environ for this session only.
# Print a reminder at the end:
print("""
ℹ️  Tokens loaded for this session only — not saved to disk.
   To avoid re-entering them next time, run option 1 to create
   a ~/.max_api_keys file.
""")
```

**If option 3 — skip:**

```python
print("""
Skipping keys setup. API-sourced fields will be marked ⚠️ fill manually.
You can run the account-brief skill again after loading keys to refresh them.
""")
_keys_loaded = False
```

---

### Step 1 — Pull structured data from MAX API

```python
TOKEN = os.environ.get("SSC_API_TOKEN", "")
HEADERS = {
    "accept":        "application/json",
    "Authorization": f"Token {TOKEN}",
    "version":       "beta",
}

max_customer_id  = None
customer_domain  = None
vendor_count     = 0
tier_composition = []
platinum_vendors = []
gold_vendors     = []

if not TOKEN:
    print("[WARN] No SSC_API_TOKEN — API fields will need manual completion.")
else:
    resp = requests.get(f"{BASE_URL}/max/partner/managed-customers", headers=HEADERS)
    resp.raise_for_status()
    customers = resp.json().get("entries", [])

    matched = next(
        (c for c in customers
         if customer_name.lower() in c.get("customer_name","").lower()
         or c.get("customer_name","").lower() in customer_name.lower()),
        None
    )

    if matched:
        max_customer_id = matched.get("customer_id")
        customer_domain = matched.get("customer_domain","")
        customer_name   = matched.get("customer_name", customer_name)
        print(f"[BRIEF] MAX match: {customer_name} | ID: {max_customer_id} | Domain: {customer_domain}")

        import math
        vresp = requests.get(
            f"{BASE_URL}/max/partner/vendors",
            headers=HEADERS,
            params={"customer_name": customer_name, "page": 0}
        )
        if vresp.ok:
            vdata       = vresp.json()
            total       = vdata.get("total", 0)
            page_size   = vdata.get("size") or 100
            all_vendors = list(vdata.get("entries", []))
            for page in range(1, math.ceil(total / max(page_size,1))):
                r = requests.get(
                    f"{BASE_URL}/max/partner/vendors",
                    headers=HEADERS,
                    params={"customer_name": customer_name, "page": page}
                )
                if r.ok:
                    all_vendors.extend(r.json().get("entries", []))

            vendor_count     = len(all_vendors)
            tiers_raw        = [str(v.get("tier","")).strip().title()
                                for v in all_vendors if v.get("tier")]
            tier_composition = sorted(set(tiers_raw),
                                key=lambda t: {"Platinum":0,"Gold":1,"Silver":2}.get(t,9))
            platinum_vendors = [v.get("vendor_name","") for v in all_vendors
                                if str(v.get("tier","")).strip().title() == "Platinum"]
            gold_vendors     = [v.get("vendor_name","") for v in all_vendors
                                if str(v.get("tier","")).strip().title() == "Gold"]

            print(f"[BRIEF] Vendors: {vendor_count} | Tiers: {tier_composition}")
    else:
        print(f"[WARN] No MAX match for '{customer_name}'.")
        print(f"       Available: {[c.get('customer_name') for c in customers]}")
```

---

### Step 2 — Pull from coverage tracker

```python
# MCP call: Google Drive:download_file_content(fileId=COVERAGE_TRACKER_ID)
# coverage_bytes = base64.b64decode(result["content"])

import openpyxl

fte_owner     = None
contractor    = None
call_schedule = None
delivery_day  = None
account_tier  = None

try:
    coverage_wb = openpyxl.load_workbook(io.BytesIO(coverage_bytes), read_only=True)
    ws = coverage_wb.active
    headers = [str(c.value).strip().lower() if c.value else ""
               for c in next(ws.iter_rows(min_row=1, max_row=1))]

    col = {
        "account":    next((i for i,h in enumerate(headers) if "account" in h or "customer" in h), None),
        "fte":        next((i for i,h in enumerate(headers) if "fte" in h or "owner" in h or "primary" in h), None),
        "contractor": next((i for i,h in enumerate(headers) if "contractor" in h or "analyst" in h), None),
        "cadence":    next((i for i,h in enumerate(headers) if "cadence" in h or "frequency" in h), None),
        "call_day":   next((i for i,h in enumerate(headers) if "call" in h or "day" in h or "schedule" in h), None),
        "tier":       next((i for i,h in enumerate(headers) if "tier" in h or "contract" in h), None),
    }

    for row in ws.iter_rows(min_row=2, values_only=True):
        acct = str(row[col["account"]] or "").strip().lower() if col["account"] is not None else ""
        if customer_name.lower() in acct or acct in customer_name.lower():
            fte_owner     = row[col["fte"]]        if col["fte"]        is not None else None
            contractor    = row[col["contractor"]] if col["contractor"] is not None else None
            call_schedule = row[col["call_day"]]   if col["call_day"]   is not None else None
            delivery_day  = row[col["cadence"]]    if col["cadence"]    is not None else None
            account_tier  = row[col["tier"]]       if col["tier"]       is not None else None
            break

    print(f"[BRIEF] Coverage: FTE={fte_owner} | Contractor={contractor} | Call={call_schedule}")

except Exception as e:
    print(f"[WARN] Coverage tracker unavailable: {e}")
```

---

### Step 3 — Pull POC data from vendor contact sheet

```python
# MCP call: Google Drive:download_file_content(fileId=CONTACT_SHEET_ID)
# contact_bytes = base64.b64decode(result["content"])

poc_name     = None
poc_email    = None
poc_title    = None
alt_contacts = []

try:
    contact_wb = openpyxl.load_workbook(io.BytesIO(contact_bytes), read_only=True)
    cws = contact_wb.active
    cheaders = [str(c.value).strip().lower() if c.value else ""
                for c in next(cws.iter_rows(min_row=1, max_row=1))]

    ccol = {
        "account": next((i for i,h in enumerate(cheaders) if "account" in h or "customer" in h or "company" in h), None),
        "name":    next((i for i,h in enumerate(cheaders) if h == "name"), None),
        "first":   next((i for i,h in enumerate(cheaders) if "first" in h), None),
        "last":    next((i for i,h in enumerate(cheaders) if "last" in h), None),
        "email":   next((i for i,h in enumerate(cheaders) if "email" in h), None),
        "title":   next((i for i,h in enumerate(cheaders) if "title" in h or "role" in h), None),
    }

    for row in cws.iter_rows(min_row=2, values_only=True):
        acct = str(row[ccol["account"]] or "").strip().lower() if ccol["account"] is not None else ""
        if customer_name.lower() in acct or acct in customer_name.lower():
            name  = (row[ccol["name"]] if ccol["name"] is not None else
                     f"{row[ccol['first']] or ''} {row[ccol['last']] or ''}".strip()
                     if ccol["first"] is not None else None)
            email = row[ccol["email"]] if ccol["email"] is not None else None
            title = row[ccol["title"]] if ccol["title"] is not None else None
            if not poc_name:
                poc_name  = str(name).strip()  if name  else None
                poc_email = str(email).strip() if email else None
                poc_title = str(title).strip() if title else None
            else:
                alt_contacts.append({
                    "name":  str(name).strip()  if name  else "",
                    "email": str(email).strip() if email else "",
                    "title": str(title).strip() if title else "",
                })

    print(f"[BRIEF] Primary POC: {poc_name} ({poc_email})")
    print(f"[BRIEF] Additional contacts: {len(alt_contacts)}")

except Exception as e:
    print(f"[WARN] Contact sheet unavailable: {e}")
```

---

### Step 4 — Check for existing Slack channel

Search Slack for an existing internal channel for this account. The expected
naming convention is `#account-[customer-slug]` (e.g. `#account-bmo`,
`#account-dennys`). Also check for variations like `#[customer-slug]-internal`.

```python
# MCP call: Slack:slack_search_channels(query=customer_name)
# _slack_results = <MCP result>

slack_channel_id   = None
slack_channel_name = None
slack_channel_exists = False

# Build slug from customer name: lowercase, remove special chars, spaces to hyphens
_slug = re.sub(r"[^a-z0-9\-]", "", customer_name.lower().replace(" ", "-").replace("'",""))
_candidate_names = [f"account-{_slug}", f"{_slug}-internal", f"vroc-{_slug}", _slug]

try:
    # Claude searches Slack MCP for each candidate name
    # and checks results for a match
    for candidate in _candidate_names:
        # MCP call: Slack:slack_search_channels(query=candidate)
        # Check if any result name matches candidate
        # If found: set slack_channel_id, slack_channel_name, slack_channel_exists = True
        pass

    if slack_channel_exists:
        print(f"[BRIEF] ✅ Slack channel found: #{slack_channel_name} ({slack_channel_id})")
    else:
        print(f"[BRIEF] No existing Slack channel found for '{customer_name}'")
        print(f"        Checked: {_candidate_names}")

except Exception as e:
    print(f"[WARN] Slack search unavailable: {e}")
```

If no channel is found, ask the analyst:

```
No internal Slack channel was found for [customer_name].

Would you like to create one now? It will be set up as:
  Channel name : #account-[slug]
  Purpose      : Internal delivery channel for [customer_name] — vROC team use only
  Members added: [fte_owner], [contractor] (if assigned)

Options:
  1. Yes — create the channel and add team members
  2. Yes — but use a different name (I'll specify)
  3. No — I'll set it up manually later
  4. No — a channel already exists (provide the name)
```

If option 1 or 2:

```python
# Derive final channel name from analyst choice
_channel_name = f"account-{_slug}"  # or analyst-specified name

# MCP call: Slack:slack_search_users(query=fte_owner email or name)
# → _fte_user_id = result

# MCP call: Slack:slack_search_users(query=contractor email or name)  [if contractor set]
# → _contractor_user_id = result

# NOTE: Claude cannot create Slack channels directly via MCP —
# the Slack MCP provides search and messaging tools only.
# Present the analyst with the exact steps to create the channel:

print(f"""
📋 SLACK CHANNEL SETUP

Claude cannot create Slack channels directly, but here's everything you need:

Channel name  : #{_channel_name}
Purpose       : Internal delivery channel for {customer_name} — vROC team only
Members to add: {fte_owner or 'FTE owner'}, {contractor or 'contractor analyst (when assigned)'}

Steps:
  1. In Slack: click + next to "Channels" in the sidebar
  2. Select "Create a channel"
  3. Name: {_channel_name}
  4. Set as Private
  5. Purpose: Internal delivery channel for {customer_name} — vROC team only
  6. Add members: {fte_owner}, {contractor or '[contractor when assigned]'}

Once created, paste the channel ID or URL here and Claude will log it
in the Account Brief and Project Instructions.
""")

# Wait for analyst to confirm channel creation and provide ID/name
# _channel_confirmed = input("Channel created? Paste channel name or skip: ").strip()
# if _channel_confirmed:
#     slack_channel_name = _channel_confirmed.lstrip("#")
#     slack_channel_exists = True
```

If option 4 (channel already exists under a different name):

```python
# slack_channel_name = analyst-provided name
# slack_channel_exists = True
# Log it — don't create a duplicate
print(f"[BRIEF] Existing channel logged: #{slack_channel_name}")
```

---

### Step 5 — Check Drive for existing context

```python
# MCP calls (Claude executes these):
#
# 1. Find customer folder in shared drive:
#    Google Drive:search_files(
#      query="name = '<customer_name>' and mimeType = 'application/vnd.google-apps.folder'",
#      driveId='0ALKbCh3e_wq3Uk9PVA', includeItemsFromAllDrives=True
#    )
#
# 2. Check for prior reports:
#    Google Drive:search_files(
#      query="name contains 'Weekly' and '<customer_folder_id>' in parents"
#    )
#    → prior_reports_count = len(results)
#    → most_recent_report  = results[0]["name"] if results else None
#
# 3. Check for ZDaaS folder:
#    Google Drive:search_files(
#      query="name = '<customer_name>' and '<ZDAAS_ROOT_ID>' in parents
#             and mimeType = 'application/vnd.google-apps.folder'"
#    )
#    → zdaas_active = len(results) > 0
#
# 4. Check for call notes:
#    Google Drive:search_files(
#      query="(name contains 'Notes' or name contains 'Transcript')
#             and '<customer_folder_id>' in parents"
#    )
#    → call_notes_count = len(results)

print(f"[BRIEF] Drive: reports={prior_reports_count} | ZDaaS={zdaas_active} | Notes={call_notes_count}")
```

---

### Step 6 — Analyst interview

Present these questions conversationally, one section at a time.
Skip any question already resolved automatically from Steps 1–5.

```
SECTION A — Delivery & Communication

1. What day and time is the weekly call? (if not in coverage tracker)
2. Who is the primary POC — name, title, preferred communication style?
   (e.g. "Maureen O'Connell — prefers brief emails, no jargon")
3. Are there secondary stakeholders who should be cc'd or briefed?
4. Does this customer prefer a specific framing?
   Options: Compliance/regulatory | Executive summary | Technical detail | Balanced
5. Any communication landmines?
   (e.g. "do not reference breach names without clearance",
          "always copy legal on breach comms",
          "attribution errors have caused issues in the past")

SECTION B — Scope & Delivery Format

6. What vendor tiers are in scope?
   Options: All | Platinum only | Platinum + Gold | Custom
7. Are there slides that should always be included or excluded?
8. Is ZDaaS active? (confirm or correct Drive finding)
9. Any known gaps in vendor coverage (vendors with no POC on file)?

SECTION C — Account History & Context

10. What does this customer care most about?
    (e.g. "score improvements over time", "compliance posture for audits",
           "executive visibility into specific vendors")
11. Are there active escalations, sensitive vendors, or open issues?
12. Anything notable from recent calls or deliveries?
    (e.g. "customer was unhappy with attribution last month",
           "asked us to prioritize financial sector vendors")
13. Internal notes about maturity or sophistication?
    (e.g. "CISO is technically deep", "procurement-driven")

SECTION D — Operational Notes

14. Who is the backup FTE if primary is out?
15. Any blackout periods (e.g. no deliveries during board cycles)?
16. Is Chorus recording active for this customer's calls?
17. Anything else a new analyst picking up this account needs to know?
```

---

### Step 7 — Build the Account Brief Google Doc

Create a Google Doc in the customer's Drive folder. Content:

```
ACCOUNT BRIEF — [Customer Name]
Last updated: YYYY-MM-DD | Updated by: [analyst]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## At a Glance
| Field              | Value                    |
|--------------------|--------------------------|
| Customer Name      | [customer_name]          |
| Primary Domain     | [customer_domain]        |
| MAX Customer ID    | [max_customer_id]        |
| Contract Tier      | [account_tier]           |
| Total Vendors      | [vendor_count]           |
| Tier Composition   | [tier_composition]       |
| FTE Owner          | [fte_owner]              |
| Contractor Analyst | [contractor]             |
| Backup FTE         | [backup_fte]             |
| Delivery Cadence   | [delivery_day]           |
| Call Schedule      | [call_schedule]          |
| ZDaaS Active       | [zdaas_active]           |
| Slack Channel      | #[slack_channel_name]    |
| Brief Version      | [timestamp]              |

---

## Primary Contact
| Field | Value              |
|-------|--------------------|
| Name  | [poc_name]         |
| Title | [poc_title]        |
| Email | [poc_email]        |
| Style | [comm_style]       |

## Additional Contacts
[table of alt_contacts, or "None on file"]

---

## What This Customer Cares About
[interview Q10 — free text]

---

## Delivery Preferences
- **Framing:** [compliance/executive/technical/balanced]
- **Scope:** [tier scope]
- **Slides:** [included/excluded]
- **Email style:** [brief/detailed/formal/conversational]

---

## Landmines & Sensitive Context
[interview Q5, Q11, Q12 — flag each with ⚠️]

---

## Account History Notes
[interview Q12, Q13]

---

## Operational Notes
- **Backup FTE:** [interview Q14]
- **Blackout periods:** [interview Q15]
- **Chorus recording:** [interview Q16]
- **Slack channel:** #[slack_channel_name] — [slack_channel_id or "pending creation"]
- **Other:** [interview Q17]

---

## Vendor Coverage Notes
[interview Q9, Q11 — no-POC vendors, sensitive vendors, known gaps]

---

## Platinum Vendors
[platinum_vendors list, max 20 — "see MAX for full list" if more]

---
_Maintained by the FTE owner. Update after any call where new context is
learned. Do not include ARR or revenue data._
```

```python
# MCP call: Google Drive:create_file(
#   title    = f"Account Brief — {customer_name}",
#   mimeType = "application/vnd.google-apps.document",
#   parentId = customer_folder_id,
#   textContent = <rendered doc above>
# )
# → brief_doc_id  = result["id"]
# → brief_doc_url = f"https://docs.google.com/document/d/{brief_doc_id}/edit"
print(f"[BRIEF] ✅ Account Brief Doc created: {brief_doc_url}")
```

---

### Step 8 — Build Project Instructions file

This is the most operationally important output. It is a markdown block
designed to be pasted directly into the Claude project's Instructions field.
Once in place, every future conversation in this project starts with full
account context — no re-derivation, no re-running the brief skill.

Generate and display this block clearly:

```
════════════════════════════════════════════════════════
PROJECT INSTRUCTIONS — [Customer Name]
Paste everything below this line into the project Instructions field.
════════════════════════════════════════════════════════

```project-context
## Account: [customer_name]

### Identity
- **Customer name:** [customer_name]
- **Primary domain:** [customer_domain]
- **MAX customer ID:** [max_customer_id]
- **Contract tier:** [account_tier]

### Delivery Team
- **FTE owner:** [fte_owner]
- **Contractor:** [contractor or "unassigned"]
- **Backup FTE:** [backup_fte or "⚠️ not set"]
- **Slack channel:** #[slack_channel_name or "⚠️ not yet created"]

### Delivery Schedule
- **Call:** [call_schedule]
- **Delivery day:** [delivery_day]

### Vendor Portfolio
- **Total vendors:** [vendor_count]
- **Tiers:** [tier_composition]
- **ZDaaS active:** [zdaas_active]

### Primary Customer Contact
- **Name:** [poc_name]
- **Title:** [poc_title]
- **Email:** [poc_email]
- **Communication style:** [comm_style]

### Delivery Preferences
- **Framing:** [framing]
- **Scope:** [scope]
- **Slides:** [slide_preferences]
- **Email style:** [email_style]

### Landmines & Sensitive Context
[landmines — one bullet per item, each prefixed with ⚠️]

### Key Context
[2–3 sentence summary of what this customer cares about
 and anything critical for a new analyst to know]

### Resources
- **Account Brief (full):** [brief_doc_url]
- **Weekly Reporting folder:** [weekly_reporting_folder_id]
- **Last updated:** [timestamp]
```

════════════════════════════════════════════════════════
```

Also save this block as a plain text file in the customer's Drive folder:

```python
# MCP call: Google Drive:create_file(
#   title       = f"Project Instructions — {customer_name}",
#   parentId    = customer_folder_id,
#   textContent = <rendered instructions block above>,
#   contentMimeType = "text/plain",
#   disableConversionToGoogleType = True
# )
# → instructions_file_url = result url
print(f"[BRIEF] ✅ Project Instructions saved to Drive: {instructions_file_url}")
```

---

### Step 9 — Build master index row

Output a single row to append to the Account Brief Sheet:

```
| [customer_name] | [customer_domain] | [max_customer_id] | [account_tier] |
| [vendor_count] | [tier_composition] | [fte_owner] | [contractor] |
| [backup_fte] | [call_schedule] | [delivery_day] | [poc_name] | [poc_email] |
| [zdaas_active] | #[slack_channel_name] | [brief_doc_url] | [timestamp] |
```

Print clearly:

```
📋 MASTER INDEX ROW — paste into Account Brief Sheet
[single pipe-delimited row]

Columns: Customer Name | Domain | MAX ID | Contract Tier | Vendor Count |
Tier Composition | FTE Owner | Contractor | Backup FTE | Call Schedule |
Delivery Day | Primary POC | POC Email | ZDaaS Active | Slack Channel |
Brief Doc URL | Last Updated
```

---

### Step 10 — Completion summary

```python
print("\n" + "="*60)
print("✅ ACCOUNT BRIEF COMPLETE")
print("="*60)
print(f"  Customer           : {customer_name}")
print(f"  Account Brief Doc  : {brief_doc_url}")
print(f"  Project Instructions: {instructions_file_url}")
print(f"  Slack channel      : {'#' + slack_channel_name if slack_channel_exists else '⚠️ not yet created'}")
print()
print("Next steps:")
print("  1. Paste the Project Instructions block into this project's")
print("     Instructions field (gear icon → Instructions)")
print("  2. Append the master index row to the Account Brief Sheet")
print("  3. Share #" + (slack_channel_name or "[channel]") + " with the assigned contractor")
if not slack_channel_exists:
    print("  4. ⚠️  Create the Slack channel — steps were printed above")
if _project_warning:
    print()
    print("  ⚠️  This ran outside a Claude project. Move the outputs to")
    print(f"     the '{customer_name}' project for full benefit.")
print("="*60)
```

---

## Hard Rules

- **⚠️ PROJECT CHECK IS MANDATORY.** Always check for project context first.
  Never silently skip it.
- **Never include ARR, contract value, or revenue data** anywhere — not in
  the Doc, the index row, or the Project Instructions.
- **Never fabricate qualitative context.** If the analyst doesn't know, leave
  it blank with ⚠️.
- **Landmines section is sacred.** Any past delivery failure or customer
  sensitivity goes here, clearly flagged with ⚠️.
- **One brief per customer.** If a brief already exists, open it and offer to
  update specific sections rather than creating a duplicate.
- **Never overwrite analyst-supplied sections** during a refresh run — only
  update At a Glance, Platinum Vendors, and the Project Instructions
  auto-populated fields.
- **Slack channel creation is guidance only** — Claude cannot create Slack
  channels via MCP. Always print the steps and wait for analyst confirmation
  before logging the channel as created.
- **Project Instructions must be offered on every run**, even refreshes.
  Regenerate the block with updated values and prompt the analyst to
  replace the existing instructions.
- **Keys setup is never a hard requirement.** Never block the brief on missing
  tokens. Always offer to skip and fill fields manually.
- **Never display token values in output** after collection — mask them as
  `[set]` in any summary or confirmation message.
- **Keys file is named `~/.max_api_keys`** — distinct from `~/.vroc_keys` used
  by vroc-session-init. Both formats are compatible (same KEY=value syntax).
  If the analyst already has a `~/.vroc_keys` file, note that both work and
  suggest they consolidate to one file over time.
