# Entra 到 GitHub EMU 完整用户生命周期同步计划与需求文档

## 文档信息

- 文档状态：评审版
- 文档目的：冻结当前阶段的范围、需求、验收标准和实施顺序，作为后续开发的唯一基线
- 适用范围：多个 Entra 安全组到 GitHub Enterprise Managed Users 的单向用户与组生命周期同步
- 当前阶段定位：先评审需求，再继续实现或调整已实现内容

## 1. 背景

当前仓库是一个 PoC，用于在中国区 21V 运营的 Microsoft Entra ID 场景下，通过自研程序调用 Microsoft Graph 和 GitHub EMU SCIM REST API，实现 Entra 用户到 GitHub Enterprise Managed Users 的生命周期同步。

之所以采用该方案，是因为当前业务场景不能直接依赖内置的 Entra 到 GitHub EMU 标准预配路径，需要自建同步器补足该能力。

现有代码已经具备以下基础能力：

- 从单个 Entra 安全组读取用户
- 基于 externalId 和 userName 进行匹配
- 支持创建、更新、软停用、重新启用
- 支持通过配置把指定用户同步为 GitHub enterprise administrator
- 支持 DRY_RUN 安全演练模式

但在进入下一阶段之前，必须先把完整生命周期同步的目标、边界、删除语义、组同步语义、配置约束、日志审计要求、验收标准和实施顺序明确下来，避免实现再次偏离需求。

## 2. 文档目标

本文件用于明确以下目标：

1. 定义“完整用户生命周期同步”在本项目中的确切含义。
2. 明确与标准 Entra Provisioning 行为保持一致的部分，以及基于项目约束做出的裁剪。
3. 明确系统应支持多个 Entra 安全组，并以 Entra group name 作为配置输入，而不是直接配置 group id。
4. 明确需要把 Entra 组对象和组成员关系同步到 GitHub Enterprise SCIM Groups（IdP group 存储层），供 GitHub 管理员后续手动映射到 Enterprise Team 或 Organization Team。
5. 明确删除策略采用“默认软停用，硬删除受控可选”的安全基线。
6. 形成后续实现与回归测试的验收依据。

## 3. 术语说明

- Entra：指中国区 21V 运营的 Microsoft Entra ID。
- GitHub EMU：指 GitHub Enterprise Managed Users。
- SCIM：指 GitHub EMU 支持的 SCIM 2.0 REST API。
- source user：从 Entra 安全组读取到的用户对象。
- source group：从 Entra 读取到的安全组对象。
- provision：将源端用户映射到 GitHub EMU 的外部身份并写入目标端。
- group provision：将源端 Entra 组及其成员关系映射到 GitHub Enterprise SCIM Group。
- soft deprovision：通过 PATCH 设置 active=false，使用户被挂起但保留可恢复身份信息。
- hard delete：通过 DELETE 永久删除 GitHub SCIM 外部身份，此操作不可逆。
- in scope：用户属于当前配置的同步范围。
- out of scope：用户离开当前配置的同步范围。

除非另有说明，本文中“将 Entra 组同步到 GitHub Enterprise”均特指“将 Entra 组及其成员关系写入 GitHub Enterprise SCIM Groups（IdP group 存储层）”，不包含自动创建 Team、自动绑定 Team 或自动管理 Team 成员关系。

## 4. 本阶段范围

### 4.1 在范围内

本阶段明确纳入以下能力：

- 多个 Entra 安全组读取
- 通过 Entra group name 解析实际组对象
- 仅基于 direct members 计算用户与组成员范围
- 基于 externalId 的主匹配和基于 userName 的回退匹配
- 用户创建
- 用户属性更新
- 源端账号禁用导致的软停用
- 用户离开同步范围导致的默认软停用
- 已软停用用户重新回到范围内后的重新启用
- 受控可选的硬删除能力
- 将 Entra 安全组作为 IdP groups 同步到 GitHub Enterprise SCIM Groups
- 将 Entra 安全组的 direct members 成员关系同步到对应的 GitHub Enterprise SCIM Groups
- enterprise administrator 角色映射
- DRY_RUN 演练模式
- 定时执行和本地单次执行
- 本地状态文件驱动的范围差异识别
- 每次同步的运行日志、结果日志和失败日志

