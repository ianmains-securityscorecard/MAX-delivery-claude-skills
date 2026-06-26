"""
MAX Hostname-Aware Triage Script
=================================
Phase 1: Triage findings with hostnames — match → report=True, mismatch → report=False.
Phase 2: Bulk triage all remaining untriaged findings as report=True.

Loads credentials from _vroc_keys / .vroc_keys in uploads dir.
"""

import os, json, math, time, requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ── Configuration ────────────────────────────────────────────────────────────
CUSTOMER_DOMAIN = None          # Set to "example.com" to scope to one customer
DAYS_LOOKBACK   = 30            # How many days back to scan for untriaged findings
BATCH_SIZE      = 100           # MAX API hard limit per PUT
FETCH_WORKERS   = 10            # Parallel page fetches
PUT_WORKERS     = 8             # Parallel PUT batches
BASE_URL        = "https://api.securityscorecard.io"

# Multi-part TLDs for accurate root domain extraction
MULTI_TLDS = {
    "co.uk", "co.nz", "co.jp", "co.za", "co.in", "co.kr",
    "com.au", "com.br", "com.cn", "com.mx", "com.ar",
    "gov.uk", "gov.au", "net.au", "org.uk", "org.au",
}

# ── Credentials ──────────────────────────────────────────────────────────────
def load_token():
    candidates = [
        "/mnt/user-data/uploads/.vroc_keys",
        "/mnt/user-data/uploads/_vroc_keys",
        "/mnt/user-data/uploads/vroc_keys",
    ]
    for path in candidates:
        if os.path.exists(path):
            keys = {}
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        keys[k.strip()] = v.strip()
            token = keys.get("SSC_API_TOKEN") or os.environ.get("SSC_API_TOKEN")
            if token:
                return token
    token = os.environ.get("SSC_API_TOKEN")
    if token:
        return token
    raise RuntimeError("SSC_API_TOKEN not found. Run vroc-session-init first.")

TOKEN = load_token()

HEADERS_READ = {
    "accept":        "application/json",
    "Authorization": f"Token {TOKEN}",
    "version":       "beta",
}
HEADERS_WRITE = {
    **HEADERS_READ,
    "content-type": "application/json",
}

# ── Helpers ──────────────────────────────────────────────────────────────────
def fetch_all_findings(extra_params: dict = {}) -> pd.DataFrame:
    """Fetch all findings matching params, paginating in parallel."""
    base_params = {"triaged": "false"}
    if CUSTOMER_DOMAIN:
        base_params["customer_domain"] = CUSTOMER_DOMAIN
    base_params.update(extra_params)

    resp = requests.get(f"{BASE_URL}/max/partner/findings",
                        headers=HEADERS_READ, params=base_params, timeout=30)
    if resp.status_code != 200:
        print(f"  ✗ Fetch failed ({resp.status_code}): {resp.text[:200]}")
        return pd.DataFrame()

    meta        = resp.json()
    total       = meta.get("total", 0)
    size        = meta.get("size") or 50
    if total == 0:
        return pd.DataFrame()
    page_count  = math.ceil(total / size)
    all_entries = list(meta.get("entries", []))

    def _get_page(page):
        r = requests.get(f"{BASE_URL}/max/partner/findings",
                         headers=HEADERS_READ,
                         params={**base_params, "page": page},
                         timeout=30)
        return r.json().get("entries", []) if r.status_code == 200 else []

    if page_count > 1:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            for entries in pool.map(_get_page, range(1, page_count)):
                all_entries.extend(entries)

    return pd.json_normalize(all_entries).reset_index(drop=True)


def extract_root_domain(hostname: str) -> str:
    """Extract root registrable domain, multi-TLD aware."""
    hostname = hostname.lower().strip().rstrip(".")
    parts = hostname.split(".")
    if len(parts) < 2:
        return hostname
    if len(parts) >= 3:
        two_part = f"{parts[-2]}.{parts[-1]}"
        if two_part in MULTI_TLDS:
            return f"{parts[-3]}.{two_part}"
    return f"{parts[-2]}.{parts[-1]}"


def hostname_belongs_to_vendor(hostname: str, vendor_domain: str) -> bool:
    """True if hostname IS the vendor domain or is a subdomain of it."""
    h = hostname.lower().strip().rstrip(".")
    v = vendor_domain.lower().strip().rstrip(".")
    return h == v or h.endswith("." + v)


def has_valid_hostname(row) -> bool:
    val = str(row.get("hostname", ""))
    return val.strip() not in ("", "nan", "None")


