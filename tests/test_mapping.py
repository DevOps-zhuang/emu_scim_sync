from src.models import SourceGroup, SourceUser
from src.sync_engine import SyncEngine
from src.state_store import SyncState


def test_mapping_mail_fallback_to_upn():
    user = SourceUser(
        id="u-1",
        user_principal_name="alice@contoso.cn",
        display_name="Alice",
        mail=None,
        department="R&D",
        account_enabled=True,
    )
    payload = SyncEngine.to_scim_user_payload(user)
    assert payload["externalId"] == "u-1"
    assert payload["userName"] == "alice@contoso.cn"
    assert payload["emails"][0]["value"] == "alice@contoso.cn"
    assert payload["active"] is True
    assert payload["roles"] == [{"value": "user", "primary": False}]


def test_mapping_enterprise_admin_role():
    user = SourceUser(
        id="u-2",
        user_principal_name="admin@contoso.cn",
        display_name="Admin",
        mail="admin@contoso.cn",
        department=None,
        account_enabled=True,
    )
    payload = SyncEngine.to_scim_user_payload(user, is_enterprise_admin=True)
    assert payload["roles"] == [{"value": "enterprise_owner", "primary": False}]


class DummyStateStore:
    def __init__(
        self,
        synced_user_external_ids=None,
        synced_group_external_ids=None,
        pending_group_deletions=None,
    ):
        self.state = {
            "synced_user_external_ids": sorted(set(synced_user_external_ids or [])),
            "synced_group_external_ids": sorted(set(synced_group_external_ids or [])),
            "synced_users": {},
            "synced_groups": {},
            "resolved_group_name_map": {},
            "pending_group_deletions": dict(pending_group_deletions or {}),
            "last_run_utc": None,
        }
        self.saved_state = None

    def load(self):
        return SyncState(
            schema_version=3,
            state_store_backend="local_json",
            synced_user_external_ids=set(self.state["synced_user_external_ids"]),
            synced_group_external_ids=set(self.state["synced_group_external_ids"]),
            synced_users=dict(self.state["synced_users"]),
            synced_groups=dict(self.state["synced_groups"]),
            resolved_group_name_map=dict(self.state["resolved_group_name_map"]),
            pending_group_deletions=dict(self.state["pending_group_deletions"]),
            last_run_utc=self.state["last_run_utc"],
            last_run_id=None,
            last_run_status=None,
            updated_at_utc=None,
        )

    def save_state(
        self,
        synced_user_external_ids,
        synced_group_external_ids,
        synced_users,
        synced_groups,
        resolved_group_name_map,
        pending_group_deletions,
        run_id,
        run_status,
    ):
        self.saved_state = {
            "synced_user_external_ids": sorted(synced_user_external_ids),
            "synced_group_external_ids": sorted(synced_group_external_ids),
            "synced_users": dict(sorted(synced_users.items())),
            "synced_groups": dict(sorted(synced_groups.items())),
            "resolved_group_name_map": dict(sorted(resolved_group_name_map.items())),
            "pending_group_deletions": dict(sorted(pending_group_deletions.items())),
            "last_run_id": run_id,
            "last_run_status": run_status,
        }
        self.state.update(self.saved_state)

    def describe(self):
        return {"state_store_backend": "local_json", "state_file": "memory"}


class DummyScimClient:
    def __init__(self, existing_by_external_id=None, existing_by_username=None, existing_groups_by_external_id=None):
        self.existing_by_external_id = existing_by_external_id or {}
        self.existing_by_username = existing_by_username or {}
        self.existing_groups_by_external_id = existing_groups_by_external_id or {}
        self.patch_calls = []
        self.create_calls = []
        self.delete_calls = []
        self.patch_group_calls = []
        self.create_group_calls = []
        self.delete_group_calls = []

    def find_user_by_external_id(self, external_id):
        return self.existing_by_external_id.get(external_id)

    def find_user_by_username(self, username):
        return self.existing_by_username.get(username)

    def create_user(self, payload):
        self.create_calls.append(payload)
        return payload

    def patch_user(self, scim_user_id, operations):
        self.patch_calls.append((scim_user_id, operations))
        return {}

    def delete_user(self, scim_user_id):
        self.delete_calls.append(scim_user_id)

    def find_group_by_external_id(self, external_id):
        return self.existing_groups_by_external_id.get(external_id)

    def create_group(self, payload):
        self.create_group_calls.append(payload)
        created = {"id": f"created-group-{payload['externalId']}", **payload}
        self.existing_groups_by_external_id[payload["externalId"]] = created
        return created

    def patch_group(self, scim_group_id, operations):
        self.patch_group_calls.append((scim_group_id, operations))
        return {}

    def delete_group(self, scim_group_id):
        self.delete_group_calls.append(scim_group_id)


