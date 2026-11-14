# 文本 API

文本接口用于上下文断句、翻译和教学讲义。在“处理设置 → 文本与隐私”选择服务商后，会填写地址和模型建议。模型／接入点可手动修改，以账户实际开通的 ID 为准。

| 服务商 | 地址 | 配置 |
|---|---|---|
| 千问／百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 默认北京地域，也可填业务空间专属地址；Key 须与地域匹配 |
| 豆包／方舟 | `https://ark.cn-beijing.volces.com/api/v3` | 方舟 API Key，模型 ID 或 `ep-…` 接入点 |
| Kimi | `https://api.moonshot.cn/v1` | 开放平台 API Key |
| GPT／OpenAI | `https://api.openai.com/v1` | OpenAI API Key |
| DeepSeek | `https://api.deepseek.com` | DeepSeek 开放平台 API Key |
| 自定义兼容服务 | 手动填写 HTTPS 地址 | Chat Completions 接口，也可粘贴完整 `/chat/completions` 地址 |
| 本地 llama.cpp | `http://127.0.0.1:8080/v1` | 自行启动本机兼容服务并填写实际模型名 |

填写 Key 后点击“测试连接与 JSON”。测试只发送固定诊断文字，验证网络、认证、模型和结构化输出；不需要课程项目，可能产生少量 token 费用。随后勾选允许发送课程文本并应用设置。

断句、翻译和讲义共用项目预算。高级设置的单价用于人民币估算，更换模型或地域后需核对；OpenAI 预填估算采用固定换算系数，不是实时汇率。实际费用以服务商账单为准。连接测试单独记账，不计入课程预算。

服务商分别记住地址、模型和费率。Key 默认留在内存，可选保存在 Windows 凭据库，按服务商和地址隔离。改地址不会沿用旧地址的 Key；取消设置不写入凭据库，应用设置才保存或删除。

自定义接口不接受 `response_format` 时，可在高级设置关闭“接口支持 JSON 模式”；返回正文仍须符合任务 JSON。错误会显示服务商、HTTP 状态和已隐藏 Key 的平台说明。程序不自动切换数据去向。

新配置会使相关断点缓存失效。已经写入项目的内容保留，需要重新生成时使用对应操作。测试可用不表示所有模型均有权限，也不代替课程内容抽查。
