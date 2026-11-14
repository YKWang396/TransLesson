# 开发与验证

## 环境

按 [源码安装](12-another-computer.md) 建立环境，开发依赖使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[asr,diarization,dev]"
```

`requirements-windows.lock.txt` 记录打包时的依赖版本。`mathbridge` 保留为内部包名和配置命名空间。

## 回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/smoke-ui.py
.\.venv\Scripts\python.exe tools/smoke-guided-review.py
.\.venv\Scripts\python.exe tools/smoke-project-flow.py
.\.venv\Scripts\python.exe tools/smoke-pipeline.py
pwsh -NoProfile -File tools/validate-design.ps1
```

`smoke-pipeline.py` 使用合成媒体与固定文本验证中英双向系统配音及全部导出格式。自动测试中的受控 API 响应验证协议；真实模型效果另外验证。

## 真实课程

保存 API Key、允许课程文本处理，并准备识别模型及声音服务后运行：

```powershell
.\.venv\Scripts\python.exe tools/verify-course.py --source "D:\课程\lesson.mp4" --real --reference
```

工具在仓库 `tmp` 下新建独立项目，经实际 Coordinator 和 worker 从识别运行到导出，核验原文保留、产物依赖、视频时长及完整解码。公式采用明确保留原始证据的路径，不自动认定数学正确。真实文本 API 会产生费用，`--budget` 指定测试预算上限。

可加 `--seconds 120 --start 420` 测试片段；`--resume` 指定上次输出项目并保留相同参数可继续。默认英译中，反向使用 `--source-language zh-CN --target-language en`。预算和已完成结果保留在项目内，不能通过重启测试重置花费。省略 `--reference` 使用系统声音。

其他专项工具：`smoke-asr.py`、`smoke-speech-content.py`、`smoke-frozen-speech.py` 和 `smoke-installed-components.py`。真实课程路径通过参数传入；用户课程和测试产物不提交 Git。

## 打包与部署

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/build-windows.ps1
.\.venv\Scripts\python.exe tools/smoke-frozen.py --https
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/deploy-local.ps1
```

程序输出到 `dist/TransLesson`。部署前关闭正在运行的主程序；独立安装的组件保留。公开分发前还需核对依赖许可、FFmpeg 源代码提供方式与干净机器安装。许可和二进制摘要保存在运行时清单中。
