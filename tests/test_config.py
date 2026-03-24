import json
import logging
import os
from pathlib import Path

import pytest

from src.config import load_settings, validate_settings
from src.runtime_logging import (
    JsonFormatter,
    StructuredTextFormatter,
    _build_latest_log_file_path,
    _build_run_log_file_path,
    _cleanup_old_run_logs,
)
from src.state_store import LocalJsonStateStore, create_state_store


def test_load_settings_supports_multiple_group_names(monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "client")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ENTRA_SYNC_GROUP_NAMES", "Group A, Group B, group a")
    monkeypatch.setenv("GITHUB_ENTERPRISE", "enterprise")
    monkeypatch.setenv("GITHUB_PAT", "token")
    monkeypatch.setenv("GROUP_DELETE_GRACE_RUNS", "2")
    monkeypatch.setenv("GROUP_DELETE_MAX_PERCENT", "20")

    settings = load_settings()
    validate_settings(settings)

    assert settings.entra_sync_group_names == ("Group A", "Group B")
    assert settings.group_delete_grace_runs == 2
    assert settings.group_delete_max_percent == 20


def test_load_settings_supports_logging_and_state_backend(monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "client")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ENTRA_SYNC_GROUP_NAMES", "Group A")
    monkeypatch.setenv("GITHUB_ENTERPRISE", "enterprise")
    monkeypatch.setenv("GITHUB_PAT", "token")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_FILE", "logs/sync.log")
    monkeypatch.setenv("STATE_STORE_BACKEND", "local_json")

    settings = load_settings()
    validate_settings(settings)

    assert settings.log_format == "json"
    assert settings.log_file == "logs/sync.log"
    assert settings.state_store_backend == "local_json"


def test_validate_settings_accepts_group_names_for_supported_entra_group_types(monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "client")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ENTRA_SYNC_GROUP_NAMES", "Security Group, Distribution Group")
    monkeypatch.setenv("GITHUB_ENTERPRISE", "enterprise")
    monkeypatch.setenv("GITHUB_PAT", "token")

    settings = load_settings()

    validate_settings(settings)

    assert settings.entra_sync_group_names == ("Security Group", "Distribution Group")


def test_validate_settings_rejects_invalid_group_delete_threshold(monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "client")
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ENTRA_SYNC_GROUP_NAMES", "Group A")
    monkeypatch.setenv("GITHUB_ENTERPRISE", "enterprise")
    monkeypatch.setenv("GITHUB_PAT", "token")
    monkeypatch.setenv("GROUP_DELETE_MAX_PERCENT", "0")

    settings = load_settings()

    with pytest.raises(ValueError, match="GROUP_DELETE_MAX_PERCENT"):
        validate_settings(settings)


def test_state_store_reads_legacy_synced_external_ids(tmp_path):
    state_path = tmp_path / "sync_state.json"
    state_path.write_text(json.dumps({"synced_external_ids": ["u-1"]}), encoding="utf-8")

    state_store = LocalJsonStateStore(str(state_path))

    state = state_store.load()

    assert state.synced_user_external_ids == {"u-1"}
    assert state.synced_group_external_ids == set()


