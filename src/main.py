import logging
from datetime import datetime, timezone
from uuid import uuid4

from .config import load_settings, validate_settings
from .graph_client import EntraGraphClient
from .github_scim_client import GitHubScimClient
from .models import SourceGroup
from .runtime_logging import configure_logging, log_event
from .state_store import create_state_store
from .sync_engine import SyncEngine


logger = logging.getLogger(__name__)


def run_once() -> int:
    settings = load_settings()
    validate_settings(settings)
    active_log_file = configure_logging(settings)

    run_id = uuid4().hex[:12]
    started_at = datetime.now(timezone.utc)
    state_store = create_state_store(settings.state_store_backend, settings.state_file)

    log_event(
        logger,
        logging.INFO,
        "sync_run_started",
        run_id=run_id,
        dry_run=settings.dry_run,
        group_names=list(settings.entra_sync_group_names),
        log_file=active_log_file,
        state_store=state_store.describe(),
    )

    graph_client = EntraGraphClient(
        tenant_id=settings.entra_tenant_id,
        client_id=settings.entra_client_id,
        client_secret=settings.entra_client_secret,
        token_url=settings.entra_token_url,
        graph_base_url=settings.graph_base_url,
    )

    scim_client = GitHubScimClient(
        base_url=settings.github_scim_base_url,
        pat=settings.github_pat,
        user_agent=settings.github_user_agent,
    )

    engine = SyncEngine(
        scim_client=scim_client,
        state_store=state_store,
        dry_run=settings.dry_run,
        hard_delete_removed_users=settings.hard_delete_removed_users,
        group_delete_grace_runs=settings.group_delete_grace_runs,
        group_delete_max_percent=settings.group_delete_max_percent,
        enterprise_admin_upns=settings.github_enterprise_admin_upns,
    )

    try:
        resolved_groups = graph_client.resolve_groups_by_display_names(list(settings.entra_sync_group_names))
        resolved_group_name_map = {
            resolved_group.configured_name: resolved_group.id for resolved_group in resolved_groups
        }

        source_user_map = {}
        source_groups = []
        for resolved_group in resolved_groups:
            group_users = graph_client.list_users_in_group(resolved_group.id)
            member_ids = set()
            for user in group_users:
                if not user.id:
                    continue
                source_user_map[user.id] = user
                member_ids.add(user.id)

            source_groups.append(
                SourceGroup(
                    id=resolved_group.id,
                    display_name=resolved_group.display_name,
                    member_ids=frozenset(member_ids),
                )
            )

            log_event(
                logger,
                logging.INFO,
                "entra_group_resolved",
                run_id=run_id,
                configured_name=resolved_group.configured_name,
                group_id=resolved_group.id,
                direct_user_count=len(member_ids),
            )
    except Exception:
        log_event(logger, logging.ERROR, "graph_fetch_failed", run_id=run_id)
        logger.exception("graph_fetch_failed")
        return 1

    source_users = list(source_user_map.values())
    log_event(
        logger,
        logging.INFO,
        "desired_state_built",
        run_id=run_id,
        users=len(source_users),
        groups=len(source_groups),
    )

    result = engine.sync(source_users, source_groups, resolved_group_name_map, run_id=run_id)
    ended_at = datetime.now(timezone.utc)

    for blocked_action in result.blocked_actions:
        log_event(logger, logging.WARNING, "group_delete_blocked", run_id=run_id, reason=blocked_action)

    for failure in result.failures:
        log_event(
            logger,
            logging.ERROR,
            "sync_failure",
            run_id=run_id,
            object_type=failure.object_type,
            identifier=failure.identifier,
            operation=failure.operation,
            error_message=failure.message,
        )

    log_event(
        logger,
        logging.INFO,
        "sync_run_completed",
        run_id=run_id,
        started_at=started_at.isoformat(),
        ended_at=ended_at.isoformat(),
        users=len(source_users),
        groups=len(source_groups),
        user_created=result.stats.user_created,
        user_updated=result.stats.user_updated,
        user_soft_deprovisioned=result.stats.user_soft_deprovisioned,
        user_hard_deleted=result.stats.user_hard_deleted,
        user_reactivated=result.stats.user_reactivated,
        user_skipped=result.stats.user_skipped,
        user_failed=result.stats.user_failed,
        group_created=result.stats.group_created,
        group_updated=result.stats.group_updated,
        group_deleted=result.stats.group_deleted,
        group_skipped=result.stats.group_skipped,
        group_failed=result.stats.group_failed,
        blocked_group_deletions=result.stats.blocked_group_deletions,
    )

    return 0 if not result.failures else 1


if __name__ == "__main__":
    raise SystemExit(run_once())