这里的“组同步”仅指把 Entra 安全组及其成员关系写入 GitHub Enterprise 的 SCIM Group 存储层，使 GitHub Enterprise 能显示这些 IdP groups，并供管理员后续手动连接到 Enterprise Team 或 Organization Team。

### 4.2 不在范围内

本阶段明确不纳入以下能力：

- group-to-team 自动映射
- GitHub 团队成员关系自动管理
- GitHub 到 Entra 的双向同步
- 外部数据库、Blob、Table 等生产化状态存储替换
- Webhook 驱动或实时事件驱动同步
- 任意自定义字段映射框架
- 复杂冲突解决引擎

也就是说：

- 在范围内的是“Entra 组和成员关系同步到 GitHub Enterprise SCIM Groups”
- 不在范围内的是“程序自动把这些 IdP groups 绑定到 GitHub Team”

Team 与 IdP group 的连接动作仍由 GitHub 管理员在 GitHub Enterprise 侧手动完成。

## 5. 标准对齐原则

本项目应参考标准 Entra Provisioning 行为，但不机械复制，采用以下对齐原则：

1. 同步方向保持单向，以 Entra 为唯一写入源。
2. externalId 作为主身份锚点，优先保证幂等和稳定性。
3. 多组同步范围仅基于 direct members，不考虑 nested groups 或 transitive members 传播。
4. 用户处于 out-of-scope 或 disabled 时，默认进入软停用语义。
5. Entra 组必须先在 GitHub Enterprise SCIM Groups（IdP group 存储层）中存储，供后续管理员手动连接团队。
6. 永久删除必须视为高风险能力，仅在显式配置下启用。
7. 组删除属于高风险操作，必须增加缓冲、熔断和失败关闭保护，而不是即时删除。
8. DRY_RUN 默认开启，任何新增写操作都必须先支持 DRY_RUN 观测。
9. 所有需求都必须可映射为后续测试或日志验证，不允许只有描述没有验收口径。

## 6. 当前实现现状

当前代码基线已具备以下事实能力：

- 从单一 Entra 组读取 transitive members 中的用户
- externalId 优先匹配，userName 回退匹配
- 创建、更新、软停用、重新启用
- enterprise_owner 角色映射
- DRY_RUN 不持久化 state
- 用户离组后的默认软停用
- 通过配置启用硬删除
- 通过配置控制 Azure Functions 执行周期

当前代码基线尚未满足以下新增需求：

- 仍然按单个 Entra group id 配置，而不是多个 group name
- 尚未把 Entra 组同步到 GitHub Enterprise SCIM Groups（IdP group 存储层）
- 尚未显式支持组成员关系同步到 GitHub Enterprise SCIM Groups（IdP group 存储层）
- 当前用户范围仍基于 transitiveMembers 读取，不符合“只考虑 direct members”的新基线
- 当前日志偏摘要，缺少面向每次同步运行的完整成功/失败明细

当前仍未纳入本阶段范围的项不应被误写为缺陷，例如 group-to-team 自动映射、双向同步、外部状态存储替换。

## 7. 详细功能需求

### 7.1 源数据范围需求

系统必须支持从多个配置化的 Entra 安全组读取用户和组信息。

系统必须允许运维或管理员配置一组 Entra group name，而不是直接输入 group id。

推荐配置项命名应改为 ENTRA_SYNC_GROUP_NAMES。

该配置项应支持多个组名输入，推荐格式为逗号分隔列表。

系统必须在运行开始阶段先根据配置的 group name 解析出实际的 Entra 组对象。

当前阶段应支持 Entra security group 和 distribution group，并继续保持与 GitHub IdP group 同步流程兼容。

系统必须只读取每个目标组的 direct members，不得把 nested groups 的 transitive members 自动展开纳入同步范围。

系统必须把所有已解析组中的 direct members 用户集合并为当前运行时的 desired user state，并把所有已解析组集合作为 desired group state。

当用户存在于多个配置组中时，系统必须按用户维度去重，不得重复创建或重复更新同一用户。

### 7.2 组名解析与配置需求

系统必须基于 Entra group displayName 进行组解析。

当某个配置的 group name 未找到对应 Entra 组时，系统必须将本轮运行标记为配置或数据错误，并在日志中记录缺失组名。

当某个配置的 group name 解析到多个 Entra 组时，系统必须失败关闭，不得在歧义情况下继续同步。

