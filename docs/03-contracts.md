# 数据与 Provider 接口

这些是供开发实现的接口约定，并非已经实现的 API。`project.schema.json` 只覆盖可编辑课程核心；任务、缓存和导出记录是独立文件，避免把所有状态塞进一个 JSON。

## 1. 项目目录

```text
<用户选定的项目目录>/
  project.json                  # 符合 contracts/project.schema.json
  runtime.json                  # 任务、模型版本、错误、checkpoint
  artifacts.json                # 产物相对路径、sha256、依赖与导出revision
  inputs/source.link.json       # 外部原视频绝对路径与指纹；不强制复制大视频
  frames/                       # 原帧与裁剪，文件名含规范化时间
  audio/                        # 原音频、参考片段、分句配音、混音
  cache/                        # 按依赖指纹存储的可重建数据
  notes/                        # 讲义结构JSON、Markdown、公式LaTeX/SVG
  exports/<revision>/           # 视频、SRT/VTT、PDF、导出清单
  logs/                         # 默认不记录整段课程文本，不记录API密钥
```

项目逻辑路径为相对项目根目录的 POSIX 风格路径；原视频位置存于单独 link 文件。JSON 样例中的 `media/example.mp4` 只是占位路径。不得因为样例 SHA256 为零而当成真实缓存命中。重开项目若外部视频丢失，要求重新定位，并按内容指纹判断是否同一个输入。

所有 worker 输出路径先规范化并校验位于项目根目录，禁止 `../` 越界。导出到用户选择的外部目录仅由协调器执行，先写临时文件，再原子提交；目标文件已存在时明确选择覆盖或另存。

## 2. 核心实体

| 实体 | 必需字段与规则 |
|---|---|
| Project | `project_id`、`schema_version`、`revision`、语言对、源指纹、各实体列表；用户确认修改后 revision 自增 |
| Segment | 不变 ID、`start_ms/end_ms`、speaker、原文、译文、纯文本字幕、TTS读法、公式/术语ID、校对状态 |
| Formula | 原始表达、`latex`（可暂为 null）、源/目标读法、一个以上来源证据、校对状态及警告 |
| Term | 原术语、目标术语、适用上下文、锁定状态、来源；不同语义可有不同ID |
| Evidence | 时间区间、speech/frame类型；frame额外携带帧路径及归一化 `bbox_xyxy` |
| Note block | course/supplement、正文、segment/formula引用、校对状态；course至少有一种非空来源引用 |
| Speaker | 首版主讲人一个；standard 或 reference；reference 要有参考片段及已确认声音使用权限标记 |

`{{formula-1}}` 是受限公式占位符，只允许引用已有公式ID。模型不得把占位符改写成新编号。界面用数学渲染器显示，SRT导出用纯文本读法/Unicode，TTS用 `spoken_target`；后两者不是直接读 LaTeX。

`Segment` 是语义/配音单位，不等于字幕 cue。独立生成 `subtitles.json` 产物，其中每个 cue 有 `id / segment_id / start_ms / end_ms / text / formula_ids`；一个 Segment 可拆成多个 cue。人工断行与拆cue只失效字幕和相应视频渲染，不影响未改文案的 TTS。

`confirmed` 表示用户/校对流程接受的状态，不等于模型高置信输出。自动结果默认 `needs_review`，允许按项目规则自动接受低风险普通句，但不得据此自动确认高风险数学表达。`preserve_source` 表示用户已选择保留原截图/原表达，仍在讲义中标明未识别。

## 3. 时间与一致性规则

- 全项目时间以原视频规范化播放零点为基准，单位整数毫秒。`timeline_origin_ms` 记录原容器起始PTS映射；不能通过帧序号/平均帧率推算可变帧率视频的时间。
- 句子满足 `0 <= start < end <= duration`；frame证据可 `start == end`，语音区间必须有正时长。
- 坐标范围 `[0,1]` 且 `x1 < x2, y1 < y2`，记录所依据原帧分辨率到 artifacts 元数据。
- 首版单讲者配音段不重叠。字幕可分组显示，但出现时轴冲突必须在导出前修复。
- `speaker_id`、公式ID、术语ID、讲义引用必须存在，实体ID在各自集合唯一；删除被引用实体必须先修复引用。
- 已确认公式必须有非空可渲染LaTeX；无法解析则退回待校对或保留来源。渲染成功不等于数学正确。
- 术语锁定后，译文出现未允许的同义变体应报警。锁定含义与上下文，不能对普通词做全局替换。
- `source_text` 保留可追溯听写，用户纠正写入新revision；模型不得无痕覆盖历史已确认版本。

JSON Schema 验证字段与基础类型；跨字段时序、引用完整性和数学渲染由应用层验证。Schema 升级时先备份，禁止静默用旧版本程序覆盖新版本项目。

## 4. Provider 最小协议

以 Python Protocol 实现，下面描述语义。所有请求含 `request_id`、输入产物ID、语言、模型ID/revision、参数、cancel token；结果含产物路径/结构化记录、警告、耗时和能力信息。密钥仅在进程内传递，不进入这些记录。

