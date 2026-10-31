"""Provider presets and Chat Completions dialects shared by every text task."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class Provider:
    label: str
    base: str
    models: tuple[str, ...]
    input_price: float
    output_price: float
    hint: str
    docs: str


# Rates are editable CNY estimates for the first model and the stated region.
# OpenAI's USD prices use an explicit 7.3 CNY/USD estimate, not a live FX quote.
PROVIDERS = {
    "deepseek": Provider("DeepSeek", "https://api.deepseek.com", ("deepseek-flash", "deepseek-chat"), 2, 8,
        "使用 DeepSeek 开放平台的 API Key。", "https://api-docs.deepseek.com/"),
    "qwen": Provider("千问 · 阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ("qwen-plus", "qwen-flash", "qwen3.7-plus", "qwen3.8-max"), .8, 2,
        "默认北京地址；也可粘贴业务空间专属地址。Key 与地址须属于同一地域。预填费率为北京 qwen-plus 非思考短上下文估算。",
        "https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope"),
    "doubao": Provider("豆包 · 火山方舟", "https://ark.cn-beijing.volces.com/api/v3",
        ("doubao-seed-2-1-pro-260915", "doubao-seed-2-1-lite-260915"), 6, 30,
        "使用方舟 API Key；模型可填写平台 Model ID 或您的 ep-… 接入点 ID。",
        "https://docs.volcengine.com/docs/ark/structured-output-beta?lang=zh"),
    "kimi": Provider("Kimi · 月之暗面", "https://api.moonshot.cn/v1",
        ("kimi-k3.1-pro", "kimi-k3.1-pro-highspeed", "kimi-k3"), 13, 68,
        "使用开放平台 API Key。K3 系列保留思考模式并采用低推理强度；会员与 API 用量分开计费。",
        "https://platform.kimi.com/docs/guide/kimi-k3-1-pro-quickstart"),
    "openai": Provider("GPT · OpenAI", "https://api.openai.com/v1",
        ("gpt-5.6-terra", "gpt-6-astra", "gpt-5.4-mini"), 14.6, 87.6,
        "使用 OpenAI API Key。预填费率按 GPT-5.6 Terra 美元单价 × 7.3 估算；可在高级设置修改。",
        "https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create"),
    "custom": Provider("自定义 · OpenAI 兼容", "", (), 2, 8,
        "填写兼容 Chat Completions 的 HTTPS 地址、模型 ID 和 Key；请核对模型实际费率。不支持 JSON 模式时可关闭该选项。",
        ""),
    "offline": Provider("本地 llama.cpp", "http://127.0.0.1:8080/v1", ("local-model",), 0, 0,
        "仅连接本机 127.0.0.1，不需要在线 Key。", ""),
}
REMOTE_PROVIDERS = frozenset(PROVIDERS) - {"offline"}
PROFILE_FIELDS = ("api_base", "model", "input_price_per_million", "output_price_per_million",
                  "remember_api_key", "allow_text_api", "text_json_mode")


def provider_id(settings):
    if settings.get("provider") == "llama_cpp":
        return "offline"
    return settings.get("text_provider", "deepseek")


def provider_label(identifier):
    return PROVIDERS[identifier].label if identifier in PROVIDERS else "手动校对"


def preset(identifier):
    spec = PROVIDERS[identifier]
    return dict(api_base=spec.base, model=spec.models[0] if spec.models else "",
                input_price_per_million=spec.input_price, output_price_per_million=spec.output_price,
                remember_api_key=False, allow_text_api=False, text_json_mode=True)


def canonical_base(base):
    value = str(base).strip().rstrip("/")
    if value.endswith("/chat/completions"):
        value = value[:-len("/chat/completions")]
    try:
        parts = urlsplit(value)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))
    except ValueError:
        return value


def credential_namespace(settings):
    identifier = provider_id(settings)
    base = canonical_base(settings.get("api_base", ""))
    # Preserve the old saved DeepSeek key only for its original official origin.
    if identifier == "deepseek" and base in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
        return "text-api"
    digest = hashlib.sha256((identifier + "\0" + base).encode()).hexdigest()[:24]
    return "text-api-" + digest


def cache_identity(settings):
    return dict(provider=provider_id(settings), base=canonical_base(settings.get("api_base", "")),
                model=settings.get("model", ""), json_mode=settings.get("text_json_mode", True), dialect="chat-2")


def make_payload(settings, messages, max_tokens):
    identifier = provider_id(settings)
    model = settings["model"]
    result = dict(model=model, messages=messages, stream=False)
    if settings.get("text_json_mode", True):
        result["response_format"] = {"type":"json_object"}
    openai_model = bool(re.match(r"^(?:gpt-|o[134](?:-|$))", model))
    kimi3 = identifier == "kimi" and model.startswith("kimi-k3")
    if identifier == "openai" or kimi3 or identifier == "custom" and openai_model:
        result["max_completion_tokens"] = max_tokens
        if kimi3 or re.match(r"^(?:gpt-[5-9]|o[34](?:-|$))", model):
            result["reasoning_effort"] = "low"
    else:
        result.update(max_tokens=max_tokens, temperature=.1)
    if identifier == "qwen":
        result["enable_thinking"] = False
    elif identifier == "kimi" and not kimi3:
        if model.startswith("kimi-k2"):
            result.update(thinking={"type":"disabled"}, temperature=.6)
    elif identifier in {"deepseek", "doubao"}:
        result["thinking"] = {"type":"disabled"}
    return result
