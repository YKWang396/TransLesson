# 0.1 本机工程验证记录

日期：2026-10-18。平台：Windows 11 x64，Python 3.12.14。运行时精确版本、二进制指纹及许可记录见 `packaging/windows/runtime-manifest.json`；实际 Python 运行依赖锁定在 `requirements-windows.lock.txt`。

## 已完成的检查

| 检查 | 结果 | 范围 |
|---|---|---|
| `python -m pytest -q` | 67 passed，1 skipped | 项目 schema、引用/时间、路径、原子历史、局部失效、迟到结果、API 权限/预算/账本/恢复、数学规则/安全、PDF、真实 FFmpeg、ASR checkpoint。受限沙箱中的 SAPI 单项跳过。 |
| 真实 Windows SAPI | 通过 | 沙箱外生成并验证实际 WAV，系统中英声音；不涉及教师音色克隆。 |
| `tools/smoke-ui.py` | 通过 | 实际 Coordinator 编辑片段、公式、术语和讲义，revision、撤销、费用显示；普通与 1120×760 窗口截图检查。 |
| `tools/smoke-pipeline.py` | 中英两个方向通过 | 12 秒合成视频、人工源文与译文 → 独立 QProcess → 标准声音 → 有来源 PDF、SRT/VTT/ASS、WAV、MP4、Markdown、JSON、真实短片预览；产物 hash/依赖校验、配音过期拦截、保留原声策略及输出全片解码。 |
| `tools/smoke-frozen.py` | 通过 | 将 PATH 限制为 Windows 系统及 PowerShell 目录，运行便携 GUI、独立 EXE worker、UTF-8 JSONL、内置 FFmpeg、内置 Node/MathJax、QtSvg、PDF、实际系统配音和 MP4 导出。 |
| 讲义视觉检查 | 通过 | 双向工程 PDF 逐页以 150dpi 渲染，检查中文、数学式、来源时间、分页与页码。 |
| `tools/validate-design.ps1` | 通过 | 原设计 schema、13 个无效探针、重复公式、cue、依赖图及文档链接；仍仅是设计数据检查。 |

开发过程还针对打包发现并修复：开发环境 Poppler 的同名 ICU DLL 混入 Qt 包、pnpm 深目录引起 Windows 路径长度问题，以及冻结 worker 的系统默认编码不符合 UTF-8 协议。

## 不能由这些检查推出的结论

本轮没有模型权重、真实课程输入或付费 API Key。语音识别续跑使用确定性 provider 测试；API 协议使用受控响应；双向媒体闭环使用人工给出的源文和译文。没有因此声称已测试 ASR 准确率、数学翻译质量、手写 OCR、教师音色相似度、10 分钟性能或长课资源表现。

便携包通过的是本机隔离开发工具路径验证，不能替代另一台干净 Windows 的完整安装/编解码验收。公开分发所需签名安装器、完整第三方声明与对应源代码分发安排尚需完善。完整剩余范围见 [实现状态](09-implementation-status.md)。

## 2026-10-27：多人声线与参考原声配音扩展

- 回归测试：`123 passed, 1 skipped`。新增本地分人参数/逐词拆分、已译证据保护、重叠说话提示、模型下载安全、每人独立声线、参考精确裁剪、缓存局部失效、手动拆分引用与撤销、旧分人结果不覆盖人工修改、本机克隆 HTTP/WAV 协议等检查。跳过项仍是受限沙箱的 Windows SAPI。
- UI：实际 Coordinator 新增、改名、分配说话人，保存参考声线、参考预听/单句试听按钮，含 emoji 的原文拆分；检查 1120×760 校对页、配音页与声线对话框。分人复核提示正确读取 metadata 并显示在日志与状态中。
- `tools/smoke-system-speakers.py`：实际调用 Microsoft David/Zira 两个系统声音，对相同句子分别合成两位说话人的 WAV，验证结果不同、时间轴混音、字幕与 MP4 全片解码。报告在 `artifacts/system-speakers-report.json`。
- `tools/smoke-speakers.py`：真实 Worker 子进程、FFmpeg 视频、两人独立原声提取、本机 GPT-SoVITS 协议桩、跨语言请求和有效 PCM、缓存命中、模型版本变化后重生成。报告明确为协议验证，不是真实克隆模型。`--frozen` 检查便携 Worker 和内置 FFmpeg，移除外部开发运行时环境。
- 原双向标准声音/字幕/讲义/视频流程再次通过。sherpa-onnx 1.13.8 与 sherpa-onnx-core 1.13.8 已安装并加入便携构建。
- 最终更新包再次通过 `tools/smoke-speakers.py --frozen` 与 `tools/smoke-frozen.py`：隔离 PATH 的便携 GUI、双人参考原声协议、Windows 系统声音、Node/MathJax PDF 与 MP4 导出均通过。此项没有将未取得的分人模型或未安装的克隆模型计为实际推理通过。
- 真实自动分人尚未验收：官方 GitHub 与维护者 Hugging Face 模型下载分别遭遇 TLS EOF 或连接超时，没有得到模型文件。工具提供显式下载与摘要校验，未关闭 TLS 证书校验。
- 真实 GPT-SoVITS 服务和模型未安装，未评测原声相似度、跨语言自然度或多人真实课程效果。多人同时讲话目前仅标记并提示人工处理，不分离叠加人声。
