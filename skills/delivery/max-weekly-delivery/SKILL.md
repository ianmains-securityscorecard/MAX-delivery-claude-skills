---
name: max-weekly-delivery
description: >
  Orchestrates end-to-end weekly delivery for SSC MAX accounts: reads the PPTX and XLSX,
  synthesizes a customer-facing blurb, uploads files to the MAX dashboard, stages a Gmail
  draft to the POC, and posts a risk briefing Slack draft to the account channel. Use
  whenever the user says "run the weekly delivery", "deliver this week's report", "stage
  the weekly email", "full delivery for [client]", "MAX delivery", or "push the report
  out". Also trigger when *Weekly_Vendor_Report*.pptx or *.xlsx is uploaded with "deliver",
  "send", "email", or "stage" — delivery is almost always the next step. Do NOT use for
  analyst summaries (weekly-analyst-summary), findings triage (max-findings-triage),
  breach/CVE notifications (zdaas-report), QBR decks, or ROI reports.
---

# MAX Weekly Delivery Skill

Orchestrates the weekly client delivery across five phases: analyze files, synthesize
the blurb, upload to MAX dashboard, stage the Gmail draft, and post the Slack risk
briefing to the account channel.

---

## Prerequisites Check

Confirm before starting:
- [ ] PPTX uploaded: `{Client}-Weekly_Vendor_Report-{Tier}-Generated_on-{YYYY-MM-DD}_VISUAL.pptx`
- [ ] XLSX uploaded: `{Client}-Weekly_Vendor_Report-{Tier}-Datasheet-{YYYY-MM-DD}.xlsx`
- [ ] Account Slack channel known from project context (`#account-[client-slug]`)
- [ ] Customer POC email and first name known (from project context or `MS_Customer_Contact_Details`)
- [ ] `CUST_DOMAIN` known — the customer's root domain (from `MS_Customer_Contact_Details` `Domain` field)
- [ ] `analyst_name` known — the VRC analyst delivering this account (for email signature)
- [ ] `.vroc_keys` loaded if live API enrichment is needed (run `vroc-session-init`)

Parse `customer_name`, `report_date_label`, and `tier` from the PPTX filename.
If any prerequisite is missing, ask before proceeding.

---

## Phase 1 — Read and Analyze the Files

### 1a — Extract PPTX content

```python
import subprocess, sys

# Use extract-text for fast slide-by-slide text dump
result = subprocess.run(
    ["extract-text", pptx_path],
    capture_output=True, text=True
)
pptx_text = result.stdout
print(pptx_text[:3000])  # Preview first 3000 chars to confirm extraction
```

If `extract-text` is unavailable, fall back to python-pptx:

```python
from pptx import Presentation

prs = Presentation(pptx_path)
pptx_text = ""
for i, slide in enumerate(prs.slides, 1):
    slide_text = "\n".join(
        shape.text for shape in slide.shapes if shape.has_text_frame
    ).strip()
    if slide_text:
        pptx_text += f"\n## Slide {i}\n{slide_text}\n"
```

### 1b — Extract XLSX data

```python
import openpyxl

def read_sheet(wb, name):
    if name not in wb.sheetnames:
        return [], []
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers = rows[0]
    data = [dict(zip(headers, r)) for r in rows[1:] if any(v is not None for v in r)]
    return headers, data

wb = openpyxl.load_workbook(xlsx_path, data_only=True)
_, breaches  = read_sheet(wb, "Breaches")
_, critical  = read_sheet(wb, "Critical Indicators")
_, high      = read_sheet(wb, "High Indicators")
_, cves      = read_sheet(wb, "CVEs")
_, ind_count = read_sheet(wb, "Indicator Count")
_, tp_vend   = read_sheet(wb, "Third Party Vendors")

print(f"Breaches:   {len(breaches)}")
print(f"Critical:   {len(critical)}")
print(f"High:       {len(high)}")
print(f"CVEs:       {len(cves)}")
print(f"Vendors:    {len(ind_count)}")
```

### 1c — Run full analyst summary

Invoke the `weekly-analyst-summary` skill on the extracted data to produce the full
analysis. This is the source of truth for the blurb written in Phase 2.

If `weekly-analyst-summary` has already run this session, use that output directly.

---

## Phase 2 — Synthesize the Delivery Blurb

Compress the full analyst summary into a customer-facing featured update blurb.

