"""Public browser authentication settings, shared by local and static viewers."""

import os


def auth_config(defaults: dict | None = None) -> dict:
    defaults = defaults or {}
    url = os.environ.get("SUPABASE_URL", defaults.get("url", "")).strip().rstrip("/")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", defaults.get("publishableKey", "")).strip()
    if bool(url) != bool(key):
        raise ValueError("Set both SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY")
    if key.startswith("sb_secret_"):
        raise ValueError("Use a public publishable key, never a secret key")
    if key and not key.startswith("sb_publishable_"):
        raise ValueError("Use the Supabase publishable key (sb_publishable_...)")
    if url and not url.startswith("https://"):
        raise ValueError("SUPABASE_URL must use HTTPS")
    return {"url": url, "publishableKey": key} if url else {}
