# Entra 到 GitHub EMU 同步实现任务拆解

## 文档信息

- 文档状态：执行版
- 上游输入：[docs/entra-github-emu-sync-requirements.zh-cn.md](docs/entra-github-emu-sync-requirements.zh-cn.md)
- 文档目的：把需求文档拆解成可直接执行、可验证、可分阶段提交的 work items
- 适用范围：当前仓库的下一轮开发实施

## 1. 实施原则

本次拆解遵循以下原则：

1. 每个 work item 尽量只覆盖一种变化类型，不把配置、逻辑、测试、文档混成无法回滚的大改动。
2. 优先从高风险、高约束项开始实现，例如组名解析、direct members、组删除保护。
3. 每个 work item 都必须带上完成定义和最低验证方式。
4. 任何新增写操作都必须先支持 DRY_RUN。
5. 对用户生命周期和组生命周期的改动，必须同步补测试。

## 2. 建议执行顺序

建议按以下顺序执行：

1. 配置模型与状态语义扩展
2. Entra 多组按组名解析
3. direct members 用户读取与多组合并
4. GitHub SCIM Group 客户端能力
5. 组同步引擎与成员映射
6. 组删除保护机制
7. 日志与运行记录增强
8. README 与 .env.example 对齐
9. 回归测试补强

## 3. Work Items

### WI-001 配置模型扩展

- 目标：让系统能够加载多组名称、组删除保护参数和后续组同步所需配置。
- 涉及文件：
  - [src/config.py](src/config.py)
  - [.env.example](.env.example)
  - [README.md](README.md)
- 主要改动：
  - 把 ENTRA_SYNC_GROUP_ID 替换为 ENTRA_SYNC_GROUP_NAMES
  - 增加 GROUP_DELETE_GRACE_RUNS，默认值 2
  - 增加 GROUP_DELETE_MAX_PERCENT，默认值 20
  - 为新配置增加解析、默认值和校验逻辑
- 完成定义：
  - Settings 可以正确加载多个组名和两个删除保护参数
  - 非法配置会在启动时失败关闭
- 最低验证：
  - 新增配置单元测试
  - 校验 README 与 .env.example 示例一致

### WI-002 状态文件语义扩展

- 目标：让状态文件支持多组同步与组删除缓冲机制。
- 涉及文件：
  - [src/state_store.py](src/state_store.py)
  - [src/models.py](src/models.py) 如确有必要
- 主要改动：
  - 将用户与组的已同步集合分开存储
  - 新增 resolved_group_name_map
  - 新增 pending_group_deletions
  - 保持向后兼容或提供最小迁移逻辑
- 完成定义：
  - 状态文件能够同时支撑用户和组的差异判定
  - 可以记录组删除等待计数
- 最低验证：
  - 状态读写测试
  - 旧状态缺字段时的兼容测试

### WI-003 Entra 组名解析

- 目标：支持根据多个 Entra group displayName 解析目标安全组。
- 涉及文件：
  - [src/graph_client.py](src/graph_client.py)
  - [src/main.py](src/main.py)
- 主要改动：
  - 增加按 group name 查询安全组的方法
  - 对每个配置组名执行解析
  - 对未找到和重名歧义执行失败关闭
  - 输出组名解析日志
- 完成定义：
  - 支持多个组名输入
  - 缺失和歧义场景都能正确失败
- 最低验证：
  - 组名解析单测
  - 缺失组和重名组测试

### WI-004 direct members 用户读取

- 目标：把当前用户范围从 transitiveMembers 改为 direct members。
- 涉及文件：
  - [src/graph_client.py](src/graph_client.py)
- 主要改动：
  - 新增或替换为 direct members 查询路径
  - 只接收用户类型成员
  - 明确忽略 nested groups 的展开
- 完成定义：
  - direct members 被同步
  - nested groups 的成员不会被自动带入
- 最低验证：
  - direct 与 transitive 行为差异测试
  - nested group 边界测试

### WI-005 多组合并与用户 desired state

- 目标：把多个组中的用户合并成单一用户目标集合，并进行去重。
- 涉及文件：
  - [src/main.py](src/main.py)
  - [src/sync_engine.py](src/sync_engine.py)
- 主要改动：
  - 合并多个组的 direct members 用户集合
  - 按 Entra user.id 去重
  - 明确用户 out-of-scope 的判定规则
- 完成定义：
  - 同一用户出现在多个组中只处理一次
  - 离开某一组但仍在其他组中的用户不会被误停用
- 最低验证：
  - 多组合并测试
  - 交叉组成员测试

### WI-006 GitHub SCIM Group 客户端能力

