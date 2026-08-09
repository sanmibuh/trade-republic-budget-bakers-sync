# Agent Guidelines

Instructions for AI assistants working on this project. Read this before making any changes.

---

## Workflow

### TDD — tests first, always
1. Write the test and watch it fail before writing any implementation.
2. Implement the minimum code to make it pass.
3. Refactor with the tests as the safety net.

Never write implementation code without a corresponding test. Coverage should stay at or above 97%.

### Code quality
- **SOLID**: single responsibility, open/closed, no god objects.
- **Clean Code**: small functions, descriptive names, no magic numbers, no dead code.
- **OOP**: encapsulate state, prefer methods over module-level functions when state is involved.
- **DRY**: extract shared logic; never copy-paste across modules.
- Run `ruff check .` before considering any task done. Fix all warnings — do not suppress with `noqa` unless genuinely justified.

---

## Documentation

### Update `ARCHITECTURE.md` when:
- A new module is added or an existing one is renamed/removed.
- A key design decision changes (e.g. container naming, env var behaviour, data flow).
- The test count or file list changes.
- A new workflow or release mechanism is introduced.

### Update `ROADMAP.md` when:
- A roadmap item is implemented — remove or mark it done.
- A new improvement idea surfaces during a session — add it.

---

## Deploy

### Update `deploy/nas/current/docker-compose.yml` when:
- A new service is added or an existing one is renamed.
- An environment variable is added, removed, or renamed.
- The image version changes (bump `VERSION` file instead — see below).

### Update `deploy/DEPLOY.md` when:
- The deploy procedure changes (new steps, different commands, new services).
- `tr-sync.sh` commands change.

### Never:
- Commit secrets or credentials from `deploy/nas/`.
- Modify `deploy/nas/v*/` directories — those are read-only historical snapshots.

---

## Versioning

- The `VERSION` file at the repo root is the single source of truth for the release version.
- Bumping `VERSION` on `main` automatically triggers tag creation, GitHub release, and image build via CI.
- **Major** (`X.0.0`): rebuilds both the base image (`python-trade-republic`) and the app image (`tr-wallet-sync`).
- **Minor / patch** (`X.Y.Z`): rebuilds only the app image.
- Update the image tag in `deploy/nas/current/docker-compose.yml` after releasing.

---

## Project conventions

- All environment variables are read in `app/config.py` — never call `os.getenv` directly in other modules.
- `OWNER_NAME` is optional in `Config` — defaults to `"Backup"`. Sync services set it explicitly; the backup service does not.
- Container naming: `{CONTAINER_PREFIX}-sync-{instance}-1` for sync, `{CONTAINER_PREFIX}-{BACKUP_SERVICE}-1` for backup. `CONTAINER_PREFIX` matches the `name:` field in `docker-compose.yml` (`tr-sync`).
- `entrypoint.sh` is driven by `MODE=sync|backup|bot`. No heuristics.
- One Docker image (`tr-wallet-sync`) for all services — `entrypoint` is overridden per service in the compose file where needed.
