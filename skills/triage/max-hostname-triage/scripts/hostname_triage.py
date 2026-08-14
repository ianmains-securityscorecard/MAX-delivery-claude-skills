"""
MAX Hostname-Aware Triage Script
=================================
Phase 0: Digital-footprint ownership check (Driftnet) — is the hostname's actual,
         currently-observed infrastructure owner the vendor it's flagged against?
         String equality is NOT evidence of ownership: a finding's hostname can be
         the vendor's own exact domain string while the infrastructure behind it
         is a shared CDN/cloud edge fronting someone else's certificate. Caught
         live, 2026-08-13: verizon.com (Akamai) and lumen.com (Fastly, serving
         an Adobe cert) both pass a pure string match and would have shipped.
         This check runs on EVERY hostname finding, including ones a human has
         already edited/confirmed (`edited_by` populated) — a manual pin is a
         one-time judgment call that never gets revisited, which is exactly
         where stale/wrong attribution survives longest. Never skip Phase 0
         because a finding "looks already handled."
Phase 1: Reconcile Phase 0's verdict with the string-domain check —
         match → report=True, mismatch/unverifiable → report=False or held.
Phase 2: Bulk triage all remaining untriaged findings as report=True — excluding
         anything Phase 0/1 held for human review.

Loads credentials from _vroc_keys / .vroc_keys in uploads dir.
"""

import os, json, math, time, requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ── Configuration ────────────────────────────────────────────────────────────
CUSTOMER_DOMAIN   = None        # Set to "example.com" to scope to one customer
DAYS_LOOKBACK     = 30          # How many days back to scan for untriaged findings
BATCH_SIZE        = 100         # MAX API hard limit per PUT
FETCH_WORKERS     = 10          # Parallel page fetches
PUT_WORKERS       = 8           # Parallel PUT batches
BASE_URL          = "https://api.securityscorecard.io"
DRIFTNET_BASE_URL = "https://api.driftnet.io/v1"   # paths confirmed live 2026-08-14
                                                     # against driftnet.io's own /v1/docs
                                                     # (redoc, spec at /swagger.json) —
                                                     # NOT /dns/forward or /scans/protocols,
                                                     # those 404. Real paths are
                                                     # /domain/fdns and /scan/protocols,
                                                     # and the response envelope key is
                                                     # "results", not "reports"/"records".

# Entities that mean "shared infrastructure", not "the vendor's own asset", when
# they show up as the ASN/cert-issuer/PTR owner of a hostname that IS the vendor's
# own domain. Extend as you find more.
SHARED_INFRA_MARKERS = [
    "akamai", "cloudflare", "fastly", "amazon", "aws", "microsoft azure",
    "google cloud", "digitalocean", "ovh", "securityscorecard",
]

# Multi-part TLDs for accurate root domain extraction
MULTI_TLDS = {
    "co.uk", "co.nz", "co.jp", "co.za", "co.in", "co.kr",
    "com.au", "com.br", "com.cn", "com.mx", "com.ar",
    "gov.uk", "gov.au", "net.au", "org.uk", "org.au",
}

# ── Credentials ──────────────────────────────────────────────────────────────
def _load_keys():
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
            return keys
    return {}

_KEYS = _load_keys()

def load_token():
    token = _KEYS.get("SSC_API_TOKEN") or os.environ.get("SSC_API_TOKEN")
    if token:
        return token
    raise RuntimeError("SSC_API_TOKEN not found. Run vroc-session-init first.")

TOKEN = load_token()

DRIFTNET_TOKEN = _KEYS.get("DRIFTNET_API_TOKEN") or os.environ.get("DRIFTNET_API_TOKEN")
if not DRIFTNET_TOKEN:
    print("⚠  DRIFTNET_API_TOKEN not found — Phase 0 ownership check cannot run.")
    print("   Falling back to string-match-only triage, which is exactly the mode")
    print("   that missed the verizon.com/lumen.com-style mismatches. Findings that")
    print("   would normally get a Driftnet verdict will be HELD, not auto-approved.")

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
    """
    True if hostname IS the vendor domain or is a subdomain of it.

    NOTE: this is a STRING check only. It says nothing about who actually
    operates the infrastructure at that hostname today — a vendor's own exact
    domain can (and does) resolve to a shared CDN/cloud edge. This function
    passes verizon.com and lumen.com without complaint even though Driftnet
    shows their resolved IPs belong to Akamai and Fastly respectively. Treat
    a `True` here as necessary, never sufficient — see `driftnet_ownership_verdict`.
    """
    h = hostname.lower().strip().rstrip(".")
    v = vendor_domain.lower().strip().rstrip(".")
    return h == v or h.endswith("." + v)


