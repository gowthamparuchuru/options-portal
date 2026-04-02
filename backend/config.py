import os
from pathlib import Path

from dotenv import load_dotenv

REQUIRED_ENV_VARS = [
    "SHOONYA_USER_ID",
    "SHOONYA_PASSWORD",
    "SHOONYA_TOTP_SECRET",
    "SHOONYA_OAUTH_SECRET",
]

ZERODHA_ENV_VARS = [
    "ZERODHA_USER_ID",
    "ZERODHA_PASSWORD",
    "ZERODHA_TOTP_SECRET",
]


def load_config() -> dict:
    """Load broker credentials from .env in project root."""
    env_path = Path(__file__).resolve().parent.parent / ".env"

    if not env_path.exists():
        raise RuntimeError(".env not found. Place it in the project root.")

    load_dotenv(env_path)

    config = {}
    missing = []
    for var in REQUIRED_ENV_VARS:
        val = os.getenv(var)
        if not val or val.startswith("your_"):
            missing.append(var)
        else:
            config[var] = val

    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    for var in ZERODHA_ENV_VARS:
        val = os.getenv(var)
        if val and not val.startswith("your_"):
            config[var] = val

    return config


def has_zerodha_config(config: dict) -> bool:
    """Check if all Zerodha credentials are present."""
    return all(var in config for var in ZERODHA_ENV_VARS)
