# 应用架构

```text
PySide6 主窗口
  → Coordinator（任务、编辑、revision 与结果接纳）
    → ProjectStore（项目、历史、运行状态、费用与产物）
    → 独立 QProcess worker
      → FFmpeg：抽音频、取帧、混音与导出
      → faster-whisper：本地识别与逐词时间
      → 文本客户端：断句、翻译、讲义
      → Windows SAPI / GPT-SoVITS：分句配音
      → 本地 ASR：静音与漏读筛查
      → MathJax / QtSvg / ReportLab：公式和讲义 PDF
```

主窗口负责交互。计算与媒体处理运行在独立 worker 中，通过 UTF-8 JSONL 交换命令、进度和结果。后台结果携带输入 revision；过期结果不会覆盖新的编辑。

Coordinator 接纳经过验证的结果，并原子保存项目及历史快照。导出固定当前 revision，验证文件哈希、依赖和字幕时间。缓存以输入内容、模型、声音和参数指纹判定，避免修改无关句子后整课重算。

断句以有界上下文处理原文，再复查相邻边界；模型只决定分组，原文和时间来自本地证据。翻译校验片段 ID、公式引用及锁定术语。讲义先提炼章节，再汇总为课程主题。

配音先自然合成，再测量时长、分配前后停顿并调整局部速度。合成结果经过信号和内容检查；无法通过的句子进入问题清单，并阻止缺失译声的正式导出。

文本 API 的服务商、地址、模型与 JSON 模式参与断点标识。Key 按服务商和地址隔离，普通配置只保存非敏感选项。外部文本请求要求 HTTPS 和文本上传许可，本地声音服务只接受 loopback 地址。

模块位于 `src/mathbridge`：`ui`、`application`、`storage`、`domain`、`worker`、`media`、`providers`、`math` 和 `handout`。
