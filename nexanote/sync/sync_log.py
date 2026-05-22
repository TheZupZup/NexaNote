"""
NexaNote — Sanitized sync log / Journal de synchronisation assaini.

EN: Writes a single ``latest.json`` diagnostic file after each sync session
    so users can answer "what did the last sync actually do?" without
    digging through server logs. The file lives at
    ``<data_dir>/sync_logs/latest.json`` — in the Docker image where
    ``data_dir`` is ``/data`` this resolves to ``/data/sync_logs/latest.json``.
    The path is derived from the configured data dir, never hardcoded, so
    it works the same in tests (tmp dir) and in any deployment.

    The log is *sanitized by construction*:
      * it is built only from the ``SyncReport`` (counts, timing, errors)
        and the ``SyncPlan`` (ids, titles, remote paths, reasons);
      * neither of those ever carries note body text, so no body can leak;
      * error strings are additionally run through :func:`sanitize_error`
        to strip URLs (which embed the server host) and any
        ``key=value`` pair that looks like a credential.

FR: Écrit un unique fichier ``latest.json`` après chaque session de sync,
    à ``<data_dir>/sync_logs/latest.json`` (donc ``/data/sync_logs/latest.json``
    dans l'image Docker). Le chemin dérive du data_dir configuré, jamais
    codé en dur. Le journal est assaini par construction : aucun corps de
    note, aucun secret ; les erreurs passent par :func:`sanitize_error`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from nexanote.sync.plan import SyncPlan

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids circular import
    from nexanote.sync.client import SyncReport

logger = logging.getLogger("nexanote.sync.log")

SYNC_LOG_DIRNAME = "sync_logs"
SYNC_LOG_FILENAME = "latest.json"
SYNC_LOG_VERSION = 1

# EN: A URL can embed the server host (and occasionally basic-auth
#     userinfo). We never want either in a diagnostic file, so any URL is
#     collapsed to a placeholder.
# FR: Une URL peut contenir l'hôte (voire user:pass@). On la remplace.
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# EN: Defensive scrub for any ``secret = value`` / ``token: value`` pair that
#     might slip into an error message. The engine already avoids leaking
#     these, but a sync log is a durable artifact — belt and braces.
# FR: Filet de sécurité pour toute paire ``secret=valeur`` dans un message.
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[-_]?key|authorization|auth|credentials?)\s*[=:]\s*\S+"
)


def sanitize_error(message: object) -> str:
    """
    EN: Render an error into a short, log-safe string: strip URLs and any
        credential-looking ``key=value`` pairs. HTTP status reasons such as
        "401 Unauthorized" are preserved — they carry no secret.
    FR: Convertit une erreur en chaîne sûre : retire URLs et paires
        ressemblant à des identifiants. Conserve les motifs HTTP ("401…").
    """
    if message is None:
        return ""
    text = str(message)
    text = _URL_RE.sub("<url>", text)
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    return text


def sync_log_path(data_dir: Path | str) -> Path:
    """Return ``<data_dir>/sync_logs/latest.json`` for the given data dir."""
    return Path(data_dir) / SYNC_LOG_DIRNAME / SYNC_LOG_FILENAME


def build_sync_log(
    report: "SyncReport",
    plan: Optional[SyncPlan],
    *,
    dry_run: bool = False,
) -> dict:
    """
    EN: Assemble the sanitized log payload from a finished ``SyncReport``
        and its ``SyncPlan``. Contains ids/titles/paths/reasons/counts only.
    FR: Construit la charge utile assainie à partir du rapport terminé et
        de son plan. Contient seulement ids/titres/chemins/motifs/compteurs.
    """
    plan = plan or SyncPlan()

    started = getattr(report, "started_at", None)
    finished = getattr(report, "finished_at", None)
    timestamp = finished or started

    errors = [sanitize_error(e) for e in getattr(report, "errors", [])]

    # Network-reliability diagnostics — attempts per operation and whether the
    # session is worth retrying. Each entry is already URL-free (relative DAV
    # slug path only); we still scrub the reason defensively.
    operation_attempts = []
    for entry in getattr(report, "operation_attempts", []) or []:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason")
        operation_attempts.append(
            {
                "operation": entry.get("operation"),
                "path": entry.get("path"),
                "attempts": entry.get("attempts"),
                "retryable": bool(entry.get("retryable")),
                "reason": sanitize_error(reason) if reason else None,
            }
        )

    transient_reason = getattr(report, "transient_reason", None)

    payload = {
        "version": SYNC_LOG_VERSION,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
        "duration_seconds": round(report.duration_seconds(), 3),
        "dry_run": bool(dry_run),
        "success": report.success(),
        "retryable": bool(getattr(report, "retryable", False)),
        "next_retry_after_seconds": getattr(report, "next_retry_after_seconds", None),
        "transient_reason": sanitize_error(transient_reason) if transient_reason else None,
        "operation_attempts": operation_attempts,
        "counts": {
            "pulled": getattr(report, "notes_pulled", 0),
            "pushed": getattr(report, "notes_pushed", 0),
            "conflicts": getattr(report, "conflicts_resolved", 0),
            "ignored_legacy": getattr(report, "notes_ignored_legacy", 0),
            "errors": len(errors),
        },
        # Note metadata only — ids and titles, never body content.
        "pushed": [n.to_dict() for n in plan.notes_to_push],
        "pulled": [n.to_dict() for n in plan.notes_to_pull],
        "ignored": [i.to_dict() for i in plan.notes_to_ignore],
        "conflicts": [c.to_dict() for c in plan.conflicts],
        "warnings": list(plan.warnings),
        "errors": errors,
    }
    return payload


def write_sync_log(
    data_dir: Path | str,
    report: "SyncReport",
    plan: Optional[SyncPlan],
    *,
    dry_run: bool = False,
) -> Optional[Path]:
    """
    EN: Write the sanitized log to ``<data_dir>/sync_logs/latest.json``
        atomically (tmp file + ``os.replace``). Never raises — a failed log
        write must not break sync. Returns the path on success, else None.
    FR: Écrit le journal assaini de manière atomique. Ne lève jamais — un
        échec d'écriture ne doit pas casser la sync. Retourne le chemin.
    """
    path = sync_log_path(data_dir)
    try:
        payload = build_sync_log(report, plan, dry_run=dry_run)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    except Exception:
        logger.exception("could not build sync log payload")
        return None

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning("sync log write failed (%s): %s", path, exc)
        return None
    return path


def read_sync_log(data_dir: Path | str) -> Optional[dict]:
    """Return the parsed latest sync log, or None when absent/unreadable."""
    path = sync_log_path(data_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("sync log unreadable (%s): %s", path, exc)
        return None


__all__ = [
    "SYNC_LOG_DIRNAME",
    "SYNC_LOG_FILENAME",
    "SYNC_LOG_VERSION",
    "build_sync_log",
    "read_sync_log",
    "sanitize_error",
    "sync_log_path",
    "write_sync_log",
]
