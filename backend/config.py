import os
from pathlib import Path

from dotenv import load_dotenv

REQUIRED_ENV_VARS = [
    "SHOONYA_USER_ID",
    "SHOONYA_PASSWORD",
    "SHOONYA_VENDOR_CODE",
    "SHOONYA_API_SECRET",
    "SHOONYA_IMEI",
    "SHOONYA_TOTP_SECRET",
]

ZERODHA_ENV_VARS = [
    "ZERODHA_USER_ID",
    "ZERODHA_PASSWORD",
    "ZERODHA_TOTP_SECRET",
]


def load_config() -> dict:
    """Load broker credentials from .env in sibling shoonya-script folder or project root."""
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent / "shoonya-script" / ".env",
    ]

    loaded = False
    for p in candidates:
        if p.exists():
            load_dotenv(p)
            loaded = True
            break

    if not loaded:
        raise RuntimeError(
            ".env not found. Place it in project root or ensure ../shoonya-script/.env exists."
        )

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
