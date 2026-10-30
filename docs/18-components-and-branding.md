# 0.1.9：课译TransLesson 与本地组件

软件名称统一为 **课译TransLesson**，用途为 **课程翻译工作台**。窗口、任务栏、EXE、启动入口和桌面快捷方式使用提供的 `logo.png` 图标。首页、配音页和“关于”精简了宣传口号、重复说明和开发验证文案，保留操作引导、实际进度和具体错误。

## 软件目录

```text
课译TransLesson/
  课译TransLesson.exe
  translesson-worker.exe
  _internal/
  components/
    faster-whisper-small/
    GPT-SoVITS/
    启动声音服务.cmd
    停止声音服务.cmd
    gpt-sovits-service.ps1
```

双击根目录的 EXE。应用默认识别本目录中的组件，无需重复选择模型。需要更换位置时，进入“处理设置 → 组件与识别”，选择整个 `components` 目录；也可展开“自定义组件位置”分别选择两个目录。有效的自定义位置会保留，迁移电脑后旧目录失效时改用随软件放置的组件。切换整个组件目录时会同时更新两项位置，避免混用另一套组件。

Whisper 模型用于本地语音识别，faster-whisper 运行库已包含在 Windows 程序中。GPT-SoVITS 整合包包含独立 Python、PyTorch 和配音模型，必须保留整个文件夹。程序不会在使用时自动下载或更新模型。

## 启停声音服务

在“03 配音”点击“启动声音服务”，或在“处理设置 → 配音与字幕”启动、停止服务或打开日志。无需先在终端执行命令。

内置脚本使用 GPT-SoVITS 的 `api_v2.py` 和整合包的 Python，自动设置 FFmpeg 搜索路径及离线模型环境。默认地址为 `http://127.0.0.1:9880`。界面异步检查 `/openapi.json` 的 GPT-SoVITS 接口；只有模型服务实际就绪才显示“已就绪”。加载过程中界面仍可操作，启动失败时可查看 `components/GPT-SoVITS/output` 中的日志。

重复启动不会重复加载同一个已管理进程。停止操作核对 Python 路径、API 命令、端口和监听地址，仅停止本应用管理的实例；用户自行启动的其他服务不会被停止。退出课译不会自动关闭声音服务，可在设置或组件目录的脚本中停止。

组件目录中的两个 CMD 是额外的手动入口，应用内按钮使用随程序打包的同一份 PowerShell 管理脚本。更换显卡或电脑后，仍需适合该 GPU 的显卡驱动。

## 本机与 GitHub

本机整理后的完整软件位于桌面的 `课译TransLesson` 文件夹，已放入现有 GPT-SoVITS 与 faster-whisper-small。整合包较大，原桌面下载目录保留为备份。

换电脑时复制整个软件文件夹，包括 `components`；项目与课程视频另行携带。Git 源码和常规 Windows Release 不包含模型权重和 GPT-SoVITS 的大型第三方运行环境，单独下载程序包时需要补入组件。

Windows 设置与凭据仍使用原存储标识，改名不会清除已有项目、API 设置或密钥。
