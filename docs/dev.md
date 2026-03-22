# 开发记录

## 文档目的

记录本轮实现过程中按功能模块划分的主要任务、时间顺序、产出文件和验证结果，便于后续分析与追踪。

## 本轮开发时间过程

### T1 配置与状态基础改造

- 时间：2026-03-21 / 阶段 T1
- 功能模块：配置模型、状态文件
- 主要任务：
  - 将单组配置 `ENTRA_SYNC_GROUP_ID` 升级为多组配置 `ENTRA_SYNC_GROUP_NAMES`
  - 增加 `GROUP_DELETE_GRACE_RUNS` 与 `GROUP_DELETE_MAX_PERCENT`
  - 将状态文件从单一 `synced_external_ids` 扩展为用户、组、组名解析结果和组待删计数
  - 保留旧状态文件兼容读取能力
- 主要文件：
  - `src/config.py`
  - `src/state_store.py`
  - `src/models.py`
- 结果：
  - 支持多组名称加载、删除保护参数校验、旧状态向新状态语义平滑兼容

### T2 Entra 读取路径改造

- 时间：2026-03-21 / 阶段 T2
- 功能模块：Entra Graph 读取
- 主要任务：
  - 新增按 Entra group displayName 解析安全组能力
  - 缺失组名或重名歧义时失败关闭
  - 将成员读取从 `transitiveMembers` 改为 direct members
  - 保持只接收用户对象，不展开 nested groups
- 主要文件：
  - `src/graph_client.py`
  - `src/main.py`
- 结果：
  - 实现多组 direct members 读取与多组合并输入准备

### T3 GitHub SCIM 客户端扩展

- 时间：2026-03-21 / 阶段 T3
- 功能模块：GitHub SCIM Users / Groups
- 主要任务：
  - 保留用户读写删能力
  - 新增 SCIM Group 的按 externalId 查询、创建、PATCH、删除能力
  - 统一请求重试和过滤查询方式
- 主要文件：
  - `src/github_scim_client.py`
- 结果：
  - 具备用户与组双生命周期写入基础能力

### T4 同步引擎重构

- 时间：2026-03-21 / 阶段 T4
- 功能模块：用户同步、组同步、删除保护
- 主要任务：
  - 将同步主流程从仅用户同步重构为“用户同步 + 组同步”
  - 保留 externalId 主匹配和 userName 回退匹配
  - 保留用户软停用、重新启用、可选硬删除语义
  - 在组同步中使用 GitHub SCIM user id 引用成员
  - 增加组删除保护：连续缺失计数、比例熔断、失败关闭
  - 在 DRY_RUN 下跳过真实写入和状态持久化
- 主要文件：
  - `src/sync_engine.py`
- 结果：
  - 完成需求文档定义的核心同步路径基线

### T5 运行日志与入口整理

- 时间：2026-03-21 / 阶段 T5
- 功能模块：执行入口、运行记录
- 主要任务：
  - 增加 `run_id`
  - 记录开始时间、结束时间、组解析结果、用户/组汇总统计
  - 记录失败对象和组删除阻止原因
- 主要文件：
  - `src/main.py`
- 结果：
  - 单次运行已具备可追踪的日志链路

### T6 文档与示例配置对齐

- 时间：2026-03-21 / 阶段 T6
- 功能模块：文档与配置示例
- 主要任务：
  - 更新 `.env.example` 为多组按名称配置
  - 更新 `README.md` 中的 direct members、组同步和组删除保护说明
  - 记录本轮开发过程到本文件
- 主要文件：
  - `.env.example`
  - `README.md`
  - `docs/dev.md`
- 结果：
  - 仓库文档与当前实现对齐

### T7 自动化验证

- 时间：2026-03-21 / 阶段 T7
- 功能模块：测试与验证
- 主要任务：
  - 重写并扩展同步引擎测试
  - 增加配置加载与旧状态兼容测试
  - 验证组同步与组删除保护行为
- 主要文件：
  - `tests/test_mapping.py`
  - `tests/test_config.py`
- 验证结果：
  - `pytest` 通过
  - 结果：12 passed

### T8 生产化日志与状态存储改造

- 时间：2026-03-22 / 阶段 T8
- 功能模块：运行日志、状态持久化、可扩展运行时基础设施
- 主要任务：
  - 增加 `LOG_FORMAT`、`LOG_FILE`、`LOG_FILE_MAX_BYTES`、`LOG_FILE_BACKUP_COUNT`
  - 引入结构化日志工具，支持 text 和 json 两种输出
  - 支持按运行生成本地时间戳日志文件与 stable latest 日志文件，兼顾本地排查体验
  - 将状态存储重构为后端抽象，当前实现 `local_json`
  - 为本地 JSON 状态增加 schema 与运行元数据
  - 使用原子替换写入降低状态文件损坏风险
  - 保持对未来 Azure Functions 后端扩展的兼容入口，但不提前实现 Azure 专属存储后端
- 主要文件：
  - `src/config.py`
  - `src/runtime_logging.py`
  - `src/state_store.py`
  - `src/main.py`
  - `.env.example`
  - `README.md`
- 结果：
  - 形成“本地优先、未来可扩展”的日志与状态存储基线

## 当前实现覆盖情况

- 已实现：
  - 多个 Entra 安全组按 displayName 解析
  - 仅 direct members 进入同步范围
  - 多组合并与用户去重
  - 用户 create / update / soft deprovision / reactivate / optional hard delete
  - GitHub Enterprise SCIM Groups create / update / protected delete
  - 组删除保护：连续缺失 2 次、20% 熔断、失败关闭
  - DRY_RUN 不写状态
  - run_id 与结果汇总日志
  - text/json 结构化日志、按运行归档日志与 stable latest 日志文件
  - 带 schema 与运行元数据的本地 JSON 状态存储

- 本轮未扩展：
  - Team 自动绑定
  - 外部持久化状态存储
  - Webhook 或实时同步

## 追踪建议

- 后续如果继续开发，建议按以下模块继续追加记录：
  - Azure 专用状态后端实现
  - 更细粒度重试与错误分类
  - Azure Functions 端到端验证记录
  - 结构化日志接入集中式日志平台