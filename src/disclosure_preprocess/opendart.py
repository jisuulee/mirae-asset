"""Opt-in OpenDART lookup with a deterministic local response cache."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


OPEN_DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"


class OpenDartError(RuntimeError):
    """A classified OpenDART client failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


Transport = Callable[[str, float], bytes]


def _default_transport(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dart-phase4-lineage/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class OpenDartClient:
    def __init__(
        self,
        api_key: str,
        cache_dir: str | Path,
        *,
        timeout: float = 20.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OPEN_DART_API_KEY is required when OpenDART is enabled")
        self._api_key = api_key
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self._transport = transport or _default_transport

    @staticmethod
    def _cache_key(parameters: Mapping[str, str]) -> str:
        public = {key: value for key, value in parameters.items() if key != "crtfc_key"}
        canonical = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def list_filings(
        self,
        *,
        corp_code: str,
        begin_date: str,
        end_date: str,
        page_count: int = 100,
    ) -> dict[str, Any]:
        parameters = {
            "crtfc_key": self._api_key,
            "corp_code": corp_code,
            "bgn_de": begin_date.replace("-", ""),
            "end_de": end_date.replace("-", ""),
            "page_count": str(page_count),
        }
        cache_key = self._cache_key(parameters)
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise OpenDartError("CACHE_READ_ERROR", str(exc)) from exc
            if not isinstance(cached, dict):
                raise OpenDartError("CACHE_INVALID", f"Cached response is not an object: {cache_path}")
            return {"source": "cache", "cache_key": cache_key, "response": cached}

        query = urllib.parse.urlencode(parameters)
        try:
            raw = self._transport(f"{OPEN_DART_LIST_URL}?{query}", self.timeout)
        except urllib.error.HTTPError as exc:
            raise OpenDartError("HTTP_ERROR", f"OpenDART HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OpenDartError("NETWORK_ERROR", str(exc.reason)) from exc
        except TimeoutError as exc:
            raise OpenDartError("TIMEOUT", str(exc)) from exc
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenDartError("RESPONSE_INVALID", str(exc)) from exc
        if not isinstance(response, dict):
            raise OpenDartError("RESPONSE_INVALID", "OpenDART response is not an object")
        status = str(response.get("status") or "")
        if status not in {"000", "013"}:  # 013 = no data
            code = {
                "010": "API_KEY_UNREGISTERED",
                "011": "API_KEY_SUSPENDED",
                "020": "RATE_LIMIT",
                "100": "INVALID_PARAMETER",
                "800": "SYSTEM_MAINTENANCE",
                "900": "UNDEFINED_API_ERROR",
            }.get(status, "API_ERROR")
            raise OpenDartError(code, str(response.get("message") or status))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(response, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(cache_path)
        return {"source": "network", "cache_key": cache_key, "response": response}


__all__ = ["OpenDartClient", "OpenDartError"]
