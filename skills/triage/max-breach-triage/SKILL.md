---
name: max-breach-triage
description: >
  Analyze and triage all untriaged breaches in the SecurityScorecard MAX Workstation
  using the SOP-backed decision logic built from the LIFARS breach review guidance.
  Fetches untriaged breaches, classifies each as REPORT or HIDE using automated rules,
  surfaces ambiguous items for analyst review, and (with explicit confirmation) executes
  the triage write against the MAX API. Use this skill whenever the user says "triage
  breaches", "review the breach backlog", "run breach triage", "clear untriaged
  breaches", "what breaches need review", or any variation of wanting to process the
  MAX workstation breach queue. Always use this skill — do not attempt breach
  triage without it; the decision rules are non-obvious and the API headers are
  breach-specific. Requires vroc-session-init to have been run first.
version: 1.0
last_updated: 2026-06-25
owner: ian.mains@securityscorecard.io
status: active
category: triage
---

# MAX Breach Triage Skill

Applies SOP-backed decision logic to classify every untriaged MAX workstation breach
as **REPORT** or **HIDE**, surfaces edge cases for analyst review, and executes the
triage write on confirmation.

> **Prerequisites:** `SSC_API_TOKEN` must be in the environment.
> Run `/vroc-session-init` first if keys are not loaded.

> **SOP Source:** "SOP: Breach Review Guidance - MAX Workstation" and
> "SOP: Daily Findings Review — Frequency & Decision Rationale" (MAXKH Confluence space).
> See `references/decision-rules.md` for the full codified ruleset with rationale.

---

## API Notes (Breach-Specific)

| Property        | Value                                              |
|-----------------|----------------------------------------------------|
| GET endpoint    | `https://api.securityscorecard.io/max/partner/breaches` |
| PUT endpoint    | same                                               |
| Version header  | **`deprecated`** (not `beta` — breaches only)     |
| Chunk limit     | **100 items per PUT**                              |
| GET key fields  | `breach_id`, `vendor_id`, `vendor_domain`, `vendor_name`, `description`, `published_date`, `triaged`, `report`, `is_active_breach` |
| PUT payload     | `{"breaches": [{"breach_id":..., "vendor_id":..., "report": bool, "triaged": true}]}` |

---

## Step 1 — Fetch All Untriaged Breaches

```python
import os, json, math, time, requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta, datetime

# ── Load keys fresh from uploaded file ───────────────────────────────────────
candidates = ["/mnt/user-data/uploads/.vroc_keys",
              "/mnt/user-data/uploads/_vroc_keys",
              "/mnt/user-data/uploads/vroc_keys"]
keyfile = next((p for p in candidates if os.path.exists(p)), None)
if not keyfile:
    raise RuntimeError("No .vroc_keys file found — run vroc-session-init first")
keys = {}
with open(keyfile) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "=" in line:
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip()

BASE  = "https://api.securityscorecard.io"
TOKEN = keys["SSC_API_TOKEN"]

HEADERS_READ = {
    "accept":        "application/json",
    "Authorization": f"Token {TOKEN}",
    "version":       "deprecated",          # REQUIRED for breaches — not beta
}
HEADERS_WRITE = {**HEADERS_READ, "content-type": "application/json"}

# ── Fetch all untriaged breaches (parallel pagination) ────────────────────────
def fetch_all_untriaged() -> pd.DataFrame:
    resp = requests.get(f"{BASE}/max/partner/breaches",
                        headers=HEADERS_READ, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"GET breaches failed: {resp.status_code} {resp.text[:200]}")

    meta       = resp.json()
    total      = meta.get("total", 0)
    size       = meta.get("size") or 50
    all_items  = list(meta.get("entries", []))
    page_count = math.ceil(total / size)

    def _get_page(page):
        r = requests.get(f"{BASE}/max/partner/breaches",
                         headers=HEADERS_READ,
                         params={"page": page}, timeout=20)
        return r.json().get("entries", []) if r.status_code == 200 else []

    if page_count > 1:
        with ThreadPoolExecutor(max_workers=min(10, page_count - 1)) as pool:
            for entries in pool.map(_get_page, range(1, page_count)):
                all_items.extend(entries)

    df = pd.json_normalize(all_items)
    df['published_date'] = pd.to_datetime(df['published_date'], errors='coerce')
    untriaged = df[~df['triaged'].eq(True)].copy()
    print(f"Total breaches: {total:,} | Untriaged: {len(untriaged):,}")
    return untriaged
```

---

## Step 2 — Classify Each Breach

Read `references/decision-rules.md` before writing or modifying classification logic.
The rules below encode the full SOP + all analyst decisions from the May 2026 session.

