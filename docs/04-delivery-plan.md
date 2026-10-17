# 实施计划、agent分工与Git约定

## 1. 本次协作与交付状态

本次使用4个agent：1个集成负责人、3个专项agent，分别负责媒体语音、桌面架构、数学与讲义。专项研究写入工作目录，负责人统一选型、接口、主文档与Git提交；桌面agent另做数据契约独立review。交付是设计包与可验证数据样例，没有安装模型或实现桌面程序。

进入开发后沿用4个角色。先完成最小接口和短片fixture，再并行开发；不要同时让多个agent修改共享Schema、依赖锁文件或打包入口。

## 2. 开发分工

| Agent | 独占主目录 | 交付与检查 |
|---|---|---|
| 集成负责人 | `domain/ application/ storage/ worker/ packaging/`、共享契约与锁文件 | revision、任务图、协议、取消恢复、集成、安装包与最终验收 |
| 媒体语音 | `media/ providers/asr/ providers/tts/ providers/separation/` | FFmpeg、ASR、普通TTS、克隆适配、时长对齐与音轨；交中英短片及探测报告 |
| 数学知识 | `math/ providers/translation/ providers/visual_math/ handout/` | DeepSeek/本地语义adapter、公式证据、词表、讲义PDF；交公式/术语标注与PDF预览 |
| 桌面体验 | `ui/`、UI fixture | PySide6、播放器、字幕校对、公式预览、试听、导出；先假provider接协议，再接真实worker |

目录归属是协作约定，不是复杂权限系统。跨模块改动先发接口变更说明，由负责人合并；不得在各分支私自创建不同的Segment类型。

## 3. 开发仓库建议结构

下面是下一阶段的目标结构，不表示这些实现文件已经存在：

```text
src/mathbridge/
  app.py
  domain/                 # 项目、片段、公式、证据、术语、命令
  application/            # 状态机、依赖图、编辑、校对与导出快照
  worker/                 # JSONL、取消、进程树回收
  storage/                # 单写入者、原子保存、revision、缓存
  ui/                     # 窗口、视频、table models、试听
  media/                  # FFprobe、字幕cue、混音、对齐、编码
  math/                   # 语义规则、候选校验、术语一致性
  providers/
    asr/ translation/ visual_math/ tts/ separation/
  handout/                # 证据组织、MathJax helper、ReportLab
assets/fonts/             # 已验证可分发的静态字体与许可
packaging/windows/
tests/fixtures/           # 小型人工标注；大媒体走外部素材目录
contracts/
docs/adr/
```

基础worker优先独立Python 3.11环境；声音增强包可能需不同Python/PyTorch环境，接口隔离。正式锁定版本依照实际兼容测试结果，不在设计阶段编造requirements lock。

## 4. 按可验收闭环排序

| 里程碑 | 目标 | 完成条件与停止条件 |
|---|---|---|
| M0 契约与素材 | 固定时间轴、核心实体、错误码、JSONL；录制/取得有权限的短片 | 正常/错误fixture可验证，源文件只读；没有素材不做模型准确率声明 |
| M1 普通语音闭环 | 60–120秒片段→ASR→Flash翻译→普通TTS→MP4+SRT | 两个语言方向可播放、可编辑、无漏句截尾；先不追求声音像 |
| M2 数学与学习材料 | 语音公式、关键帧公式、术语锁定、证据校对、讲义PDF | 数学错误可定位修复，PDF逐页可读，来源能回查；没有来源的推导不能放行 |
| M3 音色和背景 | 至少一个跨语言克隆provider；背景轨/分离与失败降级 | Windows原生运行路径或选定API验证，中英双向试听可接受；普通TTS不能替代本里程碑 |
| M4 完整桌面体验 | 接入UI、进度、取消恢复、按段重算、费用预算 | 正在编辑不会被旧结果覆盖，试听/导出同revision；API失败保留项目 |
| M5 发布前验证 | 干净Windows安装、10分钟基准、30–60分钟长课、许可证清单 | 验收集通过、资源实测、无外网本地模式验证；不达标就缩小支持范围而非改宣传 |

UI、媒体与数学模块可以在契约冻结后并行，但发布按上述纵向里程碑合并。不要先做一套漂亮页面再等待媒体链条。

排期只能作为开发估算：4个agent协作、1名人类负责人可安排约4–6周的首版验证窗口；这是工程假设，不是承诺。Windows克隆适配、GPU兼容、模型下载与人工数学标注可能成为关键路径。M1短片闭环成功后再重新估算剩余工作。

## 5. Git使用方式

当前交付目录已经是本地Git仓库，`main`为可评审设计。没有配置远端、推送或虚构PR。提交使用任务本地的 `Codex <codex@local.invalid>` 署名，不冒用用户身份，也不修改全局Git配置。

正式开发建议4条分支及独立worktree：

```text
main
feat/core-project       # 集成负责人
feat/media-speech       # 媒体agent
feat/math-handout       # 数学agent
feat/desktop-ui         # 界面agent
```

由负责人基于同一已提交契约起点创建worktree，每个agent在自己的worktree提交。当前设计阶段只由负责人写Git，专项agent没有并发操作仓库索引；下一阶段才需要worktree隔离实现代码。

提交保持可解释的单元，例如：

```text
feat(media): normalize timestamps and extract speech audio
feat(math): preserve formula evidence and protected placeholders
fix(worker): reject stale artifacts after subtitle edits
test(export): verify dubbed video duration and subtitle cues
```

合并前提交模块示例、验证命令、已知限制和接口差异；负责人运行对应检查再合并。新增依赖必须附版本、用途、许可与体积影响，由负责人统一更新锁文件，避免多agent反复解决同一个锁文件冲突。

仓库不提交：原课程视频、完整音频、参考声音、权重、API key、缓存、生成日志与用户项目。小型自录标注可单独决定是否纳入fixture。分发模型不等于Git管理代码；需要manifest与校验和，但权重按需下载/导入。

## 6. 每次模型/提示词升级

固定保留测试集 → 与上一版本对比数学正确性/术语/时间/费用 → 只接受有记录的改进 → 更新模型manifest与缓存命名空间 → 保留用户已确认revision → 发布变更说明。

API别名升级也要视为模型变更。不能因为请求模型字符串没变就假定课程结果完全可复现。