def test_dry_run_does_not_persist_state():
    state_store = DummyStateStore()
    scim_client = DummyScimClient()
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=True)

    user = SourceUser(
        id="u-3",
        user_principal_name="dryrun@contoso.cn",
        display_name="Dry Run",
        mail=None,
        department=None,
        account_enabled=True,
    )

    group = SourceGroup(id="g-1", display_name="Platform", member_ids=frozenset({"u-3"}))

    result = engine.sync([user], [group], {"Platform": "g-1"}, run_id="run-1")

    assert result.stats.user_created == 1
    assert result.stats.group_created == 1
    assert state_store.saved_state is None


def test_sync_backfills_external_id_and_updates_profile_fields():
    state_store = DummyStateStore()
    existing = {
        "id": "scim-1",
        "userName": "admin@contoso.cn",
        "displayName": "Old Admin",
        "externalId": "",
        "active": True,
        "emails": [{"value": "old-admin@contoso.cn"}],
        "roles": [{"value": "user", "primary": False}],
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"department": "Old"},
    }
    scim_client = DummyScimClient(existing_by_username={"admin@contoso.cn": existing})
    engine = SyncEngine(
        scim_client=scim_client,
        state_store=state_store,
        dry_run=False,
        enterprise_admin_upns=frozenset({"admin@contoso.cn"}),
    )

    user = SourceUser(
        id="u-4",
        user_principal_name="admin@contoso.cn",
        display_name="Admin",
        mail="admin@contoso.cn",
        department="Platform",
        account_enabled=True,
    )

    result = engine.sync([user], [], {}, run_id="run-1")

    assert result.stats.user_updated == 1
    assert state_store.saved_state["synced_user_external_ids"] == ["u-4"]
    assert state_store.saved_state["synced_users"] == {
        "u-4": {
            "external_id": "u-4",
            "user_principal_name": "admin@contoso.cn",
            "display_name": "Admin",
        }
    }
    assert len(scim_client.patch_calls) == 1
    _, operations = scim_client.patch_calls[0]
    assert {operation["path"] for operation in operations} == {
        "externalId",
        "userName",
        "displayName",
        "emails",
        "active",
        "roles",
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department",
    }


def test_sync_logs_user_creation_with_readable_identity(caplog):
    state_store = DummyStateStore()
    scim_client = DummyScimClient()
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=False)

    user = SourceUser(
        id="u-create",
        user_principal_name="create@contoso.cn",
        display_name="Create User",
        mail="create@contoso.cn",
        department=None,
        account_enabled=True,
    )

    with caplog.at_level("INFO"):
        engine.sync([user], [], {}, run_id="run-1")

    assert "create user:" in caplog.text
    assert "userName=create@contoso.cn" in caplog.text
    assert "displayName=Create User" in caplog.text


def test_sync_logs_reactivated_user_with_readable_identity(caplog):
    state_store = DummyStateStore()
    existing = {
        "id": "scim-reactivate",
        "userName": "reactivate@contoso.cn",
        "displayName": "Reactivate User",
        "externalId": "u-reactivate",
        "active": False,
        "emails": [{"value": "reactivate@contoso.cn"}],
        "roles": [{"value": "user", "primary": False}],
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"department": "Ops"},
    }
    scim_client = DummyScimClient(existing_by_external_id={"u-reactivate": existing})
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=False)

    user = SourceUser(
        id="u-reactivate",
        user_principal_name="reactivate@contoso.cn",
        display_name="Reactivate User",
        mail="reactivate@contoso.cn",
        department="Ops",
        account_enabled=True,
    )

    with caplog.at_level("INFO"):
        engine.sync([user], [], {}, run_id="run-1")

    assert "reactivate user:" in caplog.text
    assert "userName=reactivate@contoso.cn" in caplog.text
    assert "displayName=Reactivate User" in caplog.text
    assert "changedPaths=externalId,userName,displayName,emails,active,roles,urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department" in caplog.text


