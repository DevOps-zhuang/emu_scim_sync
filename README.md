# EMU SCIM Sync PoC (21V Entra -> GitHub Enterprise Managed Users)

This PoC implements user and group lifecycle sync from 21V Entra ID to GitHub EMU using SCIM REST API.

## Scope

- Sync users from multiple Entra security groups configured by Entra group displayName.
- Only direct members are in scope. Nested groups are not expanded.
- User lifecycle: create, update, soft deprovision (active=false), reactivate, and optional hard delete for removed users.
- Group lifecycle: create, update, and protected delete for GitHub Enterprise SCIM Groups.
- Timer-based execution (every 15 minutes by default).
- Idempotent and safe by default (`DRY_RUN=true`).

## Why this design

- 21V Entra does not provide the built-in SCIM app provisioning path in this scenario.
- GitHub EMU supports SCIM 2.0 through REST API.
- Official GitHub SCIM constraints are applied:
  - Use Personal Access Token (classic) with `scim:enterprise`.
  - Always send `User-Agent`.
  - Keep one write source to SCIM endpoints.

## Quick start

1. Copy `.env.example` to `.env` and fill values.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run local sync once:

```bash
python -m src.main
```

4. For a Chinese step-by-step setup guide for Entra app registration and `.env` values, see [docs/entra-id-app-registration-guide.zh-cn.md](docs/entra-id-app-registration-guide.zh-cn.md).

## End-to-end run guide

This section describes a practical end-to-end flow for local validation before enabling real writes.

### 1. Prepare Entra and GitHub inputs

- Create or confirm one Entra app registration with Microsoft Graph application permissions.
- Confirm the Entra security groups that define the sync scope.
- Confirm a GitHub classic PAT with `scim:enterprise`.
- Confirm your GitHub Enterprise slug.

### 2. Prepare `.env`

Set at least the following values:

```dotenv
ENTRA_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ENTRA_CLIENT_SECRET=your-secret
ENTRA_SYNC_GROUP_NAMES=GitHub-EMU-Platform,GitHub-EMU-SRE
GITHUB_ENTERPRISE=your-enterprise-slug
GITHUB_PAT=ghp_xxx
DRY_RUN=true
HARD_DELETE_REMOVED_USERS=false
GROUP_DELETE_GRACE_RUNS=2
GROUP_DELETE_MAX_PERCENT=20
```

Recommended first-run posture:

- Keep `DRY_RUN=true`
- Keep `HARD_DELETE_REMOVED_USERS=false`
- Keep the default group delete protection settings
- Keep `STATE_STORE_BACKEND=local_json`
- Start with `LOG_FORMAT=text` locally, or `LOG_FORMAT=json` if you want machine-readable output

### 3. Run one dry-run sync

```bash
python -m src.main
```

Expected behavior:

- The process resolves each configured Entra group by display name.
- Only direct members are collected.
- Users are deduplicated across groups.
- Intended user and group writes are logged.
- No SCIM write is executed.
- No local state file is updated.

### 4. Review logs

Look for these stages in the output:

- `sync_run_started`
- `entra_group_resolved`
- `desired_state_built`
- `sync_failure` when an object-level error occurs
- `group_delete_blocked` when a delete protection rule prevents group deletion
- `sync_run_completed`

User-level write operations are also logged when they occur, including create, update, reactivate, soft deprovision, and removed-user skip paths.

The final summary line includes:

- `run_id`
- start and end timestamps
- user counts
- group counts
- blocked group deletion count

### Logging options

The syncer now supports a local-first logging configuration that also works in Azure Functions.

- `LOG_FORMAT=text`
  - Best for local interactive troubleshooting
- `LOG_FORMAT=json`
  - Best when logs are shipped to a collector or parsed in Azure-hosted environments
- `LOG_FILE`
  - Base local log file path used to generate one timestamped file per run
- `LOG_FILE_MAX_BYTES`
  - Reserved for backward compatibility in the current per-run file mode
- `LOG_FILE_BACKUP_COUNT`
  - Number of per-run log files to retain locally