系统必须在日志中记录每个配置组名的解析结果，包括：

- 配置的组名
- 解析得到的组 id
- 解析状态

### 7.3 用户标识与匹配需求

系统必须优先使用 Entra user.id 映射到 GitHub SCIM externalId 进行匹配。

当 externalId 未匹配到目标用户时，系统必须使用 Entra userPrincipalName 映射到 GitHub SCIM userName 进行回退查询。

当 userName 回退匹配成功时，系统必须在后续更新中把 externalId 回填到目标用户，避免后续继续依赖回退匹配。

email 字段仅用于属性映射，不得作为主身份匹配键。

### 7.4 用户创建需求

当 source user 在当前运行中属于 in scope，且目标端既不存在 externalId 匹配用户，也不存在 userName 匹配用户时，系统必须创建该用户。

创建时至少必须映射以下字段：

- externalId
- userName
- displayName
- emails
- active
- roles
- department

在 DRY_RUN 模式下，系统必须仅记录拟创建操作，不得执行真实写入。

### 7.5 用户更新需求

当目标用户已存在且任一受管属性发生变化时，系统必须执行 PATCH 更新。

当前受管属性至少包括：

- externalId
- userName
- displayName
- emails
- active
- roles
- department

当属性未发生变化时，系统不得执行多余写入。

### 7.6 用户软停用需求

系统必须在以下场景执行软停用：

1. 用户仍在同步组内，但源端 accountEnabled=false。
2. 用户离开同步范围，且未启用硬删除策略。

软停用必须通过 PATCH 设置 active=false 完成。

软停用后，系统必须保留该用户作为历史已同步用户的识别能力，以支持后续重新启用或审计。

### 7.7 用户重新启用需求

当用户重新回到同步范围，且源端 accountEnabled=true，而目标端当前为 active=false 时，系统必须重新启用该用户。

重新启用必须视为更新路径的一部分处理，并同时补齐当前受管属性。

### 7.8 受控硬删除需求

系统必须支持受控硬删除能力，但默认配置必须关闭。

硬删除的推荐触发条件仅限于：

- 用户离开同步范围，且配置 HARD_DELETE_REMOVED_USERS=true。

硬删除必须调用 GitHub SCIM DELETE /Users/{scim_user_id}。

硬删除能力必须满足以下约束：

1. 默认关闭。
2. DRY_RUN 模式下只记录拟删除动作，不得真实执行。
3. 不得把“源端 disabled”直接等同于永久删除。
4. 必须在日志中明确记录 hard delete 动作类型。

### 7.9 角色同步需求

系统必须支持把指定 Entra 用户同步为 GitHub enterprise administrator。

当前阶段角色来源限定为配置项 GITHUB_ENTERPRISE_ADMIN_UPNS 中的 userPrincipalName 列表。

当用户在该列表中时，roles 必须映射为 enterprise_owner；否则必须映射为 user。

### 7.10 组同步需求

系统必须把已解析的 Entra 安全组同步到 GitHub Enterprise SCIM Groups。

组同步的目标不是自动建立 Team 映射，而是让 GitHub Enterprise 存储这些 IdP groups，供 GitHub 管理员后续手动将其连接到 Enterprise Team 或 Organization Team。

因此，本阶段对“组”的支持边界应明确理解为两层：

1. 程序负责把 Entra 安全组和 direct members 成员关系同步为 GitHub Enterprise SCIM Groups。
2. 程序不负责自动创建 Team，也不负责自动把 SCIM Group 绑定到 Team。

组同步时至少必须映射以下字段：

- externalId，对应 Entra group id
- displayName，对应 Entra group displayName
- members，对应该组中当前 in-scope 的 direct members 用户集合

系统必须先完成用户同步，再执行组同步，保证组成员引用的都是已存在的 GitHub SCIM user id。

当 Entra 组在 GitHub Enterprise SCIM Groups 中不存在时，系统必须创建该组。

当 Entra 组已存在且组名或成员关系变化时，系统必须更新该组。

当某个历史已同步组离开配置范围时，系统必须支持受控组删除策略，但不得采用“首次发现离开范围就立即删除”的高风险策略。

为降低配置错误、组名误配或临时解析异常导致的误删风险，组删除必须满足以下保护条件：

