# MAX-delivery-claude-skills

Claude skill repository for the SecurityScorecard MAX Managed Services delivery team.

Skills are markdown instruction files (`SKILL.md`) that Claude reads at runtime to perform structured, repeatable tasks — weekly report delivery, breach triage, account onboarding, ZDaaS research, and more.

---

## What's in This Repo

```
skills/
  account/        Per-account lifecycle — briefs, setup, onboarding
  delivery/       Weekly production chain — reports, decks, email staging
  triage/         MAX workstation ops — breach, hostname, findings triage
  intelligence/   Research and reactive — ZDaaS pipeline, threat feeds
  platform/       API foundations — SSC, Driftnet, MAX Partner API
  org/            Team-level tools — persona classification, career coach
  <new-category>/ Add new categories as needed — see GOVERNANCE.md
archive/          Deprecated skills — retained for reference, not for use
CHANGELOG.md      Running log of all skill changes
GOVERNANCE.md     Versioning rules, review cadence, ownership, deprecation process
```

---

## How to Use a Skill

Skills live in `/mnt/skills/user/` on the Claude runtime filesystem.
To use a skill from this repo in a Claude session:

1. Pull the latest version from GitHub
2. Copy the relevant `SKILL.md` to `/mnt/skills/user/<skill-name>/SKILL.md`
3. Claude will detect and use it automatically based on trigger phrases in the description

For bulk sync, use the session-init notebook in `notebooks/session-init/`.

---

## How to Update a Skill

1. Edit the `SKILL.md` in the appropriate category folder
2. Bump the `version` field in the frontmatter (e.g. `1.0` → `1.1` for fixes, `1.0` → `2.0` for major changes)
3. Update `last_updated` to today's date (YYYY-MM-DD)
4. Add a one-line entry to `CHANGELOG.md` under today's date
5. Commit with a descriptive message:
   ```
   max-breach-triage: fix beta PUT envelope header
   ```
6. Notify the team in `#ps-max-working-group` if the change affects active delivery workflows

---

## How to Add a New Skill

1. Determine the correct category — or create a new one (see above)
2. Create `skills/<category>/<skill-name>/SKILL.md`
3. Include all required frontmatter fields (see `GOVERNANCE.md`)
4. Add an entry to `CHANGELOG.md`
5. Update the Confluence Skill Repository index page

---

## How to Deprecate a Skill

1. Change `status: active` → `status: deprecated` in the skill frontmatter
2. Move the folder to `archive/<skill-name>/`
3. Add a `DEPRECATED.md` file explaining why it was deprecated and what replaced it
4. Add an entry to `CHANGELOG.md`
5. Remove from `/mnt/skills/user/` on active runtime environments

---

## How to Add a New Category

Categories are just folders under `skills/`. Add one any time a new cluster of skills doesn't fit existing categories:

1. Create `skills/<new-category>/`
2. Add a `README.md` describing what belongs there
3. Add the category to the table in this README and in `GOVERNANCE.md`
4. Add a `CHANGELOG.md` entry

---

## Skill Categories

| Category | Description |
|---|---|
| `account` | Per-account lifecycle — briefs, setup, onboarding |
| `delivery` | Weekly production — reports, decks, email staging, branding |
| `triage` | MAX workstation — breach, hostname, and findings triage |
| `intelligence` | Research and reactive — ZDaaS pipeline, threat feeds |
| `platform` | API foundations — SSC, Driftnet, MAX Partner, Vault |
| `org` | Team-level — persona classification, career coach, daily triage |
| _(add new categories here)_ | |

---

## Governance

See `GOVERNANCE.md` for full rules on versioning, ownership, review cadence, and deprecation.

---

## Team

| Role | Name |
|---|---|
| Repo owner | Ian Mains (`ian.mains@securityscorecard.io`) |
| FTE delivery team | Simon Jones, Daniel Cassidy, Julio Santiago, Otavio Artur, Dominika Gawel |
| Regional leads | Ronica (APAC), Joan (EMEA) |

Questions or contributions → `#ps-max-working-group`
