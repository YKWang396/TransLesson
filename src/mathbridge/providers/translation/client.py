"""Shared opt-in JSON API session with durable cost reservations."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .providers import PROVIDERS, cache_identity, canonical_base, make_payload, provider_id, provider_label


class TranslationError(RuntimeError):
    def __init__(self, code: str, message: str, usage: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.usage = usage or {}


def _cancelled(cancel, usage=None):
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise TranslationError("CANCELLED", "任务已取消，已完成结果保留于 checkpoint", usage)


class TextClient:
    def __init__(self, settings, cancel=None, *, prompt_version="course-ai-1"):
        identifier = provider_id(settings)
        if identifier not in PROVIDERS:
            raise TranslationError("INVALID_SETTINGS", "请选择支持的文本服务商")
        local = identifier == "offline"
        base = canonical_base(settings.get("api_base", PROVIDERS[identifier].base))
        try:
            parsed = urllib.parse.urlsplit(base)
            valid = bool(parsed.hostname) and parsed.port != 0
        except ValueError:
            valid = False
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise TranslationError("INVALID_SETTINGS", "API 地址不得含凭据或查询参数")
        if local:
            if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
                raise TranslationError("INVALID_SETTINGS", "离线 llama.cpp 只允许显式 127.0.0.1 HTTP endpoint")
        elif not settings.get("allow_text_api", False):
            raise TranslationError("NETWORK_DISABLED", "请先允许将课程文本发送到所选 API")
        elif parsed.scheme != "https":
            raise TranslationError("INVALID_SETTINGS", "外部 API 必须使用 HTTPS")
        model = settings.get("model")
        if not isinstance(model, str) or not model.strip():
            raise TranslationError("INVALID_SETTINGS", "必须明确选择模型名称")
        if not local and not settings.get("api_key"):
            raise TranslationError("API_AUTH_FAILED", "未提供 API 密钥")
        budget = float(settings.get("budget_cny", 0))
        max_calls = int(settings.get("max_calls", 0))
        input_price = float(settings.get("input_price_per_million", 0))
        output_price = float(settings.get("output_price_per_million", 0))
        if (not all(math.isfinite(v) and v >= 0 for v in (budget, input_price, output_price)) or max_calls < 1
                or (not local and (budget <= 0 or input_price <= 0 or output_price <= 0))):
            raise TranslationError("INVALID_SETTINGS", "设置预算、调用上限和当前输入/输出单价后再开始")
        max_tokens = min(max(int(settings.get("max_output_tokens", 4096)), 128), 16384)
        usage = {"provider": "llama_cpp" if local else identifier, "requested_model": model,
                 "returned_models": [], "prompt_version": prompt_version,
                 "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "calls": 0,
                 "prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0,
                 "cost_cny": 0.0, "uncertain_cost_cny": 0.0, "uncertain_requests": 0,
                 "input_price_per_million": input_price, "output_price_per_million": output_price}
        request_id = settings.get("_request_id") or "standalone-" + uuid.uuid4().hex
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,95}", request_id):
            raise TranslationError("INVALID_SETTINGS", "无效 request_id，无法保存费用预留账本", usage)
        ledger_path = None
        if settings.get("_project_dir"):
            ledger_root = Path(settings["_project_dir"]).resolve()
            ledger_path = ledger_root / "cache" / "api-reservations" / (request_id + ".json")
            if not ledger_path.resolve().is_relative_to(ledger_root):
                raise TranslationError("INVALID_SETTINGS", "费用账本目录越界", usage)
            ledger_path.parent.mkdir(parents=True, exist_ok=True)

        def persist_ledger(pending_cost=0.0):
            if ledger_path is None:
                return
            # The request id makes root settlement idempotent. Calls already includes
            # the pending POST, so pending_calls is deliberately zero.
            ledger = {"request_id": request_id, "usage": usage,
                      "pending_cost_cny": float(pending_cost), "pending_calls": 0}
            temporary = ledger_path.with_name(ledger_path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                    json.dump(ledger, stream, ensure_ascii=False, indent=2, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, ledger_path)
            finally:
                temporary.unlink(missing_ok=True)

        persist_ledger()
        prior_cost = float(settings.get("usage_spent_cny", 0))
        prior_calls = int(settings.get("usage_calls", 0))
        if not math.isfinite(prior_cost) or prior_cost < 0 or prior_calls < 0:
            raise TranslationError("INVALID_SETTINGS", "无效累计 usage")
        endpoint = base + "/chat/completions"
        self.last_message = None

        def redacted(value):
            value = str(value)
            if settings.get("api_key"):
                value = value.replace(settings["api_key"], "[已隐藏 Key]")
            return re.sub(r"[\x00-\x1f]", " ", value)[:360]

        def request(payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            # UTF-8 bytes are a conservative upper estimate for text token count.
            reserve = (len(body) * input_price + max_tokens * output_price) / 1_000_000
            for attempt in range(3):
                _cancelled(cancel, usage)
                if prior_calls + usage["calls"] >= max_calls or prior_cost + usage["cost_cny"] + usage["uncertain_cost_cny"] + reserve > budget:
                    raise TranslationError("BUDGET_EXCEEDED", "已达调用或预算上限，已完成批次可保留", usage)
                usage["calls"] += 1
                # This must finish before opening the socket. If the worker is killed
                # while waiting, root recovers this conservative unsettled reservation.
                persist_ledger(reserve)
                headers = {"Content-Type": "application/json"}
                if settings.get("api_key"):
                    headers["Authorization"] = "Bearer " + settings["api_key"]
                req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
                accounted = False
                try:
                    # No redirects: avoid transmitting the bearer key to a redirected host.
                    class NoRedirect(urllib.request.HTTPRedirectHandler):
                        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                            return None
                    with urllib.request.build_opener(NoRedirect).open(req, timeout=min(float(settings.get("timeout", 90)), 180)) as response:
                        raw = response.read(4_000_001)
                        if len(raw) > 4_000_000:
                            raise ValueError("API response too large")
                    response = json.loads(raw)
                    counters = response.get("usage")
                    if not isinstance(counters, dict) or any(not isinstance(counters.get(k), int) or counters[k] < 0 for k in ("prompt_tokens", "completion_tokens")):
                        usage["uncertain_requests"] += 1
                        usage["uncertain_cost_cny"] += reserve
                        persist_ledger()
                        raise TranslationError("INVALID_TRANSLATION", "API 未返回可记账的实际 usage，已保守预留费用", usage)
                    usage["prompt_tokens"] += counters["prompt_tokens"]
                    usage["completion_tokens"] += counters["completion_tokens"]
                    detail = counters.get("prompt_tokens_details") or {}
                    hits = counters.get("prompt_cache_hit_tokens", detail.get("cached_tokens", 0) if isinstance(detail,dict) else 0)
                    usage["cache_hit_tokens"] += hits if type(hits) is int and hits >= 0 else 0
                    # Charge all input at supplied uncached price, avoiding optimistic cache pricing.
                    usage["cost_cny"] += (counters["prompt_tokens"] * input_price + counters["completion_tokens"] * output_price) / 1_000_000
                    accounted = True
                    if response.get("model") and response["model"] not in usage["returned_models"]:
                        usage["returned_models"].append(response["model"])
                    persist_ledger()
                    choices = response.get("choices", [])
                    if choices and choices[0].get("finish_reason") == "length":
                        raise TranslationError("OUTPUT_TOO_LONG", "AI 输出超过长度限制，已保留完成的章节；可再次点击继续整理", usage)
                    if not choices or choices[0].get("finish_reason") not in ("stop", None):
                        raise TranslationError("INVALID_TRANSLATION", "AI 输出未完整结束，已保留完成的批次，可重试", usage)
                    self.last_message = choices[0]["message"]
                    content = self.last_message.get("content")
                    if self.last_message.get("refusal"):
                        raise TranslationError("MODEL_REFUSED", "所选模型拒绝了本次文本请求，已保留完成的批次", usage)
                    if not isinstance(content,str) or not content.strip():
                        raise TranslationError("INVALID_TRANSLATION", "模型未返回正文 JSON；请检查模型能力及输出 token 上限", usage)
                    return content
                except urllib.error.HTTPError as exc:
                    if exc.code in (401, 403):
                        persist_ledger()
                        raise TranslationError("API_AUTH_FAILED", provider_label(identifier) + " 身份或权限验证失败，请检查 Key、开通权限及地域", usage) from None
                    if exc.code == 429 or 500 <= exc.code <= 599:
                        # Failed upstream requests may have incurred billing.
                        usage["uncertain_requests"] += 1
                        usage["uncertain_cost_cny"] += reserve
                        persist_ledger()
                        if attempt < 2:
                            time.sleep(min(0.5 * (2 ** attempt), 2))
                            continue
                    persist_ledger()
                    detail = ""
                    try:
                        error = json.loads(exc.read(8192))
                        error = error.get("error", error)
                        if isinstance(error,dict):
                            detail = redacted(error.get("message") or error.get("code") or "")
                    except (OSError, ValueError, TypeError, AttributeError):
                        pass
                    raise TranslationError("PROVIDER_HTTP_ERROR", f"{provider_label(identifier)} HTTP {exc.code}；"
                                           "检查 API 地址、模型 ID 及参数兼容性。" + detail, usage) from None
                except (TimeoutError, urllib.error.URLError, OSError) as exc:
                    usage["uncertain_requests"] += 1
                    usage["uncertain_cost_cny"] += reserve
                    persist_ledger()
                    # A timed-out POST may already be charged; no silent repeat.
                    reason = getattr(exc, "reason", exc)
                    detail = type(reason).__name__ + ": " + redacted(reason)
                    raise TranslationError("PROVIDER_TIMEOUT", "API 连接未完成（" + detail + "），可能已计费；完成的批次已保留", usage) from None
                except (KeyError, ValueError, TypeError):
                    if not accounted:
                        usage["uncertain_requests"] += 1
                        usage["uncertain_cost_cny"] += reserve
                    persist_ledger()
                    raise TranslationError("INVALID_TRANSLATION", "API 响应不是有效的完整 JSON envelope", usage) from None

        self.request = request
        self.usage = usage
        self.model, self.local, self.max_tokens = model, local, max_tokens
        self.settings = dict(settings)
        self.provider_id = identifier
        self.cache_identity = cache_identity(dict(settings, api_base=base))
        self.cancel = cancel

    def payload(self, messages):
        return make_payload(self.settings, messages, self.max_tokens)

    def assistant_message(self, content):
        message = {"role":"assistant", "content":content}
        if self.provider_id == "kimi" and self.last_message and self.last_message.get("content") == content:
            if self.last_message.get("reasoning_content") is not None:
                message["reasoning_content"] = self.last_message["reasoning_content"]
        return message

    def json(self, system, context, validate):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
        for repair in range(2):
            _cancelled(self.cancel, self.usage)
            payload = self.payload(messages)
            try:
                content = self.request(payload)
            except TranslationError as exc:
                if exc.code != "OUTPUT_TOO_LONG" or repair:
                    raise
                messages.append({"role": "user", "content":
                    "The previous response exceeded the output limit. Return a more concise complete JSON. "
                    "For notes use at most 6 direct source IDs per section, not every ID, and concise substantive bodies."})
                continue
            try:
                return validate(json.loads(content))
            except (ValueError, TypeError, KeyError) as exc:
                if repair:
                    raise TranslationError("INVALID_TRANSLATION", "一次结构修复后仍失败：" + str(exc), self.usage) from None
                messages.extend([self.assistant_message(content),
                                 {"role": "user", "content": "Repair JSON once: " + str(exc)}])
        raise AssertionError("unreachable")