Local recommendation:

```dotenv
LOG_FORMAT=text
LOG_FILE=logs/emu_scim_sync.log
LOG_FILE_MAX_BYTES=1048576
LOG_FILE_BACKUP_COUNT=5
```

When `LOG_FILE` is configured, the syncer writes one timestamped file per run.
Examples:

```text
logs/emu_scim_sync_20260322_090015_dryrun.log
logs/emu_scim_sync_20260322_090220_apply.log
```

The active file path is also emitted in the `sync_run_started` event.
The syncer also refreshes a stable latest file for quick inspection:

```text
logs/emu_scim_sync_latest.log
```

Old per-run files are pruned according to `LOG_FILE_BACKUP_COUNT`.

Azure Functions recommendation:

- Prefer stdout logging
- Set `LOG_FORMAT=json`
- Leave `LOG_FILE` empty unless you explicitly understand the filesystem tradeoffs

### 5. Enable real writes

After the dry run output matches expectations:

```dotenv
DRY_RUN=false
```

Run again:

```bash
python -m src.main
```

Expected behavior:

- Users are created or updated in GitHub EMU SCIM.
- Entra groups are created or updated as GitHub Enterprise SCIM Groups.
- The state file is persisted to `STATE_FILE`.
- The persisted state includes schema and run metadata such as the last successful run id and backend.
- The state file is not a log file. It also stores readable snapshots under `synced_users` and `synced_groups` so operators can inspect names alongside Entra external IDs.

### 6. Validate results

Validate at least these points:

- New users appear in GitHub Enterprise Managed Users.
- Disabled in-scope users are set to `active=false`.
- Users removed from all configured groups are soft deprovisioned by default.
- Removed-user logs include `externalId`, `userName`, and `displayName` when available, making deprovision traces easier to inspect.
- User create, update, and reactivate logs include `externalId`, `userName`, `displayName`, and changed SCIM paths.
- IdP groups appear in GitHub Enterprise SCIM Groups.
- Group members match the current direct-member set.

### 7. Validate protected deletion behavior

To validate group deletion protection safely:

- Remove one configured group from `ENTRA_SYNC_GROUP_NAMES`
- Keep `DRY_RUN=true` for the first observation pass
- Confirm the log reports postponed or blocked deletion instead of immediate removal
- Only after 2 successful runs out of scope can a real delete occur when `DRY_RUN=false`

## Deployment

## Deployment architecture

- Runtime: Azure Functions Python worker
- Trigger: root-level [function_app.py](function_app.py) imports the timer app defined in [src/function_app.py](src/function_app.py)
- Azure Functions host configuration: [host.json](host.json)
- Entry point: [src/main.py](src/main.py)
- State store: pluggable backend interface, currently implemented as local JSON through `STATE_STORE_BACKEND=local_json`
- Logging: structured text or JSON through a shared runtime logging configuration

## Deployment prerequisites

- Azure Functions environment for Python
- All environment variables from `.env.example`
- Network access to:
  - `login.partner.microsoftonline.cn`
  - `microsoftgraph.chinacloudapi.cn`
  - `api.github.com`

## Deployment configuration

Set these application settings in Azure Functions:

- `ENTRA_TENANT_ID`
- `ENTRA_CLIENT_ID`
- `ENTRA_CLIENT_SECRET`
- `ENTRA_SYNC_GROUP_NAMES`
- `GITHUB_ENTERPRISE`
- `GITHUB_PAT`
- `GITHUB_USER_AGENT`
- `DRY_RUN`
- `HARD_DELETE_REMOVED_USERS`
- `GROUP_DELETE_GRACE_RUNS`
- `GROUP_DELETE_MAX_PERCENT`
- `LOG_FORMAT`
- `LOG_FILE`
- `LOG_FILE_MAX_BYTES`
- `LOG_FILE_BACKUP_COUNT`
- `SYNC_INTERVAL_MINUTES`
- `STATE_STORE_BACKEND`
- `STATE_FILE`
- `ENTRA_TOKEN_URL` when overriding the default China endpoint
- `GRAPH_BASE_URL` when overriding the default China Graph endpoint
- `GITHUB_SCIM_BASE_URL` when overriding the default GitHub SCIM base URL

