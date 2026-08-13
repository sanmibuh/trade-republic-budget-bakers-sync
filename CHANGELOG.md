# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
