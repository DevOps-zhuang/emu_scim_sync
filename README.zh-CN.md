# EMU SCIM Sync PoC（21V Entra -> GitHub Enterprise Managed Users）

[English](README.md) | [简体中文](README.zh-CN.md)

这是一个概念验证项目，用于通过 GitHub SCIM REST API，将 21V 运营的 Microsoft Entra ID 中的用户和组生命周期数据同步到 GitHub Enterprise Managed Users。

## 范围

- 按 Entra 组 displayName 配置多个源安全组。
- 仅同步 direct members，不展开 nested groups。
- 用户生命周期支持：创建、更新、软停用（`active=false`）、重新启用，以及可选的移除后硬删除。
- 组生命周期支持：创建、更新，以及带保护策略的 GitHub Enterprise SCIM Group 删除。
- 支持本地单次执行，也支持 Azure Functions 定时执行。
- 默认安全优先，`DRY_RUN=true`。

## 设计原因

- 在当前 21V 场景下，不直接依赖内建的 partner app provisioning 路径。
- GitHub EMU 提供标准 SCIM 2.0 REST API，可由当前同步器稳定调用。
- 遵循 GitHub 官方 SCIM 约束：
  - 使用 classic PAT，并具备 `scim:enterprise`
  - 所有请求必须带 `User-Agent`
  - SCIM 写入只保留一个权威来源

## 快速开始

1. 将 `.env.example` 复制为 `.env`，并填写实际配置。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 本地执行一次同步：

```bash
python -m src.main
```

4. 如果需要中英文分步配置文档，特别是 21V Entra 的 SAML Enterprise App、GitHub SAML 配置，以及本项目所需 App Registration / Client ID / Secret 获取流程，请查看 [docs/entra-id-app-registration-guide.zh-cn.md](docs/entra-id-app-registration-guide.zh-cn.md) 和 [docs/entra-id-app-registration-guide.md](docs/entra-id-app-registration-guide.md)。

## 端到端运行说明

这部分描述一个适合本地验证的完整流程，建议先确认 dry run 结果，再开启真实写入。

### 1. 准备 Entra 与 GitHub 输入项

- 准备一个可调用 Microsoft Graph 的 Entra App Registration。
- 确认定义同步范围的 Entra 安全组。
- 准备一个具备 `scim:enterprise` 的 GitHub classic PAT。
- 确认 GitHub Enterprise slug。

### 2. 准备 `.env`

至少配置以下字段：

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

推荐首次执行策略：

- 保持 `DRY_RUN=true`
- 保持 `HARD_DELETE_REMOVED_USERS=false`
- 保持默认的组删除保护配置
- 保持 `STATE_STORE_BACKEND=local_json`
- 本地优先使用 `LOG_FORMAT=text`；如果要机器解析，则用 `LOG_FORMAT=json`

### 3. 先执行一次 dry run

```bash
python -m src.main
```

预期行为：

- 程序按 displayName 解析每个配置的 Entra 组
- 只读取各组 direct members
- 多组用户去重
- 输出即将执行的用户和组写入动作
- 不会真正调用 SCIM 写接口
- 不会更新本地状态文件

### 4. 查看日志

重点关注这些阶段事件：

- `sync_run_started`
- `entra_group_resolved`
- `desired_state_built`
- `sync_failure`
- `group_delete_blocked`
- `sync_run_completed`

如果发生用户写入动作，还会记录逐用户日志，包括 create、update、reactivate、soft deprovision 以及 removed-user skip。

最终汇总日志会包含：

- `run_id`
- 开始和结束时间
- 用户统计
- 组统计
- 被保护策略阻止的组删除数量

### 日志配置

当前日志设计同时兼容本地优先和后续 Azure Functions 使用。

- `LOG_FORMAT=text`
  - 适合本地人工排障
- `LOG_FORMAT=json`
  - 适合日志采集和云端分析
- `LOG_FILE`
  - 本地日志基路径，会自动派生每次运行的时间戳日志文件
- `LOG_FILE_MAX_BYTES`
  - 在当前按次日志模型中保留，主要用于兼容配置
- `LOG_FILE_BACKUP_COUNT`
  - 本地保留的历史按次日志数量

本地推荐：

```dotenv
LOG_FORMAT=text
LOG_FILE=logs/emu_scim_sync.log
LOG_FILE_MAX_BYTES=1048576
LOG_FILE_BACKUP_COUNT=5
```

如果配置了 `LOG_FILE`，每次运行都会生成一个带时间戳的日志文件，例如：

```text
logs/emu_scim_sync_20260322_090015_dryrun.log
logs/emu_scim_sync_20260322_090220_apply.log
```

`sync_run_started` 事件中也会记录本次实际日志文件路径。程序还会刷新一个固定名称的最新日志文件：

```text
logs/emu_scim_sync_latest.log
```

旧的按次日志会根据 `LOG_FILE_BACKUP_COUNT` 自动清理。

Azure Functions 推荐：

- 优先使用 stdout
- 设置 `LOG_FORMAT=json`
- 除非充分理解 Azure Functions 文件系统特性，否则不要依赖本地文件日志

### 5. 开启真实写入

当 dry run 输出符合预期后：

```dotenv
DRY_RUN=false
```

重新执行：

```bash
python -m src.main
```

预期行为：

- GitHub EMU SCIM 中的用户会被创建或更新
- Entra 组会被创建或更新为 GitHub Enterprise SCIM Groups
- 状态文件写入 `STATE_FILE`
- 状态文件中保留 schema、run metadata，以及可读的 `synced_users`、`synced_groups`