def test_removed_user_is_soft_deprovisioned_by_default():
    state_store = DummyStateStore(synced_user_external_ids={"u-removed"})
    existing = {
        "id": "scim-removed",
        "externalId": "u-removed",
        "active": True,
    }
    scim_client = DummyScimClient(existing_by_external_id={"u-removed": existing})
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=False)

    result = engine.sync([], [], {}, run_id="run-1")

    assert result.stats.user_soft_deprovisioned == 1
    assert result.stats.user_hard_deleted == 0
    assert scim_client.delete_calls == []
    assert scim_client.patch_calls == [
        ("scim-removed", [{"op": "replace", "path": "active", "value": False}])
    ]
    assert state_store.saved_state["synced_user_external_ids"] == ["u-removed"]


def test_removed_user_can_be_hard_deleted_when_enabled():
    state_store = DummyStateStore(synced_user_external_ids={"u-removed"})
    existing = {
        "id": "scim-removed",
        "externalId": "u-removed",
        "active": True,
    }
    scim_client = DummyScimClient(existing_by_external_id={"u-removed": existing})
    engine = SyncEngine(
        scim_client=scim_client,
        state_store=state_store,
        dry_run=False,
        hard_delete_removed_users=True,
    )

    result = engine.sync([], [], {}, run_id="run-1")

    assert result.stats.user_soft_deprovisioned == 0
    assert result.stats.user_hard_deleted == 1
    assert scim_client.patch_calls == []
    assert scim_client.delete_calls == ["scim-removed"]
    assert state_store.saved_state["synced_user_external_ids"] == []


def test_in_scope_disabled_user_counts_as_soft_deprovision():
    state_store = DummyStateStore()
    existing = {
        "id": "scim-1",
        "userName": "disabled@contoso.cn",
        "displayName": "Disabled User",
        "externalId": "u-disabled",
        "active": True,
        "emails": [{"value": "disabled@contoso.cn"}],
        "roles": [{"value": "user", "primary": False}],
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"department": "Ops"},
    }
    scim_client = DummyScimClient(existing_by_external_id={"u-disabled": existing})
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=False)

    user = SourceUser(
        id="u-disabled",
        user_principal_name="disabled@contoso.cn",
        display_name="Disabled User",
        mail="disabled@contoso.cn",
        department="Ops",
        account_enabled=False,
    )

    result = engine.sync([user], [], {}, run_id="run-1")

    assert result.stats.user_updated == 1
    assert result.stats.user_soft_deprovisioned == 1
    assert scim_client.patch_calls[0][0] == "scim-1"


def test_group_sync_updates_members_using_user_scim_ids():
    state_store = DummyStateStore()
    existing_user = {
        "id": "scim-user-1",
        "externalId": "u-1",
        "userName": "alice@contoso.cn",
        "displayName": "Alice",
        "active": True,
        "emails": [{"value": "alice@contoso.cn"}],
        "roles": [{"value": "user", "primary": False}],
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"department": "R&D"},
    }
    existing_group = {
        "id": "scim-group-1",
        "externalId": "g-1",
        "displayName": "Old Platform",
        "members": [],
    }
    scim_client = DummyScimClient(
        existing_by_external_id={"u-1": existing_user},
        existing_groups_by_external_id={"g-1": existing_group},
    )
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=False)

    user = SourceUser(
        id="u-1",
        user_principal_name="alice@contoso.cn",
        display_name="Alice",
        mail="alice@contoso.cn",
        department="R&D",
        account_enabled=True,
    )
    group = SourceGroup(id="g-1", display_name="Platform", member_ids=frozenset({"u-1"}))

    result = engine.sync([user], [group], {"Platform": "g-1"}, run_id="run-1")

    assert result.stats.user_skipped == 1
    assert result.stats.group_updated == 1
    assert state_store.saved_state["synced_groups"] == {
        "g-1": {
            "external_id": "g-1",
            "display_name": "Platform",
            "configured_name": "Platform",
        }
    }
    assert scim_client.patch_group_calls == [
        (
            "scim-group-1",
            [
                {"op": "replace", "path": "displayName", "value": "Platform"},
                {"op": "replace", "path": "members", "value": [{"value": "scim-user-1"}]},
            ],
        )
    ]