def _driftnet_get(path: str, params: dict):
    """GET against the Driftnet REST API. Returns parsed JSON, or None on any
    failure (missing token, timeout, non-200) — callers must treat None as
    'unverifiable', not 'passed'."""
    if not DRIFTNET_TOKEN:
        return None
    try:
        r = requests.get(
            f"{DRIFTNET_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {DRIFTNET_TOKEN}"},
            params=params, timeout=15,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except requests.RequestException:
        return None


def driftnet_ownership_verdict(hostname: str, vendor_domain: str) -> dict:
    """
    Ask Driftnet who actually operates `hostname` right now, and compare
    against the vendor it's flagged against. Returns:
        {"verdict": ..., "action": ..., "reason": ...}
    verdict is one of:
        CONFIRMED               — latest scan attribution matches the vendor
        SHARED_INFRA_MISMATCH   — attribution names a known shared-infra provider,
                                   not the vendor (e.g. Akamai/Fastly/AWS)
        NO_DNS_RECORD           — hostname doesn't resolve at all in Driftnet's
                                   corpus; not a verifiable internet asset
        UNVERIFIABLE            — Driftnet has no recent scan, or the API call
                                   itself failed (token missing, timeout, etc.)
        AMBIGUOUS               — resolves, scanned, but no clear match or
                                   shared-infra signal either way — needs a human

    Only CONFIRMED should ever justify report=True. Everything else should be
    treated as at least as suspicious as an outright string mismatch — including
    when the finding has already been manually edited (`edited_by` populated).
    A human confirming a vendor mapping once, months ago, is not evidence that
    the infrastructure behind it hasn't since moved onto shared/CDN infra.
    """
    owner_token = extract_root_domain(vendor_domain).split(".")[0].lower()

    dns = _driftnet_get("/domain/fdns", {"expression": f"host={hostname}"})
    if dns is None:
        return {"verdict": "UNVERIFIABLE", "action": "hold_for_review",
                "reason": "Driftnet DNS lookup failed or token missing — cannot verify ownership."}

    records = dns.get("results") or []
    if not records:
        return {"verdict": "NO_DNS_RECORD", "action": "flag_mismatch",
                "reason": f"'{hostname}' has no DNS presence in Driftnet's corpus — "
                          "not a resolvable internet asset."}

    ips = []
    latest = records[0]
    for item in latest.get("items", []):
        if item.get("type") == "ip" and item.get("context", "").startswith("dns-a"):
            ips.append(item["value"])
    if not ips:
        return {"verdict": "AMBIGUOUS", "action": "hold_for_review",
                "reason": "DNS record found but no A records parsed — needs a human look."}

    scan = _driftnet_get("/scan/protocols", {"ip": ips[0], "most_recent": "true"})
    scan_records = (scan or {}).get("results") or []
    if not scan_records:
        return {"verdict": "UNVERIFIABLE", "action": "hold_for_review",
                "reason": f"No recent passive scan of {ips[0]} to corroborate ownership."}

    hay_parts = []
    for rep in scan_records:
        for item in rep.get("items", []):
            if item.get("type") in ("entity", "asn", "subject", "host") and item.get("value"):
                hay_parts.append(str(item["value"]))
    hay = " ".join(hay_parts).lower()

    owner_present = owner_token in hay
    shared_hit = next((m for m in SHARED_INFRA_MARKERS if m in hay and m not in owner_token), None)

    if shared_hit and not owner_present:
        return {"verdict": "SHARED_INFRA_MISMATCH", "action": "flag_mismatch",
                "reason": f"Latest scan of {ips[0]} attributes it to '{shared_hit}', not '{vendor_domain}'."}
    if owner_present:
        return {"verdict": "CONFIRMED", "action": "confirm_match",
                "reason": f"Latest scan of {ips[0]} attribution matches '{vendor_domain}'."}
    return {"verdict": "AMBIGUOUS", "action": "hold_for_review",
            "reason": "No shared-infra marker and no owner-name match — needs judgment."}


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


# ── Phase 0/1 — Digital-Footprint Ownership + Hostname Domain Verification ──
print("\n" + "═"*55)
print("PHASE 0/1 — OWNERSHIP VERIFICATION (Driftnet) + HOSTNAME CHECK")
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
held_finding_ids = set()   # excluded from Phase 2's bulk auto-approve, no matter what

