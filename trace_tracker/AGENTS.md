# Trace Tracker 约定

- 只读本地 trace，不启动任务；运行控制归调用方。保留原生文件格式，viewer 适配源，不重写导入数据。
- root 发现、跨 root 聚合和原生事件解析分层；列表按 root 懒加载，不能首屏扫描全部 session。
- 发现 catalog 时跳过 Project 代码、venv、home 与工作产物；找到 trace root 后停止递归发现，避免钻入 payload。
- session 分组在分页前完成，避免 main/subagent 分离；复制的 parent metadata 不能覆盖真实 thread 身份。
- reasoning 可见性由原始 response items 判断，usage 为零也可能有 reasoning；保持 content/summary 两种来源。
- Claude 深层 subagent 路径与 context sidecar 通过同一 parser 处理，viewer 和调用方不各造一套日志解析。
- 验证 root 解析要覆盖实际入口和多目录隔离，命令/默认路径查 `src/trace_tracker/run.py`，不要在本文复制。

- 公开包仅依赖可独立使用的 trace 格式与文件接口；业务平台适配与部署目录发现留在内部调用方。测试仅使用合成数据，不携带真实 trace 或请求上下文。
