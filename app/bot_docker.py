"""Docker exec helpers for the Telegram bot.

All interactions with the Docker SDK are isolated here so they can be
unit-tested independently of the bot's Telegram polling loop.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
from collections.abc import Callable, Generator
from typing import Any

import docker

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Script run inside sync containers to fetch the most recent sync summary.
# ---------------------------------------------------------------------------

_LAST_SYNC_SUMMARY_SCRIPT = """
import json
import os
import sqlite3
from pathlib import Path

data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
db_path = data_dir / "sync.db"
instance = os.environ.get("INSTANCE", "")
result = None
if db_path.exists() and instance:
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT status, ran_at, saved, failed, excluded FROM sync_runs WHERE instance = ?",
            (instance,),
        ).fetchone()
        if row:
            result = {
                "status": row[0],
                "ran_at": row[1],
                "saved": row[2],
                "failed": row[3],
                "excluded": row[4],
            }
    except Exception:
        pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
print(json.dumps(result))
"""


# ---------------------------------------------------------------------------
# Docker client context manager
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _docker_client_ctx() -> Generator[docker.DockerClient | None, None, None]:
    """Context manager that yields a Docker client and closes it on exit.

    Yields ``None`` (without raising) if the Docker daemon is unreachable so
    callers can handle the unavailable-client case without extra try/except.
    """
    client = None
    try:
        client = docker.from_env()
    except Exception as exc:
        log.debug("docker client init failed: %s", exc)
    try:
        yield client
    finally:
        if client is not None:
            client.close()


# ---------------------------------------------------------------------------
# Container introspection
# ---------------------------------------------------------------------------


def _docker_check_awaiting_code(
    container_name: str, client: docker.DockerClient | None = None
) -> bool | None:
    """Check whether *container_name* is currently waiting for a 2FA code.

    Runs ``python -m app check-pending`` inside the container via the Docker SDK.

    Returns:
        True   — the container has an active pending-login marker (exit code 0).
        False  — no pending login (exit code 1).
        None   — container unreachable or exec failed unexpectedly.
    """
    try:
        client = client or docker.from_env()
        container = client.containers.get(container_name)
        exit_code, _ = container.exec_run(["python", "-m", "app", "check-pending"])
        if exit_code == 0:
            return True
        if exit_code == 1:
            return False
        log.warning(
            "check-pending exited with unexpected code %s for %s",
            exit_code,
            container_name,
        )
        return None
    except Exception as exc:
        log.debug("check-pending exec failed for %s: %s", container_name, exc)
        return None


def _docker_check_session(
    container_name: str, client: docker.DockerClient | None = None
) -> bool | None:
    """Check whether the saved Trade Republic session is valid for *container_name*.

    Runs ``python -m app check-session`` inside the container via the Docker SDK.

    Returns:
        True   — session is valid (exit code 0).
        False  — session needs renewal (exit code 1).
        None   — container unreachable or exec failed unexpectedly.
    """
    try:
        client = client or docker.from_env()
        container = client.containers.get(container_name)
        exit_code, _ = container.exec_run(["python", "-m", "app", "check-session"])
        if exit_code == 0:
            return True
        if exit_code == 1:
            return False
        log.warning(
            "check-session exited with unexpected code %s for %s",
            exit_code,
            container_name,
        )
        return None
    except Exception as exc:
        log.debug("check-session exec failed for %s: %s", container_name, exc)
        return None


def _docker_container_status(
    container_name: str, client: docker.DockerClient | None = None
) -> str | None:
    """Return the Docker container status string for *container_name*."""
    try:
        client = client or docker.from_env()
        container = client.containers.get(container_name)
        return container.status
    except Exception as exc:
        log.debug("container status lookup failed for %s: %s", container_name, exc)
        return None


def _format_sync_timestamp(raw: str) -> str:
    """Format log/DB timestamps as ``YYYY/MM/DD HH:MM UTC`` for Telegram output."""
    try:
        if "T" in raw:
            parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            parsed = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=datetime.UTC
            )
        return parsed.astimezone(datetime.UTC).strftime("%Y/%m/%d %H:%M UTC")
    except ValueError:
        return raw


def _docker_last_sync_summary(
    container_name: str, client: docker.DockerClient | None = None
) -> str | None:
    """Return a human-readable summary of the most recent sync run from the DB."""
    try:
        client = client or docker.from_env()
        container = client.containers.get(container_name)
        exit_code, output = container.exec_run(
            ["python", "-c", _LAST_SYNC_SUMMARY_SCRIPT]
        )
        if exit_code != 0:
            return None
        payload = json.loads(output.decode(errors="replace"))
    except Exception as exc:
        log.debug("last sync lookup failed for %s: %s", container_name, exc)
        return None

    if payload is None:
        return None

    status = payload.get("status")
    ran_at = payload.get("ran_at")
    if status in {"success", "partial", "failed"}:
        icon: dict[str, str] = {"success": "✅", "partial": "⚠️", "failed": "❌"}
        parts = [f"{icon[status]} {status}"]
        if ran_at:
            parts[0] = f"{parts[0]} at {_format_sync_timestamp(ran_at)}"
        saved = payload.get("saved")
        failed = payload.get("failed")
        excluded = payload.get("excluded")
        if saved is not None:
            parts.append(f"saved {saved}")
        if failed is not None:
            parts.append(f"failed {failed}")
        if excluded is not None:
            parts.append(f"excluded {excluded}")
        return " · ".join(parts)
    return None


def _docker_logs_today(
    container_name: str,
    since: datetime.datetime,
    client: docker.DockerClient | None = None,
) -> str:
    """Return stdout/stderr logs for *container_name* since *since* (UTC datetime)."""
    client = client or docker.from_env()
    container = client.containers.get(container_name)
    raw: bytes = container.logs(since=since, timestamps=False)
    return raw.decode(errors="replace")


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _docker_exec_silent(
    container_name: str,
    app_args: list[str],
    on_error: Callable[[str], None] | None = None,
    on_success: Callable[[], None] | None = None,
) -> None:
    """Run ``python -m app <app_args>`` inside a container via the Docker SDK.

    Does NOT send any Telegram message on success by default — the container's
    own Notifier handles that.  Callers that need explicit success feedback
    (e.g. ``login``) can pass ``on_success``.  On failure, ``on_error(message)``
    is called if provided.
    """
    cmd = ["python", "-m", "app", *app_args]
    log.info("Executing: docker exec %s %s", container_name, " ".join(cmd))
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        env: Any = container.attrs["Config"]["Env"]
        exit_code, output = container.exec_run(cmd, environment=env)
        if exit_code == 0:
            log.info(
                "docker exec finished successfully for container %s", container_name
            )
            if on_success:
                on_success()
        else:
            details = output.decode(errors="replace").strip() if output else ""
            log.warning(
                "docker exec exited with code %s for container %s:\n%s",
                exit_code,
                container_name,
                details,
            )
            if on_error:
                on_error(
                    f"❌ Command failed on `{container_name}` \\(exit {exit_code}\\)\\."
                )
    except Exception as exc:
        log.warning("docker exec failed for container %s: %s", container_name, exc)
        if on_error:
            on_error(f"❌ Could not exec on `{container_name}`: {exc}")