def test_local_json_state_store_persists_metadata(tmp_path):
    state_path = tmp_path / "sync_state.json"
    state_store = LocalJsonStateStore(str(state_path))

    state_store.save_state(
        synced_user_external_ids={"u-1"},
        synced_group_external_ids={"g-1"},
        synced_users={
            "u-1": {
                "external_id": "u-1",
                "user_principal_name": "alice@contoso.cn",
                "display_name": "Alice",
            }
        },
        synced_groups={
            "g-1": {
                "external_id": "g-1",
                "display_name": "Platform",
                "configured_name": "Platform",
            }
        },
        resolved_group_name_map={"Platform": "g-1"},
        pending_group_deletions={"g-2": 1},
        run_id="run-123",
        run_status="success",
    )

    loaded = state_store.load()

    assert loaded.schema_version == 3
    assert loaded.state_store_backend == "local_json"
    assert loaded.synced_user_external_ids == {"u-1"}
    assert loaded.synced_group_external_ids == {"g-1"}
    assert loaded.synced_users == {
        "u-1": {
            "external_id": "u-1",
            "user_principal_name": "alice@contoso.cn",
            "display_name": "Alice",
        }
    }
    assert loaded.synced_groups == {
        "g-1": {
            "external_id": "g-1",
            "display_name": "Platform",
            "configured_name": "Platform",
        }
    }
    assert loaded.resolved_group_name_map == {"Platform": "g-1"}
    assert loaded.pending_group_deletions == {"g-2": 1}
    assert loaded.last_run_id == "run-123"
    assert loaded.last_run_status == "success"
    assert loaded.updated_at_utc is not None


def test_state_store_reads_missing_readable_snapshots_as_empty(tmp_path):
    state_path = tmp_path / "sync_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "synced_user_external_ids": ["u-1"],
                "synced_group_external_ids": ["g-1"],
            }
        ),
        encoding="utf-8",
    )

    state_store = LocalJsonStateStore(str(state_path))

    loaded = state_store.load()

    assert loaded.synced_users == {}
    assert loaded.synced_groups == {}


def test_create_state_store_returns_local_json_backend(tmp_path):
    state_store = create_state_store("local_json", str(tmp_path / "sync_state.json"))

    assert state_store.describe()["state_store_backend"] == "local_json"


def test_json_formatter_outputs_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    record.event = "sync_run_started"
    record.fields = {"run_id": "abc123", "dry_run": True}

    rendered = formatter.format(record)

    assert '"event": "sync_run_started"' in rendered
    assert '"run_id": "abc123"' in rendered


def test_structured_text_formatter_outputs_fields():
    formatter = StructuredTextFormatter()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    record.event = "sync_run_started"
    record.fields = {"run_id": "abc123"}

    rendered = formatter.format(record)

    assert "event=sync_run_started" in rendered
    assert "run_id='abc123'" in rendered


def test_build_run_log_file_path_adds_timestamp_and_run_kind():
    path = _build_run_log_file_path("logs/emu_scim_sync.log", dry_run=False)

    assert path.startswith("logs")
    assert path.endswith("_apply.log")
    assert "emu_scim_sync_" in path


def test_build_run_log_file_path_defaults_extension_when_missing():
    path = _build_run_log_file_path("logs/emu_scim_sync", dry_run=True)

    assert path.endswith("_dryrun.log")


def test_build_latest_log_file_path_uses_stable_name():
    path = _build_latest_log_file_path("logs/emu_scim_sync.log")

    assert path == os.path.join("logs", "emu_scim_sync_latest.log")


def test_cleanup_old_run_logs_keeps_latest_files(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    file_names = [
        "emu_scim_sync_20260322_090000_apply.log",
        "emu_scim_sync_20260322_090100_apply.log",
        "emu_scim_sync_20260322_090200_apply.log",
    ]
    for file_name in file_names:
        (log_dir / file_name).write_text("log", encoding="utf-8")

    _cleanup_old_run_logs(str(log_dir / "emu_scim_sync.log"), keep_count=2)

    remaining = sorted(path.name for path in Path(log_dir).iterdir())
    assert remaining == file_names[1:]


def test_cleanup_old_run_logs_keeps_stable_latest_log(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for file_name in [
        "emu_scim_sync_20260322_090000_apply.log",
        "emu_scim_sync_20260322_090100_apply.log",
        "emu_scim_sync_latest.log",
    ]:
        (log_dir / file_name).write_text("log", encoding="utf-8")

    _cleanup_old_run_logs(str(log_dir / "emu_scim_sync.log"), keep_count=1)

    remaining = sorted(path.name for path in Path(log_dir).iterdir())
    assert remaining == ["emu_scim_sync_20260322_090100_apply.log", "emu_scim_sync_latest.log"]