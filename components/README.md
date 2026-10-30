# 本地组件

将组件分别放入软件根目录的以下位置：

```text
课译TransLesson.exe
components/
  faster-whisper-small/
    model.bin
    config.json
    tokenizer.json
  GPT-SoVITS/
    runtime/python.exe
    api_v2.py
    GPT_SoVITS/configs/tts_infer.yaml
```

应用默认识别随软件放置的组件。也可在“处理设置 → 组件与识别”选择整个 `components` 目录，或展开自定义位置分别指定目录；有效的自定义位置会保留。

在“03 配音”或“处理设置 → 配音与字幕”点击“启动声音服务”。启动、停止脚本随软件提供，无需在 GPT-SoVITS 自己的目录里另找脚本。

faster-whisper 的运行库已包含在 Windows 程序中，这里只需模型文件。GPT-SoVITS 使用自身整合包中的 Python、PyTorch、CUDA 运行库与权重，保留整个整合包。程序不自动下载或更新它们。

换电脑时复制整个软件目录，包括 `components`；机器仍需适合 GPU 的显卡驱动。模型、第三方运行环境和许可证属于各自项目，不放入本应用 Git 源码。
