import os
from dataclasses import dataclass
from typing import FrozenSet, Tuple

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    log_level: str
    log_format: str
    log_file: str
    log_file_max_bytes: int
    log_file_backup_count: int
    sync_interval_minutes: int
    dry_run: bool
    hard_delete_removed_users: bool
    group_delete_grace_runs: int
    group_delete_max_percent: int
    state_store_backend: str
    entra_tenant_id: str
    entra_client_id: str
    entra_client_secret: str
    entra_token_url_template: str
    graph_base_url: str
    entra_sync_group_names: Tuple[str, ...]
    github_enterprise: str
    github_scim_base_url_template: str
    github_pat: str
    github_user_agent: str
    github_enterprise_admin_upns: FrozenSet[str]
    state_file: str

    @property
    def entra_token_url(self) -> str:
        return self.entra_token_url_template.format(tenant_id=self.entra_tenant_id)

    @property
    def github_scim_base_url(self) -> str:
        return self.github_scim_base_url_template.format(enterprise=self.github_enterprise)


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_csv_set(value: str) -> FrozenSet[str]:
    if not value:
        return frozenset()
    return frozenset(item.strip().lower() for item in value.split(",") if item.strip())


def _to_csv_tuple(value: str) -> Tuple[str, ...]:
    if not value:
        return tuple()

    items = []
    seen = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        dedupe_key = item.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(item)
    return tuple(items)


def _to_int(value: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def load_settings() -> Settings:
    load_dotenv()

    return Settings(
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_format=os.getenv("LOG_FORMAT", "text").strip().lower(),
        log_file=os.getenv("LOG_FILE", "").strip(),
        log_file_max_bytes=_to_int(os.getenv("LOG_FILE_MAX_BYTES", "1048576"), default=1048576),
        log_file_backup_count=_to_int(os.getenv("LOG_FILE_BACKUP_COUNT", "5"), default=5),
        sync_interval_minutes=_to_int(os.getenv("SYNC_INTERVAL_MINUTES", "15"), default=15),
        dry_run=_to_bool(os.getenv("DRY_RUN", "true"), default=True),
        hard_delete_removed_users=_to_bool(os.getenv("HARD_DELETE_REMOVED_USERS", "false"), default=False),
        group_delete_grace_runs=_to_int(os.getenv("GROUP_DELETE_GRACE_RUNS", "2"), default=2),
        group_delete_max_percent=_to_int(os.getenv("GROUP_DELETE_MAX_PERCENT", "20"), default=20),
        state_store_backend=os.getenv("STATE_STORE_BACKEND", "local_json").strip().lower(),
        entra_tenant_id=os.getenv("ENTRA_TENANT_ID", ""),
        entra_client_id=os.getenv("ENTRA_CLIENT_ID", ""),
        entra_client_secret=os.getenv("ENTRA_CLIENT_SECRET", ""),
        entra_token_url_template=os.getenv(
            "ENTRA_TOKEN_URL",
            "https://login.partner.microsoftonline.cn/{tenant_id}/oauth2/v2.0/token",
        ),
        graph_base_url=os.getenv("GRAPH_BASE_URL", "https://microsoftgraph.chinacloudapi.cn/v1.0"),
        entra_sync_group_names=_to_csv_tuple(os.getenv("ENTRA_SYNC_GROUP_NAMES", "")),
        github_enterprise=os.getenv("GITHUB_ENTERPRISE", ""),
        github_scim_base_url_template=os.getenv(
            "GITHUB_SCIM_BASE_URL",
            "https://api.github.com/scim/v2/enterprises/{enterprise}",
        ),
        github_pat=os.getenv("GITHUB_PAT", ""),
        github_user_agent=os.getenv("GITHUB_USER_AGENT", "emu-scim-sync/0.1"),
        github_enterprise_admin_upns=_to_csv_set(os.getenv("GITHUB_ENTERPRISE_ADMIN_UPNS", "")),
        state_file=os.getenv("STATE_FILE", "state/sync_state.json"),
    )


def validate_settings(settings: Settings) -> None:
    required = {
        "ENTRA_TENANT_ID": settings.entra_tenant_id,
        "ENTRA_CLIENT_ID": settings.entra_client_id,
        "ENTRA_CLIENT_SECRET": settings.entra_client_secret,
        "ENTRA_SYNC_GROUP_NAMES": ",".join(settings.entra_sync_group_names),
        "GITHUB_ENTERPRISE": settings.github_enterprise,
        "GITHUB_PAT": settings.github_pat,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    if settings.sync_interval_minutes <= 0:
        raise ValueError("SYNC_INTERVAL_MINUTES must be a positive integer")

    if settings.sync_interval_minutes != 60 and (
        settings.sync_interval_minutes >= 60 or 60 % settings.sync_interval_minutes != 0
    ):
        raise ValueError("SYNC_INTERVAL_MINUTES must be 1-59 and divide 60, or be exactly 60")

    if settings.group_delete_grace_runs <= 0:
        raise ValueError("GROUP_DELETE_GRACE_RUNS must be a positive integer")

    if settings.group_delete_max_percent <= 0 or settings.group_delete_max_percent > 100:
        raise ValueError("GROUP_DELETE_MAX_PERCENT must be between 1 and 100")

    if settings.log_format not in {"text", "json"}:
        raise ValueError("LOG_FORMAT must be either 'text' or 'json'")

    if settings.log_file_max_bytes <= 0:
        raise ValueError("LOG_FILE_MAX_BYTES must be a positive integer")

    if settings.log_file_backup_count < 0:
        raise ValueError("LOG_FILE_BACKUP_COUNT must be zero or a positive integer")

    if settings.state_store_backend not in {"local_json"}:
        raise ValueError("STATE_STORE_BACKEND currently supports only 'local_json'")