```python
SKILL_RUN_DATE    = date.today()
FIVE_YEARS_AGO    = SKILL_RUN_DATE - timedelta(days=5 * 365)
NINETY_DAYS_AGO   = SKILL_RUN_DATE - timedelta(days=90)

# Keywords that indicate an unconfirmed/alleged claim (→ 90-day rule applies)
ALLEGED_KEYWORDS = ['allegedly', 'alleged ', 'claims to have']

# Keywords that suggest a product vulnerability rather than a data breach
VULN_KEYWORDS = [
    'vulnerability', 'cve-', 'zero-day', 'zero day', 'exploit',
    'rce ', 'remote code execution', 'sql injection', 'buffer overflow',
    'patch', 'security advisory',
]

# These override VULN_KEYWORDS — if present, treat as a confirmed breach
# even when vuln language appears (e.g. "zero-day was the attack vector")
BREACH_CONFIRMED_OVERRIDES = [
    'data breach', 'personal information', 'affected', 'individuals',
    'notified', 'notification', 'social security', 'financial account',
    'medical record', 'unauthorized access', 'compromised',
    'exposed', 'stolen', 'leaked',
]

def classify_breach(row) -> tuple[str, str]:
    """Returns (recommendation, reasoning) — 'REPORT', 'HIDE', or 'FLAG'."""
    desc   = (row.get('description') or '').lower()
    domain = (row.get('vendor_domain') or '').lower()
    pub_dt = row.get('published_date')
    pub    = pub_dt.date() if pd.notna(pub_dt) else None

    if pub is None:
        return 'FLAG', 'No published date — cannot auto-classify, needs analyst review'

    # ── Rule 1: Age >5 years → HIDE ─────────────────────────────────────────
    if pub < FIVE_YEARS_AGO:
        return 'HIDE', f'H2: Published {pub} — older than 5 years, auto-hide'

    # ── Rule 2: H1 — Product vulnerability (NOT a data breach) ──────────────
    # IMPORTANT: Only hide if vuln keywords present AND no confirmed-breach
    # language. A "zero-day" can be the attack vector of a real breach.
    is_vuln = any(k in desc for k in VULN_KEYWORDS)
    is_confirmed_breach = any(k in desc for k in BREACH_CONFIRMED_OVERRIDES)
    if is_vuln and not is_confirmed_breach:
        return 'HIDE', 'H1: Product/software vulnerability with no breach evidence — hide'

    # ── Rule 3: Alleged/unconfirmed claim — 90-day window ───────────────────
    is_alleged = any(k in desc for k in ALLEGED_KEYWORDS)
    if is_alleged:
        if pub >= NINETY_DAYS_AGO:
            return 'REPORT', f'Q7: Unconfirmed claim but within 90 days of run date ({pub}) — report'
        else:
            return 'HIDE', f'Q7: Unconfirmed claim older than 90 days ({pub}) — hide'

    # ── Rule 4: Confirmed breach within 5yr window → REPORT ─────────────────
    return 'REPORT', f'Confirmed breach published {pub}, within 5yr window — report'


def classify_all(df: pd.DataFrame) -> pd.DataFrame:
    results = df.apply(classify_breach, axis=1, result_type='expand')
    df = df.copy()
    df['recommendation'] = results[0]
    df['reasoning']      = results[1]
    return df
```

---

## Step 3 — Surface Analyst Review Items

Before executing, print a summary and list any FLAG items for human review.
**Do not execute the triage write until the analyst has confirmed.**

```python
def surface_review(df: pd.DataFrame):
    counts = df['recommendation'].value_counts()
    print(f"\n{'='*55}")
    print(f"  BREACH TRIAGE ANALYSIS — {SKILL_RUN_DATE}")
    print(f"{'='*55}")
    for rec in ['REPORT', 'HIDE', 'FLAG']:
        n = counts.get(rec, 0)
        sym = {'REPORT': '✅', 'HIDE': '🔴', 'FLAG': '❓'}.get(rec)
        print(f"  {sym}  {rec:<8}  {n:>4}")
    print(f"{'='*55}")

    flags = df[df['recommendation'] == 'FLAG']
    if not flags.empty:
        print(f"\n❓ {len(flags)} ITEM(S) NEED ANALYST REVIEW BEFORE EXECUTING:\n")
        for _, r in flags.iterrows():
            pub = r['published_date'].date() if pd.notna(r['published_date']) else 'unknown'
            print(f"  [{pub}] {r['vendor_name']} ({r['vendor_domain']})")
            print(f"         → {r['reasoning']}")
            desc_short = (r.get('description') or '')[:150]
            print(f"         Description: {desc_short}...")
        print("\nResolve FLAG items before proceeding.")
        return False   # block execution
    else:
        print("\n✓ No FLAG items — ready to execute on analyst confirmation.")
        return True    # safe to proceed
```

---

## Step 4 — Execute Triage Write (on analyst confirmation only)