def put_findings(items: list, report: bool) -> tuple[int, int]:
    """
    PUT a list of {finding_id, vendor_id} dicts with given report value.
    Chunks into BATCH_SIZE, parallel PUTs, returns (success_count, fail_count).
    """
    if not items:
        return 0, 0

    chunks = [items[i:i+BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    success, failed = 0, 0

    def _put(chunk):
        payload = {
            "findings": [
                {
                    "finding_id": r["finding_id"],
                    "vendor_id":  r["vendor_id"],
                    "report":     report,
                    "triaged":    True,
                }
                for r in chunk
            ]
        }
        for attempt in range(1, 4):
            resp = requests.put(f"{BASE_URL}/max/partner/findings",
                                json=payload, headers=HEADERS_WRITE, timeout=30)
            if resp.status_code in (200, 204):
                return len(chunk), None
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            return 0, f"HTTP {resp.status_code}: {resp.text[:100]}"
        return 0, "Max retries exceeded"

    with ThreadPoolExecutor(max_workers=PUT_WORKERS) as pool:
        futures = {pool.submit(_put, c): c for c in chunks}
        for future in as_completed(futures):
            count, err = future.result()
            if err:
                failed += len(futures[future])
                print(f"    ✗ Batch error: {err}")
            else:
                success += count

    return success, failed


# ── Phase 1 — Hostname Domain Verification ───────────────────────────────────
print("\n" + "═"*55)
print("PHASE 1 — HOSTNAME DOMAIN VERIFICATION")
print("═"*55)

DATE_FROM = (datetime.today() - timedelta(days=DAYS_LOOKBACK)).strftime('%Y-%m-%d')
print(f"Fetching untriaged findings (last {DAYS_LOOKBACK} days)...")

df_all = fetch_all_findings({
    "filters": json.dumps({"first_seen": {"operator": "gte", "value": DATE_FROM}})
})

# Filter to rows with a hostname
if df_all.empty or "hostname" not in df_all.columns:
    df_with_host = pd.DataFrame()
else:
    df_with_host = df_all[df_all.apply(has_valid_hostname, axis=1)].copy()

print(f"  Total untriaged fetched:  {len(df_all):,}")
print(f"  Findings with hostname:   {len(df_with_host):,}")

p1_report_ok = p1_report_fail = p1_hide_ok = p1_hide_fail = 0

if not df_with_host.empty:
    df_with_host["_matches"] = df_with_host.apply(
        lambda r: hostname_belongs_to_vendor(str(r["hostname"]), str(r["vendor_domain"])),
        axis=1
    )

    df_match    = df_with_host[df_with_host["_matches"]].copy()
    df_mismatch = df_with_host[~df_with_host["_matches"]].copy()

    print(f"\n  Hostname MATCHES vendor:  {len(df_match):,}  → report=True")
    print(f"  Hostname MISMATCH:        {len(df_mismatch):,}  → report=False (hide)")

    if not df_match.empty:
        print(f"\n  Submitting REPORT batch...")
        p1_report_ok, p1_report_fail = put_findings(
            df_match[["finding_id", "vendor_id"]].to_dict("records"), report=True
        )
        print(f"    ✓ {p1_report_ok:,} marked report=True")

    if not df_mismatch.empty:
        print(f"\n  Submitting HIDE batch...")
        p1_hide_ok, p1_hide_fail = put_findings(
            df_mismatch[["finding_id", "vendor_id"]].to_dict("records"), report=False
        )
        print(f"    ✓ {p1_hide_ok:,} marked report=False")

    # Print detail tables
    print(f"\n  ── Match detail ──")
    for _, r in df_match.iterrows():
        print(f"    ✓  {str(r['vendor_name']):<35s}  {r['hostname']}")

    print(f"\n  ── Mismatch detail ──")
    for _, r in df_mismatch.iterrows():
        actual = extract_root_domain(str(r["hostname"]))
        print(f"    ✗  {str(r['vendor_name']):<35s}  {r['hostname']}  (actual: {actual})")
else:
    print("  No untriaged findings with a hostname found — skipping Phase 1.")


# ── Phase 2 — Bulk Clear Remaining ──────────────────────────────────────────
print("\n" + "═"*55)
print("PHASE 2 — BULK CLEAR REMAINING UNTRIAGED")
print("═"*55)

print("Fetching all remaining untriaged findings...")
df_remaining = fetch_all_findings()
print(f"  Remaining untriaged: {len(df_remaining):,}")

p2_ok = p2_fail = 0

if not df_remaining.empty:
    records = df_remaining[["finding_id", "vendor_id"]].to_dict("records")
    chunks  = [records[i:i+BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]
    print(f"  Batches of {BATCH_SIZE}: {len(chunks)}")
    print("  Submitting...")

    def _put_batch(chunk):
        payload = {
            "findings": [
                {"finding_id": r["finding_id"], "vendor_id": r["vendor_id"],
                 "report": True, "triaged": True}
                for r in chunk
            ]
        }
        for attempt in range(1, 4):
            resp = requests.put(f"{BASE_URL}/max/partner/findings",
                                json=payload, headers=HEADERS_WRITE, timeout=30)
            if resp.status_code in (200, 204):
                return len(chunk), None
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            return 0, f"HTTP {resp.status_code}: {resp.text[:100]}"
        return 0, "Max retries exceeded"

    with ThreadPoolExecutor(max_workers=PUT_WORKERS) as pool:
        futures = {pool.submit(_put_batch, c): c for c in chunks}
        completed = 0
        for future in as_completed(futures):
            count, err = future.result()
            completed += 1
            if err:
                p2_fail += len(futures[future])
                print(f"    ✗ {err}")
            else:
                p2_ok += count
            if completed % 50 == 0 or completed == len(chunks):
                print(f"    Progress: {completed}/{len(chunks)} batches | "
                      f"{p2_ok:,} triaged | {p2_fail} failed")
else:
    print("  No remaining untriaged findings — workstation already clean.")


# ── Final Summary ────────────────────────────────────────────────────────────
total_processed = p1_report_ok + p1_hide_ok + p2_ok
total_failed    = p1_report_fail + p1_hide_fail + p2_fail

print("\n" + "═"*55)
print("COMPLETE")
print("═"*55)
print(f"  Phase 1 — marked REPORT:  {p1_report_ok:,}")
print(f"  Phase 1 — marked HIDE:    {p1_hide_ok:,}")
print(f"  Phase 2 — bulk triaged:   {p2_ok:,}")
print(f"  ─────────────────────────────")
print(f"  Total processed:          {total_processed:,}")
if total_failed:
    print(f"  ✗ Total failed:           {total_failed:,}")
else:
    print(f"  ✓ Zero failures")
print("═"*55)