1. 本轮运行中所有配置的 group name 都已成功解析。
2. 本轮用户同步和组同步主流程未发生全局失败。
3. 待删除组已连续多轮运行不在配置范围内，默认保护阈值应为 2 个连续成功运行周期。
4. 当单次运行检测到离开范围的组数量超过预设阈值时，系统必须熔断组删除，仅记录告警日志。
5. DRY_RUN 模式下只记录拟删除组动作，不得真实执行。

新增配置项 GROUP_DELETE_GRACE_RUNS，默认值固定为 2，用于定义连续缺失多少个成功运行周期后才允许真实删除组。

新增配置项 GROUP_DELETE_MAX_PERCENT，默认值固定为 20，表示当单次待删除组占历史已同步组比例超过该阈值时，系统必须熔断并停止组删除。

组同步必须与 GitHub 关于 identity provider groups 的使用方式保持一致，使 GitHub 管理员能够在 Enterprise 侧查看 IdP groups、组成员以及后续连接到 team 的关系，但 Team 绑定动作仍由管理员手动完成。

### 7.11 DRY_RUN 需求

DRY_RUN 必须作为默认安全模式。

在 DRY_RUN=true 时，系统必须满足以下要求：

1. 不执行任何真实写入。
2. 不持久化状态文件。
3. 记录拟创建、拟更新、拟软停用、拟硬删除动作。
4. 记录拟创建组、拟更新组、拟删除组动作。

### 7.12 状态文件需求

系统必须继续使用本地状态文件识别历史已同步用户集合和组集合。

当前阶段状态文件至少应保存：

- synced_user_external_ids
- synced_group_external_ids
- resolved_group_name_map
- pending_group_deletions
- last_run_utc

状态文件语义必须服务于以下目的：

- 判断哪些历史用户已离开当前同步范围
- 判断哪些历史组已离开当前配置范围
- 支持软停用或硬删除判定
- 支持组同步差异判定
- 支持组删除缓冲期与连续缺失计数
- 保证重复执行时行为可预测

### 7.13 调度与执行需求

系统必须同时支持以下两种执行方式：

1. 本地命令行单次执行
2. Azure Functions 定时执行

SYNC_INTERVAL_MINUTES 必须作为 Azure Functions 定时周期的配置来源。

当前阶段支持的周期值为：

- 1 到 59 且能整除 60 的值
- 60，表示每小时执行一次

### 7.14 日志与运行记录需求

系统必须在每次同步时记录一条完整的运行日志链路，至少覆盖运行开始、运行过程、运行结束三个阶段。

每次运行至少必须记录以下信息：

- 同步开始时间
- 同步结束时间
- 本次运行的唯一标识，例如 run_id
- 配置输入的组名列表
- 解析成功的组列表及其 Entra group id
- 本次拉取的用户总数和组总数
- 用户创建、更新、软停用、硬删除、重新启用、跳过、失败统计
- 组创建、更新、删除、跳过、失败统计
- 失败对象清单及失败原因

对于失败日志，系统至少必须记录对象类型、对象标识、操作类型、错误代码或错误消息。

日志输出必须同时满足人工排查可读性和后续转接到日志平台的可扩展性。

## 8. 非功能需求

### 8.1 幂等性

重复执行相同输入时，系统不应产生重复用户或不必要的 PATCH。

### 8.2 可追踪性

每次运行至少应能从日志中看出：

- 运行开始和结束时间
- 运行唯一标识
- 配置的组名列表及解析结果
- 本次拉取的用户数
- 本次同步的组数
- 创建数
- 更新数
- 软停用数
- 硬删除数
- 重新启用数
- 组创建数
- 组更新数
- 组删除数
- 跳过数
- 失败数

日志必须能够区分用户对象失败和组对象失败。

### 8.3 安全性

系统不得在文档、代码或日志示例中固化真实凭据。

默认行为必须偏向低风险，即 DRY_RUN=true 且 HARD_DELETE_REMOVED_USERS=false。

### 8.4 可恢复性

对于 429 和 5xx 类暂时性错误，系统应保留重试能力。

对于永久性错误，系统必须在日志中保留失败信息，便于人工排查。

当部分对象同步失败时，系统应尽可能保留本轮已完成对象的处理结果和失败明细，而不是只输出总失败数。

### 8.5 最小改动原则

