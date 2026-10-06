from __future__ import annotations

import gzip
import io
import json
import urllib.request
from typing import Any

import pandas as pd
import streamlit as st

from supabase_client import get_client


SOURCE_KEYS_OTIF = ("relatorio_geral", "for022", "cadastros")
DERIVED_KEY_OTIF = "relatorio_mrp"
SOURCE_KEYS_ACURACIA = ("analitico", "movimentacao")


def _response_data(response: Any) -> Any:
    data = getattr(response, "data", response)
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data).decode("utf-8")
        return json.loads(raw) if raw else {}
    if isinstance(data, str):
        return json.loads(data) if data.strip() else {}
    return data


def central_api_call(action: str, payload: dict | None = None) -> dict:
    token = str(st.session_state.get("acesso_access_token") or "").strip()
    options: dict[str, Any] = {
        "body": {
            "action": str(action),
            "payload": payload or {},
        }
    }
    if token:
        options["headers"] = {"Authorization": f"Bearer {token}"}

    response = get_client().functions.invoke(
        "setta-data-api",
        invoke_options=options,
    )
    data = _response_data(response)
    if not isinstance(data, dict):
        raise RuntimeError("Resposta inválida da Central SETTA.")
    if data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "Falha na Central SETTA."))
    return data


def _meta_map(rows: list[dict], key_name: str) -> dict[str, dict]:
    return {
        str(row.get(key_name) or ""): row
        for row in (rows or [])
        if isinstance(row, dict) and str(row.get(key_name) or "")
    }


@st.cache_data(ttl=120, show_spinner=False)
def status_otif_central() -> dict:
    sources_resp = central_api_call(
        "source_status",
        {"keys": list(SOURCE_KEYS_OTIF)},
    )
    derived_resp = central_api_call(
        "derived_status",
        {"keys": [DERIVED_KEY_OTIF]},
    )
    sources = _meta_map(sources_resp.get("data") or [], "source_key")
    derived = _meta_map(derived_resp.get("data") or [], "base_key")
    return {
        "sources": sources,
        "derived": derived,
        "ready": all(bool((sources.get(k) or {}).get("available")) for k in SOURCE_KEYS_OTIF)
        and bool((derived.get(DERIVED_KEY_OTIF) or {}).get("available")),
    }


def _download_url(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SETTA-GestaoOperacional/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


@st.cache_data(ttl=3600, show_spinner=False, max_entries=12)
def _download_source_cached(source_key: str, version: int, updated_at: str) -> bytes:
    del version, updated_at
    data = central_api_call(
        "source_download",
        {"source_key": source_key},
    ).get("data") or {}
    signed_url = str(data.get("signed_url") or "")
    if not signed_url:
        raise RuntimeError(f"Fonte {source_key} sem URL de leitura.")
    return _download_url(signed_url)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=12)
def _download_normalized_source_cached(
    source_key: str,
    version: int,
    updated_at: str,
) -> dict:
    del version, updated_at
    data = central_api_call(
        "source_normalized_download",
        {"source_key": source_key},
    ).get("data") or {}
    signed_url = str(data.get("signed_url") or "")
    if not signed_url:
        raise RuntimeError(f"Fonte normalizada {source_key} sem URL de leitura.")
    compressed = _download_url(signed_url)
    pack = json.loads(gzip.decompress(compressed).decode("utf-8"))
    if str(pack.get("format") or "") != "SETTA_SOURCE_V1":
        raise RuntimeError(f"Formato normalizado inválido para {source_key}.")
    return pack


def _excel_col_to_index(value: str) -> int:
    result = 0
    for char in str(value).strip().upper():
        if not ("A" <= char <= "Z"):
            raise ValueError(f"Coluna Excel inválida: {value}")
        result = result * 26 + (ord(char) - 64)
    return result - 1


def _usecols_indexes(usecols: Any, width: int) -> list[int] | None:
    if usecols is None:
        return None
    if isinstance(usecols, (list, tuple)):
        return [int(x) for x in usecols]
    text = str(usecols).strip()
    if not text:
        return None
    indexes: list[int] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            left, right = token.split(":", 1)
            a = _excel_col_to_index(left)
            b = _excel_col_to_index(right)
            indexes.extend(range(min(a, b), max(a, b) + 1))
        else:
            indexes.append(_excel_col_to_index(token))
    return [idx for idx in indexes if 0 <= idx < width]


def source_frame(
    pack: dict,
    *,
    sheet_name: str | int = 0,
    header: int | None = 0,
    usecols: Any = None,
    dtype: Any = None,
) -> pd.DataFrame:
    sheets = [
        item for item in (pack.get("sheets") or [])
        if isinstance(item, dict)
    ]
    if not sheets:
        raise ValueError("Pacote normalizado sem planilhas.")

    if isinstance(sheet_name, str):
        selected = next(
            (item for item in sheets if str(item.get("name") or "") == sheet_name),
            None,
        )
        if selected is None:
            raise ValueError(f"A planilha '{sheet_name}' não foi encontrada.")
    else:
        index = int(sheet_name)
        if index < 0 or index >= len(sheets):
            raise ValueError(f"Índice de planilha inválido: {index}.")
        selected = sheets[index]

    raw = pd.DataFrame(selected.get("rows") or [])
    indexes = _usecols_indexes(usecols, raw.shape[1])
    if indexes is not None:
        raw = raw.iloc[:, indexes].copy()

    if header is None:
        frame = raw.reset_index(drop=True)
    else:
        header_index = int(header)
        if header_index < 0 or header_index >= len(raw):
            raise ValueError(f"Linha de cabeçalho inválida: {header_index}.")
        values = raw.iloc[header_index].tolist()
        used: dict[str, int] = {}
        columns = []
        for idx, value in enumerate(values):
            base = (
                f"Unnamed: {idx}"
                if value is None or str(value).strip() == ""
                else str(value)
            )
            count = used.get(base, 0)
            used[base] = count + 1
            columns.append(base if count == 0 else f"{base}.{count}")
        frame = raw.iloc[header_index + 1 :].reset_index(drop=True).copy()
        frame.columns = columns

    if dtype is str:
        for col in frame.columns:
            frame[col] = frame[col].map(
                lambda value: value if pd.isna(value) else str(value)
            )
    return frame