**Rules:**
- 3–5 sentences maximum — customers read this before opening the report
- Lead with the single highest-priority signal (worst vendor grade, new KEV, breach, score drop >5)
- Include one concrete action or awareness item
- Close with a forward-looking note (next touchpoint, outreach in progress)
- Tone: direct, professional — no filler ("We are pleased to...", "This week saw...")
- No SSC-internal jargon (no "Gold tier", "Platinum", "MAX workstation")

**Compression prompt (run via Claude API or compose inline):**

```
You are a managed security services analyst delivering a weekly risk briefing to {customer_name}.
Based on the following analyst summary, write a 3–5 sentence featured update for the customer.
Lead with the most important finding. Include one concrete action. Close with a forward note.
Be direct. No preamble. No filler phrases.

ANALYST SUMMARY:
{full_analyst_summary_text}
```

Store result as `featured_update_text`.

---

## Phase 2.5 — Upload to MAX Dashboard

Run between Phase 2 (blurb synthesis) and Phase 3 (Gmail draft). Upload the PPTX and
XLSX to the customer's MAX workstation so the report is available before the call.

**Base URL:** `platform-api.securityscorecard.io` — different from all other MAX endpoints.

```python
import os, requests, json, re

API_BASE      = "https://api.securityscorecard.io"         # managed-customers endpoint
PLATFORM_BASE = "https://platform-api.securityscorecard.io"  # documents endpoint
MAX_H = {
    "accept":        "application/json; charset=utf-8",
    "Authorization": f"Token {os.environ['SSC_API_TOKEN']}",
    "version":       "beta",
}

# ── Step A: Resolve customer_id from /max/partner/managed-customers ───────────
# Single call — returns customer_id directly. Much faster than paginating vendors.
def get_customer_id(target_name: str, target_domain: str = "") -> str | None:
    r = requests.get(
        f"{API_BASE}/max/partner/managed-customers",
        headers=MAX_H,
        timeout=15,
    )
    if not r.ok:
        print(f"  ✗ managed-customers {r.status_code}: {r.text[:200]}")
        return None

    clean = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    for entry in r.json().get("entries", []):
        cname   = entry.get("customer_name",   "")
        cdomain = entry.get("customer_domain", "")
        cid     = entry.get("customer_id")
        if target_domain and cdomain.lower().strip() == target_domain.lower().strip():
            print(f"  ✓ Matched by domain: {cdomain} → customer_id={cid}")
            return cid
        if clean(cname) == clean(target_name):
            print(f"  ✓ Matched by name: {cname} → customer_id={cid}")
            return cid

    print(f"  ✗ No customer_id found for '{target_name}'")
    return None

# ── Step B: Upload files ──────────────────────────────────────────────────────
MIME = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf":  "application/pdf",
}

def upload_to_max_dashboard(file_path: str, customer_id: str,
                             description: str, vendor_ids: list | None = None) -> bool:
    ext       = os.path.splitext(file_path)[1].lower()
    mime      = MIME.get(ext, "application/octet-stream")
    form_data = {"customerId": customer_id, "description": description}
    if vendor_ids:
        form_data["associatedVendorIds"] = json.dumps(vendor_ids)  # JSON string, not list

    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{PLATFORM_BASE}/max/partner/documents",
            headers=MAX_H,       # Do NOT set Content-Type — requests sets multipart boundary
            files={"file": (os.path.basename(file_path), f, mime)},
            data=form_data,
            timeout=120,
        )
    if resp.status_code in (200, 201, 202):
        print(f"  ✓ Uploaded: {os.path.basename(file_path)}")
        return True
    print(f"  ✗ Upload failed ({resp.status_code}): {resp.text[:300]}")
    return False

# ── Execute ───────────────────────────────────────────────────────────────────
customer_id   = get_customer_id(customer_name, target_domain=CUST_DOMAIN)
description   = f"Weekly Vendor Risk Report — {report_date_label}"
upload_ok     = False
pptx_filename = os.path.basename(pptx_path)
xlsx_filename = os.path.basename(xlsx_path)

if customer_id:
    paths   = [p for p in [pptx_path, xlsx_path] if os.path.exists(p)]
    results = [upload_to_max_dashboard(p, customer_id, description) for p in paths]
    upload_ok = bool(results) and all(results)  # all([]) == True; guard with bool(results)
else:
    print("  ⚠ MAX upload skipped — customer_id not resolved")
```

Add `customer_id` and `upload_ok` to the variable scope before Phase 3.

---

## Phase 3 — Stage Gmail Draft

Compose and stage the delivery email. **Never send — always draft only.**