后续实现必须优先扩展现有 client、sync engine 和 tests，不轻易引入新的抽象层。

## 9. 配置需求

本阶段必须明确以下配置项：

### 9.1 必填配置

- ENTRA_TENANT_ID
- ENTRA_CLIENT_ID
- ENTRA_CLIENT_SECRET
- ENTRA_SYNC_GROUP_NAMES
- GITHUB_ENTERPRISE
- GITHUB_PAT

### 9.2 默认安全配置

- DRY_RUN=true
- HARD_DELETE_REMOVED_USERS=false
- SYNC_INTERVAL_MINUTES=15
- GROUP_DELETE_GRACE_RUNS=2
- GROUP_DELETE_MAX_PERCENT=20

### 9.3 可选配置

- GITHUB_ENTERPRISE_ADMIN_UPNS
- GITHUB_USER_AGENT
- STATE_FILE
- LOG_LEVEL
- GROUP_DELETE_GRACE_RUNS
- GROUP_DELETE_MAX_PERCENT
- ENTRA_TOKEN_URL
- GRAPH_BASE_URL
- GITHUB_SCIM_BASE_URL

### 9.4 组配置约束

ENTRA_SYNC_GROUP_NAMES 必须以 Entra group displayName 为输入，而不是 group id。

如果未来出于兼容性保留 group id 输入能力，也不得替代 group name 作为主配置方式。

ENTRA_SYNC_GROUP_NAMES 采用逗号分隔格式作为默认输入方式。

## 10. 错误处理与审计需求

系统至少应覆盖以下错误与审计要求：

1. 当 Graph 获取失败时，应终止本轮运行并输出可定位日志。
2. 当组名解析失败时，应终止本轮运行并输出缺失或歧义组名。
3. 当 GitHub SCIM 用户写入失败时，应计入 user failed 统计。
4. 当 GitHub SCIM 组写入失败时，应计入 group failed 统计。
5. 对于 429 和 5xx，应保留重试策略。
6. 对于 hard delete，应在日志中显式区分于 soft deprovision。
7. 对于 group create、group update、group delete，应有独立日志类型。
8. DRY_RUN 日志必须能够区分 create、patch、soft deprovision、hard delete、group create、group update、group delete。
9. 每次运行应产出一条可汇总的最终结果日志。
10. 当组删除因为保护阈值、解析异常或熔断规则被阻止时，系统必须记录明确的阻止原因。

当前阶段不强制要求结构化 JSON 日志，但日志字段设计应预留后续结构化扩展能力。

## 11. 验收标准

以下条目构成当前阶段的验收基线：

### 11.1 创建验收

当任一配置的 Entra 组中出现一个 GitHub 端不存在的新用户时，系统能够创建该用户，且映射字段符合预期。

### 11.2 多组解析验收

当配置多个 Entra group name 时，系统能够正确解析所有目标组；如果存在缺失或重名歧义，系统能够失败关闭并输出明确日志。

### 11.3 direct members 范围验收

当目标组包含 nested groups 时，系统只同步 direct members，不自动展开 nested groups 的成员。

### 11.4 更新验收

当 displayName、emails、department、roles、userName 或 externalId 变化时，系统能够执行 PATCH 更新。

### 11.5 软停用验收

当用户被禁用或离开同步范围且未开启硬删除时，系统能够把目标用户设置为 active=false。

### 11.6 重新启用验收

当已被软停用的用户重新回到范围且源端已启用时，系统能够恢复 active=true 并补齐属性。

### 11.7 硬删除验收

当 HARD_DELETE_REMOVED_USERS=true 且用户离开同步范围时，系统能够调用 GitHub SCIM DELETE 永久删除该用户。

### 11.8 组同步验收

当配置组存在于 Entra 且 GitHub Enterprise 尚未存储对应 IdP group 时，系统能够创建对应 GitHub SCIM Group，并同步组名和成员关系。

当组名或成员关系变化时，系统能够更新 GitHub SCIM Group。

上述验收仅要求完成 GitHub Enterprise SCIM Group 层的对象与成员关系同步，不要求程序自动完成 Team 绑定。

### 11.9 组删除保护验收

当某个组首次离开配置范围时，系统不会立即删除，而是进入待删除状态并记录保护日志。

