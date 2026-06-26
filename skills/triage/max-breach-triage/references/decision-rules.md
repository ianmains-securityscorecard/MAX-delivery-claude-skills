# Breach Triage Decision Rules

Complete ruleset, rationale, and edge-case guidance for the `max-breach-triage` skill.
Derived from the LIFARS/MAXKH Confluence SOPs and the analyst session conducted May 2026.

---

## Source SOPs

| Document | Confluence Page ID | Space |
|---|---|---|
| SOP: Breach Review Guidance - MAX Workstation (Updated 8 May 2026) | 4970807304 | MAXKH |
| SOP: Daily Findings Review — Frequency & Decision Rationale (Updated 8 May 2026) | 4991779529 | MAXKH |

---

## Core SOP Guidance (verbatim intent)

**HIDE if any of the following are true:**
- **H1** — It is a clear product vulnerability (not a data breach)
- **H2** — It is re-reporting an old breach
- **H3** — It is not relevant to how clients are using the vendor (niche sub-product/use case)
- **H4** — It is reported as a breach but is just a news article mentioning a company

**REPORT if:**
- None of the hide criteria apply
- It is a genuine data breach affecting the vendor
- It is a confirmed breach within the lookback window

**LEAVE AS PENDING (FLAG) if:**
- No clear date can be identified after research
- There is nuance about the nature of the breach requiring internal discussion
- A data quality error makes the record unclassifiable

---

## Codified Rules (in precedence order)

### Rule 0: Specific Known Overrides
Analyst-confirmed manual overrides take precedence over all automated rules.
These must be hard-coded when confirmed, documented here with rationale.

**Active overrides as of May 2026:**

| Vendor | Domain | Published | Decision | Rationale |
|---|---|---|---|---|
| Taiwan President's Office | president.gov.tw | 2025-01-07 | HIDE | SSC feed misattributed a Philippine government breach (Ferdinand Marcos/OPS) to Taiwan's Presidential Office domain |
| Capital One | capitalone.com | 2024-02-17 | HIDE | 2024 notification of the 2019 settlement — re-report of old breach, not a new incident |
| Burr & Forman LLP | burr.com | 2024-01-16 | HIDE | Description references an "October 2024" breach on a January 2024 published date — impossible, data quality error in SSC feed |
| Coinbase | coinbase.com | 2024-05-26 | HIDE | Customer phishing fraud ($37M via impersonation site) — not a Coinbase systems breach (H3: not a breach of the vendor itself) |

> When new overrides are confirmed by an analyst, add them to this table and
> update the corresponding logic in SKILL.md Step 2.

---

### Rule 1: Age > 5 Years → HIDE (H2)
**Threshold:** `published_date < today - 5 years`
**Rationale:** Per analyst confirmation (May 2026). Breaches older than 5 years are
considered historical re-reports. The SSC feed sometimes surfaces very old breach records
(some going back to 2005) that were ingested recently but represent long-resolved incidents.
These should not be surfaced to customers via the MAX workstation.

**Note:** The SOP says "old breach" without defining a threshold. Five years was confirmed
by the vROC manager as the operational cutoff.

---

### Rule 2: Product Vulnerability Without Breach Evidence → HIDE (H1)
**Detection:** Description contains vulnerability/exploit keywords AND does NOT contain
confirmed-breach language.

**Vulnerability keywords:** `vulnerability`, `cve-`, `zero-day`, `zero day`, `exploit`,
`rce `, `remote code execution`, `sql injection`, `buffer overflow`, `patch`, `security advisory`

**Confirmed-breach override keywords (prevent H1 hide):**
`data breach`, `personal information`, `affected`, `individuals`, `notified`, `notification`,
`social security`, `financial account`, `medical record`, `unauthorized access`,
`compromised`, `exposed`, `stolen`, `leaked`

**⚠️ Critical lesson (Parexel, May 2026):** A zero-day or CVE can be the *attack vector*
of a genuine data breach. Parexel's 2025 breach was caused by a zero-day in Oracle
E-Business Suite but exposed 6,620 individuals' SSNs and financial data — a real breach.
The override keywords catch this correctly.

