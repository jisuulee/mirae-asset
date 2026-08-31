"""
HyperCLOVA X 클라이언트 v2 (CLOVA Studio)

v1 대비 수정 사항 (공식 문서 확인 결과 반영):
  - HCX-007 은 thinking 기본값이 'low'. 명시하지 않으면 추론이 켜진다.
    → Structured Outputs / Function calling 사용 시 반드시 effort='none' 을 함께 전송.
  - 추론 응답은 content 와 thinkingContent 가 분리된다. 둘 다 파싱한다.
  - 추론 사용 시 stop 파라미터 사용 불가.
  - maxCompletionTokens 는 최대 32768.

설계 지침:
  - 라우팅/분류/구조화        → HCX-DASH-002 또는 HCX-007(effort=none)
  - 툴 선택(function calling) → effort='none' 필수
  - 최종 답변/복합추론        → HCX-007 (effort='low'~'high'), 단 툴/스키마와 동시 사용 불가
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import requests

DEFAULT_HOST = "https://clovastudio.stream.ntruss.com"
OPENAI_COMPAT_PATH = "/v1/openai"

MODEL_LIGHT = "HCX-DASH-002"
MODEL_MAIN = "HCX-007"

# thinking / reasoning_effort 파라미터를 지원하는 모델 (그 외 모델에 보내면 400)
REASONING_MODELS = {"HCX-007"}

Effort = Literal["none", "low", "medium", "high"]


class HCXError(RuntimeError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


@dataclass
class HCXResponse:
    text: str = ""                 # 최종 답변 (content)
    thinking: str = ""             # 추론 과정 (thinkingContent) → think_trace 재료
    raw: dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""

    def json(self) -> Any:
        body = self.text.strip()
        if body.startswith("```"):
            parts = body.split("```")
            body = parts[1] if len(parts) > 1 else body
            if body.startswith("json"):
                body = body[4:]
        return json.loads(body.strip())


def _usage_from(node: dict[str, Any]) -> Usage:
    u = node.get("usage") or {}
    return Usage(
        prompt_tokens=u.get("promptTokens", u.get("prompt_tokens", 0)) or 0,
        completion_tokens=u.get("completionTokens", u.get("completion_tokens", 0)) or 0,
        total_tokens=u.get("totalTokens", u.get("total_tokens", 0)) or 0,
    )


class NativeHCX:
    """CLOVA Studio v3 chat-completions 직접 호출."""

    def __init__(
        self,
        api_key: str | None = None,
        host: str = DEFAULT_HOST,
        timeout: float = 90.0,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ.get("CLOVASTUDIO_API_KEY", "")
        if not self.api_key:
            raise HCXError("CLOVASTUDIO_API_KEY 가 없습니다.")
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": uuid.uuid4().hex,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = MODEL_MAIN,
        *,
        temperature: float = 0.1,
        top_p: float = 0.8,
        max_completion_tokens: int = 4096,
        repetition_penalty: float = 1.1,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        thinking_effort: Effort | None = None,
        stop: Iterable[str] = (),
    ) -> HCXResponse:
        """
        thinking_effort 규칙:
          - response_format 또는 tools 사용 시 자동으로 'none' 강제 (동시 사용 불가)
          - 둘 다 없고 미지정이면 모델 기본값 사용 (HCX-007 은 'low')
        """
        force_none = response_format is not None or tools is not None
        effort: Effort | None = "none" if force_none else thinking_effort

        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "topP": top_p,
            "topK": 0,
            "maxCompletionTokens": max_completion_tokens,
            "repetitionPenalty": repetition_penalty,
        }
        # thinking 파라미터는 추론 모델에만 유효. DASH 등에 보내면 400.
        if effort is not None and model in REASONING_MODELS:
            payload["thinking"] = {"effort": effort}
        # 추론 사용 시 stop 불가
        if effort in (None, "none") and stop:
            payload["stop"] = list(stop)
        if response_format is not None:
            payload["responseFormat"] = response_format
        if tools is not None:
            payload["tools"] = tools
            payload["toolChoice"] = "auto"

        url = f"{self.host}/v3/chat-completions/{model}"
        raw = self._post(url, payload)

        result = raw.get("result") or {}
        message = result.get("message") or {}
        calls = []
        for tc in message.get("toolCalls") or []:
            fn = tc.get("function") or {}
            calls.append(
                {"id": tc.get("id"), "name": fn.get("name"), "arguments": fn.get("arguments")}
            )

        return HCXResponse(
            text=message.get("content") or "",
            thinking=message.get("thinkingContent") or "",
            raw=raw,
            usage=_usage_from(result),
            latency_s=raw.get("_latency_s", 0.0),
            tool_calls=calls,
            finish_reason=result.get("finishReason") or result.get("stopReason") or "",
        )

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            started = time.perf_counter()
            try:
                r = self.session.post(
                    url, headers=self._headers(), json=payload, timeout=self.timeout
                )
                if r.status_code == 429 or r.status_code >= 500:
                    raise HCXError(f"HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code != 200:
                    raise HCXError(f"HTTP {r.status_code}: {r.text[:500]}")
                data = r.json()
                data["_latency_s"] = time.perf_counter() - started
                return data
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if "HTTP 4" in str(exc):
                    break
                time.sleep(1.5 * (2**attempt))
        raise HCXError(f"요청 실패: {last_err}")


class OpenAICompatHCX:
    """OpenAI SDK 호환 경로."""

    def __init__(
        self,
        api_key: str | None = None,
        host: str = DEFAULT_HOST,
        timeout: float = 90.0,
    ) -> None:
        from openai import OpenAI

        key = api_key or os.environ.get("CLOVASTUDIO_API_KEY", "")
        if not key:
            raise HCXError("CLOVASTUDIO_API_KEY 가 없습니다.")
        self.client = OpenAI(
            api_key=key,
            base_url=f"{host.rstrip('/')}{OPENAI_COMPAT_PATH}",
            timeout=timeout,
            max_retries=3,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str = MODEL_MAIN,
        *,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: Effort | None = None,
    ) -> HCXResponse:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            # 실측 결과: CLOVA 호환 API 는 max_tokens 를 거부, max_completion_tokens 만 허용
            "max_completion_tokens": max_tokens,
        }
        force_none = tools is not None or response_format is not None
        effort = "none" if force_none else reasoning_effort
        if effort is not None and model in REASONING_MODELS:
            kwargs["reasoning_effort"] = effort
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format

        resp = None
        for attempt in range(4):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001
                is_429 = "429" in str(exc) or exc.__class__.__name__ == "RateLimitError"
                if not is_429 or attempt == 3:
                    raise
                time.sleep(10.0 * (attempt + 1))   # 429: 10/20/30초 백오프
        latency = time.perf_counter() - started

        choice = resp.choices[0]
        calls = []
        for tc in getattr(choice.message, "tool_calls", None) or []:
            calls.append(
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            )

        u = resp.usage
        return HCXResponse(
            text=choice.message.content or "",
            raw=resp.model_dump(),
            usage=Usage(
                prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(u, "completion_tokens", 0) or 0,
                total_tokens=getattr(u, "total_tokens", 0) or 0,
            ),
            latency_s=latency,
            tool_calls=calls,
            finish_reason=choice.finish_reason or "",
        )