当某个组连续达到 GROUP_DELETE_GRACE_RUNS 个成功运行周期后仍不在配置范围内，系统才允许真实删除。

当单次待删除组比例超过 GROUP_DELETE_MAX_PERCENT，系统必须熔断组删除。

### 11.10 日志验收

每次同步都能够输出起止时间、运行标识、组解析结果、用户结果汇总、组结果汇总、失败清单。

### 11.11 DRY_RUN 验收

当 DRY_RUN=true 时，系统不得执行真实写入，且不得写入状态文件。

### 11.12 调度验收

当配置合法的 SYNC_INTERVAL_MINUTES 时，Azure Functions 能够构建有效定时表达式；当配置非法值时，应在启动阶段暴露配置错误。

## 12. 实施计划

后续开发应按以下顺序推进，避免一次性混入过多变化：

### 阶段 1：需求冻结

- 评审本文件
- 确认范围、删除语义和验收标准
- 确认是否接受 docs 根目录评审版定位

### 阶段 2：核心生命周期实现对齐

- 校对多组按组名解析能力
- 校对 direct members 读取路径，替换 transitiveMembers 依赖
- 校对用户 desired state 的多组聚合逻辑
- 校对现有 create、update、soft deprovision、reactivate 路径
- 完整实现并验证受控 hard delete
- 设计并实现组同步路径
- 设计并实现组删除保护机制
- 校对统计项和日志输出

### 阶段 3：配置与运行行为对齐

- 校对 ENTRA_SYNC_GROUP_NAMES
- 校对组名解析失败策略
- 校对 HARD_DELETE_REMOVED_USERS
- 校对 SYNC_INTERVAL_MINUTES
- 校对 README 和 .env.example

### 阶段 4：回归测试补强

- 补充多组解析测试
- 补充 direct members 与 nested groups 边界测试
- 补充组同步测试
- 补充删除策略测试
- 补充组删除保护与熔断测试
- 补充 DRY_RUN 行为测试
- 补充日志字段测试
- 补充调度配置测试
- 补充边界条件测试

### 阶段 5：评审与收尾

- 对照本文件进行功能验收
- 对不符合项做补正
- 再决定是否进入下一批需求，例如 group-to-team 自动映射或生产化状态存储替换

## 13. 风险与假设

### 13.1 风险

1. 为支持多组同步，状态文件语义必须从仅记录用户集合扩展为同时覆盖用户集合、组集合和组名解析结果，否则 removed-user 与 removed-group 判定会不可靠。
2. 如果使用 group name 作为输入但不处理同名歧义，可能导致错误组被同步。
3. 如果误把源端 disabled 等同于 hard delete，可能导致不可逆的账户清除风险。
4. 如果凭据管理不严格，PAT 或 client secret 泄露会直接带来高风险。
5. 如果 GitHub 侧存在人工改动而系统无冲突策略，后续可能出现覆盖性更新。
6. 如果组成员同步依赖用户 SCIM id 映射但缺少稳定缓存，组同步可能出现成员引用失败。
7. 如果组删除没有缓冲、阈值和失败关闭保护，配置错误会直接造成 IdP groups 被批量误删。

### 13.2 假设

1. Entra user.id 在生命周期内可作为稳定唯一标识。
2. 当前业务允许以多个受支持 Entra 组的并集定义同步范围，包括 security group 和 distribution group。
3. GitHub EMU SCIM 是唯一受支持的目标写接口。
4. GitHub 管理员将手动把已同步到 GitHub Enterprise SCIM Groups 的 IdP groups 连接到 Enterprise Team 或 Organization Team。
5. 当前阶段允许继续使用本地 JSON 状态文件。
6. 当前阶段对多组范围的定义仅基于 direct members，不覆盖 nested groups 传播语义。

## 14. 本阶段输出与下一步

本文件是当前阶段的正式计划与需求基线。

在本文件评审确认之前，不应把新的功能扩展视为需求已确认的正式实现。

本文件确认后，下一步应输出实现任务拆解，至少包含：

- 多组按组名解析实现清单
- 删除策略实现清单
- 组同步实现清单
- 同步引擎改动清单
- 配置与文档改动清单
- 日志与测试补强清单

---

如果后续需要按 ASPICE 规范沉淀正式产物，可在确认本文件内容后，再将其转化为对应阶段目录中的正式文档。