**False positive check:** If classification returns H1 HIDE for a recent vendor, verify the
description manually before executing. The override keyword list may need expansion.

---

### Rule 3: Unconfirmed/Alleged Claim — 90-Day Window (H4 variant)
**Detection:** Description contains `allegedly`, `alleged `, or `claims to have`
**Rationale:** Ransomware gang victim claims and unconfirmed reports ("allegedly hacked as
reported by Clop/LockBit/Conti ransomware") are not confirmed breaches.

**Decision:**
- If `published_date >= today - 90 days` → **REPORT** (recent enough to surface; confirmation may follow)
- If `published_date < today - 90 days` → **HIDE** (old unconfirmed claim, low residual value)

**Why 90 days?** Recent unconfirmed claims may still be investigated/confirmed and are
relevant to customers. Claims older than 90 days that were never confirmed are unlikely to
develop further and add noise to customer-facing reporting.

**Examples of "allegedly" pattern:**
- `"companyx.com allegedly hacked as reported by Clop ransomware"`
- `"company allegedly held for ransom as reported by Comparitech"`
- `"hacker claims hack of [vendor]"`

---

### Rule 4: Confirmed Breach Within 5-Year Window → REPORT
**Condition:** None of Rules 1–3 triggered, published within 5 years
**Rationale:** Any breach that is confirmed (no alleged keywords), recent enough (within 5yr),
and not a product-only vulnerability should be reported to customers.

**Note on 3–5 year range:** Confirmed breaches from 3–5 years ago are **REPORT** per
analyst decision (May 2026). The rationale is that these may still be relevant to
liability tracking, Likelihood Assessments, and customer awareness.

---

## FLAG Conditions (requires analyst before executing)

| Condition | Action |
|---|---|
| No `published_date` in record | Surface for analyst — cannot classify without date |
| Description is a bare URL only (e.g., AG filing link with no text) | Surface for analyst — no describable content to classify against |
| `is_active_breach == True` AND recommendation would be HIDE | Override to FLAG — active breaches should not be auto-hidden |
| Description < 20 characters | Surface for analyst — insufficient information |

---

## Downstream Impact Reminder

Per the Daily Findings Review SOP:

- **REPORT=True:** Finding visible to customer in MAX Dashboard, included in Likelihood
  Assessments, available for email notifications and API ingestion.
- **REPORT=False (HIDE):** Excluded from all reporting, suppressed from Likelihood
  Assessment score calculations. This action is **irreversible via normal workflow** —
  a hidden breach does not surface again unless re-triaged manually.

> Always surface the full list to the analyst before executing. The execute step
> is destructive and permanent from the customer's perspective.

---

## Slack Notification Requirement

Per the Breach Review SOP (Step 6):
> "Until this article is updated and reporting has been shifted to the MAX Workstation,
> if a breach has been reported to the MAX workstation but we choose to hide it, please
> post in `#max_daily_breach_details` Slack channel listing out the misattributed breach
> and the MAX clients who have that vendor in their list."

When this skill hides any breach, the analyst should post in `#max_daily_breach_details`
for any breach that was HIDE for reasons other than pure age (H2). Specifically:
- H1 hides (product vulnerability misclassified as breach)
- H4 hides (news article / unconfirmed claim)
- Manual override hides (misattribution, data quality errors)

Age-only hides (Rule 1: >5yr) do not require Slack notification.

---

## Change Log

| Date | Change | Authorized by |
|---|---|---|
| May 2026 | Initial rules codified from SOP + analyst session | Ian Mains (vROC Manager) |
| May 2026 | Age threshold confirmed: 5 years | Ian Mains |
| May 2026 | Unconfirmed claims: 90-day window rule | Ian Mains |
| May 2026 | Q8: Confirmed breaches 3–5yr = REPORT | Ian Mains |
| May 2026 | Parexel H1 false-positive lesson documented | Ian Mains |
| May 2026 | Four specific manual overrides added | Ian Mains |
