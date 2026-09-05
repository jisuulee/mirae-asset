"""Provider-neutral embedding contracts and CLOVA Embedding v2 adapter."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.message import Message
from threading import Lock
from typing import Protocol, Sequence


def embedding_content_hash(text: str) -> str:
    """Hash the exact UTF-8 text sent to the embedding provider."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingSpec:
    provider: str
    model: str
    version: str
    dimension: int
    distance: str = "cosine"
    max_tokens: int | None = None


@dataclass(frozen=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    input_tokens: int | None = None


@dataclass
class EmbeddingStats:
    requests: int = 0
    successes: int = 0
    failed_attempts: int = 0
    permanent_failures: int = 0
    retries: int = 0
    rate_limit_retries: int = 0
    input_tokens: int = 0


class EmbeddingProvider(Protocol):
    spec: EmbeddingSpec

    def embed(self, texts: Sequence[str]) -> list[EmbeddingResult]: ...


def validate_embedding(result: EmbeddingResult, spec: EmbeddingSpec) -> None:
    if len(result.vector) != spec.dimension:
        raise ValueError(f"expected dimension {spec.dimension}, got {len(result.vector)}")
    if any(not math.isfinite(value) for value in result.vector):
        raise ValueError("embedding contains a non-finite value")


class ClovaEmbeddingV2:
    """Minimal dependency-free adapter for CLOVA Studio Embedding v2/bge-m3.

    The API currently accepts one text per request, so ``embed`` is a bounded
    sequential batch with retries. Callers own checkpointing/idempotency.
    """

    spec = EmbeddingSpec(
        provider="clova-studio",
        model="clova-studio/bge-m3",
        version="embedding-v2",
        dimension=1024,
        distance="cosine",
        max_tokens=8192,
    )

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2/",
        timeout: float = 90.0,
        max_retries: int = 3,
        concurrency: int = 1,
    ) -> None:
        if not api_key:
            raise ValueError("CLOVA Studio API key is required")
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        self.concurrency = concurrency
        self.stats = EmbeddingStats()
        self.rate_limit_headers: dict[str, str] = {}
        self._rate_lock = Lock()
        self._tokens_per_character: float | None = None

    def _record_headers(self, headers: Message) -> None:
        with self._rate_lock:
            self.rate_limit_headers = {
                name.lower(): value for name, value in headers.items()
                if name.lower().startswith("x-ratelimit") or name.lower() == "retry-after"
            }

    @staticmethod
    def _duration(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            return float(value)
        except ValueError:
            total = 0.0
            for amount, unit in re.findall(r"([0-9.]+)(ms|s|m|h)", value.lower()):
                total += float(amount) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
            return total

    def _wait_for_capacity(self, estimated_tokens: int) -> None:
        with self._rate_lock:
            headers = dict(self.rate_limit_headers)
        waits = []
        try:
            if int(headers.get("x-ratelimit-remaining-requests", "1")) < 1:
                waits.append(self._duration(headers.get("x-ratelimit-reset-requests")))
            if int(headers.get("x-ratelimit-remaining-tokens", str(estimated_tokens))) < estimated_tokens:
                waits.append(self._duration(headers.get("x-ratelimit-reset-tokens")))
        except ValueError:
            return
        if waits and max(waits) > 0:
            time.sleep(max(waits) + 0.25)

    def _retry_delay(self, exc: BaseException, attempt: int) -> float:
        if isinstance(exc, urllib.error.HTTPError):
            retry_after = exc.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    pass
            resets = [
                self._duration(exc.headers.get("x-ratelimit-reset-requests")),
                self._duration(exc.headers.get("x-ratelimit-reset-tokens")),
            ]
            if max(resets) > 0:
                return max(resets) + 0.25
        return float(min(2**attempt, 8))

    def _one(self, text: str) -> EmbeddingResult:
        payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            with self._rate_lock:
                ratio = self._tokens_per_character
            estimated_tokens = len(text) if ratio is None else max(1, math.ceil(len(text) * ratio * 1.1))
            self._wait_for_capacity(estimated_tokens)
            self.stats.requests += 1
            request = urllib.request.Request(
                self.endpoint,
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._record_headers(response.headers)
                    body = json.loads(response.read().decode("utf-8"))
                if str(body.get("status", {}).get("code")) != "20000":
                    raise ValueError(f"CLOVA embedding status: {body.get('status', {}).get('code')}")
                result = body["result"]
                embedded = EmbeddingResult(tuple(float(value) for value in result["embedding"]), result.get("inputTokens"))
                validate_embedding(embedded, self.spec)
                self.stats.successes += 1
                self.stats.input_tokens += embedded.input_tokens or 0
                if embedded.input_tokens is not None and text:
                    observed = embedded.input_tokens / len(text)
                    with self._rate_lock:
                        self._tokens_per_character = max(self._tokens_per_character or 0.0, observed)
                return embedded
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                self.stats.failed_attempts += 1
                if isinstance(exc, urllib.error.HTTPError):
                    self._record_headers(exc.headers)
                if attempt >= self.max_retries:
                    self.stats.permanent_failures += 1
                    raise RuntimeError(f"CLOVA embedding failed after {attempt + 1} attempts") from exc
                self.stats.retries += 1
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                    self.stats.rate_limit_retries += 1
                time.sleep(self._retry_delay(exc, attempt))
        raise AssertionError("unreachable")

    def embed(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        if self.concurrency == 1 or len(texts) < 2:
            return [self._one(text) for text in texts]
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(texts))) as executor:
            return list(executor.map(self._one, texts))


class DeterministicTestEmbedding:
    """Stable test-only vectors; never suitable for a retrieval baseline."""

    def __init__(self, dimension: int = 8) -> None:
        self.spec = EmbeddingSpec("test", "sha256", "v1", dimension, max_tokens=None)
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[EmbeddingResult]:
        results = []
        for text in texts:
            self.calls += 1
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            values = tuple((digest[index % len(digest)] / 127.5) - 1.0 for index in range(self.spec.dimension))
            results.append(EmbeddingResult(values, len(text.split())))
        return results
