# 课译 MathBridge — 数学课程视频翻译桌面应用

输入本地课程视频，得到目标语言视频、可编辑字幕和带来源的课程讲义 PDF。首版支持中英互译，Windows 优先，单机使用。

**交付状态：0.1.1 Windows 原生桌面开发版本。** 已实现项目管理、独立 worker、步骤引导与简洁字幕校对、公式证据、可选文本 API、本地 ASR、多人说话人区分适配、每人独立系统声线或本地 GPT-SoVITS 参考原声配音、讲义 PDF 与视频导出。真实课程质量、自动分人与克隆音色效果、自动视觉 OCR、长课和干净机器发布验收仍待完成；不将工程测试视为完整产品验收。

## 启动

当前工作目录已经准备好开发环境，双击 **`Start-MathBridge.cmd`** 即可启动。便携包构建成功后也可直接运行 `dist/MathBridge/MathBridge.exe`，需要保留整个目录。

换一台机器从源码安装时，先安装 Python 3.11/3.12 x64 和 Node.js，再运行 `Setup-MathBridge.cmd`。本地语音识别组件可执行 `powershell -File tools/setup.ps1 -WithASR` 安装；Whisper 权重另行准备并在设置里选择。程序不会自动下载模型或上传视频；API 默认关闭，需配置 Key 并明确允许发送文本。

无需模型与 API 的使用路径：建立视频项目 → 导入已有 SRT/VTT → 手动填写译文、字幕和读法 → 校对数学内容 → 标准配音 → 讲义及导出。

详细操作见 [使用说明](docs/08-user-guide.md) 与 [多人原声配音配置](docs/11-voice-backends.md)，当前功能和验证边界见 [实现状态](docs/09-implementation-status.md) 与 [本机验证记录](docs/10-validation-report.md)。原始设计文档继续作为产品目标保留。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/smoke-ui.py
.\.venv\Scripts\python.exe tools/smoke-pipeline.py
```

最后一项用人工转写/译文与合成短片验证真实进程、双向标准配音、PDF、字幕和视频导出，不调用付费 API，不代表 ASR 或翻译质量评测。Windows SAPI 在受限执行沙箱中可能不可用，应在普通本机进程测试。

## 阅读顺序

1. [产品需求与边界](docs/01-requirements.md)
2. [完整设计方案](docs/02-design.md)
3. [数据与 Provider 接口](docs/03-contracts.md)
4. [实施计划、agent 分工与 Git 约定](docs/04-delivery-plan.md)
5. [验收标准与测试素材计划](docs/05-acceptance.md)
6. [技术来源与选型记录](docs/06-sources.md)
7. [AI API、公式准确性与成本](docs/07-api-strategy.md)

[数据契约 Schema](contracts/project.schema.json)、[示例课程数据](examples/project.json) 与 [字幕及导出快照](examples/render-plan.json) 可用于前后端并行开发。示例是人工构造的演示数据，不是模型处理结果。

## 设计原则

- 本地项目文件是事实来源；无需账号、服务器或多用户数据库。
- 原文、译文、公式、读法和来源分别保存，便于校对与追踪。
- 自动处理默认继续进行，存在低置信数学内容时在导出前进入校对。
- 配音引擎、识别引擎与翻译引擎可替换，模型按需下载。
- 支持本地桌面搭配 DeepSeek Flash API：语义任务可联网，媒体与工程管理仍在本地。
- 默认不上传视频或音频；联网模型下载和外部 API 处理分别明确提示。
- 每次修改只重跑受影响的步骤，避免整段课程反复计算。

## Git

仓库使用 `main`，源代码、依赖锁文件、测试和文档纳入本地 Git。当前应用快照标签为 `v0.1.1-guided-review`，新增清楚的校对步骤引导；上一版 `v0.1.0-multispeaker` 继续保留为回退点。模型环境和效果验收范围仍见实现状态。

Git 可以恢复到已提交的快照，未提交改动不会自动保留；当前快照之前的应用开发中间状态没有分别提交。后续修改应按完成并验证的功能提交，较大改动前先保存回退点。

课程媒体、用户项目、模型、密钥、环境、生成缓存及 `dist` 中的 EXE 不纳入 Git。回退源码后需按对应版本重新安装依赖并打包。仓库当前没有远端，本地提交不等于异地备份。具体工作流见实施计划。

## 设计数据校验

在 PowerShell 7.4+ 中运行 `pwsh -File tools/validate-design.ps1`。它校验两份 JSON Schema、样例跨字段约束、13 个无效数据探针、重复公式引用、字幕cue/产物依赖与文档本地链接。它不验证视频处理或模型质量。

设计日期：2026-10-18。模型能力与许可须在锁定发布版本时复核。
