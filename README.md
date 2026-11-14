# 课译TransLesson

课程翻译工作台。将本地中英文课程制作成目标语言视频、字幕和教学讲义。

## 开始使用

从 Releases 下载 Windows 程序包，完整解压后双击 **课译TransLesson.exe**。保留同目录的 worker、`_internal` 和其他文件。

1. 建立项目并选择课程视频，或打开已有项目。
2. 识别原文，也可导入已有 SRT/VTT。
3. 执行 **AI 断句并翻译**，抽查原文、译文和数学表达。
4. 选择系统声音或参考原声音色，自动生成并匹配配音。
5. 生成 **AI 教学讲义**，导出视频、字幕、音频或 PDF。

项目默认保存处理结果；导出时可以另选目录。已完成的片段会按内容指纹复用，修改后仅重算受影响部分。

## 功能

- 本地 faster-whisper 识别、上下文断句、中英互译及可编辑字幕。
- 千问、豆包、Kimi、GPT、DeepSeek、自定义兼容文本接口及本地 llama.cpp。
- 数学表达、术语、读法和来源分开保存；讲义按课程主题组织。
- 多说话人声音配置、原声参考自动填写、配音节奏匹配和漏读筛查。
- 项目恢复、编辑历史、问题定位、集中试听及任务取消。
- MP4、WAV、SRT/VTT/ASS、PDF、Markdown 和项目 JSON 导出。

## 本地组件

```text
课译TransLesson/
  课译TransLesson.exe
  translesson-worker.exe
  _internal/
  components/
    faster-whisper-small/  # 识别模型
    GPT-SoVITS/            # 可选的参考原声配音服务及模型
```

程序包包含应用运行依赖；大型模型与 GPT-SoVITS 环境单独准备。在“处理设置 → 组件与识别”选择组件目录。文本处理需要相应平台的 API Key；Key 可保存在 Windows 凭据库，课程视频和模型不会随源码上传。

## 文档

- [操作指南](docs/08-user-guide.md)
- [文本 API](docs/20-multiple-text-apis.md)
- [声音与多人配音](docs/11-voice-backends.md)
- [安装与迁移](docs/12-another-computer.md)
- [架构](docs/02-design.md) · [数据契约](docs/03-contracts.md)
- [开发与验证](docs/development.md) · [当前验证结果](docs/testing.md)

自动识别、翻译和声音筛查仍需抽查数学条件、变量、否定词与发音；音色参考不保证完整复现原讲者的表现力。
