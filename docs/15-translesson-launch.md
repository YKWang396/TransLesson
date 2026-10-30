# 0.1.6：课译 TransLesson 与 EXE 启动入口

更新日期：2026-10-31。

软件正式名称为 **课译 TransLesson**。窗口标题、首页品牌、提示框、关于窗口、EXE 文件属性和便携包名称均使用这个名称。

## 直接启动

当前电脑打开 `MathVedioTranslation` 文件夹即可看到 **课译 TransLesson.exe**。进入其中的源码文件夹，也有同名 EXE。这两个入口按相对路径找到完整运行程序，不依赖当前电脑的 Python 开发环境；整个文件夹移动后仍可使用。

Windows 便携包完整解压后，第一层就是 **课译 TransLesson.exe**，双击启动。请一起保留 `_internal`、`translesson-worker.exe` 等运行文件。普通使用无需运行 Start 脚本或安装源码开发环境。

已有课程可从“最近项目”打开；原项目、模型目录、API 设置和 Windows 凭据继续兼容。新建项目默认建议保存在文档目录的 `TransLessonProjects` 中。

## 开发构建

源码开发环境准备完成后执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/build-windows.ps1
```

构建完整便携程序到 `dist/TransLesson`，并生成源码目录和外层工作目录的 EXE 启动入口。工作目录入口为小型 Windows GUI 程序，普通启动不会显示命令行窗口；实际应用、工作进程和依赖仍在便携目录。

只运行源码进行调试时，可使用 `powershell.exe -File tools/run-dev.ps1`。开发安装入口改名为 `Setup-TransLesson.cmd`，原 `Start-MathBridge.cmd` 已移除。

应用继续使用原来的项目格式、内部 Python 包与设置存储标识，避免改名造成旧设置或课程记录丢失。GitHub 仓库地址保留原地址，发布文件使用 TransLesson 名称。
