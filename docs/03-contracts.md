# 项目与任务数据

核心 Schema 位于 `contracts/project.schema.json`，应用副本位于 `src/mathbridge/schemas`。渲染快照 Schema 位于 `contracts/render-plan.schema.json`。`examples` 提供人工构造的数据样例。

```text
项目目录/
  project.json             # 课程核心数据与 revision
  runtime.json             # 阶段状态、进度、错误与累计费用
  artifacts.json           # 文件路径、哈希与依赖
  inputs/source.link.json  # 原视频路径与指纹
  history/                 # 不可变编辑快照
  audio/                   # 原音、识别结果、参考原声和配音
  cache/                   # 任务和 API 断点、费用预留账本
  exports/                 # 导出文件及 render-plan、export-report
```

原视频通过路径和 SHA256 关联。移动后可以重连内容相同的文件；不能用重新压制的同名视频替换。迁移时同时保留整个项目和原视频。

每个句子保留 ID、说话人、起止时间、原文、译文、字幕、配音读法及公式／术语引用。公式保存原始内容、可选 LaTeX、读法、来源证据及复核状态。讲义引用真实句子和公式。

Worker 从标准输入接收一条命令：`request_id`、`stage`、`project_dir`、`source_path`、`project`、`project_revision`、`settings`、`options` 和 `artifacts`。标准输出每行一个 `progress` 或 `done` 消息，原生库输出进入标准错误。取消通过后续 `cancel` 消息传入。

主要阶段：`create`、`transcribe`、`diarize`、`sentences`、`analyze`、`translate`、`notes`、`speaker_reference`、`dub`、`preview`、`export`。固定接口诊断使用 `test_api`。

API 请求前持久化预算预留，返回后按实际 usage 结算。未确定的请求保留费用占用；恢复处理不会重置项目预算。Key 仅通过进程管道传递，不写入项目、普通配置和账本。

导出检查包括 revision、内容指纹、文件哈希、引用、依赖环和时间范围。只有内容指纹仍匹配的旧配音才能复用。
