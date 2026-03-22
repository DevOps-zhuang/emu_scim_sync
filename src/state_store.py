import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Protocol, Set


@dataclass(frozen=True)
class SyncState:
    schema_version: int
    state_store_backend: str
    synced_user_external_ids: Set[str]
    synced_group_external_ids: Set[str]
    synced_users: Dict[str, Dict[str, str]]
    synced_groups: Dict[str, Dict[str, str]]
    resolved_group_name_map: Dict[str, str]
    pending_group_deletions: Dict[str, int]
    last_run_utc: str | None
    last_run_id: str | None
    last_run_status: str | None
    updated_at_utc: str | None


class StateStore(Protocol):
    def load(self) -> SyncState:
        ...

    def save_state(
        self,
        synced_user_external_ids: Set[str],
        synced_group_external_ids: Set[str],
        synced_users: Dict[str, Dict[str, str]],
        synced_groups: Dict[str, Dict[str, str]],
        resolved_group_name_map: Dict[str, str],
        pending_group_deletions: Dict[str, int],
        run_id: str,
        run_status: str,
    ) -> None:
        ...

    def describe(self) -> Dict[str, str]:
        ...


class LocalJsonStateStore:
    schema_version = 3

    def __init__(self, path: str):
        self.path = path

    @classmethod
    def _normalize_snapshot_entries(
        cls,
        raw_entries: Any,
        key_name: str,
        allowed_fields: Set[str],
    ) -> Dict[str, Dict[str, str]]:
        normalized: Dict[str, Dict[str, str]] = {}

        if isinstance(raw_entries, dict):
            iterator = []
            for external_id, raw_value in raw_entries.items():
                if isinstance(raw_value, dict):
                    entry = dict(raw_value)
                    entry.setdefault(key_name, external_id)
                else:
                    entry = {key_name: external_id}
                iterator.append(entry)
        elif isinstance(raw_entries, list):
            iterator = [entry for entry in raw_entries if isinstance(entry, dict)]
        else:
            iterator = []

        for entry in iterator:
            external_id = str(entry.get(key_name, "") or "").strip()
            if not external_id:
                continue

            normalized_entry = {key_name: external_id}
            for field_name in allowed_fields:
                field_value = entry.get(field_name)
                if field_value is None:
                    continue
                normalized_entry[field_name] = str(field_value)
            normalized[external_id] = normalized_entry

        return normalized

    @classmethod
    def _serialize_snapshot_entries(
        cls,
        snapshot: Dict[str, Dict[str, str]],
        key_name: str,
        field_order: tuple[str, ...],
    ) -> list[Dict[str, str]]:
        serialized_entries = []
        for external_id in sorted(snapshot):
            raw_entry = snapshot[external_id] or {}
            entry = {key_name: external_id}
            for field_name in field_order:
                field_value = raw_entry.get(field_name)
                if field_value:
                    entry[field_name] = str(field_value)
            serialized_entries.append(entry)
        return serialized_entries

    @classmethod
    def _normalize_state(cls, raw_state: Dict[str, Any]) -> SyncState:
        synced_user_external_ids = raw_state.get("synced_user_external_ids")
        if synced_user_external_ids is None:
            synced_user_external_ids = raw_state.get("synced_external_ids", [])

        synced_users = cls._normalize_snapshot_entries(
            raw_state.get("synced_users", []),
            key_name="external_id",
            allowed_fields={"user_principal_name", "display_name"},
        )
        synced_groups = cls._normalize_snapshot_entries(
            raw_state.get("synced_groups", []),
            key_name="external_id",
            allowed_fields={"display_name", "configured_name"},
        )

        pending_group_deletions = {}
        for group_id, count in (raw_state.get("pending_group_deletions", {}) or {}).items():
            try:
                normalized_count = int(count)
            except (TypeError, ValueError):
                continue
            if normalized_count > 0:
                pending_group_deletions[str(group_id)] = normalized_count

        return SyncState(
            schema_version=int(raw_state.get("schema_version", cls.schema_version)),
            state_store_backend=str(raw_state.get("state_store_backend", "local_json")),
            synced_user_external_ids=set(synced_user_external_ids or []),
            synced_group_external_ids=set(raw_state.get("synced_group_external_ids", []) or []),
            synced_users=synced_users,
            synced_groups=synced_groups,
            resolved_group_name_map=dict(raw_state.get("resolved_group_name_map", {}) or {}),
            pending_group_deletions=pending_group_deletions,
            last_run_utc=raw_state.get("last_run_utc"),
            last_run_id=raw_state.get("last_run_id"),
            last_run_status=raw_state.get("last_run_status"),
            updated_at_utc=raw_state.get("updated_at_utc"),
        )

    def load(self) -> SyncState:
        if not os.path.exists(self.path):
            return self._normalize_state({})
        with open(self.path, "r", encoding="utf-8") as f:
            return self._normalize_state(json.load(f))

    def save_state(
        self,
        synced_user_external_ids: Set[str],
        synced_group_external_ids: Set[str],
        synced_users: Dict[str, Dict[str, str]],
        synced_groups: Dict[str, Dict[str, str]],
        resolved_group_name_map: Dict[str, str],
        pending_group_deletions: Dict[str, int],
        run_id: str,
        run_status: str,
    ) -> None:
        state_dir = Path(self.path).parent
        if str(state_dir):
            state_dir.mkdir(parents=True, exist_ok=True)

        now_utc = datetime.now(timezone.utc).isoformat()
        state = {
            "schema_version": self.schema_version,
            "state_store_backend": "local_json",
            "synced_user_external_ids": sorted(synced_user_external_ids),
            "synced_group_external_ids": sorted(synced_group_external_ids),
            "synced_users": self._serialize_snapshot_entries(
                synced_users,
                key_name="external_id",
                field_order=("user_principal_name", "display_name"),
            ),
            "synced_groups": self._serialize_snapshot_entries(
                synced_groups,
                key_name="external_id",
                field_order=("display_name", "configured_name"),
            ),
            "resolved_group_name_map": dict(sorted(resolved_group_name_map.items())),
            "pending_group_deletions": dict(sorted(pending_group_deletions.items())),
            "last_run_utc": now_utc,
            "last_run_id": run_id,
            "last_run_status": run_status,
            "updated_at_utc": now_utc,
        }

        with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=state_dir or None) as temp_file:
            json.dump(state, temp_file, ensure_ascii=True, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = temp_file.name

        os.replace(temp_path, self.path)

    def describe(self) -> Dict[str, str]:
        return {
            "state_store_backend": "local_json",
            "state_file": self.path,
        }


def create_state_store(backend: str, path: str) -> StateStore:
    if backend == "local_json":
        return LocalJsonStateStore(path)
    raise ValueError(f"Unsupported state store backend: {backend}")
