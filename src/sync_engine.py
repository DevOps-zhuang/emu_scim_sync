import logging
from typing import Dict, FrozenSet, List, Set, Tuple

from .github_scim_client import GitHubScimClient
from .models import SourceGroup, SourceUser, SyncFailure, SyncResult
from .state_store import StateStore


class SyncEngine:
    def __init__(
        self,
        scim_client: GitHubScimClient,
        state_store: StateStore,
        dry_run: bool = True,
        hard_delete_removed_users: bool = False,
        group_delete_grace_runs: int = 2,
        group_delete_max_percent: int = 20,
        enterprise_admin_upns: FrozenSet[str] = frozenset(),
    ):
        self.scim_client = scim_client
        self.state_store = state_store
        self.dry_run = dry_run
        self.hard_delete_removed_users = hard_delete_removed_users
        self.group_delete_grace_runs = group_delete_grace_runs
        self.group_delete_max_percent = group_delete_max_percent
        self.enterprise_admin_upns = enterprise_admin_upns

    @staticmethod
    def _build_user_state_snapshot(source_users: List[SourceUser]) -> Dict[str, Dict[str, str]]:
        snapshot = {}
        for user in source_users:
            if not user.id:
                continue
            snapshot[user.id] = {
                "external_id": user.id,
                "user_principal_name": user.user_principal_name,
                "display_name": user.display_name,
            }
        return snapshot

    @staticmethod
    def _build_group_state_snapshot(
        source_groups: List[SourceGroup],
        resolved_group_name_map: Dict[str, str],
    ) -> Dict[str, Dict[str, str]]:
        configured_name_by_id = {
            group_id: configured_name for configured_name, group_id in resolved_group_name_map.items()
        }
        snapshot = {}
        for group in source_groups:
            if not group.id:
                continue
            entry = {
                "external_id": group.id,
                "display_name": group.display_name,
            }
            configured_name = configured_name_by_id.get(group.id)
            if configured_name:
                entry["configured_name"] = configured_name
            snapshot[group.id] = entry
        return snapshot

    @staticmethod
    def _merge_state_snapshot(
        persisted_external_ids: Set[str],
        desired_snapshot: Dict[str, Dict[str, str]],
        previous_snapshot: Dict[str, Dict[str, str]],
    ) -> Dict[str, Dict[str, str]]:
        merged_snapshot = {}
        for external_id in persisted_external_ids:
            entry = desired_snapshot.get(external_id) or previous_snapshot.get(external_id) or {}
            merged_snapshot[external_id] = {
                "external_id": external_id,
                **{key: value for key, value in entry.items() if key != "external_id" and value},
            }
        return merged_snapshot

    @staticmethod
    def _build_roles_payload(is_enterprise_admin: bool) -> List[Dict]:
        role_value = "enterprise_owner" if is_enterprise_admin else "user"
        return [{"value": role_value, "primary": False}]

    @classmethod
    def to_scim_user_payload(cls, user: SourceUser, is_enterprise_admin: bool = False) -> Dict:
        email_value = user.mail or user.user_principal_name
        return {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "externalId": user.id,
            "userName": user.user_principal_name,
            "displayName": user.display_name,
            "active": user.account_enabled,
            "roles": cls._build_roles_payload(is_enterprise_admin),
            "emails": [
                {
                    "value": email_value,
                    "type": "work",
                    "primary": True,
                }
            ],
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
                "department": user.department or "",
            },
        }

    @staticmethod
    def to_scim_group_payload(group: SourceGroup, member_scim_ids: List[str]) -> Dict:
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "externalId": group.id,
            "displayName": group.display_name,
            "members": [{"value": member_scim_id} for member_scim_id in member_scim_ids],
        }

    @staticmethod
    def _normalized_roles(resource: Dict) -> Set[str]:
        roles = resource.get("roles") or []
        return {
            str(role.get("value", "")).strip().lower()
            for role in roles
            if str(role.get("value", "")).strip()
        }

    @staticmethod
    def _normalized_group_members(resource: Dict) -> Set[str]:
        members = resource.get("members") or []
        return {
            str(member.get("value", "")).strip()
            for member in members
            if str(member.get("value", "")).strip()
        }

    @staticmethod
    def _record_failure(result: SyncResult, object_type: str, identifier: str, operation: str, error: Exception) -> None:
        result.failures.append(
            SyncFailure(
                object_type=object_type,
                identifier=identifier,
                operation=operation,
                message=str(error),
            )
        )

    def _log_user_change(
        self,
        action: str,
        external_id: str,
        user_name: str,
        display_name: str,
        scim_user_id: str = "",
        changed_paths: List[str] | None = None,
    ) -> None:
        message = action if not self.dry_run else f"[DRY_RUN] {action}"
        details = [
            f"externalId={external_id}",
            f"userName={user_name}",
            f"displayName={display_name}",
        ]
        if scim_user_id:
            details.insert(0, f"scimId={scim_user_id}")
        if changed_paths:
            details.append(f"changedPaths={','.join(changed_paths)}")
        logging.info("%s: %s", message, " ".join(details))

    def _needs_profile_update(self, existing: Dict, desired: Dict) -> bool:
        existing_display = existing.get("displayName") or ""
        desired_display = desired.get("displayName") or ""

        existing_user_name = existing.get("userName") or ""
        desired_user_name = desired.get("userName") or ""

        existing_external_id = existing.get("externalId") or ""
        desired_external_id = desired.get("externalId") or ""

        existing_emails = existing.get("emails") or []
        existing_email = existing_emails[0].get("value") if existing_emails else ""
        desired_email = (desired.get("emails") or [{}])[0].get("value", "")

        existing_active = bool(existing.get("active", True))
        desired_active = bool(desired.get("active", True))

        existing_roles = self._normalized_roles(existing)
        desired_roles = self._normalized_roles(desired)

        existing_enterprise = existing.get("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User") or {}
        desired_enterprise = desired.get("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User") or {}
        existing_department = existing_enterprise.get("department") or ""
        desired_department = desired_enterprise.get("department") or ""

        return (
            existing_display != desired_display
            or existing_user_name != desired_user_name
            or existing_external_id != desired_external_id
            or existing_email != desired_email
            or existing_active != desired_active
            or existing_roles != desired_roles
            or existing_department != desired_department
        )

    def _needs_group_update(self, existing: Dict, desired: Dict) -> bool:
        return (
            (existing.get("displayName") or "") != (desired.get("displayName") or "")
            or self._normalized_group_members(existing) != self._normalized_group_members(desired)
        )

    def _sync_desired_users(self, source_users: List[SourceUser], result: SyncResult) -> Tuple[Dict[str, str], Set[str]]:
        user_scim_ids: Dict[str, str] = {}
        persisted_user_external_ids: Set[str] = set()

        source_map = {user.id: user for user in source_users if user.id}

        for external_id, source_user in source_map.items():
            existing = None
            remote_exists = False
            scim_user_id = ""
            try:
                is_enterprise_admin = source_user.user_principal_name.strip().lower() in self.enterprise_admin_upns
                desired_payload = self.to_scim_user_payload(source_user, is_enterprise_admin=is_enterprise_admin)
                existing = self.scim_client.find_user_by_external_id(external_id)

                if not existing:
                    existing = self.scim_client.find_user_by_username(source_user.user_principal_name)

                if existing:
                    remote_exists = True
                    scim_user_id = existing.get("id") or ""
                    if scim_user_id:
                        user_scim_ids[external_id] = scim_user_id

                if not existing:
                    if self.dry_run:
                        scim_user_id = f"dryrun:{external_id}"
                    else:
                        created = self.scim_client.create_user(desired_payload)
                        scim_user_id = created.get("id") or ""
                        remote_exists = True
                    self._log_user_change(
                        "create user",
                        external_id=external_id,
                        user_name=source_user.user_principal_name,
                        display_name=source_user.display_name,
                        scim_user_id=scim_user_id,
                    )
                    if scim_user_id:
                        user_scim_ids[external_id] = scim_user_id
                    result.stats.user_created += 1
                    if remote_exists:
                        persisted_user_external_ids.add(external_id)
                    continue

                if remote_exists:
                    persisted_user_external_ids.add(external_id)

                scim_user_id = existing.get("id") or scim_user_id
                was_active = bool(existing.get("active", True))
                desired_active = bool(desired_payload.get("active", True))

                if self._needs_profile_update(existing, desired_payload):
                    operations = [
                        {"op": "replace", "path": "externalId", "value": desired_payload.get("externalId")},
                        {"op": "replace", "path": "userName", "value": desired_payload.get("userName")},
                        {"op": "replace", "path": "displayName", "value": desired_payload.get("displayName")},
                        {"op": "replace", "path": "emails", "value": desired_payload.get("emails")},
                        {"op": "replace", "path": "active", "value": desired_active},
                        {"op": "replace", "path": "roles", "value": desired_payload.get("roles")},
                        {
                            "op": "replace",
                            "path": "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department",
                            "value": (
                                desired_payload.get("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User") or {}
                            ).get("department", ""),
                        },
                    ]
                    changed_paths = [operation["path"] for operation in operations]
                    action = "update user"
                    if (not was_active) and desired_active:
                        action = "reactivate user"
                    elif was_active and (not desired_active):
                        action = "soft deprovision in-scope user"
                    self._log_user_change(
                        action,
                        external_id=external_id,
                        user_name=source_user.user_principal_name,
                        display_name=source_user.display_name,
                        scim_user_id=scim_user_id,
                        changed_paths=changed_paths,
                    )
                    if not self.dry_run:
                        self.scim_client.patch_user(scim_user_id, operations)
                    result.stats.user_updated += 1

                    if was_active and (not desired_active):
                        result.stats.user_soft_deprovisioned += 1

                    if (not was_active) and desired_active:
                        result.stats.user_reactivated += 1
                else:
                    result.stats.user_skipped += 1

            except Exception as error:
                logging.exception("Failed syncing user externalId=%s", external_id)
                result.stats.user_failed += 1
                if remote_exists:
                    persisted_user_external_ids.add(external_id)
                if scim_user_id:
                    user_scim_ids.setdefault(external_id, scim_user_id)
                self._record_failure(result, "user", external_id, "sync", error)

        return user_scim_ids, persisted_user_external_ids

    def _sync_removed_users(
        self,
        previous_external_ids: Set[str],
        desired_external_ids: Set[str],
        result: SyncResult,
        persisted_user_external_ids: Set[str],
        previous_user_snapshot: Dict[str, Dict[str, str]],
    ) -> None:
        removed_external_ids = previous_external_ids - desired_external_ids

        for external_id in sorted(removed_external_ids):
            try:
                existing = self.scim_client.find_user_by_external_id(external_id)
                if not existing:
                    continue

                scim_user_id = existing.get("id")
                if not scim_user_id:
                    raise RuntimeError(f"Missing GitHub SCIM user id for externalId={external_id}")

                snapshot = previous_user_snapshot.get(external_id, {})
                user_name = existing.get("userName") or snapshot.get("user_principal_name") or ""
                display_name = existing.get("displayName") or snapshot.get("display_name") or ""

                if self.hard_delete_removed_users:
                    if self.dry_run:
                        logging.info(
                            "[DRY_RUN] hard delete user: scimId=%s externalId=%s userName=%s displayName=%s",
                            scim_user_id,
                            external_id,
                            user_name,
                            display_name,
                        )
                        persisted_user_external_ids.add(external_id)
                    else:
                        logging.info(
                            "hard delete user: scimId=%s externalId=%s userName=%s displayName=%s",
                            scim_user_id,
                            external_id,
                            user_name,
                            display_name,
                        )
                        self.scim_client.delete_user(scim_user_id)
                    result.stats.user_hard_deleted += 1
                    continue

                if not bool(existing.get("active", True)):
                    logging.info(
                        "skip removed user already inactive: scimId=%s externalId=%s userName=%s displayName=%s",
                        scim_user_id,
                        external_id,
                        user_name,
                        display_name,
                    )
                    persisted_user_external_ids.add(external_id)
                    continue

                operations = [{"op": "replace", "path": "active", "value": False}]
                if self.dry_run:
                    logging.info(
                        "[DRY_RUN] soft deprovision user: scimId=%s externalId=%s userName=%s displayName=%s",
                        scim_user_id,
                        external_id,
                        user_name,
                        display_name,
                    )
                else:
                    logging.info(
                        "soft deprovision user: scimId=%s externalId=%s userName=%s displayName=%s",
                        scim_user_id,
                        external_id,
                        user_name,
                        display_name,
                    )
                    self.scim_client.patch_user(scim_user_id, operations)
                result.stats.user_soft_deprovisioned += 1
                persisted_user_external_ids.add(external_id)
            except Exception as error:
                action = "hard delete" if self.hard_delete_removed_users else "soft deprovision"
                logging.exception("Failed %s externalId=%s", action, external_id)
                result.stats.user_failed += 1
                persisted_user_external_ids.add(external_id)
                self._record_failure(result, "user", external_id, action, error)

    def _sync_groups(
        self,
        source_groups: List[SourceGroup],
        previous_group_external_ids: Set[str],
        pending_group_deletions: Dict[str, int],
        user_scim_ids: Dict[str, str],
        result: SyncResult,
    ) -> Tuple[Set[str], Dict[str, int]]:
        persisted_group_external_ids: Set[str] = set()
        desired_group_external_ids = {group.id for group in source_groups if group.id}
        next_pending_group_deletions = {
            group_id: count
            for group_id, count in pending_group_deletions.items()
            if group_id not in desired_group_external_ids
        }

        for group in source_groups:
            remote_exists = False
            try:
                missing_member_ids = [member_id for member_id in sorted(group.member_ids) if member_id not in user_scim_ids]
                if missing_member_ids:
                    raise RuntimeError(
                        "Missing GitHub SCIM user ids for group members: " + ", ".join(missing_member_ids)
                    )

                desired_payload = self.to_scim_group_payload(
                    group,
                    [user_scim_ids[member_id] for member_id in sorted(group.member_ids)],
                )
                existing = self.scim_client.find_group_by_external_id(group.id)
                if existing:
                    remote_exists = True
                    persisted_group_external_ids.add(group.id)

                if not existing:
                    if self.dry_run:
                        logging.info("[DRY_RUN] create group: externalId=%s displayName=%s", group.id, group.display_name)
                    else:
                        self.scim_client.create_group(desired_payload)
                        remote_exists = True
                    result.stats.group_created += 1
                    if remote_exists:
                        persisted_group_external_ids.add(group.id)
                    continue

                scim_group_id = existing.get("id")
                if not scim_group_id:
                    raise RuntimeError(f"Missing GitHub SCIM group id for externalId={group.id}")

                if self._needs_group_update(existing, desired_payload):
                    operations = [
                        {"op": "replace", "path": "displayName", "value": desired_payload.get("displayName")},
                        {"op": "replace", "path": "members", "value": desired_payload.get("members")},
                    ]
                    if self.dry_run:
                        logging.info("[DRY_RUN] patch group: scimId=%s externalId=%s", scim_group_id, group.id)
                    else:
                        self.scim_client.patch_group(scim_group_id, operations)
                    result.stats.group_updated += 1
                else:
                    result.stats.group_skipped += 1
            except Exception as error:
                logging.exception("Failed syncing group externalId=%s", group.id)
                result.stats.group_failed += 1
                if remote_exists:
                    persisted_group_external_ids.add(group.id)
                self._record_failure(result, "group", group.id, "sync", error)

        removed_group_external_ids = sorted(previous_group_external_ids - desired_group_external_ids)
        if not removed_group_external_ids:
            return persisted_group_external_ids, next_pending_group_deletions

        if result.stats.user_failed or result.stats.group_failed:
            reason = "Group deletions blocked because the current run contains user or group sync failures"
            result.blocked_actions.append(reason)
            result.stats.blocked_group_deletions += len(removed_group_external_ids)
            for group_id in removed_group_external_ids:
                persisted_group_external_ids.add(group_id)
                if group_id in pending_group_deletions:
                    next_pending_group_deletions[group_id] = pending_group_deletions[group_id]
            return persisted_group_external_ids, next_pending_group_deletions

        if previous_group_external_ids:
            removal_percent = (len(removed_group_external_ids) * 100) / len(previous_group_external_ids)
            if removal_percent > self.group_delete_max_percent:
                reason = (
                    "Group deletions blocked because removed groups exceed the configured threshold: "
                    f"removed={len(removed_group_external_ids)} total={len(previous_group_external_ids)} "
                    f"threshold={self.group_delete_max_percent}%"
                )
                result.blocked_actions.append(reason)
                result.stats.blocked_group_deletions += len(removed_group_external_ids)
                for group_id in removed_group_external_ids:
                    persisted_group_external_ids.add(group_id)
                    if group_id in pending_group_deletions:
                        next_pending_group_deletions[group_id] = pending_group_deletions[group_id]
                return persisted_group_external_ids, next_pending_group_deletions

        for group_id in removed_group_external_ids:
            current_missing_count = pending_group_deletions.get(group_id, 0) + 1
            if current_missing_count < self.group_delete_grace_runs:
                next_pending_group_deletions[group_id] = current_missing_count
                persisted_group_external_ids.add(group_id)
                result.stats.blocked_group_deletions += 1
                result.blocked_actions.append(
                    f"Group deletion postponed for externalId={group_id}: grace run {current_missing_count}/{self.group_delete_grace_runs}"
                )
                continue

            try:
                existing = self.scim_client.find_group_by_external_id(group_id)
                if not existing:
                    continue

                scim_group_id = existing.get("id")
                if not scim_group_id:
                    raise RuntimeError(f"Missing GitHub SCIM group id for externalId={group_id}")

                if self.dry_run:
                    logging.info("[DRY_RUN] delete group: scimId=%s externalId=%s", scim_group_id, group_id)
                    persisted_group_external_ids.add(group_id)
                else:
                    self.scim_client.delete_group(scim_group_id)
                result.stats.group_deleted += 1
                next_pending_group_deletions.pop(group_id, None)
            except Exception as error:
                logging.exception("Failed deleting group externalId=%s", group_id)
                result.stats.group_failed += 1
                persisted_group_external_ids.add(group_id)
                next_pending_group_deletions[group_id] = current_missing_count
                self._record_failure(result, "group", group_id, "delete", error)

        return persisted_group_external_ids, next_pending_group_deletions

    def sync(
        self,
        source_users: List[SourceUser],
        source_groups: List[SourceGroup],
        resolved_group_name_map: Dict[str, str],
        run_id: str,
    ) -> SyncResult:
        result = SyncResult()
        state = self.state_store.load()
        previous_user_external_ids = set(state.synced_user_external_ids)
        previous_group_external_ids = set(state.synced_group_external_ids)
        previous_user_snapshot = dict(state.synced_users)
        previous_group_snapshot = dict(state.synced_groups)
        pending_group_deletions = dict(state.pending_group_deletions)
        desired_user_external_ids = {user.id for user in source_users if user.id}
        desired_user_snapshot = self._build_user_state_snapshot(source_users)
        desired_group_snapshot = self._build_group_state_snapshot(source_groups, resolved_group_name_map)

        user_scim_ids, persisted_user_external_ids = self._sync_desired_users(source_users, result)
        self._sync_removed_users(
            previous_user_external_ids,
            desired_user_external_ids,
            result,
            persisted_user_external_ids,
            previous_user_snapshot,
        )
        persisted_group_external_ids, next_pending_group_deletions = self._sync_groups(
            source_groups,
            previous_group_external_ids,
            pending_group_deletions,
            user_scim_ids,
            result,
        )
        persisted_user_snapshot = self._merge_state_snapshot(
            persisted_user_external_ids,
            desired_user_snapshot,
            previous_user_snapshot,
        )
        persisted_group_snapshot = self._merge_state_snapshot(
            persisted_group_external_ids,
            desired_group_snapshot,
            previous_group_snapshot,
        )

        if self.dry_run:
            logging.info("[DRY_RUN] skip state persistence")
            return result

        self.state_store.save_state(
            synced_user_external_ids=persisted_user_external_ids,
            synced_group_external_ids=persisted_group_external_ids,
            synced_users=persisted_user_snapshot,
            synced_groups=persisted_group_snapshot,
            resolved_group_name_map=resolved_group_name_map,
            pending_group_deletions=next_pending_group_deletions,
            run_id=run_id,
            run_status="success" if not result.failures else "partial_failure",
        )
        return result
