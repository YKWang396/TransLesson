# 多服务商文本 API（0.2.1）

在“处理设置 → 文本与隐私”选择服务商，填写对应 Key 和账户可用的模型，再点“测试连接与 JSON”。勾选允许发送课程文本并应用设置后，AI 断句、翻译和教学讲义都使用这一配置。它们共用项目预算，切换平台不重置已发生费用。

| 服务商 | 预填地址 | 配置要点 |
|---|---|---|
| 千问／阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 默认北京地域；可修改为业务空间专属地址，Key 与地址须同地域。 |
| 豆包／火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 使用方舟 API Key；模型可填平台 Model ID 或已开通的 `ep-…` 接入点 ID。 |
| Kimi／月之暗面 | `https://api.moonshot.cn/v1` | 使用开放平台 API Key；K3 系列采用低推理强度，保留平台要求的思考消息。 |
| GPT／OpenAI | `https://api.openai.com/v1` | 使用 OpenAI API Key；按模型使用输出及推理参数，不固定传入温度。 |
| DeepSeek | `https://api.deepseek.com` | 原来的官方地址和已保存 Key 继续可用。 |
| 自定义兼容接口 | 手动填写 | 支持 HTTPS Chat Completions；可直接粘贴完整 `/chat/completions` 地址。 |
| 本地 llama.cpp | `http://127.0.0.1:8080/v1` | 需要自行启动本机兼容服务，模型名称可编辑。 |

模型列表是可编辑建议，不表示你的账户已拥有访问权限。地址、模型、文本上传许可和费率分别记住；Key 按“服务商＋地址”隔离，不会把 DeepSeek Key 自动发送给另一平台。切换到新地址时需要填写该地址的 Key；切回来可恢复会话中的原 Key。取消设置不会改变 Windows 凭据库，应用设置才保存或删除勾选的凭据。Key 不随项目、Git 或另一台电脑迁移。

连接测试只发送固定的 `{"ok":true}` 请求，不需要打开课程，也不发送视频、音频或课程文字。它验证网络、认证、模型和 JSON 结构，可能产生少量 token 费用，结果显示按配置费率估算的金额。测试在后台运行，取消设置或切换服务商／地址时停止本次测试。它是单独的诊断请求，不计入课程项目预算；服务商账单仍包含该请求。

日常只需设置项目金额上限。高级设置可修改每百万 token 的人民币估算费率；预填费率对应列表首选模型，其他模型、上下文档位、地域、缓存和优惠均可能不同。OpenAI 预填估算使用美元单价乘固定 7.3，不是实时汇率。实际费用以平台账单为准。自定义接口若不接受 `response_format`，可关闭“接口支持 JSON 模式”；程序仍会严格检查返回正文的 JSON 和来源引用，失败不会静默更换平台。

典型报错会显示服务商与原因：认证／权限失败时核对 Key 和地域；HTTP 400 等会保留已隐藏 Key 的平台说明，帮助查模型 ID 或参数。断点缓存包含平台、地址、模型及 JSON 模式，切换后重新执行的任务不会误用另一配置的缓存；已写入项目的文字仍保留，需要重新翻译时使用相应重做操作。

本版只扩展文本 API。配音继续使用现有本地声音后端。

## 验证范围

265 项自动测试通过。每个平台的参数契约、完整断句／翻译／两轮讲义流程、缓存失效、用量记账和 Key 隔离均有自动回归测试。固定连接测试经过真实独立 worker 和本机受控 HTTP 服务验证；源程序与打包 worker 也通过现有 DeepSeek 账户的固定 JSON 请求。除 DeepSeek 外，没有其他平台的用户 Key，因此新平台尚未通过你的账户进行真实整课效果验收；可先用设置中的测试按钮验证自己的模型权限。

## 参数依据

以下为 2026-10-31 核对的官方文档；模型与价格可能变化，界面均允许手动填写。

- [阿里云百炼兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)及[计费](https://help.aliyun.com/zh/model-studio/model-pricing)。
- [火山方舟结构化输出](https://docs.volcengine.com/docs/ark/structured-output-beta?lang=zh)及[计费](https://docs.volcengine.com/docs/lake-ai-service/large-model-billing?lang=zh)。
- [Kimi K3.1 Pro 接口](https://platform.kimi.com/docs/guide/kimi-k3-1-pro-quickstart)、[JSON 模式](https://platform.kimi.com/docs/guide/response_format)及[价格](https://www.kimi.com/membership/pricing?tab=api)。
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)及[GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)。
- [DeepSeek API](https://api-docs.deepseek.com/)。
