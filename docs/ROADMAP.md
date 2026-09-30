# Roadmap

| Phase | Scope | New credentials |
|---|---|---|
| 0 | Scaffold: structure, schemas, privacy tooling, CI, docs, demo persona | none |
| 1 ✅ | Interview, voice profile, story bank; English rules; deterministic QA and duplicate checks; local pipeline up to a hash-bound human approval (`READY_TO_PUBLISH`); no publisher | none |
| 1.5 ✅ | Read-only Web Control Center (local real data, static fictional demo for GitHub Pages) | none |
| 2 ✅ | Automation & scheduling: timezone/DST-aware slots, one persisted job per slot, lock + leases, retries, reconciliation, dry run, deterministic steps up to human approval, agent handoff for LLM steps (`lce-run-jobs`), dashboard views. No trigger configured, no publishing | none |
| 2.x (not started) | Unattended trigger (Claude Code routine or a local scheduler calling `lce automation run-once`) and pull-request-based approval in the private repo | GitHub app install only |
| 3 | Publishing adapter(s), scheduler, reconciliation, token health checks | one provider credential, after explicit approval |
| 4 | German and Persian rulesets; additional approval channels; analytics | as required |
