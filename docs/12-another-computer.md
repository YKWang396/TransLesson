# 安装与迁移

## 使用 Windows 程序包

完整解压 Releases 中的 Windows x64 程序包，运行第一层的课译TransLesson.exe。程序包包含桌面运行依赖，不需要再安装 Python 或 Node.js。请保留 worker、`_internal`、组件脚本及其他文件。

大型识别模型和 GPT-SoVITS 环境单独准备：

```text
components/faster-whisper-small/
components/GPT-SoVITS/
```

在“处理设置 → 组件与识别”挂载该目录，也可自定义各组件的位置。标准配音需要 Windows 中可用的目标语言声音。

## 换电脑继续课程

1. 复制完整软件目录，包括已安装的 `components`。
2. 复制整个课程项目目录和原视频；不要只复制 `project.json`。
3. 打开项目，必要时重新挂载组件和重连原视频。
4. 重新填写各平台 API Key 并测试。Windows 凭据库不会随项目或程序复制。

如果新机器更换了声音或模型，相关配音缓存需要重新生成。Git 中保存程序源码；课程、模型、Key 和生成缓存单独备份。

## 从源码安装

准备 Python 3.12 x64、Node.js（含 npm）和 Git。取得源码后在仓库目录执行：

```powershell
py -3.12 -m venv .venv
.\Setup-TransLesson.cmd
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/setup.ps1 -WithASR
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/run-dev.ps1
```

Setup 会安装软件依赖和 MathJax，不自动下载识别模型。使用现成字幕且不做本地识别时，可跳过 `-WithASR`。开发、测试与打包见 [开发说明](development.md)。
