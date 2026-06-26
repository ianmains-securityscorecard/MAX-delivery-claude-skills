# Governance

Rules for maintaining the `MAX-delivery-claude-skills` repository.

---

## Frontmatter Requirements

Every `SKILL.md` must include these fields in its frontmatter:

```yaml
---
name: skill-name
description: >
  One paragraph. Must be ≤1024 characters. Include trigger phrases
  and explicit DO NOT USE cases. This is what Claude reads to decide
  whether to invoke the skill.
version: 1.0
last_updated: YYYY-MM-DD
owner: email@securityscorecard.io
status: active         # active | draft | deprecated
category: delivery     # account | delivery | triage | intelligence | platform | org
---
```

**All fields are required.** PRs with missing frontmatter fields will not be merged.

---

## Versioning

Follow semantic versioning lite:

| Change type | Version bump | Example |
|---|---|---|
| Bug fix, wording tweak, minor clarification | Patch: x.x → x.x+1 | 1.1 → 1.2 |
| New section, new step, behavioral change | Minor: x.0 → x+1.0 | 1.2 → 2.0 |
| Full rewrite or major workflow change | Major: bump to next integer | 2.0 → 3.0 |

When in doubt, bump minor. Never leave version unchanged when editing content.

---

## Category Definitions

| Category | What belongs here |
|---|---|
| `account` | Skills that run once per account to establish or refresh context. Lifecycle skills — brief, setup, onboarding. |
| `delivery` | Skills that run on the weekly production cadence. Report generation, deck editing, email staging, branding. |
| `triage` | Skills that operate on the MAX workstation queue. Breach, hostname, and findings triage. |
| `intelligence` | Skills for research and reactive workflows. ZDaaS pipeline (phases 1–3), threat feeds, breach analysis. |
| `platform` | Skills that wrap API calls. SSC, Driftnet, MAX Partner, Vault. These are building blocks, not workflows. |
| `org` | Skills that operate at team level rather than account level. Persona classification, career coaching, daily triage orchestration. |

If a skill spans two categories, assign the one that matches its primary trigger context.

---

## Ownership

- Every skill has a named owner in the `owner` frontmatter field.
- The owner is responsible for keeping the skill current when platform or workflow changes occur.
- When an owner leaves the team or changes roles, skills must be reassigned before their last day.
- The repo owner (Ian Mains) is the default owner for any skill without a named owner.

**Current owners:**

| Owner | Skills |
|---|---|
| ian.mains@securityscorecard.io | All skills as of 2026-06-25 |

---

## Review Cadence

**Monthly** — first week of each month:

1. Scan `#max-dev-internal` for platform changes since last review
2. Identify any skills affected by API or workflow changes
3. Update affected skills, bump versions, update `CHANGELOG.md`
4. Post a one-line summary to `#ps-max-working-group`

**Trigger-based** — review immediately when:
- MAX API schema changes (breach schema, findings schema, partner API)
- A skill produces incorrect output in production
- A workflow it supports is deprecated or replaced (e.g. Autonomous replacing a Colab script)
- A new contractor is onboarded and runs a skill for the first time

---

## Deprecation Process

1. Change `status: active` → `status: deprecated` in frontmatter
2. Move skill folder from `skills/<category>/` to `archive/`
3. Create `archive/<skill-name>/DEPRECATED.md`:
   ```markdown
   # Deprecated: <skill-name>

   **Date deprecated:** YYYY-MM-DD
   **Deprecated by:** name
   **Reason:** one sentence explaining why
   **Replaced by:** <replacement-skill-name> or "nothing — workflow no longer needed"
   ```
4. Add entry to `CHANGELOG.md`
5. Remove from `/mnt/skills/user/` on all active runtime environments
6. Post notice to `#ps-max-working-group`

Archived skills are never deleted. They serve as reference for rebuilding or auditing.

---

## Branch and Commit Standards

**Branches:**
- `main` — production. All active skills here are considered current.
- Feature branches for any non-trivial change: `skill/<skill-name>-<what-changed>`
- Direct commits to `main` are acceptable for single-skill patch fixes.

**Commit messages:**
```
<skill-name>: <what changed>

Examples:
  max-breach-triage: fix beta PUT envelope header
  account-brief: add Slack channel check step
  zdaas-report: update for new DOCX template format
  GOVERNANCE: add branch standards section
```

---

## Adding a New Category

Categories are open-ended — add one any time a new cluster of skills doesn't fit existing categories.

1. Create `skills/<new-category>/` with a `README.md` describing what belongs there
2. Add the new category to the table in `README.md` and to the Category Definitions table above
3. Add `category: <new-category>` as a valid frontmatter value going forward
4. Add a `CHANGELOG.md` entry: `GOVERNANCE: added <new-category> category`

No approval process required — use judgment and document the decision in the changelog.

---

## Confluence Integration

The Confluence `MAX Managed Services / Skill Repository` page links to this repo.
It is not a mirror — Confluence contains links and summaries only, never skill content.

When adding a new skill or making a major change, update the Confluence index page to reflect it.
Confluence page: [MAX Managed Services / Skill Repository] — link TBD once Confluence space is set up.

---

## Questions

Raise in `#ps-max-working-group` or open a GitHub issue.