def _download_preferred_source(
    source_key: str,
    version: int,
    updated_at: str,
) -> Any:
    try:
        return _download_normalized_source_cached(
            source_key,
            version,
            updated_at,
        )
    except Exception:
        return io.BytesIO(
            _download_source_cached(source_key, version, updated_at)
        )


@st.cache_data(ttl=3600, show_spinner=False, max_entries=8)
def _download_derived_cached(
    base_key: str,
    processed_at: str,
    source_versions_token: str,
) -> pd.DataFrame:
    del processed_at, source_versions_token
    data = central_api_call(
        "derived_download",
        {"base_key": base_key},
    ).get("data") or {}
    signed_url = str(data.get("signed_url") or "")
    if not signed_url:
        raise RuntimeError(f"Base {base_key} sem URL de leitura.")
    compressed = _download_url(signed_url)
    raw = gzip.decompress(compressed)
    return pd.read_json(io.BytesIO(raw), orient="table")


def _derived_token(meta: dict) -> str:
    return json.dumps(
        meta.get("source_versions") or {},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def carregar_otif_central() -> dict:
    state = status_otif_central()
    if not state.get("ready"):
        ausentes = []
        for key in SOURCE_KEYS_OTIF:
            if not bool((state.get("sources", {}).get(key) or {}).get("available")):
                ausentes.append(key)
        if not bool((state.get("derived", {}).get(DERIVED_KEY_OTIF) or {}).get("available")):
            ausentes.append(DERIVED_KEY_OTIF)
        raise RuntimeError(
            "Base(s) indisponível(is) na Central SETTA: " + ", ".join(ausentes)
        )

    sources = state["sources"]
    derived = state["derived"][DERIVED_KEY_OTIF]

    arquivos: dict[str, io.BytesIO] = {}
    for key in SOURCE_KEYS_OTIF:
        meta = sources[key]
        arquivos[key] = _download_preferred_source(
            key,
            int(meta.get("version") or 0),
            str(meta.get("last_update_at") or meta.get("updated_at") or ""),
        )

    mrp = _download_derived_cached(
        DERIVED_KEY_OTIF,
        str(derived.get("processed_at") or ""),
        _derived_token(derived),
    )

    fingerprint_payload = {
        "sources": {
            key: {
                "version": int((sources.get(key) or {}).get("version") or 0),
                "updated_at": str(
                    (sources.get(key) or {}).get("last_update_at")
                    or (sources.get(key) or {}).get("updated_at")
                    or ""
                ),
            }
            for key in SOURCE_KEYS_OTIF
        },
        "relatorio_mrp": {
            "processed_at": str(derived.get("processed_at") or ""),
            "source_versions": derived.get("source_versions") or {},
        },
    }

    return {
        "relatorio": arquivos["relatorio_geral"],
        "for022": arquivos["for022"],
        "cadastro": arquivos["cadastros"],
        "mrp": mrp,
        "status": state,
        "fingerprint": json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ),
    }


def rotulo_fonte(meta: dict | None) -> str:
    meta = meta or {}
    if not meta.get("available"):
        return "INDISPONÍVEL"
    versao = meta.get("version")
    linhas = meta.get("rows_count")
    partes = ["ATUALIZADA"]
    if versao not in (None, ""):
        partes.append(f"v{int(versao)}")
    if linhas not in (None, ""):
        partes.append(f"{int(linhas):,} linhas".replace(",", "."))
    return " · ".join(partes)

@st.cache_data(ttl=120, show_spinner=False)
def status_acuracia_central() -> dict:
    resp = central_api_call(
        "source_status",
        {"keys": list(SOURCE_KEYS_ACURACIA)},
    )
    fontes = _meta_map(resp.get("data") or [], "source_key")
    return {
        "sources": fontes,
        "ready": all(bool((fontes.get(k) or {}).get("available")) for k in SOURCE_KEYS_ACURACIA),
    }


def carregar_acuracia_central() -> dict:
    state = status_acuracia_central()
    fontes = state.get("sources") or {}
    if not state.get("ready"):
        ausentes = [k for k in SOURCE_KEYS_ACURACIA if not bool((fontes.get(k) or {}).get("available"))]
        raise RuntimeError("Base(s) indisponível(is) na Central SETTA: " + ", ".join(ausentes))

    bundle = {}
    fp = {}
    for key in SOURCE_KEYS_ACURACIA:
        meta = fontes.get(key) or {}
        bundle[key] = _download_preferred_source(
            key,
            int(meta.get("version") or 0),
            str(meta.get("last_update_at") or meta.get("updated_at") or ""),
        )
        fp[key] = {
            "version": int(meta.get("version") or 0),
            "updated_at": str(meta.get("last_update_at") or meta.get("updated_at") or ""),
            "rows_count": meta.get("rows_count"),
        }

    return {
        "analitico": bundle["analitico"],
        "movimentacao": bundle["movimentacao"],
        "status": state,
        "fingerprint": json.dumps(
            fp,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ),
    }