```python
def execute_triage(df: pd.DataFrame):
    """Write all REPORT and HIDE decisions to the MAX API."""
    report_df = df[df['recommendation'] == 'REPORT']
    hide_df   = df[df['recommendation'] == 'HIDE']

    batch = (
        [{"breach_id": r["breach_id"], "vendor_id": r["vendor_id"],
          "report": True,  "triaged": True} for _, r in report_df.iterrows()] +
        [{"breach_id": r["breach_id"], "vendor_id": r["vendor_id"],
          "report": False, "triaged": True} for _, r in hide_df.iterrows()]
    )

    CHUNK_SIZE = 100
    chunks = [batch[i:i+CHUNK_SIZE] for i in range(0, len(batch), CHUNK_SIZE)]
    print(f"\nSending {len(chunks)} PUT request(s) ({len(batch)} total items)...")

    success_report, success_hide, failed = 0, 0, 0

    for i, chunk in enumerate(chunks):
        r = requests.put(
            f"{BASE}/max/partner/breaches",
            headers=HEADERS_WRITE,
            json={"breaches": chunk},
            timeout=30
        )
        if r.status_code in (200, 204):
            n_r = sum(1 for x in chunk if x['report'])
            n_h = sum(1 for x in chunk if not x['report'])
            success_report += n_r
            success_hide   += n_h
            print(f"  Chunk {i+1}/{len(chunks)} ✓  ({n_r} report, {n_h} hide)")
        else:
            failed += len(chunk)
            print(f"  Chunk {i+1}/{len(chunks)} ✗  {r.status_code} — {r.text[:200]}")
        time.sleep(0.1)

    print(f"\n{'='*40}")
    print(f"  REPORT written: {success_report}")
    print(f"  HIDE written:   {success_hide}")
    print(f"  Failed:         {failed}")
    print(f"{'='*40}")

    # ── Verify ────────────────────────────────────────────────────────────────
    time.sleep(2)
    resp = requests.get(f"{BASE}/max/partner/breaches",
                        headers=HEADERS_READ,
                        params={"triaged": "false"},
                        timeout=15)
    remaining = resp.json().get("total", "?")
    print(f"\nPost-triage verification — untriaged remaining: {remaining}")
    if remaining == 0:
        print("✓ Workstation breach queue fully cleared.")
    else:
        print(f"⚠ {remaining} untriaged breach(es) remain — re-run to catch stragglers.")
```

---

## Step 5 — Full Orchestration

```python
# 1. Fetch
df_untriaged = fetch_all_untriaged()

if df_untriaged.empty:
    print("✓ No untriaged breaches found — workstation is clear.")
else:
    # 2. Classify
    df_classified = classify_all(df_untriaged)

    # 3. Surface for review — STOP HERE, show analyst, wait for confirmation
    ready = surface_review(df_classified)

    # 4. Execute only after analyst says "execute" or "go"
    # if ready:
    #     execute_triage(df_classified)
```

> **Important:** Step 4 is commented out intentionally. Present the analysis to
> the analyst first. Only call `execute_triage()` after explicit confirmation.

---

## Gotchas & Lessons Learned

| Issue | Resolution |
|---|---|
| Breaches require `version: deprecated` | Both GET and PUT — using `beta` returns empty or wrong data |
| H1 false positive on "zero-day" | Check for confirmed-breach overrides before hiding — "zero-day" can be the attack vector of a real breach (Parexel, May 2026) |
| Settlement notifications of old breaches | Appear recent (2024 publish date) but describe 2019 events — read description, not just date |
| Misattribution in SSC feed | Domain attribution can be wrong (Taiwan `president.gov.tw` showed Philippine president) — description review catches this |
| Date inconsistencies in feed | `published_date` can predate the incident described — treat as data quality FLAG |
| Ransomware gang claims | "Allegedly hacked as reported by [gang]" = unconfirmed → 90-day rule applies |
| Phishing/fraud ≠ data breach | Coinbase 2024: customer phishing fraud — not a breach of Coinbase's systems (H3) |
| All vendors are monitored | Do not apply H3 based on assumed lack of customer exposure — every vendor in MAX is monitored by someone |

---

## Parameters Reference

| Parameter | Value | Change when... |
|---|---|---|
| `OLD_BREACH_DAYS` | 5 × 365 (5 years) | Leadership decides to shift the lookback window |
| `ALLEGED_RECENT_DAYS` | 90 days | Policy changes on how long to surface unconfirmed claims |
| `ALLEGED_KEYWORDS` | `['allegedly', 'alleged ', 'claims to have']` | New phrasing patterns appear in the feed |
| `VULN_KEYWORDS` | See Step 2 | New vulnerability-report patterns emerge |
| `BREACH_CONFIRMED_OVERRIDES` | See Step 2 | New confirmed-breach evidence patterns emerge |

For full SOP context and rule rationale → `references/decision-rules.md`