**Subject line:**
```
SecurityScorecard MAX | Weekly Vendor Risk Update — {customer_name} | {report_date_label}
```

**Body:**
```
Hi {first_name},

{featured_update_text}

The full report and data sheet are ready for your review ahead of {next_call_day}. Any questions before the call — reach out directly.

{analyst_name}
Virtual Risk Operations Center | SecurityScorecard
```

**Gmail MCP call:**
```
tool:    Gmail:create_draft
to:      {customer_email}
subject: SecurityScorecard MAX | Weekly Vendor Risk Update — {customer_name} | {report_date_label}
body:    <full composed email above>
```

Confirm draft ID after creation. If the Gmail MCP supports file attachment and the PPTX
is under 25 MB, attach it. Otherwise the body suffices.

---

## Phase 4 — Stage Slack Draft

Post a risk briefing to the account's private channel so CSMs, sales, and other account
team members are informed on this week's key findings ahead of the touchpoint.
**Always draft — never send directly.**

**Resolve the channel:**
```
tool:  Slack:slack_search_channels
query: account-{client_slug}   ← e.g. "account-university-of-denver"
```
Use the returned `channel_id` for the draft call.

**Message:**
```
:bar_chart: *{customer_name} — Weekly Risk Update* | {report_date_label}

{featured_update_text}

:calendar: Touchpoint: {next_call_day}
```

**Slack MCP call:**
```
tool:       Slack:slack_send_message_draft
channel_id: {resolved channel_id}
message:    <composed message above>
```

---

## Phase 5 — Delivery Receipt

Print a clean summary after all phases complete:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WEEKLY DELIVERY STAGED — {customer_name}
  {report_date_label}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ Analysis complete (weekly-analyst-summary)
  ✓ MAX dashboard upload → {customer_id}
      {pptx_filename}
      {xlsx_filename}
  ✓ Gmail draft staged → {customer_email}
      Subject: SecurityScorecard MAX | Weekly Vendor Risk Update...
  ✓ Slack draft staged → #account-{client_slug}

  ACTION REQUIRED: Review + send Gmail draft before {next_call_day}.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

If Phase 2.5 upload failed, replace the MAX dashboard line with:
```
  ⚠ MAX dashboard upload skipped — customer_id not resolved.
    Run get_customer_id("{customer_name}") and retry upload manually.
```

---

## Error Handling

| Error | Action |
|---|---|
| PPTX extraction fails | Try python-pptx fallback; if both fail, ask user to paste slide 4 text manually |
| XLSX sheet missing | Log the gap; continue with available sheets |
| MAX upload — customer_id not resolved | Log warning; continue to Phase 3; note in receipt |
| MAX upload — file POST fails | Log per-file error; continue; note in receipt |
| Gmail MCP unavailable | Skip Phase 3; note in receipt; remind user to draft manually |
| Slack channel not found | Ask for exact channel name; retry search with broader query |
| `weekly-analyst-summary` not yet run | Run it inline before Phase 2 |
| Any phase fails | Surface full error; do not silently skip |

---

## Variable Reference

| Variable | Source |
|---|---|
| `customer_name` | Parsed from PPTX filename |
| `report_date_label` | Parsed from PPTX filename (`YYYY-MM-DD`) |
| `tier` | Parsed from PPTX filename (Gold / Platinum / Silver) |
| `client_slug` | `customer_name.lower().replace(" ", "-")` |
| `customer_email` | Project context or `MS_Customer_Contact_Details` |
| `first_name` | Same source as `customer_email` |
| `analyst_name` | Project context — the VRC delivering this account (e.g. "Ian Swanson") |
| `next_call_day` | Project context (e.g. "Monday") |
| `CUST_DOMAIN` | Contact reference file `Domain` field for this customer |
| `pptx_path` | Uploaded file path |
| `xlsx_path` | Uploaded file path |
| `customer_id` | Phase 2.5 — from `/max/partner/managed-customers` |
| `upload_ok` | Phase 2.5 — `True` if both files uploaded successfully |
| `featured_update_text` | Phase 2 output |
| `full_analyst_summary_text` | `weekly-analyst-summary` output |

---

## Dependencies

- `weekly-analyst-summary` — full analytical output (Phase 1c input)
- `vroc-session-init` — optional; needed only for live API enrichment in analyst summary
- Gmail MCP (`Gmail:create_draft`)
- Slack MCP (`Slack:slack_send_message_draft`, `Slack:slack_search_channels`)
