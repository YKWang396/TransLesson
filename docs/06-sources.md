# 技术来源与选型记录

核查日期：2026-10-18。以下为官方仓库、官方模型卡或官方文档。链接内容可能更新；发布时固定实际文件revision、SHA256、运行库与许可，记录到模型/依赖manifest。本次没有安装模型或调用付费API。

## 桌面与媒体

| 依据 | 对设计的影响 |
|---|---|
| [Qt for Python](https://doc.qt.io/qtforpython-6/) / [QThread](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html) / [QProcess](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QProcess.html) | 窗口与计算分离，使用异步信号和独立进程 |
| [Qt许可](https://doc.qt.io/qtforpython-6/commercial/index.html) / [模块许可](https://doc.qt.io/qt-6/licensing.html) / [部署](https://doc.qt.io/qtforpython-6/deployment/index.html) | PySide6/Qt按实际模块处理LGPL/GPL/商业许可和打包 |
| [Tauri process model](https://v2.tauri.app/concept/process-model/) / [sidecar](https://v2.tauri.app/develop/sidecar/) / [Windows installer](https://v2.tauri.app/distribute/windows-installer/) | 比较后首版选Python统一生态，保留换壳可能 |
| [FFmpeg滤镜](https://ffmpeg.org/ffmpeg-filters.html) / [许可](https://ffmpeg.org/legal.html) | atempo、混音与音量工具可用；分发义务由实际构建决定 |

## 识别、语言与声音

| 依据 | 对设计的影响 |
|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 多语种ASR、CPU int8、VAD、时间戳；GPU运行库按版本锁定 |
| [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B) / [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) | 离线量化文本路径，结构化输出后仍做本地语义校验 |
| [DeepSeek模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) / [图像理解](https://api-docs.deepseek.com/guides/vision/) / [思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/) | 推荐混合语义provider，别名/能力/成本独立配置，按需发公式裁剪 |
| [Windows SpeechSynthesizer](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer) | 枚举已安装语音、合成为WAV；目标语言声音不保证预装 |
| [CosyVoice](https://github.com/QwenAudio/CosyVoice) / [所选CosyVoice3模型卡](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512) | 支持中英跨语言参考合成的候选；Windows安装仍须单独验证 |
| [ElevenLabs voices](https://elevenlabs.io/docs/overview/capabilities/voices) / [TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert) | 可选Instant克隆API，不引入Professional训练流程；权限/费用另查账户配置 |
| [Demucs](https://github.com/facebookresearch/demucs) / [Windows文档](https://github.com/facebookresearch/demucs/blob/main/docs/windows.md) | 归档音乐分轨项目，仅可选兼容适配，不保证课堂人声擦除 |

## 数学与PDF

| 依据 | 对设计的影响 |
|---|---|
| [Pix2Text](https://github.com/breezedeus/Pix2Text) / [用法](https://github.com/breezedeus/Pix2Text/blob/main/docs/usage.md) / [许可](https://github.com/breezedeus/Pix2Text/blob/main/LICENSE) | 本地文字/公式混排与区域识别；首版只加载必要模块 |
| [MFD 1.5模型卡](https://huggingface.co/breezedeus/pix2text-mfd-1.5) / [MFR 1.5模型卡](https://huggingface.co/breezedeus/pix2text-mfr-1.5) | 所选卡标MIT，不能据此推断所有高级/商业变体许可 |
| [MathJax Node组件](https://docs.mathjax.org/en/latest/server/components.html) / [SVG配置](https://docs.mathjax.org/en/v4.0/options/output/svg.html) / [Safe边界](https://docs.mathjax.org/en/latest/options/safe.html) / [LICENSE](https://github.com/mathjax/MathJax/blob/master/LICENSE) | 离线LaTeX数学子集→SVG；限制宏和外部资源，Safe并非完整沙箱 |
| [ReportLab Platypus](https://docs.reportlab.com/reportlab/userguide/ch5_platypus/) / [字体](https://docs.reportlab.com/reportlab/userguide/ch3_fonts/) / [FAQ](https://docs.reportlab.com/developerfaqs/) | PDF分块分页、TrueType嵌入、中文断行、BSD Toolkit |
| [Typst面向LaTeX用户的指南](https://typst.app/docs/guides/for-latex-users/) | 数学语法不同，首版不把识别出的LaTeX直接当Typst源码 |
| [svglib](https://github.com/deeplook/svglib) | 当前LGPL-3.0，首版不用其SVG→PDF路径，未来引入单独核验 |

## 需要实验才能确定的内容

官方有功能描述不等于本应用已完成集成。尤其需要验证：DeepSeek对本课程公式准确率与usage、4B量化模型的数学翻译水平、Pix2Text手写效果、CosyVoice原生Windows路径、跨语言音色自然度、课堂背景分离、Qt媒体后端编解码、中文字体和长式分页、安装包与模型实际大小。

所有性能数字、质量门槛与估算排期均是本项目建议，不是从厂商材料推导出的保证。
