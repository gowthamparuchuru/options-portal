import os
from pathlib import Path

from dotenv import load_dotenv

SHOONYA_ENV_VARS = [
    "SHOONYA_USER_ID",
    "SHOONYA_PASSWORD",
    "SHOONYA_TOTP_SECRET",
    "SHOONYA_API_SECRET",
]

UPSTOX_ENV_VARS = [
    "UPSTOX_API_KEY",
    "UPSTOX_API_SECRET",
    "UPSTOX_REDIRECT_URI",
    "UPSTOX_MOBILE_NUMBER",
    "UPSTOX_TOTP_SECRET",
    "UPSTOX_PIN",
]


def load_config() -> dict:
    """Load broker credentials from .env in project root."""
    env_path = Path(__file__).resolve().parent.parent / ".env"

    if not env_path.exists():
        raise RuntimeError(".env not found. Place it in the project root.")

    load_dotenv(env_path)

    config: dict[str, str] = {}
    missing: list[str] = []

    for var in SHOONYA_ENV_VARS:
        val = os.getenv(var)
        if not val or val.startswith("your_"):
            missing.append(var)
        else:
            config[var] = val

    if missing:
        raise RuntimeError(f"Missing Shoonya env vars: {', '.join(missing)}")

    missing_upstox: list[str] = []
    for var in UPSTOX_ENV_VARS:
        val = os.getenv(var)
        if not val or val.startswith("your_"):
            missing_upstox.append(var)
        else:
            config[var] = val

    if missing_upstox:
        raise RuntimeError(f"Missing Upstox env vars: {', '.join(missing_upstox)}")

    return config


def has_upstox_config(config: dict) -> bool:
    return all(var in config for var in UPSTOX_ENV_VARS)
