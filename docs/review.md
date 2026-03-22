# 差异审计与评审记录

## 文档目的

记录本轮 README 补强、需求差异审计结果，以及本轮通过 todo tool 跟踪的任务项与完成结果，供后续分析和追踪。

## 本轮 todo tool 记录

### 本轮任务项

| 序号 | todo 标题 | 结果 |
| --- | --- | --- |
| 1 | 审阅README与需求 | 已完成 |
| 2 | 补充端到端运行说明 | 已完成 |
| 3 | 补充README部署章节 | 已完成 |
| 4 | 执行差异审计 | 已完成 |
| 5 | 整理review文档 | 已完成 |

### 过程结果摘要

- 已补齐 README 的端到端运行说明
- 已补齐 README 的部署章节
- 已针对需求文档再次执行差异审计
- 审计过程中发现并补齐了一处实现差异：
  - 对于“用户仍在同步范围内但源端 accountEnabled=false”的场景，原实现能正确写入 `active=false`，但统计维度只记为 `user_updated`
  - 现已修正为同时计入 `user_soft_deprovisioned`
- 审计过程中还补齐了 Graph 侧的 429 / 5xx 重试能力，使其与 GitHub SCIM 客户端保持一致

## 本轮变更产物

- README 运行与部署说明更新：[README.md](README.md)
- 差异修正：Graph 重试与 soft deprovision 统计修正
  - [src/graph_client.py](src/graph_client.py)
  - [src/sync_engine.py](src/sync_engine.py)
- 回归测试更新：
  - [tests/test_mapping.py](tests/test_mapping.py)

## 生产化改造追加记录

### 本轮追加目标

- 明确日志记录的详细策略
- 明确状态存储的本地优先方案
- 保持对未来 Azure Functions 版本的兼容扩展点

### 本轮追加实现结果

- 已新增结构化日志能力：
  - text 输出，便于本地直接查看
  - json 输出，便于未来 Azure Functions 或日志平台采集
- 已支持本地按运行日志文件与 stable latest 日志文件：
  - `LOG_FILE`
  - `LOG_FILE_MAX_BYTES`
  - `LOG_FILE_BACKUP_COUNT`
- 已将状态存储重构为后端抽象：
  - 当前仅实现 `STATE_STORE_BACKEND=local_json`
  - 后续可新增 Azure 专属后端而不改同步主逻辑
- 已为状态文件增加运行元数据：
  - `schema_version`
  - `state_store_backend`
  - `last_run_id`
  - `last_run_status`
  - `updated_at_utc`
- 已将状态写入改为原子替换，降低本地文件损坏概率

## 需求差异审计

### 审计范围

- 上游基线文档：[docs/entra-github-emu-sync-requirements.zh-cn.md](docs/entra-github-emu-sync-requirements.zh-cn.md)
- 对照对象：当前 `src/` 实现、`README.md`、测试用例

### 审计结论总览

- 已满足：核心同步需求、组同步需求、组删除保护、DRY_RUN、安全默认值、基础日志链路、配置模型、多组 direct members 范围
- 已在本轮补齐：
  - in-scope disabled 用户的 soft deprovision 统计口径
  - Graph 侧 429 / 5xx 重试策略
- 仍建议后续补强，但不构成当前阶段的阻断缺陷：
  - 将日志进一步结构化为稳定 JSON 字段输出
  - 对 Graph 侧重试策略补充更细的自动化单测
  - 为 Azure Functions 场景增加更贴近实际部署的集成验证记录

## 分项审计结果

### 1. 配置与范围语义

- 需求：支持 `ENTRA_SYNC_GROUP_NAMES`、`GROUP_DELETE_GRACE_RUNS`、`GROUP_DELETE_MAX_PERCENT`
- 结果：已满足
- 依据：
  - [src/config.py](src/config.py)
  - [.env.example](.env.example)

### 2. 多组按名称解析

- 需求：按 Entra group displayName 解析多个组；缺失或歧义时失败关闭
- 结果：已满足
- 依据：
  - [src/graph_client.py](src/graph_client.py)
  - [src/main.py](src/main.py)

### 3. direct members 范围

- 需求：仅 direct members 进入范围，不展开 nested groups
- 结果：已满足
- 依据：
  - [src/graph_client.py](src/graph_client.py)

