"""Minimal Gemini REST client.

Deliberately not using the `google-genai` SDK: version 2.x requires Python >= 3.10,
which would pin to 1.47.x locally (3.9) while CI installed 2.x — a version split inside
a single repo. Calling the REST endpoint keeps one code path everywhere and holds total
dependencies to `requests` + `feedparser`.

Robustness is layered, because a model returning almost-JSON is the normal failure mode:

  1. `responseMimeType: application/json` + an explicit `responseSchema` constrains the
     output at the API level, which is far stronger than asking nicely in the prompt.
  2. The parser still strips code fences and surrounding prose defensively.
  3. One retry with a terser instruction on a parse failure.
  4. After that, the caller falls back to deterministic enrichment.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

import requests

import config

log = logging.getLogger(__name__)


class GeminiError(RuntimeError):
    """Raised when a response cannot be obtained or parsed after the retry."""


# Set to False the first time the API rejects `thinkingConfig`, so a run against a model
# family that predates it (Gemini 2.x used `thinkingBudget`) degrades once rather than
# failing every call. Single-element list because this is mutated from worker threads.
_THINKING_SUPPORTED = [True]


# ------------------------------------------------------------------------------------
# Key handling
# ------------------------------------------------------------------------------------


def api_key() -> Optional[str]:
    key = os.environ.get(config.GEMINI_API_KEY_ENV, "").strip()
    return key or None


def is_available() -> bool:
    return api_key() is not None


# ------------------------------------------------------------------------------------
# Response parsing
# ------------------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


def strip_fences(text: str) -> str:
    """Remove markdown code fences and any prose around the JSON payload."""
    if not text:
        return ""

    cleaned = _FENCE.sub("", text.strip())

    # If prose still surrounds the payload, recover the outermost JSON value.
    # Both bracket styles are tried and the one starting earliest wins, so a top-level
    # array is not mistaken for the first object nested inside it.
    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            candidates.append((start, cleaned[start : end + 1]))

    for _, candidate in sorted(candidates):
        try:
            json.loads(candidate)
            return candidate
        except ValueError:
            continue

    return cleaned.strip()


def _extract_text(payload: Dict[str, Any]) -> str:
    """Pull the generated text out of a generateContent response.

    Guards every hop: a blocked or truncated response can legitimately arrive with no
    candidates or no parts, and that must read as a clean failure rather than a
    KeyError deep in the pipeline.
    """
    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        reason = feedback.get("blockReason")
        raise GeminiError("no candidates returned{0}".format(
            " (blocked: {0})".format(reason) if reason else ""
        ))

    candidate = candidates[0]
    finish = candidate.get("finishReason")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()

    # Report truncation as truncation. On reasoning models the thinking tokens draw from
    # the same maxOutputTokens budget, so an undersized budget yields JSON cut off
    # mid-string — which would otherwise surface as a baffling "Unterminated string".
    if finish == "MAX_TOKENS":
        usage = payload.get("usageMetadata") or {}
        raise GeminiError(
            "response truncated at maxOutputTokens "
            "(thinking={0}, answer={1}) — raise GEMINI_MAX_OUTPUT_TOKENS".format(
                usage.get("thoughtsTokenCount", "?"),
                usage.get("candidatesTokenCount", "?"),
            )
        )

    if not text:
        raise GeminiError("empty response text{0}".format(
            " (finishReason: {0})".format(finish) if finish else ""
        ))

    return text


# ------------------------------------------------------------------------------------
# Request
# ------------------------------------------------------------------------------------


def _post(body: Dict[str, Any], key: str, timeout: int) -> Dict[str, Any]:
    url = config.GEMINI_ENDPOINT.format(model=config.GEMINI_MODEL)
    headers = {
        "Content-Type": "application/json",
        # Header auth keeps the key out of URLs, logs and proxy history.
        "x-goog-api-key": key,
    }

    last_exc: Optional[Exception] = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError("retryable status {0}".format(resp.status_code))
            if resp.status_code >= 400:
                # Surface the API's own error message; it names the actual problem
                # (bad model id, key without access, quota exhausted).
                detail = ""
                try:
                    detail = (resp.json().get("error") or {}).get("message", "")
                except ValueError:
                    detail = resp.text[:200]
                raise GeminiError("HTTP {0}: {1}".format(resp.status_code, detail))
            return resp.json()
        except GeminiError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport-level, retry
            last_exc = exc
            if attempt < config.HTTP_RETRIES:
                time.sleep(config.HTTP_BACKOFF ** attempt)

    raise GeminiError("request failed after retries: {0}".format(last_exc))


def generate_json(
    prompt: str,
    schema: Dict[str, Any],
    system_instruction: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Call Gemini and return parsed JSON conforming to `schema`.

    Raises GeminiError if no valid JSON can be obtained after one retry.
    """
    key = api_key()
    if not key:
        raise GeminiError("{0} is not set".format(config.GEMINI_API_KEY_ENV))

    def build(instruction: str, user_prompt: str) -> Dict[str, Any]:
        generation_config: Dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": config.GEMINI_TEMPERATURE,
            "maxOutputTokens": max_output_tokens or config.GEMINI_MAX_OUTPUT_TOKENS,
        }
        if config.GEMINI_THINKING_LEVEL and _THINKING_SUPPORTED[0]:
            generation_config["thinkingConfig"] = {
                "thinkingLevel": config.GEMINI_THINKING_LEVEL
            }

        body: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": generation_config,
        }
        if instruction:
            body["systemInstruction"] = {"parts": [{"text": instruction}]}
        return body

    attempts = [
        (system_instruction or "", prompt),
        # Retry is terser and repeats the JSON-only demand; verbose reasoning is the
        # usual cause of a payload the parser cannot recover.
        (
            (system_instruction or "") + "\n\nReturn ONLY the JSON value. No prose, no "
            "markdown fences, no explanation.",
            prompt + "\n\nRespond with the JSON value only.",
        ),
    ]

    last_error: Optional[str] = None

    for index, (instruction, user_prompt) in enumerate(attempts):
        try:
            payload = _post(build(instruction, user_prompt), key, timeout or config.GEMINI_TIMEOUT)
            text = _extract_text(payload)
            return json.loads(strip_fences(text))
        except GeminiError as exc:
            last_error = str(exc)

            # The model family does not know `thinkingConfig`. Disable it and retry
            # rather than failing every remaining item in the run.
            if "thinking" in last_error.lower() and _THINKING_SUPPORTED[0]:
                log.warning("model rejected thinkingConfig; disabling it for this run")
                _THINKING_SUPPORTED[0] = False
                continue

            # A key/quota/model error will not be fixed by rephrasing; stop immediately.
            if "HTTP 4" in last_error and "429" not in last_error:
                break
        except ValueError as exc:
            last_error = "JSON parse failed: {0}".format(exc)

        if index == 0:
            log.debug("gemini retrying after: %s", last_error)

    raise GeminiError(last_error or "unknown failure")
