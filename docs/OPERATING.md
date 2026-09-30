# Operating procedure

The owner's routine, end to end. Private data lives in the private data
directory (`export LCE_DATA_DIR=<path>`); nothing below writes to this
repository. `lce readiness` shows which steps are available in your setup.

## Weekly (or per slot)

1. **Prepare content** (Claude Code, in the private data directory):
   `lce automation run-once`, then the `lce-run-jobs` skill. For each open slot
   `lce jobs brief` gives the brand strategy (pillar, theme, evidence mode,
   candidates, saturated topics). The agent researches, selects, writes,
   humanizes and decides the image (`lce image decide` / `lce image chart`).
   Experience-based themes without a PUBLIC story stay `NEEDS_INPUT`; the
   agent never invents a story.
2. **Review and approve** (you, in your own terminal): read
   `posts/<id>/APPROVAL.md` (text, image, sources, QA, duplicate check), then
   `lce approve <id> --hash <prefix>` and type `APPROVE <id>`, then `lce ready <id>`.
3. **Schedule**
   - Cloud (device-independent): `lce cloud push <id>` (type `DELEGATE <id>`),
     then `lce cloud consent <id>` (defaults to the post's scheduled slot; type
     `SCHEDULE <id>`), or schedule it in the Cloud Control Center. Auto-publish
     must be on (dashboard, phrase `ENABLE AUTO-PUBLISH`).
   - Local: `lce publish <id>` (type `PUBLISH <id>`) at the time you choose.
   - By hand: post the approved text and image yourself, then
     `lce publish manual <id> --published-url <URL>` (type `PUBLISHED <id>`).
4. **Verify**
   - Cloud: `lce cloud pull` mirrors outcomes and the publication record.
   - An unclear outcome is `NEEDS_RECONCILE`: check LinkedIn, then reconcile in
     the dashboard or with `lce publish reconcile <id> --published-url <URL>` /
     `--not-published`. Nothing is retried automatically.
5. **Record results** a few days after publishing: `lce analytics record <id>
   --impressions … --reactions … --comments … --reposts …`, or
   `lce analytics import <csv>`.

## Monthly

- `lce analytics insights` and `lce brand status`: what works, saturated topics,
  pillar balance.
- `lce analytics suggest-mix`: apply a suggestion only if you agree
  (`lce interview set brand_mix --value '…'`).
- Add PUBLIC stories to the story bank when you have real experiences to
  share; they unlock the experience-based themes.
- `lce privacy-denylist` after changing private data.
- LinkedIn tokens expire after 60 days: the Cloud Control Center and
  `lce linkedin status` show the remaining days.

## Never

- approve from a script or an agent (approval is terminal-only);
- paste a token into a file, chat, issue or the dashboard (Keychain or Worker
  secret only);
- publish an unapproved or edited text or image (every publisher re-checks the
  approved hashes).
