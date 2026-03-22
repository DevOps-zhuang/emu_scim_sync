import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


class StructuredTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        event = getattr(record, "event", "log")
        fields = getattr(record, "fields", {}) or {}
        message = record.getMessage()
        field_text = " ".join(f"{key}={fields[key]!r}" for key in sorted(fields))

        parts = [timestamp, record.levelname, record.name, f"event={event}"]
        if message:
            parts.append(message)
        if field_text:
            parts.append(field_text)
        rendered = " ".join(parts)

        if record.exc_info:
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"

        return rendered


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True)


def _build_run_log_file_path(base_path: str, dry_run: bool) -> str:
    normalized_path = base_path.strip()
    if not normalized_path:
        return ""

    directory, file_name = os.path.split(normalized_path)
    stem, extension = os.path.splitext(file_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_kind = "dryrun" if dry_run else "apply"

    if not stem:
        stem = "emu_scim_sync"
    if not extension:
        extension = ".log"

    return os.path.join(directory, f"{stem}_{timestamp}_{run_kind}{extension}")


def _build_latest_log_file_path(base_path: str) -> str:
    normalized_path = base_path.strip()
    if not normalized_path:
        return ""

    directory, file_name = os.path.split(normalized_path)
    stem, extension = os.path.splitext(file_name)
    if not stem:
        stem = "emu_scim_sync"
    if not extension:
        extension = ".log"

    return os.path.join(directory, f"{stem}_latest{extension}")


def _cleanup_old_run_logs(base_path: str, keep_count: int) -> None:
    normalized_path = base_path.strip()
    if not normalized_path:
        return

    directory, file_name = os.path.split(normalized_path)
    stem, extension = os.path.splitext(file_name)
    if not stem:
        stem = "emu_scim_sync"
    if not extension:
        extension = ".log"

    latest_log_name = os.path.basename(_build_latest_log_file_path(base_path))

    log_dir = Path(directory or ".")
    if not log_dir.exists():
        return

    matching_files = sorted(
        [
            path
            for path in log_dir.iterdir()
            if (
                path.is_file()
                and path.name != latest_log_name
                and path.name.startswith(f"{stem}_")
                and path.name.endswith(extension)
            )
        ],
        key=lambda path: path.name,
        reverse=True,
    )

    for obsolete_path in matching_files[max(1, keep_count) :]:
        obsolete_path.unlink(missing_ok=True)


def configure_logging(settings: Any) -> str:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    formatter: logging.Formatter
    if settings.log_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = StructuredTextFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    resolved_log_file = ""
    if settings.log_file:
        resolved_log_file = _build_run_log_file_path(settings.log_file, settings.dry_run)
        latest_log_file = _build_latest_log_file_path(settings.log_file)
        log_dir = os.path.dirname(resolved_log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.FileHandler(resolved_log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        latest_file_handler = logging.FileHandler(latest_log_file, mode="w", encoding="utf-8")
        latest_file_handler.setFormatter(formatter)
        root_logger.addHandler(latest_file_handler)

        _cleanup_old_run_logs(settings.log_file, settings.log_file_backup_count)

    return resolved_log_file


def log_event(logger: logging.Logger, level: int, event: str, message: str = "", **fields: Any) -> None:
    logger.log(level, message or event, extra={"event": event, "fields": fields})