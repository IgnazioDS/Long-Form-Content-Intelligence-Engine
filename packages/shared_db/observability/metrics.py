from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

_REGISTRY = CollectorRegistry()

_HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["method", "path", "status"],
    registry=_REGISTRY,
)
_HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "path"],
    registry=_REGISTRY,
)
_LLM_CHAT_REQUESTS_TOTAL = Counter(
    "llm_chat_requests_total",
    "Total LLM chat requests.",
    ["provider", "model", "outcome"],
    registry=_REGISTRY,
)
_LLM_CHAT_LATENCY_SECONDS = Histogram(
    "llm_chat_latency_seconds",
    "LLM chat latency in seconds.",
    ["provider", "model"],
    registry=_REGISTRY,
)
_LLM_CHAT_ERRORS_TOTAL = Counter(
    "llm_chat_errors_total",
    "Total LLM chat errors.",
    ["provider", "model", "error_type"],
    registry=_REGISTRY,
)
_LLM_CHAT_TOKENS_TOTAL = Counter(
    "llm_chat_tokens_total",
    "Total LLM chat tokens.",
    ["provider", "model", "token_type"],
    registry=_REGISTRY,
)
_VERIFICATION_SUMMARY_INCONSISTENT_TOTAL = Counter(
    "verification_summary_inconsistent_total",
    "Total verification summary inconsistencies detected on persisted reads.",
    registry=_REGISTRY,
)


def get_registry() -> CollectorRegistry:
    return _REGISTRY


def _normalize_label(value: str | None, default: str = "unknown") -> str:
    if value is None:
        return default
    cleaned = str(value).strip()
    return cleaned or default


def record_http_request(method: str, path: str, status: int, duration: float) -> None:
    method_label = _normalize_label(method, "UNKNOWN")
    path_label = _normalize_label(path, "unknown")
    status_label = _normalize_label(str(status), "unknown")
    _HTTP_REQUESTS_TOTAL.labels(method_label, path_label, status_label).inc()
    _HTTP_REQUEST_DURATION_SECONDS.labels(method_label, path_label).observe(duration)


def record_llm_chat_request(provider: str, model: str, outcome: str, duration: float) -> None:
    provider_label = _normalize_label(provider)
    model_label = _normalize_label(model)
    outcome_label = _normalize_label(outcome)
    _LLM_CHAT_REQUESTS_TOTAL.labels(provider_label, model_label, outcome_label).inc()
    _LLM_CHAT_LATENCY_SECONDS.labels(provider_label, model_label).observe(duration)


def record_llm_chat_error(provider: str, model: str, error_type: str) -> None:
    provider_label = _normalize_label(provider)
    model_label = _normalize_label(model)
    error_label = _normalize_label(error_type, "error")
    _LLM_CHAT_ERRORS_TOTAL.labels(provider_label, model_label, error_label).inc()


def record_llm_chat_tokens(
    provider: str,
    model: str,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
) -> None:
    provider_label = _normalize_label(provider)
    model_label = _normalize_label(model)
    if isinstance(prompt_tokens, int):
        _LLM_CHAT_TOKENS_TOTAL.labels(provider_label, model_label, "prompt").inc(
            prompt_tokens
        )
    if isinstance(completion_tokens, int):
        _LLM_CHAT_TOKENS_TOTAL.labels(provider_label, model_label, "completion").inc(
            completion_tokens
        )
    if isinstance(total_tokens, int):
        _LLM_CHAT_TOKENS_TOTAL.labels(provider_label, model_label, "total").inc(total_tokens)


def record_verification_summary_inconsistent() -> None:
    _VERIFICATION_SUMMARY_INCONSISTENT_TOTAL.inc()
