# Roadmap

| Phase | Scope | New credentials |
|---|---|---|
| 0 | Scaffold: structure, schemas, privacy tooling, CI, docs, demo persona | none |
| 1 ✅ | Interview, voice profile, story bank; English rules; deterministic QA and duplicate checks; local pipeline up to a hash-bound human approval (`READY_TO_PUBLISH`); no publisher | none |
| 1.5 ✅ | Read-only Web Control Center (local real data, static fictional demo for GitHub Pages) | none |
| 2 ✅ | Automation & scheduling: timezone/DST-aware slots, one persisted job per slot, lock + leases, retries, reconciliation, dry run, deterministic steps up to human approval, agent handoff for LLM steps (`lce-run-jobs`), dashboard views. No trigger configured, no publishing | none |
| 2.x (not started) | Unattended trigger (Claude Code routine or a local scheduler calling `lce automation run-once`) and pull-request-based approval in the private repo | GitHub app install only |
| 3 ✅ (untested against LinkedIn) | Human-triggered publishing via the official LinkedIn Posts API: adapter, little escaping, Keychain token, intent record, deterministic outcome handling, manual reconciliation, dashboard. First live test pending your setup | LinkedIn access token in the macOS Keychain (created by you) |
| 4A ✅ | Cloud architecture (Cloudflare Worker + D1 + Cron + Access) | none |
| 4B ✅ (not deployed) | Cloud runtime: cron publisher with gates, atomic claim, kill switch, consent API, parity with the Python publisher, `lce cloud` CLI, one publisher per post | none created; LinkedIn token (Worker secret) and deploy token (GitHub secret) set by the owner later |
| 4C | Remote dashboard on the Worker (behind Access) | — |
| 4D | Controlled live publishing test | owner's LinkedIn token |
| 5 | German and Persian rulesets; additional approval channels; analytics | as required |