- 目标：补齐 GitHub Enterprise SCIM Groups 的读写删能力。
- 涉及文件：
  - [src/github_scim_client.py](src/github_scim_client.py)
- 主要改动：
  - 增加按 externalId 查询组
  - 增加创建组
  - 增加更新组
  - 增加删除组
  - 保持用户接口风格一致
- 完成定义：
  - 可以独立完成组的 GET、POST、PATCH 或 PUT、DELETE
- 最低验证：
  - 客户端请求 payload 单测
  - 响应处理测试

### WI-007 组同步引擎

- 目标：把 Entra 组及其 direct members 同步到 GitHub Enterprise SCIM Groups。
- 涉及文件：
  - [src/sync_engine.py](src/sync_engine.py)
  - [src/models.py](src/models.py) 如确有必要
- 主要改动：
  - 在用户同步后执行组同步
  - 为每个组构造 desired payload
  - 维护组的 create、update、skip 统计
  - 保证成员引用使用已存在的 GitHub SCIM user id
- 完成定义：
  - 新组可创建
  - 组名变化和成员变化可更新
  - 组成员引用稳定
- 最低验证：
  - 组创建测试
  - 组更新测试
  - 组成员映射测试

### WI-008 组删除保护机制

- 目标：实现“连续缺失 2 个成功周期 + 删除比例超过 20% 熔断 + 解析失败不删除”的受控组删除策略。
- 涉及文件：
  - [src/sync_engine.py](src/sync_engine.py)
  - [src/state_store.py](src/state_store.py)
- 主要改动：
  - 维护 pending_group_deletions 计数
  - 仅在连续 2 个成功运行周期缺失后执行组删除
  - 当待删除比例超过 20% 时熔断
  - 当存在解析失败或主流程失败时跳过组删除
- 完成定义：
  - 首次离开范围不会被删
  - 第二次连续成功缺失才允许删
  - 比例过高时只告警不删除
- 最低验证：
  - 连续缺失计数测试
  - 熔断阈值测试
  - 解析失败跳过删除测试

### WI-009 运行日志增强

- 目标：让每次同步都具备完整的运行日志链路和结果汇总。
- 涉及文件：
  - [src/main.py](src/main.py)
  - [src/sync_engine.py](src/sync_engine.py)
- 主要改动：
  - 生成 run_id
  - 记录开始时间和结束时间
  - 记录组名解析结果
  - 记录用户和组的成功/失败统计
  - 记录失败对象清单和阻止删除原因
- 完成定义：
  - 单次运行可以从日志中完整复盘
  - 删除被阻止时有明确原因
- 最低验证：
  - 日志字段测试
  - DRY_RUN 日志测试

### WI-010 README 与示例配置更新

- 目标：让仓库文档和示例配置与最新需求保持一致。
- 涉及文件：
  - [README.md](README.md)
  - [.env.example](.env.example)
- 主要改动：
  - 更新多组按组名配置说明
  - 更新 direct members 语义说明
  - 更新组同步与组删除保护说明
  - 更新日志行为说明
- 完成定义：
  - README、.env.example 与实现一致
- 最低验证：
  - 人工对照需求文档检查

### WI-011 回归测试补强

- 目标：把新基线对应的核心场景全部纳入自动化测试。
- 涉及文件：
  - [tests/test_mapping.py](tests/test_mapping.py)
  - 视情况新增 [tests/test_group_sync.py](tests/test_group_sync.py)
- 主要改动：
  - 多组解析测试
  - direct members 范围测试
  - 多组合并测试
  - 组创建/更新测试
  - 组删除保护测试
  - 日志输出测试
- 完成定义：
  - 所有核心需求均有自动化覆盖
- 最低验证：
  - pytest 全量通过

## 4. 推荐提交分组

为了降低回归风险，建议按以下提交批次推进：

1. 配置与状态基础改造
包含 WI-001、WI-002

2. Entra 读取路径改造
包含 WI-003、WI-004、WI-005

3. GitHub Group 客户端与同步主路径
包含 WI-006、WI-007

4. 组删除保护与日志增强
包含 WI-008、WI-009

5. 文档与测试收尾
包含 WI-010、WI-011

## 5. 建议验收顺序

1. 先验收组名解析和 direct members 语义
2. 再验收多组合并与用户生命周期不回归
3. 再验收组同步创建与更新
4. 最后验收组删除保护与日志完整性

## 6. 当前建议

如果下一步开始正式开发，建议优先从 WI-001 到 WI-003 开始，而不要先动组删除逻辑。

原因是：

1. 没有完成多组按组名解析，后续所有组同步逻辑都缺少稳定输入。
2. 没有完成 direct members 读取，组成员语义仍然是错的。
3. 没有完成状态语义扩展，组删除保护无法可靠落地。