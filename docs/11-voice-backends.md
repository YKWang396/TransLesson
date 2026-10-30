# 多人原声配音与本地声音服务

更新日期：2026-10-30。

配音默认自动匹配节奏，通常无需预设整体速度；剩余问题可集中试听。操作与边界见 [自动节奏配音](14-adaptive-dubbing.md)。

## 参考原声配音：GPT-SoVITS

参考原声配音使用每位说话人的原视频片段作为声音参考，再生成目标语言的讲话。英语参考音频可以用于生成中文配音，中文参考音频也可以用于生成英语配音。音色相似度取决于参考录音、模型及译文的读法，需要试听后确认。Windows 系统声音仍可用于不需要模仿音色的项目。

MathBridge 提供 GPT-SoVITS `api_v2.py` 的本机客户端。**GPT-SoVITS 服务与模型不包含在 MathBridge 便携包中，需要另外准备并启动。** 本次没有下载大型声音模型，也没有对真实老师的音色相似度做验收。

### 1. 准备服务

从 [GPT-SoVITS 官方项目](https://github.com/RVC-Boss/GPT-SoVITS) 获取 Windows 整合包，或按[官方中文安装说明](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/docs/cn/README.md)安装独立环境。请将该环境放在单独文件夹；MathBridge 自带的 Python 环境用于桌面程序，不承担 GPT-SoVITS 的 PyTorch 环境。

下载并准备与所选版本匹配的 GPT、SoVITS、中文文本前端及语音特征模型；官方说明中的预训练模型目录是 `GPT_SoVITS/pretrained_models`。模型版本和文件名会变化，应以当前使用的服务版本配置为准。首先在 GPT-SoVITS 自带界面确认中英文推理正常，再连接 MathBridge。

编辑 GPT-SoVITS 的 `GPT_SoVITS/configs/tts_infer.yaml`，核对 `custom` 下的模型文件路径及版本。CPU 环境使用 `device: cpu`、`is_half: false`；使用 NVIDIA 显卡时，应按该服务自己的安装说明准备匹配的 PyTorch/CUDA 环境并配置设备。不要直接沿用示例配置中的 CUDA 设置。

在 **GPT-SoVITS 根目录**打开 PowerShell。使用它的独立 Python 环境运行：

```powershell
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

如果使用包含 `runtime/python.exe` 的 Windows 整合包，可在整合包根目录运行：

```powershell
.\runtime\python.exe -I api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

服务启动后保持运行。只打开 WebUI 不等于启动了 `api_v2.py` 服务。默认连接地址为 `http://127.0.0.1:9880`，地址末尾带 `/tts` 也可。MathBridge 只允许本机回环 HTTP 地址，不跟随重定向，也不使用系统代理。

### 2. 为每人选取参考原声

每位说话人应使用自己的 **3–10 秒 PCM WAV**，建议选择 4–8 秒连续说话、音量正常且没有其他人抢话的片段。参考原文须准确对应这段音频，参考语言选择音频实际语言。背景音乐、混响、嘈杂和多人重叠会降低音色匹配效果。

例如，老师使用一段英语讲解作为参考，学生使用一段自己的英语提问作为参考；给老师和学生的中文译文分别调用配音时，传入各自的参考音频和原文。不要将两人的声音混在同一个参考片段中。

当前客户端要求服务与 MathBridge 运行在同一台电脑，因为服务读取传入的绝对参考文件路径。Docker、WSL 或另一台机器无法直接读取 Windows 工程路径时，需要先解决路径挂载；当前客户端不代做跨环境路径转换。

### 3. 连接检查与异常处理

连接检查读取本地 FastAPI 的 `/openapi.json`，确认存在 `POST /tts`；这只能证明接口可访问，不能代替实际试听。客户端向 `/tts` 发送目标读法、目标语言、参考 WAV 路径、参考原文与参考语言，并要求非流式 WAV 响应，协议见[官方 API 源码](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/api_v2.py)。

返回音频经过格式、长度和完整性检查后才原子保存；JSON 错误、无效音频和超时不会替换已有的成功结果。取消操作会立即停止客户端等待并阻止迟到结果写入工程；独立的 GPT-SoVITS 服务可能仍在计算该请求，需要在服务控制台观察。MathBridge 不会自动重试生成，也不会自动切换成另一种声音。

服务端切换模型后，应同步更改 MathBridge 的声音模型标识，避免把旧模型生成的音频当成新结果复用。模型标识是缓存区分信息，不会让客户端远程切换服务端权重。

## 验证范围

客户端测试覆盖了真实本机 HTTP 请求、跨语言参数、有效 PCM WAV、服务错误、禁止远程地址和重定向、等待响应时取消、超时，以及无效输出保留旧文件。本轮没有安装或启动真实 GPT-SoVITS 大模型，因此没有将声音相似度、速度或显存占用列为已验证结果。

## 本地多人说话人区分：sherpa-onnx

说话人区分判断原视频的每个时间段属于哪一位说话人，再将结果与 Whisper 的逐词时间对齐。声线编号按首次出现顺序分配，可在应用中改为老师、学生等名字；系统不会根据声音猜测真实身份。

本功能使用 CPU ONNX 推理，不需要 CUDA，也不需要 Hugging Face Token。需要两个独立模型：Pyannote 分段模型与 NeMo Titanet small 声线特征模型。下载来源、用法及模型许可请参看 [sherpa-onnx 官方模型说明](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html)。它们与 Whisper 模型用途不同，不能将 Whisper 模型目录填到说话人设置中。

在 MathBridge 源码根目录打开 PowerShell，运行：

```powershell
.\.venv\Scripts\python.exe -m pip install ".[diarization]"
.\.venv\Scripts\python.exe .\tools\setup-speakers.py
```

第一条安装本地识别组件；若当前便携包已包含该组件，则无需再安装。第二条是**用户显式运行的模型下载工具**，会从 k2-fsa/sherpa-onnx 官方 GitHub Releases 下载模型，不上传视频或语音。应用处理课程时不会自动下载模型。工具只读取压缩包内指定的普通模型文件，不展开任意压缩包路径；官方 Release API 提供 SHA-256 时会核对下载摘要，并在模型目录的 `manifest.json` 中记录下载来源与文件摘要。

默认模型文件位于源码根目录下的 `models/speakers/segmentation.onnx` 与 `models/speakers/embedding.onnx`。在说话人设置中分别选择这两个文件。人数填 0 时自动估计；已知实际人数时可直接填写，以减少聚类错误。

如果当前网络无法访问 GitHub Releases，可显式选择维护者 Hugging Face 副本，工具会核对固定的原模型 SHA-256：

```powershell
.\.venv\Scripts\python.exe .\tools\setup-speakers.py --source huggingface
```

副本由 sherpa-onnx 维护者提供：[分段模型](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0)、[声线特征模型](https://huggingface.co/csukuangfj/speaker-embedding-models)。此选项只替换两个模型的下载源，测试音频仍取自官方 GitHub Release。

先进行本地转写，再分析说话人，最后翻译与配音。原文与逐词时间仍匹配的未翻译段可以在换人处自动拆分。已经翻译、含公式或术语、被讲义引用的段会保留文字与引用，并提示手动检查归属。字幕来源没有逐词时间时，系统也会保守保留整段，避免凭空拆分文字。

多人同时讲话会被标记为待校对；当前分析只识别重叠情形，不将同时讲话的人声分离成干净的独立音轨。短促插话、相似声线、远处收音和背景音乐仍可能造成误分，需要试听并修正。

开发验证可显式下载官方四人样例：

```powershell
.\.venv\Scripts\python.exe .\tools\setup-speakers.py --with-test-audio
```

样例保存在 `tmp/speaker-diarization/0-four-speakers-zh.wav`，不进入课程工程，也不作为真实数学课准确率证明。