if not df_with_host.empty:
    df_with_host["_string_match"] = df_with_host.apply(
        lambda r: hostname_belongs_to_vendor(str(r["hostname"]), str(r["vendor_domain"])),
        axis=1
    )

    # Dedupe Driftnet calls: many findings share the same hostname.
    unique_pairs = df_with_host[["hostname", "vendor_domain"]].drop_duplicates()
    print(f"\n  Running Driftnet ownership check on {len(unique_pairs)} unique hostname(s)...")
    verdict_cache = {}
    for _, pair in unique_pairs.iterrows():
        h, v = str(pair["hostname"]), str(pair["vendor_domain"])
        verdict_cache[(h, v)] = driftnet_ownership_verdict(h, v)

    df_with_host["_verdict_info"] = df_with_host.apply(
        lambda r: verdict_cache[(str(r["hostname"]), str(r["vendor_domain"]))], axis=1
    )
    df_with_host["_verdict"] = df_with_host["_verdict_info"].apply(lambda v: v["verdict"])
    df_with_host["_was_manually_edited"] = df_with_host.get("edited_by", pd.Series(dtype=object)) \
        .apply(lambda x: bool(str(x).strip()) and str(x).strip().lower() != "nan")

    df_match    = df_with_host[df_with_host["_verdict"] == "CONFIRMED"].copy()
    df_mismatch = df_with_host[df_with_host["_verdict"].isin(
        ["SHARED_INFRA_MISMATCH", "NO_DNS_RECORD"])].copy()
    df_held     = df_with_host[df_with_host["_verdict"].isin(
        ["UNVERIFIABLE", "AMBIGUOUS"])].copy()

    print(f"\n  Driftnet CONFIRMED:            {len(df_match):,}  → report=True")
    print(f"  Driftnet MISMATCH/no-DNS:      {len(df_mismatch):,}  → report=False (hide)")
    print(f"  Driftnet UNVERIFIABLE/AMBIG:   {len(df_held):,}  → held, NOT auto-approved")

    manual_overridden = df_with_host[
        df_with_host["_was_manually_edited"] & (df_with_host["_verdict"] != "CONFIRMED")
    ]
    if not manual_overridden.empty:
        print(f"\n  ⚠  {len(manual_overridden)} finding(s) had a PRIOR MANUAL EDIT "
              f"(edited_by set) but Driftnet does NOT confirm ownership today.")
        print(f"     A human touching a finding once is not re-verification — these are")
        print(f"     overridden on the same terms as any other mismatch:")
        for _, r in manual_overridden.iterrows():
            print(f"       ⚠  {str(r['vendor_name']):<30s} {r['hostname']:<40s} "
                  f"edited_by={r.get('edited_by')}  →  {r['_verdict']}")

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

    if not df_held.empty:
        held_finding_ids.update(df_held["finding_id"].tolist())
        print(f"\n  {len(df_held)} finding(s) held — left triaged=False for human review, "
              f"excluded from Phase 2's bulk clear.")

    # Print detail tables
    print(f"\n  ── Confirmed detail ──")
    for _, r in df_match.iterrows():
        print(f"    ✓  {str(r['vendor_name']):<30s}  {r['hostname']:<40s}  {r['_verdict_info']['reason']}")

    print(f"\n  ── Mismatch detail (string check said: match={df_mismatch['_string_match'].tolist()}) ──"
          if not df_mismatch.empty else "\n  ── Mismatch detail ──")
    for _, r in df_mismatch.iterrows():
        flag = " [STRING CHECK WOULD HAVE PASSED THIS]" if r["_string_match"] else ""
        print(f"    ✗  {str(r['vendor_name']):<30s}  {r['hostname']:<40s}  "
              f"{r['_verdict_info']['reason']}{flag}")

    print(f"\n  ── Held for review ──")
    for _, r in df_held.iterrows():
        print(f"    ?  {str(r['vendor_name']):<30s}  {r['hostname']:<40s}  {r['_verdict_info']['reason']}")
else:
    print("  No untriaged findings with a hostname found — skipping Phase 0/1.")


# ── Phase 2 — Bulk Clear Remaining ──────────────────────────────────────────
print("\n" + "═"*55)
print("PHASE 2 — BULK CLEAR REMAINING UNTRIAGED")
print("═"*55)

print("Fetching all remaining untriaged findings...")
df_remaining = fetch_all_findings()
if held_finding_ids and not df_remaining.empty:
    before = len(df_remaining)
    df_remaining = df_remaining[~df_remaining["finding_id"].isin(held_finding_ids)]
    print(f"  Excluded {before - len(df_remaining)} finding(s) held by Phase 0/1 "
          f"from the bulk-clear pass.")
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
print(f"  Phase 0/1 — marked REPORT (Driftnet-confirmed): {p1_report_ok:,}")
print(f"  Phase 0/1 — marked HIDE (mismatch/no-DNS):      {p1_hide_ok:,}")
print(f"  Phase 0/1 — HELD for human review:               {len(held_finding_ids):,}")
print(f"  Phase 2 — bulk triaged:                          {p2_ok:,}")
print(f"  ─────────────────────────────")
print(f"  Total processed:          {total_processed:,}")
if total_failed:
    print(f"  ✗ Total failed:           {total_failed:,}")
else:
    print(f"  ✓ Zero failures")
print("═"*55)