### 4. 用户匹配与回填

- 需求：externalId 主匹配，userName 回退匹配，回退命中后回填 externalId
- 结果：已满足
- 依据：
  - [src/sync_engine.py](src/sync_engine.py)
  - [tests/test_mapping.py](tests/test_mapping.py)

### 5. 用户生命周期

- 需求：create / update / soft deprovision / reactivate / optional hard delete
- 结果：已满足
- 说明：
  - 本轮已补齐 disabled in-scope 用户场景下的 `user_soft_deprovisioned` 统计语义
- 依据：
  - [src/sync_engine.py](src/sync_engine.py)
  - [tests/test_mapping.py](tests/test_mapping.py)

### 6. GitHub Enterprise SCIM Groups 同步

- 需求：组 create / update / protected delete，成员引用使用 GitHub SCIM user id
- 结果：已满足
- 依据：
  - [src/github_scim_client.py](src/github_scim_client.py)
  - [src/sync_engine.py](src/sync_engine.py)
  - [tests/test_mapping.py](tests/test_mapping.py)

### 7. 组删除保护

- 需求：2 次连续成功缺失、20% 熔断、失败关闭保护
- 结果：已满足
- 依据：
  - [src/sync_engine.py](src/sync_engine.py)
  - [src/state_store.py](src/state_store.py)
  - [tests/test_mapping.py](tests/test_mapping.py)

### 8. DRY_RUN 语义

- 需求：不执行真实写入、不持久化状态、记录拟动作
- 结果：已满足
- 依据：
  - [src/sync_engine.py](src/sync_engine.py)
  - [tests/test_mapping.py](tests/test_mapping.py)

### 9. 错误处理与重试

- 需求：429 与 5xx 保留重试能力
- 结果：已满足
- 说明：
  - GitHub SCIM 客户端原已满足
  - 本轮已补齐 Graph token 获取与 GET 请求的重试策略
- 依据：
  - [src/github_scim_client.py](src/github_scim_client.py)
  - [src/graph_client.py](src/graph_client.py)

### 10. 日志与运行记录

- 需求：run_id、开始/结束时间、组解析结果、用户/组统计、失败对象清单、阻止原因
- 结果：已满足，并已按生产化方向补强
- 说明：
  - 当前已具备 run_id、汇总统计、失败对象与阻止原因
  - 本轮已支持 text/json 两种结构化输出方式
  - 本地可使用按运行日志文件与 latest 日志文件，Azure Functions 场景建议使用 stdout + json
- 依据：
  - [src/main.py](src/main.py)
  - [src/runtime_logging.py](src/runtime_logging.py)

### 10.1 状态存储

- 需求：本地优先，同时为未来 Azure Functions 版本保留兼容扩展点
- 结果：已满足当前阶段设计目标
- 说明：
  - 当前默认且唯一实现后端为 `local_json`
  - 已通过后端抽象隔离同步逻辑与存储实现
  - 本地状态写入使用原子替换并带运行元数据
  - 尚未实现 Azure 专属后端，但扩展点已就位
- 依据：
  - [src/state_store.py](src/state_store.py)
  - [src/config.py](src/config.py)

### 11. README 与部署说明

- 需求：仓库文档与实现保持一致，便于运行和验证
- 结果：已满足
- 本轮补充内容：
  - 本地端到端运行步骤
  - dry run 到真实写入的切换流程
  - Azure Functions 部署前置条件、配置项和谨慎项
- 依据：
  - [README.md](README.md)

## 测试与验证结果

- 已执行：`pytest`
- 当前结果：18 passed

## 当前结论

本轮对照需求文档的差异审计后，当前实现未发现新的阻断性遗漏项。

当前阶段可以认为：

- 需求文档中的核心功能项已基本落地
- README 已具备端到端运行与部署说明
- 审计中发现的实现差异已在本轮修正并通过回归验证

## 后续建议

1. 实现 Azure 专属状态后端，例如 Blob 或 Table，并保持与当前 `StateStore` 抽象兼容
2. 为 Azure Functions 真正部署路径增加一次端到端运行记录
3. 将 json 日志对接到集中式日志平台或 Application Insights