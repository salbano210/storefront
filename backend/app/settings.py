"""Environment-driven settings. Secrets never leave the server."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BACKEND_DIR / "data"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


# --- OpenRouter -----------------------------------------------------------
OPENROUTER_BASE_URL = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_APP_NAME = _env("OPENROUTER_APP_NAME", "storefront")
OPENROUTER_APP_URL = _env("OPENROUTER_APP_URL", "https://github.com/salbano210/storefront")
OPENROUTER_TIMEOUT_SECONDS = _env_float("OPENROUTER_TIMEOUT_SECONDS", 60.0)
# Shared OpenRouter backends intermittently answer 502 "provider returned an empty
# response". Retrying the same model is much better than switching models
# mid-conversation, so a transient blip should never reach the fallback.
OPENROUTER_MAX_RETRIES = _env_int("OPENROUTER_MAX_RETRIES", 2)
OPENROUTER_RETRY_BACKOFF_SECONDS = _env_float("OPENROUTER_RETRY_BACKOFF_SECONDS", 0.5)

# Default models. Seeded into the brand config on first boot; the admin UI can
# change them afterwards without touching code.
DEFAULT_MODELS = {
    "router": _env("OPENROUTER_MODEL_ROUTER", "google/gemini-2.5-flash-lite"),
    "gift_request": _env("OPENROUTER_MODEL_GIFT_REQUEST", "google/gemini-2.5-flash-lite"),
    "creator_pitch": _env("OPENROUTER_MODEL_CREATOR_PITCH", "google/gemini-2.5-flash-lite"),
    "general_inquiry": _env("OPENROUTER_MODEL_GENERAL_INQUIRY", "meta-llama/llama-3.1-8b-instruct"),
    "fallback": _env("OPENROUTER_MODEL_FALLBACK", "openai/gpt-4.1-nano"),
}

# --- Demo protection ------------------------------------------------------
ADMIN_PASSCODE = _env("ADMIN_PASSCODE")
SESSION_SECRET = _env("SESSION_SECRET", "dev-secret-change-me")
SESSION_COOKIE = "storefront_session"
SESSION_TTL_DAYS = _env_int("SESSION_TTL_DAYS", 30)

# --- Budget + abuse guards ------------------------------------------------
MAX_DAILY_SPEND_USD = _env_float("MAX_DAILY_SPEND_USD", 2.0)
SESSION_MESSAGE_CAP = _env_int("SESSION_MESSAGE_CAP", 40)
IP_RATE_LIMIT_PER_MINUTE = _env_int("IP_RATE_LIMIT_PER_MINUTE", 10)

# --- Storage --------------------------------------------------------------
DATA_DIR = Path(_env("DATA_DIR") or DEFAULT_DATA_DIR).expanduser().resolve()
DATABASE_URL = _env("DATABASE_URL")
STATIC_DIR = _env("STATIC_DIR")

# --- Misc -----------------------------------------------------------------
IS_DEPLOYED = bool(_env("RENDER")) or bool(_env("IS_DEPLOYED"))
HOST_KEY_AVAILABLE = bool(OPENROUTER_API_KEY)