## Deployment procedure

1. Deploy the function app package.
2. Configure application settings.
3. Start with `DRY_RUN=true`.
4. Observe at least one full timer-triggered execution.
5. Validate the summary log and object-level failures.
6. Only then switch to `DRY_RUN=false`.

## Deployment cautions

- The current implemented state backend is `local_json`. It is the primary supported mode and is the safest option for local execution.
- The state layer is now abstracted so a future Azure-specific backend can be added without rewriting sync logic, but that backend is not implemented yet.
- If the function app runs on multiple instances or on ephemeral storage, state semantics can drift.
- If you use Azure Functions, prefer stdout logging with `LOG_FORMAT=json` and avoid relying on local files unless you control the runtime storage behavior.
- Keep `HARD_DELETE_REMOVED_USERS=false` unless you intentionally accept irreversible user deletion.
- Group deletion remains protected by grace runs and threshold rules, but configuration mistakes can still block or defer expected deletes.

## Azure Functions timer

`src/function_app.py` exposes a timer trigger entrypoint for scheduled sync.

- For PoC local execution, use `python -m src.main`.
- For cloud deployment, map environment variables from `.env.example`.
- `SYNC_INTERVAL_MINUTES` controls the timer schedule. Supported values are 1-59 that divide 60, or 60 for hourly execution.

## Data mapping

- `externalId` <- Entra `id`
- `userName` <- Entra `userPrincipalName`
- `displayName` <- Entra `displayName`
- `emails[0].value` <- Entra `mail` fallback to `userPrincipalName`
- `active` <- Entra `accountEnabled`
- `roles` <- `enterprise_owner` when `GITHUB_ENTERPRISE_ADMIN_UPNS` contains the Entra `userPrincipalName`, otherwise `user`

## Group sync behavior

- Configure source groups through `ENTRA_SYNC_GROUP_NAMES` with a comma-separated list of Entra group display names.
- The syncer resolves each group name to one Entra security group and fails closed when a group is missing or ambiguous.
- Users are collected from each group's direct members and deduplicated by Entra `id`.
- Entra groups are synced to GitHub Enterprise SCIM Groups as IdP groups.
- Group-to-Team binding is intentionally not automated in this phase.
- Removed groups are protected by:
  - `GROUP_DELETE_GRACE_RUNS=2`
  - `GROUP_DELETE_MAX_PERCENT=20`

The delete protection means a group must remain out of scope for 2 successful runs before deletion is allowed, and mass deletion is blocked when the removed-group ratio exceeds the configured threshold.

## Enterprise administrator sync

If you need specific Entra users to be provisioned as GitHub enterprise administrators,
set `GITHUB_ENTERPRISE_ADMIN_UPNS` in `.env` with a comma-separated list of Entra
`userPrincipalName` values.

Example:

```dotenv
GITHUB_ENTERPRISE_ADMIN_UPNS=admin1@contoso.cn,admin2@contoso.cn
```

Users in this list are provisioned through SCIM with the `enterprise_owner` role.

## Notes

- Current implementation stores sync state through a backend abstraction. The default and only implemented backend is local JSON (`STATE_STORE_BACKEND=local_json`, `STATE_FILE=...`).
- Local JSON state persistence now uses atomic replace semantics and includes run metadata such as schema version, backend, last run id, last run status, updated timestamp, and readable user/group snapshots in addition to the persisted external ID sets.
- In `DRY_RUN=true`, the sync engine logs intended user and group writes and skips state persistence.
- Removed users are soft deprovisioned by default. Set `HARD_DELETE_REMOVED_USERS=true` only when you intentionally want removed users to be permanently deleted through GitHub SCIM.
- Removed groups are not deleted immediately. The sync engine applies grace-run and percentage-threshold protection before deleting GitHub SCIM Groups.
- Production can replace this with durable storage (Table/Blob/SQL).
- Group-to-Team mapping is intentionally excluded in this phase.
