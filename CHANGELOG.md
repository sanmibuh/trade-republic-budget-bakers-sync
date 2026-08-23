# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [7.4.0] - 2026-08-23

### What's Changed
* Refactor: _escape_markdown is a private cross-module import — promote to public API — [#196](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/196)
* 🔧 Fix nightly AI review: update Gemini models and sync workflow with tedee-auto — [#194](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/194)
* Correctness: filter_by_lookback silently passes events with unparseable timestamps — [#192](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/192)
* Perf: /status opens two DB connections per instance — merge into one — [#191](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/191)
* Flatten instance directories: name session files at data root, remove per-instance subdirs — [#190](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/190)
* DRY: extract shared row-builder in EventRepository to remove mark_processed duplication — [#189](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/189)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v7.3.0...v7.4.0


## [7.3.0] - 2026-08-22

### What's Changed
* Unify databases: single shared sync.db instead of one per instance — [#185](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/185)
* Reorder bot commands: sync, status, logs, backup, resync — [#184](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/184)
* Remove /login command — sync already triggers re-authentication automatically — [#183](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/183)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v7.2.0...v7.3.0


## [7.2.0] - 2026-08-22

### What's Changed
* Remove legacy single-container mode and hardcode instances config path — [#168](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/168)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v7.1.1...v7.2.0


## [7.1.1] - 2026-08-21

### What's Changed
<!-- add release notes here -->
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v7.1.0...v7.1.1


## [7.1.0] - 2026-08-21

### What's Changed
<!-- add release notes here -->
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v7.0.0...v7.1.0


## [7.0.0] - 2026-08-20

### What's Changed
* Phase 4: Update docker-compose.yml and deploy docs for single container — [#157](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/157)
* Phase 3.5: Centralise logging to a shared /data/logs directory — [#156](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/156)
* Phase 3: Remove Docker SDK from bot — direct in-process calls — [#154](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/154)
* Phase 2: Single-container cron for N sync instances + backup — [#153](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/153)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.7.0...v7.0.0


## [6.7.0] - 2026-08-17

### What's Changed
* Plain-digit 2FA reply not recognized when login is triggered by sync (not by /login command) — [#144](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/144)
* 🧪 Nightly: improve test coverage (2026-08-16) — [#142](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/142)
* fix: bump-version workflow triggers incorrectly on requirements-dev.txt changes — [#141](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/141)
* Handle TRADING_SAVINGSPLAN_EXECUTION_PENDING event type — [#138](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/138)
* deps: bump ruff from 0.16.2 to 0.16.3 in the pip-dependencies group — [#135](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/135)
* nightly: run daily at 4am and add AI-powered suggestion job — [#133](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/133)
* refactor: break up run() orchestration function in main.py — [#131](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/131)
* refactor: split bot.py god class into focused collaborators — [#130](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/130)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.6.0...v6.7.0


## [6.6.0] - 2026-08-15

### What's Changed
* Auto-categorize wallet records before sync using history-based matching — [#123](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/123)
* CI Results comment: delete and recreate instead of edit so it stays at the bottom — [#122](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/122)
* Persist last sync result to DB instead of iterating logs — [#121](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/121)
* Refactor excluded count handoff in the orchestrator — [#120](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/120)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.5.0...v6.6.0


## [6.5.0] - 2026-08-15

### What's Changed
* ✂️ Nightly: refactor 39 long functions (2026-08-14) — [#117](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/117)
* Persist auth/login failure state to DB so /status reports it correctly — [#116](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/116)
* Dismiss pending 2FA code prompt when the timeout expires — [#114](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/114)
* Bug: plain-digit 2FA reply is silently ignored when login was triggered by cron sync — [#113](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/113)
* Force re-sync of a specific day (bypass dedup, upsert) — [#106](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/106)
* Extract SyncRunner class from main.py to avoid testing private functions directly — [#104](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/104)
* Encapsulate SSL circuit-breaker state in a class instead of module-level globals — [#103](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/103)
* Add read API to EventRepository to avoid ._conn access in tests — [#102](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/102)
* Test config helpers through public Config API instead of importing private functions — [#101](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/101)
* Remove dead label_ids parameter from _make_record in tr_mapper.py — [#100](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/100)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.4.0...v6.5.0


## [6.4.0] - 2026-08-13

### What's Changed
* ci: add explicit permissions to lint job to resolve CodeQL alert — [#92](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/92)
* fix: auth status ⚠️, auto-sync after login, easier 2FA code entry — [#91](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/91)
* fix: CHANGELOG 6.3.0 format inconsistent and prepare-release workflow generates wrong format — [#89](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/89)
* chore: add ruff config and enforce linting in CI — [#87](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/87)
* 🧹 Auto-fix: lint & formatting — [#82](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/82)
**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.3.0...v6.4.0


## [6.3.0] - 2026-08-12

### What's Changed
* fix: YAML syntax error in prepare-release.yml (line 62) — [#80](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/80)
* Store Wallet record ID in dedup DB to enable insert/update on reprocessing — [#78](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/78)
* Extend `/status` with container runtime state and last-sync summary — [#76](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/76)
* feat: maintain CHANGELOG.md — bootstrap from existing releases and auto-update on prepare-release — [#75](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/75)
* fix: CI Results comment duplicated when concurrent runs post to the same PR — [#73](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/73)
* Nightly — [#71](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/71)
* [improve-ci-report]: feat: enhance CI report by adding comment deleti… — [#59](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/59)
* [atomic-write-backup]: feat: implement atomic write for JSON backups … — [#58](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/58)
* Login status bot — [#57](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/57)
* [bot-logs]: feat: add /logs command to fetch and send today's logs fo… — [#56](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/56)
* [remove-2fa-code-telegram]: feat: remove sensitive 2FA code messages … — [#55](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/55)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.2.1...v6.3.0

## [6.2.1] - 2026-08-10

### What's Changed
* feat: add on_success callback to `_docker_exec_s…` (2fa-human-friendly) — [#53](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/53)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.2.0...v6.2.1

## [6.2.0] - 2026-08-10

### What's Changed
* feat: 2FA via Telegram — [#51](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/51)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.1.1...v6.2.0

## [6.1.1] - 2026-08-10

### What's Changed
* feat: expand roadmap with new ideas for robustness, config… — [#48](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/48)
* feat: add `SessionExpiredError` for non-interactive 2FA (login-refresh) — [#49](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/49)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.1.0...v6.1.1

## [6.1.0] - 2026-08-10

### What's Changed
* fix: update CI results table to include coverage (ci-results-table-fix) — [#32](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/32)
* deps: pin package versions in requirements files (pin-versions) — [#34](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/34)
* fix: update CI workflow to save and compare ci-stats.json (ci-stats) — [#35](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/35)
* fix: stop logging raw Trade Republic event dicts in clear text — [#36](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/36)
* feat: refund transaction support — [#39](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/39)
* feat: sync notification improvements — [#40](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/40)
* feat: add health checks for cron-based services and Telegram (healt-check) — [#41](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/41)
* feat: split credentials config (split-credentials) — [#42](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/42)
* feat: add workflow for preparing release with version bump (prepare-release) — [#44](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/44)
* fix: simplify version update step in release workflow (release-pipeline-fix) — [#45](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/45)
* fix: release workflow improvements (release-fix-2) — [#46](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/46)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v6.0.0...v6.1.0

## [6.0.0] - 2026-08-09

### What's Changed
* feat: enhance CI metrics extraction and reporting (test-results) — [#27](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/27)
* deps: bump pip-dependencies group with 5 updates — [#29](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/29)
* fix: backup API bug — [#31](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/31)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v5.0.1...v6.0.0

## [5.0.1] - 2026-08-09

### What's Changed
* refactor: introduce `BackupConfig` for backup configuration (env-validation-mode) — [#30](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/30)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v5.0.0...v5.0.1

## [5.0.0] - 2026-08-09

### What's Changed
* fix: replace subprocess docker exec with Docker SDK — [#28](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/28)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v4.0.0...v5.0.0

## [4.0.0] - 2026-08-09

### What's Changed
* ci: add Dependabot configuration — [#16](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/16)
* ci: remove coverage.json after pushing updates (ci-fix) — [#21](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/21)
* ci: group Dependabot updates — [#22](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/22)
* ci: fix coverage.json handling (ci-fix-coverage) — [#24](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/24)
* ci: format coverage.json for better readability (coverage-human-friendly) — [#25](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/25)
* feat: bot backup service — [#26](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/26)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v3.0.0...v4.0.0

## [3.0.0] - 2026-08-09

### What's Changed
* feat: Telegram bot — [#15](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/15)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v2.0.1...v3.0.0

## [2.0.1] - 2026-08-09

### What's Changed
* fix: enhance cron job environment variable handling and update docs (fix-cron) — [#14](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/14)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v2.0.0...v2.0.1

## [2.0.0] - 2026-08-08

### What's Changed
* feat: backup service — [#13](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/13)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v1.4.1...v2.0.0

## [1.4.1] - 2026-08-08

### What's Changed
* docs: add architecture documentation and update README (docu) — [#11](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/11)
* fix: export environment variables for cron job inheritance (fix-crond) — [#12](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/12)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v1.4.0...v1.4.1

## [1.4.0] - 2026-08-08

### What's Changed
* feat: add support for optional label assignment to transactions (transaction-label) — [#10](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/10)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v1.3.0...v1.4.0

## [1.3.0] - 2026-08-08

### What's Changed
* feat: add cron support for scheduled execution and update README (crond) — [#9](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/9)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v1.2.0...v1.3.0

## [1.2.0] - 2026-08-08

### What's Changed
* feat: various improvements — [#7](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/7)
* feat: bank transfer support — [#8](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/8)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v1.1.1...v1.2.0

## [1.1.1] - 2026-08-07

### What's Changed
* fix: update tag patterns in publish workflows for minor and patch releases (release-fix) — [#6](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/6)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v1.1.0...v1.1.1

## [1.1.0] - 2026-08-07

### What's Changed
* refactor: clean code improvements — [#5](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/5)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/compare/v1.0.0...v1.1.0

## [1.0.0] - 2026-08-07

### What's Changed
* feat: bootstrap Dockerized Trade Republic → BudgetBakers sync service with event mapping, dedup, and auth alerting — [#1](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/1)
* test: add initial test suite — [#2](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/2)
* feat: 2FA support — [#3](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/3)
* ci: add GitHub Actions workflows for publishing app and base images (publish-workflows) — [#4](https://github.com/sanmibuh/trade-republic-budget-bakers-sync/pull/4)

**Full Changelog**: https://github.com/sanmibuh/trade-republic-budget-bakers-sync/commits/v1.0.0
