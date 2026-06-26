---
name: vroc-session-init
description: >
  Load VROC API keys from an uploaded ~/.vroc_keys file and initialize the session environment
  for SecurityScorecard, Driftnet, and Strike API access. Use this skill whenever the user
  uploads a file named .vroc_keys or vroc_keys, or says anything like "load my keys",
  "initialize my session", "set up my API keys", "load API keys", or "I've uploaded my keys".
  Also trigger when the user wants to call the SSC, Driftnet, or Strike APIs but no tokens are
  loaded yet in the session. This skill MUST run before any SSC, Driftnet, or Strike API calls
  are attempted in a new session. Always run it automatically if a .vroc_keys file is present
  in uploads and tokens are not yet in the environment.
---

# VROC Session Init

Loads API credentials from the user's uploaded `.vroc_keys` file into the session environment,
then validates each key with a live auth check.

## Expected File Format

The `.vroc_keys` file is a simple `KEY=value` file, one per line, no quotes required:

```
SSC_API_TOKEN=abc123...
DRIFTNET_API_TOKEN=def456...
STRIKE_API_TOKEN=ghi789...
```

Lines starting with `#` are treated as comments and ignored. Blank lines are ignored.

## Step 1 — Find the uploaded file

```python
import os

# Check all possible upload filenames (dot-prefixed, underscore-prefixed, plain)
candidates = [
    "/mnt/user-data/uploads/.vroc_keys",
    "/mnt/user-data/uploads/_vroc_keys",
    "/mnt/user-data/uploads/vroc_keys",
]
keyfile = next((p for p in candidates if os.path.exists(p)), None)

if not keyfile:
    print("ERROR: No .vroc_keys file found in uploads.")
    print("Please upload your ~/.vroc_keys file and try again.")
    exit(1)

print(f"Found key file: {keyfile}")
```

## Step 2 — Parse and export

```python
keys = {}
with open(keyfile) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip()

# Export to environment
for k, v in keys.items():
    os.environ[k] = v

print(f"Loaded {len(keys)} keys: {', '.join(keys.keys())}")
```

## Step 3 — Validate each key

Run a lightweight auth check for each key that was loaded. Only check services whose key is present.

**Important:** `bash_tool` environment variables do not persist between invocations. Always
re-read `keys` from the parsed dict (not `os.environ`) when the token is needed within the
same Python block. All validation must happen in a single `bash_tool` call alongside the
parse step above.

### SSC validation

Use `/portfolios` — not `/users/self` (which returns 404 for this account type).

```python
import requests

if "SSC_API_TOKEN" in keys:
    resp = requests.get(
        "https://api.securityscorecard.io/portfolios",
        headers={"Authorization": f"Token {keys['SSC_API_TOKEN']}"},
        timeout=10
    )
    if resp.status_code == 200:
        portfolios = resp.json().get("entries", [])
        count = len(portfolios)
        print(f"✓ SSC authenticated — {count} portfolio(s) visible")
    else:
        print(f"✗ SSC auth failed ({resp.status_code}) — check your token")
```

### Driftnet validation

```python
if "DRIFTNET_API_TOKEN" in keys:
    resp = requests.get(
        "https://api.driftnet.io/v1/admin/user",
        headers={"Authorization": f"Bearer {keys['DRIFTNET_API_TOKEN']}"},
        timeout=10
    )
    if resp.status_code == 200:
        data = resp.json()
        email = data.get("email", "unknown")
        usage = data.get("quota", {}).get("api_usage", "?")
        limit = data.get("quota", {}).get("api_limit", "?")
        print(f"✓ Driftnet authenticated as: {email} (quota: {usage}/{limit})")
    else:
        print(f"✗ Driftnet auth failed ({resp.status_code}) — check your token")
```

### Strike validation

```python
if "STRIKE_API_TOKEN" in keys:
    # No Strike validation endpoint configured yet — just confirm key is loaded
    print(f"✓ Strike key loaded (no validation endpoint configured yet)")
```

## Step 4 — Confirm to user

After running all checks, give the user a clean summary:

```
Session initialized ✓
─────────────────────────────
SSC         ✓  authenticated — 3 portfolio(s) visible
Driftnet    ✓  logged in as jeremy.turner@securityscorecard.io (quota: 1806162/10000000)
Strike      ✓  key loaded
─────────────────────────────
All VROC API skills are ready to use.
```

If any key failed validation, flag it clearly and suggest the user check that value in their `~/.vroc_keys` file.

## Notes

- Keys are loaded into the session environment only — they are never written to disk, logged, or included in any output or artifact.
- This skill must be re-run at the start of each new conversation. It only persists for the duration of the current session.
- `bash_tool` does NOT persist `os.environ` between invocations — always parse `keys` from the file fresh in any bash block that needs tokens.
- To update a key, edit `~/.vroc_keys` on your Mac, upload the new version, and say "reload my keys".
- To add a new key in the future, add a new `KEY=value` line to `~/.vroc_keys` and update this skill's validation block.
- Supported upload filenames: `.vroc_keys`, `_vroc_keys`, `vroc_keys` (macOS strips the dot on upload).