### 6. 验证结果

至少确认以下内容：

- 新用户已出现在 GitHub Enterprise Managed Users 中
- 同步范围内已禁用用户被设置为 `active=false`
- 从所有配置组中移除的用户，默认会被 soft deprovision
- removed-user 日志含有 `externalId`、`userName`、`displayName`
- create、update、reactivate 日志含有 `externalId`、`userName`、`displayName` 和 `changedPaths`
- IdP groups 已出现在 GitHub Enterprise SCIM Groups 中
- 组成员和 direct-member 范围一致

### 7. 验证组删除保护

为了安全验证 group delete protection：

- 从 `ENTRA_SYNC_GROUP_NAMES` 中移除一个组
- 第一轮观察保持 `DRY_RUN=true`
- 确认日志显示 postponed 或 blocked，而不是立即删除
- 只有当该组连续 2 次成功运行都在 scope 外，且 `DRY_RUN=false` 时，才会发生真实删除

## 部署

## 部署架构

- 运行时：Azure Functions Python worker
- 触发入口：根目录 [function_app.py](function_app.py) 引入 [src/function_app.py](src/function_app.py) 中的 timer app
- Azure Functions host 配置： [host.json](host.json)
- 本地入口： [src/main.py](src/main.py)
- 状态存储：可插拔接口，目前实现为 `STATE_STORE_BACKEND=local_json`
- 日志：共享的结构化 text/json 运行日志配置

## 部署前提

- Azure Functions Python 运行环境
- `.env.example` 中全部必要环境变量
- 可以访问以下地址：
  - `login.partner.microsoftonline.cn`
  - `microsoftgraph.chinacloudapi.cn`
  - `api.github.com`

## Azure Functions 应用设置

需要配置以下应用设置：

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
- `ENTRA_TOKEN_URL`
- `GRAPH_BASE_URL`
- `GITHUB_SCIM_BASE_URL`

## 部署步骤

1. 部署 Function App 包。
2. 配置应用设置。
3. 初始保持 `DRY_RUN=true`。
4. 至少观察一轮完整的定时执行。
5. 检查汇总日志和对象级失败日志。
6. 确认无误后再切换 `DRY_RUN=false`。

## 部署注意事项

- 当前唯一实现并经过验证的状态后端是 `local_json`。
- 状态层已抽象，未来可增加 Azure 专用持久化后端，但当前仓库未实现。
- 如果 Azure Functions 运行在多实例或临时文件系统上，本地状态语义可能漂移。
- 在 Azure Functions 中，建议优先采用 stdout + `LOG_FORMAT=json`。
- 除非明确接受不可逆删除风险，否则不要启用 `HARD_DELETE_REMOVED_USERS=true`。
- 组删除虽然有 grace runs 与阈值保护，但错误配置仍可能造成删除延后或阻断。

## Azure Functions 定时器

[src/function_app.py](src/function_app.py) 暴露了定时触发入口。

- 本地 PoC 运行方式：`python -m src.main`
- 云端部署时，将 `.env.example` 对应变量映射到应用设置
- `SYNC_INTERVAL_MINUTES` 支持 1-59 中能整除 60 的值，或 `60` 表示每小时执行一次

## 数据映射

- `externalId` <- Entra `id`
- `userName` <- Entra `userPrincipalName`
- `displayName` <- Entra `displayName`
- `emails[0].value` <- Entra `mail`，为空时回退到 `userPrincipalName`
- `active` <- Entra `accountEnabled`
- `roles` <- 如果 `GITHUB_ENTERPRISE_ADMIN_UPNS` 包含该 `userPrincipalName` 则为 `enterprise_owner`，否则为 `user`

## 组同步行为

- 通过 `ENTRA_SYNC_GROUP_NAMES` 配置源组列表，多个 displayName 用逗号分隔。
- 程序会把每个组名解析到唯一的 Entra security group，若缺失或歧义会 fail closed。
- 用户从各组 direct members 收集，并按 Entra `id` 去重。
- Entra 组会同步到 GitHub Enterprise SCIM Groups。
- 当前阶段不自动做 group-to-team 绑定。
- 删除保护使用：
  - `GROUP_DELETE_GRACE_RUNS=2`
  - `GROUP_DELETE_MAX_PERCENT=20`

这意味着：某个组必须连续 2 次成功运行都在同步范围外，才可能删除；当移除组占比超过阈值时，程序会阻止批量删除。

## 企业管理员同步

如果希望某些 Entra 用户被同步为 GitHub enterprise administrator，可在 `.env` 中配置 `GITHUB_ENTERPRISE_ADMIN_UPNS`，填写逗号分隔的 Entra `userPrincipalName`。

示例：

```dotenv
GITHUB_ENTERPRISE_ADMIN_UPNS=admin1@contoso.cn,admin2@contoso.cn
```

这些用户在通过 SCIM 创建或更新时，会带上 `enterprise_owner` 角色。

## 备注

- 默认状态持久化使用本地 JSON：`STATE_STORE_BACKEND=local_json`
- 本地 JSON 写入采用原子替换，并保存 schema version、backend、last run id、last run status、updated timestamp 以及可读的用户/组快照
- 在 `DRY_RUN=true` 下，只记录预期写入，不持久化状态
- 默认移除用户走 soft deprovision；只有显式设置 `HARD_DELETE_REMOVED_USERS=true` 才会做硬删除
- 移除组不会被立即删除，而是先经过 grace-run 和比例阈值保护
- 如果未来要生产化，可以替换为 Blob/Table/SQL 等持久化后端
- 当前阶段明确不包含 group-to-team 自动映射