```python
class ASRProvider:
    def transcribe(self, audio, language, glossary, ctx) -> Transcript: ...

class VisionMathProvider:
    def recognize(self, frame_regions, ctx) -> list[FormulaCandidate]: ...

class TranslationProvider:
    def translate(self, segments, context, locked_glossary, ctx) -> TranslationBatch: ...
    def draft_notes(self, evidence_blocks, outline, ctx) -> NoteDraft: ...

class TTSProvider:
    def capabilities(self) -> TTSCapabilities: ...
    def synthesize(self, text, language, voice, reference, ctx) -> AudioArtifact: ...

class SeparationProvider:
    def separate(self, audio, ctx) -> StemArtifacts: ...
```

`TTSCapabilities` 至少声明语言列表、是否支持跨语言参考音色、参考时长范围、CPU/GPU、可取消粒度、可用音频格式。UI 只展示当前 provider 真正支持的选项，不用“声线相似”通用开关假定所有引擎都支持。

结果为结构化JSON，不从自由文本里猜字段。翻译批次按ID回填，检测缺句、重复ID、公式占位符丢失、目标语言异常；最多一次自动修复，然后保留原结果与错误让用户重试。所有模型版本与提示词版本参与缓存key。

## 5. worker 消息协议

主进程与 worker 用 stdin/stdout JSON Lines 通信；日志写 stderr。模型子进程原始 stdout 不可混进协议流。无需启动对外服务器。

```json
{"type":"command","request_id":"req-1","op":"run_stage","stage":"translate","project_revision":3,"inputs":["asr:sha256-..."]}
{"type":"progress","request_id":"req-1","stage":"translate","completed":12,"total":40,"unit":"segment"}
{"type":"artifact","request_id":"req-1","id":"translation:sha256-...","path":"cache/translation.json","input_revision":3}
{"type":"warning","request_id":"req-1","code":"FORMULA_AMBIGUOUS","entity_ids":["formula-1"],"retryable":false}
{"type":"done","request_id":"req-1","status":"succeeded","input_revision":3}
```

协调器检查 `request_id`、阶段状态及输入revision；旧任务迟到的结果不覆盖新编辑。取消先发合作式 cancel，在provider的安全边界保存checkpoint；超时后终止整个任务进程树。重开项目将遗留 running 标为 interrupted，验证产物后恢复，不冒充仍在运行。

## 6. 状态、缓存与导出快照

阶段状态：`pending → running → succeeded`；还包括 `failed`、`cancelled`、`interrupted`、`stale`、`skipped`。校对状态独立保存，不用任务状态代替。

缓存键：`SHA256(stage + input_artifact_hashes + settings + model_revision + prompt_version + schema_version)`。输入媒体的完整指纹在首次任务流式计算；文件mtime/大小只可用于快速提示，不能证明未变化。checkpoint在安全单位（句、批次、帧）完成后提交。

| 用户修改 | 必须失效 | 可复用 |
|---|---|---|
| 译文/目标读法 | 该句TTS、字幕、含该句讲义、最终混音/导出 | 原ASR、帧OCR |
| 公式LaTeX | 公式渲染、引用它的字幕/读法/讲义；读法改变才重跑对应TTS | 不相关句与帧 |
| 术语映射 | 使用该词义的译句与讲义，随后其TTS及导出 | ASR与无关译句 |
| 源语言 | ASR及其全部下游；视觉文字语言改变则OCR也失效 | 原媒体提取与原帧 |
| 目标语言 | 翻译、术语目标映射、配音、字幕、讲义与导出 | ASR、视觉LaTeX和原始证据 |
| 配音人/参考片段 | 相关TTS与混音/视频导出 | 译文、字幕、讲义 |
| 字幕风格/输出分辨率 | 对应视频渲染 | 识别、翻译、配音 |
| 外部源文件内容改变 | 新项目输入，全流水线重算 | 不沿用旧输入缓存 |

编辑影响下游由依赖图传播。导出固定使用一份revision快照；导出过程中继续编辑不能改变正在生成的内容。产物清单保存所有依赖hash、模型/软件版本、背景音策略、残留警告与用户确认项，完成后显示“导出基于revision N”。

## 7. 最小错误码

`UNSUPPORTED_MEDIA`、`MISSING_MODEL`、`LANGUAGE_UNAVAILABLE`、`OUT_OF_MEMORY`、`DISK_FULL`、`SOURCE_CHANGED`、`SOURCE_MISSING`、`NETWORK_DISABLED`、`API_AUTH_FAILED`、`PROVIDER_TIMEOUT`、`INVALID_TRANSLATION`、`FORMULA_AMBIGUOUS`、`TTS_TOO_LONG`、`BACKGROUND_LEAKAGE`、`PDF_RENDER_FAILED`。

错误需含：受影响对象、已保留哪些结果、能否重试、建议操作；不把“发生错误”当成可用的失败界面。