def test_group_deletion_requires_grace_runs():
    existing_group = {
        "id": "scim-group-1",
        "externalId": "g-1",
        "displayName": "Platform",
        "members": [],
    }
    scim_client = DummyScimClient(existing_groups_by_external_id={"g-1": existing_group})

    first_state = DummyStateStore(synced_group_external_ids={"g-1"})
    first_engine = SyncEngine(
        scim_client=scim_client,
        state_store=first_state,
        dry_run=False,
        group_delete_grace_runs=2,
        group_delete_max_percent=100,
    )
    first_result = first_engine.sync([], [], {}, run_id="run-1")

    assert first_result.stats.group_deleted == 0
    assert first_result.stats.blocked_group_deletions == 1
    assert first_state.saved_state["pending_group_deletions"] == {"g-1": 1}
    assert first_state.saved_state["synced_group_external_ids"] == ["g-1"]

    second_state = DummyStateStore(synced_group_external_ids={"g-1"}, pending_group_deletions={"g-1": 1})
    second_engine = SyncEngine(
        scim_client=scim_client,
        state_store=second_state,
        dry_run=False,
        group_delete_grace_runs=2,
        group_delete_max_percent=100,
    )
    second_result = second_engine.sync([], [], {}, run_id="run-2")

    assert second_result.stats.group_deleted == 1
    assert scim_client.delete_group_calls == ["scim-group-1"]
    assert second_state.saved_state["pending_group_deletions"] == {}
    assert second_state.saved_state["synced_group_external_ids"] == []


def test_group_delete_threshold_blocks_mass_delete():
    synced_groups = {"g-1", "g-2", "g-3", "g-4", "g-5"}
    state_store = DummyStateStore(synced_group_external_ids=synced_groups)
    scim_client = DummyScimClient()
    engine = SyncEngine(
        scim_client=scim_client,
        state_store=state_store,
        dry_run=False,
        group_delete_grace_runs=2,
        group_delete_max_percent=20,
    )

    result = engine.sync([], [], {}, run_id="run-1")

    assert result.stats.group_deleted == 0
    assert result.stats.blocked_group_deletions == 5
    assert scim_client.delete_group_calls == []
    assert state_store.saved_state["synced_group_external_ids"] == sorted(synced_groups)


def test_removed_user_keeps_previous_readable_snapshot_when_soft_deprovisioned():
    state_store = DummyStateStore(synced_user_external_ids={"u-removed"})
    state_store.state["synced_users"] = {
        "u-removed": {
            "external_id": "u-removed",
            "user_principal_name": "removed@contoso.cn",
            "display_name": "Removed User",
        }
    }
    existing = {
        "id": "scim-removed",
        "externalId": "u-removed",
        "active": True,
    }
    scim_client = DummyScimClient(existing_by_external_id={"u-removed": existing})
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=False)

    result = engine.sync([], [], {}, run_id="run-1")

    assert result.stats.user_soft_deprovisioned == 1
    assert state_store.saved_state["synced_users"] == {
        "u-removed": {
            "external_id": "u-removed",
            "user_principal_name": "removed@contoso.cn",
            "display_name": "Removed User",
        }
    }


def test_removed_user_log_includes_readable_identity(caplog):
    state_store = DummyStateStore(synced_user_external_ids={"u-removed"})
    state_store.state["synced_users"] = {
        "u-removed": {
            "external_id": "u-removed",
            "user_principal_name": "removed@contoso.cn",
            "display_name": "Removed User",
        }
    }
    existing = {
        "id": "scim-removed",
        "externalId": "u-removed",
        "active": False,
    }
    scim_client = DummyScimClient(existing_by_external_id={"u-removed": existing})
    engine = SyncEngine(scim_client=scim_client, state_store=state_store, dry_run=False)

    with caplog.at_level("INFO"):
        engine.sync([], [], {}, run_id="run-1")

    assert "userName=removed@contoso.cn" in caplog.text
    assert "displayName=Removed User" in caplog.text
