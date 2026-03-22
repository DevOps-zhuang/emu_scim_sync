from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional


@dataclass(frozen=True)
class SourceUser:
    id: str
    user_principal_name: str
    display_name: str
    mail: Optional[str]
    department: Optional[str]
    account_enabled: bool


@dataclass(frozen=True)
class SourceGroup:
    id: str
    display_name: str
    member_ids: FrozenSet[str]


@dataclass(frozen=True)
class ResolvedGroup:
    configured_name: str
    id: str
    display_name: str


@dataclass(frozen=True)
class SyncFailure:
    object_type: str
    identifier: str
    operation: str
    message: str


@dataclass
class SyncStats:
    user_created: int = 0
    user_updated: int = 0
    user_soft_deprovisioned: int = 0
    user_hard_deleted: int = 0
    user_reactivated: int = 0
    user_skipped: int = 0
    user_failed: int = 0
    group_created: int = 0
    group_updated: int = 0
    group_deleted: int = 0
    group_skipped: int = 0
    group_failed: int = 0
    blocked_group_deletions: int = 0


@dataclass
class SyncResult:
    stats: SyncStats = field(default_factory=SyncStats)
    failures: List[SyncFailure] = field(default_factory=list)
    blocked_actions: List[str] = field(default_factory=list)
