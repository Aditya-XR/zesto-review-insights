"""A small LLM client.

Gemini, OpenAI and Groq all speak the OpenAI "chat completions" protocol, so one client
covers all three. The provider is picked by whichever standard key is set in the environment.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

# (key variable, base URL, default model). The first key that is set wins.
PROVIDERS = [
    ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"),
    ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
]


class LLMError(Exception):
    pass


class LLMNotConfigured(LLMError):
    pass


@dataclass
class LLMConfig:
    key_name: str
    api_key: str
    base_url: str
    model: str


def get_config() -> LLMConfig | None:
    for key_name, base_url, default_model in PROVIDERS:
        api_key = os.environ.get(key_name, "").strip()
        if api_key:
            return LLMConfig(
                key_name=key_name,
                api_key=api_key,
                base_url=os.environ.get("LLM_BASE_URL", "").strip() or base_url,
                model=_model_for(key_name, default_model),
            )
    return None


def _model_for(key_name: str, default_model: str) -> str:
    """LLM_MODEL, unless it obviously belongs to another provider (e.g. .env says gemini-2.5-flash
    but only OPENAI_API_KEY was filled in): then fall back to this provider's default."""
    model = os.environ.get("LLM_MODEL", "").strip()
    if not model or model.startswith("gemini") != (key_name == "GEMINI_API_KEY"):
        return default_model
    return model


def is_configured() -> bool:
    return get_config() is not None


def parse_json(text: str):
    """Parse a JSON reply, tolerating ```json fences or text around the object."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def chat_json(system: str, user: str, *, max_retries: int = 6, timeout: int = 120) -> dict:
    """Send one prompt and return the parsed JSON object from the reply.

    Retries rate limits (429) and server errors with backoff, since free tiers hit them often.
    """
    cfg = get_config()
    if cfg is None:
        raise LLMNotConfigured("No LLM API key is configured.")

    body = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    effort = os.environ.get("LLM_REASONING_EFFORT", "").strip()
    if effort:
        body["reasoning_effort"] = effort

    delay = 4.0
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                f"{cfg.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json=body,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            err = f"network error: {exc}"
        else:
            if resp.status_code == 200:
                try:
                    return parse_json(resp.json()["choices"][0]["message"]["content"] or "")
                except (KeyError, IndexError, ValueError) as exc:
                    err = f"unparseable reply: {exc}"
            elif resp.status_code in (429, 500, 502, 503, 504):
                err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.replace(".", "", 1).isdigit():
                    delay = max(delay, float(retry_after))
            else:
                # 400/401/403/404: retrying won't help (bad key, bad model name...).
                raise LLMError(f"LLM request failed with HTTP {resp.status_code}: {resp.text[:300]}")
        log.warning("LLM attempt %d/%d failed: %s", attempt, max_retries, err)
        if attempt == max_retries:
            raise LLMError(f"LLM request failed after {max_retries} attempts: {err}")
        time.sleep(min(delay, 60))
        delay *= 2
    raise AssertionError("unreachable")
