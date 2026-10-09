"""Multi-provider payload normalization adapters for LLM FinOps.

Supports standard provider formats:
1. OpenAI ChatCompletion format (prompt_tokens, completion_tokens, prompt_tokens_details.cached_tokens)
2. Anthropic Messages format (input_tokens, output_tokens, cache_read_input_tokens)
3. Google Gemini format (promptTokenCount, candidatesTokenCount, cachedContentTokenCount)
4. Simulated Provider format (custom tokens object, simulated metrics, custom tags)
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple
import uuid

from core.models import RequestRecord
from core.attribution import AttributionParser


class ProviderAdapterRegistry:
    """Detects provider format and normalizes raw API responses/logs into RequestRecord."""

    def __init__(self, attribution_parser: Optional[AttributionParser] = None):
        self.attribution_parser = attribution_parser or AttributionParser()

    def detect_format(self, payload: Dict[str, Any], format_hint: Optional[str] = None) -> str:
        """Infer provider format from explicit hint or payload signature."""
        if format_hint:
            clean_hint = format_hint.strip().lower()
            if clean_hint in {"openai", "anthropic", "gemini", "google", "simulated", "custom"}:
                return "google" if clean_hint == "gemini" else clean_hint

        # Signature matching
        if "simulated_request_id" in payload or payload.get("provider") == "simulated":
            return "simulated"

        if "usageMetadata" in payload or "candidates" in payload:
            return "google"

        usage = payload.get("usage", {})
        if isinstance(usage, dict):
            if "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage or payload.get("type") == "message":
                return "anthropic"
            if "prompt_tokens" in usage or payload.get("object") == "chat.completion":
                return "openai"

        # Fallback based on model name prefix
        model = str(payload.get("model", "")).lower()
        if model.startswith("claude"):
            return "anthropic"
        if model.startswith("gemini"):
            return "google"
        if "sim" in model:
            return "simulated"

        return "openai"

    def parse_openai(self, payload: Dict[str, Any], headers: Optional[Dict[str, Any]] = None) -> RequestRecord:
        """Parse OpenAI ChatCompletion format."""
        req_id = str(payload.get("id") or f"chatcmpl_{uuid.uuid4().hex[:12]}")
        model = str(payload.get("model", "gpt-4o")).strip().lower()

        created_ts = payload.get("created")
        if created_ts and isinstance(created_ts, (int, float)):
            ts = datetime.fromtimestamp(created_ts, tz=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        usage = payload.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0))
        out_tok = int(usage.get("completion_tokens", 0))

        # Check prompt_tokens_details for cached tokens
        prompt_details = usage.get("prompt_tokens_details", {})
        cached_tok = int(prompt_details.get("cached_tokens", 0)) if isinstance(prompt_details, dict) else 0

        # Attribution metadata
        attr_input = dict(headers or {})
        if "user" in payload and "user_id" not in attr_input:
            attr_input["user_id"] = payload["user"]
        if "metadata" in payload and isinstance(payload["metadata"], dict):
            attr_input.update(payload["metadata"])

        attr = self.attribution_parser.parse_attribution(attr_input)

        return RequestRecord(
            request_id=req_id,
            timestamp_utc=ts,
            team=attr["team"],
            feature=attr["feature"],
            user_id=attr["user_id"],
            provider="openai",
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cached_tok,
            status="success",
            latency_ms=int(payload.get("latency_ms", 0)),
            env=attr.get("env", "production"),
            source="openai_adapter"
        )

    def parse_anthropic(self, payload: Dict[str, Any], headers: Optional[Dict[str, Any]] = None) -> RequestRecord:
        """Parse Anthropic Messages API format."""
        req_id = str(payload.get("id") or f"msg_{uuid.uuid4().hex[:14]}")
        model = str(payload.get("model", "claude-3-5-sonnet")).strip().lower()

        usage = payload.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        cached_tok = int(usage.get("cache_read_input_tokens", 0))

        attr_input = dict(headers or {})
        if "metadata" in payload and isinstance(payload["metadata"], dict):
            if "user_id" in payload["metadata"]:
                attr_input["user_id"] = payload["metadata"]["user_id"]
            attr_input.update(payload["metadata"])

        attr = self.attribution_parser.parse_attribution(attr_input)

        return RequestRecord(
            request_id=req_id,
            timestamp_utc=datetime.now(timezone.utc),
            team=attr["team"],
            feature=attr["feature"],
            user_id=attr["user_id"],
            provider="anthropic",
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cached_tok,
            status="success",
            latency_ms=int(payload.get("latency_ms", 0)),
            env=attr.get("env", "production"),
            source="anthropic_adapter"
        )

    def parse_google(self, payload: Dict[str, Any], headers: Optional[Dict[str, Any]] = None) -> RequestRecord:
        """Parse Google Gemini format."""
        req_id = str(payload.get("responseId") or payload.get("id") or f"gemini_{uuid.uuid4().hex[:12]}")
        model = str(payload.get("model", "gemini-1.5-flash")).strip().lower()

        usage = payload.get("usageMetadata", {})
        in_tok = int(usage.get("promptTokenCount", 0))
        out_tok = int(usage.get("candidatesTokenCount", 0))
        cached_tok = int(usage.get("cachedContentTokenCount", 0))

        attr_input = dict(headers or {})
        if "metadata" in payload and isinstance(payload["metadata"], dict):
            attr_input.update(payload["metadata"])

        attr = self.attribution_parser.parse_attribution(attr_input)

        return RequestRecord(
            request_id=req_id,
            timestamp_utc=datetime.now(timezone.utc),
            team=attr["team"],
            feature=attr["feature"],
            user_id=attr["user_id"],
            provider="google",
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cached_tok,
            status="success",
            latency_ms=int(payload.get("latency_ms", 0)),
            env=attr.get("env", "production"),
            source="google_adapter"
        )

    def parse_simulated(self, payload: Dict[str, Any], headers: Optional[Dict[str, Any]] = None) -> RequestRecord:
        """Parse Simulated / Custom provider format."""
        req_id = str(payload.get("simulated_request_id") or payload.get("id") or f"sim_{uuid.uuid4().hex[:10]}")
        model = str(payload.get("model", "simulated-fast-llm")).strip().lower()

        # Handle nested tokens object or flat keys
        tokens_obj = payload.get("tokens", {}) if isinstance(payload.get("tokens"), dict) else payload
        in_tok = int(tokens_obj.get("input_tokens") or tokens_obj.get("prompt_tokens") or 0)
        out_tok = int(tokens_obj.get("output_tokens") or tokens_obj.get("generated_tokens") or 0)
        cached_tok = int(tokens_obj.get("cached_tokens") or tokens_obj.get("cache_hits") or 0)

        latency = int(payload.get("execution_ms") or payload.get("latency_ms") or 0)

        attr_input = dict(headers or {})
        tags = payload.get("tags") or payload.get("attribution") or {}
        if isinstance(tags, dict):
            attr_input.update(tags)

        attr = self.attribution_parser.parse_attribution(attr_input)

        return RequestRecord(
            request_id=req_id,
            timestamp_utc=datetime.now(timezone.utc),
            team=attr["team"],
            feature=attr["feature"],
            user_id=attr["user_id"],
            provider="simulated",
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cached_tok,
            status=str(payload.get("status", "success")).lower(),
            latency_ms=latency,
            env=attr.get("env", "production"),
            source="simulated_adapter"
        )

    def parse_payload(
        self,
        payload: Dict[str, Any],
        format_hint: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> Tuple[RequestRecord, str]:
        """Normalize any supported provider payload into a RequestRecord."""
        fmt = self.detect_format(payload, format_hint=format_hint)

        if fmt == "anthropic":
            return self.parse_anthropic(payload, headers=headers), fmt
        elif fmt == "google":
            return self.parse_google(payload, headers=headers), fmt
        elif fmt == "simulated":
            return self.parse_simulated(payload, headers=headers), fmt
        else:
            return self.parse_openai(payload, headers=headers), fmt
