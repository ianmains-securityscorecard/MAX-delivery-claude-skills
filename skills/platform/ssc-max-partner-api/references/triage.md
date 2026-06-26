# MAX API — Triage Write Operations

Triage marks findings and breaches as `triaged=True` and `report=True` via a single
batched PUT request. The MAX API accepts all items in one call — not one-per-row.

---

## batch_triage() Function

```python
def batch_triage(
    endpoint: str,   # '/max/partner/findings' or '/max/partner/breaches'
    item_key: str,   # 'findings' or 'breaches'
    id_field: str,   # 'finding_id' or 'breach_id'
    rows: pd.DataFrame,
    headers: dict,   # MUST be HEADERS_BETA_WRITE (includes content-type)
) -> None:
    """
    Batch triage all rows in a single PUT request.
    Both vendor_id and the item ID are required per item.
    """
    if rows.empty:
        print(f"  → No untriaged {item_key} to process.")
        return

    payload = {
        item_key: [
            {
                id_field:    r[id_field],
                "vendor_id": r["vendor_id"],
                "report":    True,
                "triaged":   True,
            }
            for r in rows[["vendor_id", id_field]].to_dict("records")
        ]
    }

    resp = requests.put(f"{BASE_URL}{endpoint}", json=payload, headers=headers)

    if resp.status_code == 200:
        print(f"  ✓ Triaged {len(payload[item_key])} {item_key} in 1 request.")
    else:
        print(f"  ✗ Triage PUT failed – HTTP {resp.status_code}: {resp.text[:200]}")
```

---

## Usage — Findings

```python
# Find untriaged rows (boolean column — use .ne(True) to handle None/NaN safely)
untriaged = df_findings[df_findings["triaged"].ne(True)].copy()
print(f"→ {len(untriaged)} untriaged finding(s)")

batch_triage(
    endpoint = "/max/partner/findings",
    item_key = "findings",
    id_field = "finding_id",
    rows     = untriaged,
    headers  = HEADERS_BETA_WRITE,
)
```

## Usage — Breaches

```python
untriaged = df_breaches[df_breaches["triaged"].ne(True)].copy()
print(f"→ {len(untriaged)} untriaged breach(es)")

batch_triage(
    endpoint = "/max/partner/breaches",
    item_key = "breaches",
    id_field = "breach_id",
    rows     = untriaged,
    headers  = HEADERS_BETA_WRITE,
)
```

---

## Required Headers for PUT

```python
HEADERS_BETA_WRITE = {
    "accept":        "application/json",
    "Authorization": f"Token {TOKEN}",
    "version":       "beta",
    "content-type":  "application/json",   # ← required for PUT; omitting = 400
}
```

---

## Payload Structure Reference

### Findings PUT body
```json
{
  "findings": [
    {
      "finding_id": "abc123",
      "vendor_id":  "vendor-uuid",
      "report":     true,
      "triaged":    true
    },
    ...
  ]
}
```

### Breaches PUT body
```json
{
  "breaches": [
    {
      "breach_id": "def456",
      "vendor_id": "vendor-uuid",
      "report":    true,
      "triaged":   true
    },
    ...
  ]
}
```

---

## Retry Pattern for Rate Limits

```python
import time

def triage_with_retry(endpoint, payload, headers, max_attempts=3):
    for attempt in range(1, max_attempts + 1):
        resp = requests.put(f"{BASE_URL}{endpoint}", json=payload, headers=headers)
        if resp.status_code == 200:
            return resp
        if resp.status_code == 429:
            sleep_time = 2 ** attempt
            print(f"  ⚠ 429 rate limit — sleeping {sleep_time}s (attempt {attempt})")
            time.sleep(sleep_time)
        else:
            print(f"  ✗ PUT failed ({resp.status_code}): {resp.text[:200]}")
            return resp
    raise RuntimeError(f"PUT failed after {max_attempts} attempts")
```
