# 课译TransLesson — 数学课程视频翻译桌面应用

输入本地课程视频，得到目标语言视频、可编辑字幕和带来源的课程讲义 PDF。首版支持中英互译，Windows 优先，单机使用。

**交付状态：0.1.9 Windows 原生桌面开发版本。** 本版统一图标与课译TransLesson名称，精简界面说明，支持随软件放置组件及内置 GPT-SoVITS 启停，并保留课译TransLesson 名称、主目录 EXE 启动入口、原声停顿分析、自动配音时长分配、集中试听、便携版 HTTPS 修复、AI 上下文断句与翻译、按知识点生成教学讲义、项目上下文恢复和简化预算。数学字幕可显示 `2+3`、`x²` 等符号，配音保留自然读法；PDF 会嵌入数学符号字体，避免负号消失。已有项目打开后直接继续，媒体和工作目录自动恢复，成品默认导出到项目内。此前的长课校对、问题定位、多人声音配置、可撤销编辑及本地 ASR 继续保留。真实课程的文本 API 流程已验证；全文数学准确性、完整配音自然度、视觉 OCR 和干净机器验收仍需进一步测试。

## 启动

打开文件夹，直接双击 **`课译TransLesson.exe`** 启动。当前工作目录与源码目录均有这个 EXE 入口；下载的 Windows 程序包完整解压后，第一层也能直接看到同名 EXE。请保留整个文件夹，无需使用 Start 脚本。详细结构见 [EXE 启动与改名说明](docs/15-translesson-launch.md)。

换一台机器从源码安装时，先安装 Python 3.11/3.12 x64 和 Node.js，再运行 `Setup-TransLesson.cmd`。本地语音识别组件可执行 `powershell -File tools/setup.ps1 -WithASR` 安装；Whisper 权重另行准备并在设置里选择。程序不会自动下载模型或上传视频；API 默认关闭，需配置 Key 并明确允许发送文本。

推荐路径：建立或打开项目 → 识别原文／导入字幕 → **AI 断句并翻译** → 抽查 → 配音 → **AI 教学讲义** → 导出。只需配置一次文本 API 与项目预算；手动编辑是可选修订。没有 API 时仍可导入字幕并人工修订，但不能自动生成教学讲义。

软件图标、组件目录与内置服务操作见 [0.1.9 更新说明](docs/18-components-and-branding.md)；断句、静音利用和多段音色参考见 [0.1.8 更新说明](docs/17-sentence-timing-voice.md)；声音克隆参考自动填写及原生服务启动见 [0.1.7 更新说明](docs/16-auto-voice-reference.md)；自动配音与试听见 [0.1.5 更新说明](docs/14-adaptive-dubbing.md)；AI 文本工作流的入口、恢复方式与验证范围见 [本次更新说明](docs/13-ai-course-workflow.md)。

详细操作见 [使用说明](docs/08-user-guide.md)、[换电脑与课程迁移](docs/12-another-computer.md) 与 [多人原声配音配置](docs/11-voice-backends.md)，当前功能和验证边界见 [实现状态](docs/09-implementation-status.md) 与 [本机验证记录](docs/10-validation-report.md)。原始设计文档继续作为产品目标保留。

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

仓库使用 `main`，源代码、依赖锁文件、测试和文档纳入 Git。当前应用快照为 `v0.1.9-components-branding`；此前 `v0.1.8-course-voice`、 `v0.1.7-auto-reference`、 `v0.1.6-translesson`、 `v0.1.5-adaptive-dubbing`、 `v0.1.4-portable-api`、 `v0.1.3-ai-course`、`v0.1.2-long-course`、`v0.1.1-guided-review` 与 `v0.1.0-multispeaker` 继续保留为回退点。

Git 可以恢复到已提交的快照，未提交改动不会自动保留；当前快照之前的应用开发中间状态没有分别提交。后续修改应按完成并验证的功能提交，较大改动前先保存回退点。

课程媒体、用户项目、模型、密钥、环境、生成缓存及 `dist` 中的 EXE 不纳入 Git。回退源码后需按对应版本重新安装依赖并打包。只有成功推送到远端的提交才有异地副本；私有仓库访问和另一台电脑的准备步骤见 [迁移说明](docs/12-another-computer.md)。课程项目与模型仍须另行复制，Git 不会替你备份这些文件。

## 设计数据校验

在 PowerShell 7.4+ 中运行 `pwsh -File tools/validate-design.ps1`。它校验两份 JSON Schema、样例跨字段约束、13 个无效数据探针、重复公式引用、字幕cue/产物依赖与文档本地链接。它不验证视频处理或模型质量。

设计日期：2026-10-18。模型能力与许可须在锁定发布版本时复核。
