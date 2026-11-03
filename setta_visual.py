"""Identidade global SETTA, mantida pelo Monitor de APIs.

O Gestão Operacional mantém sua própria aparência. Apenas o favicon do
navegador é obtido da configuração institucional central.
"""
from __future__ import annotations

import base64
from io import BytesIO
import os
from pathlib import Path

from PIL import Image, UnidentifiedImageError
import requests
import streamlit as st

DEFAULT_URL = "https://cuixazpxkvniqldmmnth.supabase.co"
FALLBACK_ICON = Path(__file__).with_name("setta_favicon.png")


def _secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
        if value:
            return str(value).strip().strip('"').strip("'")
    except Exception:
        pass
    return str(os.getenv(name) or "").strip().strip('"').strip("'")


@st.cache_data(ttl=300, show_spinner=False, max_entries=1)
def _global_favicon_bytes() -> bytes:
    """Consulta a mesma chave setta_global usada no Monitor de APIs."""
    key = _secret("SUPABASE_ANON_KEY") or _secret("SUPABASE_KEY")
    if not key:
        return b""
    url = _secret("SUPABASE_URL") or DEFAULT_URL
    try:
        response = requests.post(
            f"{url.rstrip('/')}/functions/v1/setta-data-api",
            headers={
                "Authorization": f"Bearer {key}",
                "apikey": key,
                "Content-Type": "application/json",
            },
            json={"action": "visual_get", "payload": {"app_key": "setta_global"}},
            timeout=(3, 8),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return b""
        config = payload.get("data") or {}
        encoded = str(config.get("favicon_data") or "")
        if not encoded or len(encoded) > 3_000_000:
            return b""
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 2_000_000:
            return b""
        with Image.open(BytesIO(raw)) as img:
            img.verify()
        return raw
    except (requests.RequestException, ValueError, TypeError, OSError, UnidentifiedImageError):
        return b""


def centralized_page_icon():
    """Nunca impede a abertura do app se a Central estiver indisponível."""
    raw = _global_favicon_bytes()
    if raw:
        try:
            with Image.open(BytesIO(raw)) as img:
                return img.convert("RGBA")
        except (OSError, ValueError, UnidentifiedImageError):
            pass
    try:
        with Image.open(FALLBACK_ICON) as img:
            return img.convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError):
        return "📊"
