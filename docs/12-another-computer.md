# 在另一台 Windows 电脑继续使用

更新日期：2026-10-31，适用 0.1.7 开发版本。**换电脑需要分别准备程序、识别模型和课程项目。GitHub 源码不包含后两项，也不包含 API Key；Releases 另提供完整 Windows 便携程序。**

0.1.4 的操作入口见 [AI 课程工作流](13-ai-course-workflow.md)。请使用本版便携包，0.1.3 遗漏 TLS 证书策略文件，会导致打包后 API 连接失败。项目目录中若带有原媒体且指纹一致，程序能自动恢复关联；否则仍须重连原媒体。

## 最省事：复制完整便携程序

从原电脑复制整个 `dist/TransLesson` 文件夹，在另一台 Windows x64 电脑运行其中的 `课译 TransLesson.exe`。不要只复制 EXE，旁边的 worker、`_internal` 及其余文件都要保留。这条路径不需要再安装 Python、Node.js 或 faster-whisper 程序组件。

便携包目前通过的是本机隔离开发工具后的运行检查，尚未在另一台干净 Windows 电脑完成验收。标准配音依赖 Windows 已安装的目标语言声音；如果没有中文或英文声音，需要在 Windows 设置里安装对应语音。

## 从私有 GitHub 仓库取得源码

仓库创建并成功推送后，使用获准访问 `YKWang396/MathBridge` 的 GitHub 账户，在另一台电脑安装 Git 并登录，再运行：

```powershell
git clone https://github.com/YKWang396/MathBridge.git
cd MathBridge
```

私有仓库需要账户权限。登录后也可在仓库页面使用 **Code → Download ZIP** 下载源码并完整解压。ZIP 和 `git clone` 都不带 `.venv`、模型、课程视频、用户项目或本机生成的 EXE。

### 安装与启动源码版

1. 安装 **Python 3.12 x64** 与 **Node.js LTS（包含 npm）**。如果机器上还有其他 Python 版本，先用下面的命令明确创建 3.12 环境。
2. 在刚取得的 `MathBridge` 源码目录打开 PowerShell，执行：

```powershell
py -3.12 -m venv .venv
.\Setup-TransLesson.cmd
```

3. 需要本地识别音频时，再安装 ASR 可选组件：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\setup.ps1 -WithASR
```

4. 使用 `powershell.exe -File tools/run-dev.ps1` 运行源码。Setup 会联网安装软件依赖与本地 MathJax；不会自动下载识别模型或上传课程。

不要从原电脑搬 `.venv` 代替安装，它绑定原电脑的环境和路径。若只导入已有 SRT/VTT 并手动翻译，可跳过 ASR 组件和识别模型。

### 在新电脑生成便携 EXE

完成上述准备后，在源码目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[asr,diarization,dev]"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build-windows.ps1
```

构建完成后，程序在 `dist/TransLesson/课译 TransLesson.exe`。仍须保留完整 `dist/TransLesson` 目录。若需要自动区分说话人，按 [声音后端说明](11-voice-backends.md) 另行准备 sherpa-onnx 组件和模型；参考原声音色还需自行准备 GPT-SoVITS 服务及其模型。

## 复制已有模型和课程

从当前电脑单独复制以下文件夹，可使用移动硬盘或自己的文件传输工具：

| 文件夹 | 用途 | 是否在 GitHub 中 |
|---|---|---|
| 桌面的 `faster-whisper-small` | 把讲话识别为带时间的源字幕；至少保留 `model.bin`、`config.json`、`tokenizer.json`，建议完整复制 | 否 |
| 桌面的 `trans` 整个文件夹 | 课程原文、译文、项目历史、音频和处理缓存 | 否 |
| 原始课程视频 | 播放、证据定位、配音混音和视频导出所需的源文件 | 否 |

当前课程视频也在 `trans` 中；完整复制该目录时一并带走。其他课程的源视频可能在目录之外，必须另行复制。不要只取 `project.json`，否则历史、证据图片及缓存可能缺失。

在新电脑启动后：

1. 在“处理设置 → 本地处理”重新选择复制来的 `faster-whisper-small` 模型目录。
2. 点击“打开项目”，选择复制来的 `trans` 文件夹。
3. 如果提示原媒体找不到，在校对页“重连媒体”选择复制后的原视频。程序会检查文件内容指纹；仅同名、重新压制过的视频不能代替原视频。
4. 在“处理设置 → 文本与隐私”重新填写自己的 API 服务、模型、Key、预算，并明确允许发送课程文本。原电脑 Windows 凭据库中的 Key 不会随项目或 Git 迁移。
5. 已生成的原文和译文可以直接继续编辑，不需要重新识别 594 句。只有修改、清空或明确修复的部分才需要重新处理；新电脑声音不同或配音读法改变时，旧配音缓存可能需要重新生成。

## 继续处理当前课程

0.1.2 提供“问题清单”和“只看问题句”；普通字幕不必逐句点击确认完才能试配音或导出字幕。批量确认代表你接受当前已填写的文本，不会替你证明翻译或数学正确。

当前课程有 11 个旧规则造成的误识别公式：程序把 `the x / take x / suppose x` 中的字母片段当成了 `e^x`，并影响了对应译文。使用新版提供的旧公式修复入口，只清理能明确证明由该错误规则产生的候选，并重新生成这 11 句受影响的译文，再检查结果。不要把它们全部标为“公式正确”来跳过问题。

配音过长时，问题清单会显示具体句子、时间、合成时长和可用时长，点击后可缩短“配音读法”，或把同一说话人的相邻短句合并。有限借用后方停顿可以减少过快朗读，但无法保证所有长译文都能自然塞入原时间。

## 仍需人工检查的内容

真实数学课的识别与翻译质量尚未完成系统验收，尤其要检查变量、否定词、公式、上下标和论证条件。自动分人和参考音色也仍需真实模型验证；程序尚不自动识别全部手写板书、不分离同时重叠的讲话，也不保证干净背景声。完整课程的配音与视频导出完成后，应试听并检查末尾内容。

Git 能恢复已提交的程序版本，项目的 `history` 保存课程编辑快照；两者都不代替课程媒体、模型及项目文件夹的独立备份。
