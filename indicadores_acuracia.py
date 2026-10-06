from __future__ import annotations

import json
import re
import unicodedata
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from indicadores_api import (
    carregar_movimentacao_central,
    rotulo_fonte,
    source_frame,
    status_acuracia_central,
)
from supabase_client import get_client


INDICADOR = "ACURÁCIA DE ESTOQUE"
TZ_APP = ZoneInfo("America/Sao_Paulo")
LOGIC_VERSION = "2026-10-06-acuracia-movimentacao-v1"
AJUSTES_TESA = {"020", "520"}


def _agora_local() -> datetime:
    return datetime.now(TZ_APP)


def _ultimo_dia_mes(data_ref: date) -> date:
    return date(data_ref.year, data_ref.month, monthrange(data_ref.year, data_ref.month)[1])


def _normalizar_cabecalho(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()
    return re.sub(r"\s+", " ", text)


def _normalizar_tesa(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip().upper()
    if not text or text == "NAN":
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    digits = re.sub(r"[^0-9]", "", text)
    if digits:
        try:
            return str(int(digits)).zfill(3)
        except Exception:
            pass
    return text


def _to_dates(series: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(series, dayfirst=True, errors="coerce", format="mixed")
    except TypeError:
        return pd.to_datetime(series, dayfirst=True, errors="coerce")


def _read_movimentacao(source) -> pd.DataFrame:
    if isinstance(source, dict) and str(source.get("format") or "") == "SETTA_SOURCE_V1":
        return source_frame(source, sheet_name=0, header=0, dtype=str)
    if hasattr(source, "seek"):
        source.seek(0)
    return pd.read_excel(source, sheet_name=0, dtype=str)


def _localizar_coluna(df: pd.DataFrame, aliases: set[str]) -> str:
    norm = {_normalizar_cabecalho(col): col for col in df.columns}
    for alias in aliases:
        key = _normalizar_cabecalho(alias)
        if key in norm:
            return norm[key]
    raise ValueError(
        "Não foi possível localizar uma das colunas esperadas: "
        + ", ".join(sorted(aliases))
    )


def _meta_mensal(data_ref: date) -> float:
    # Histórico oficial: JAN = 92,00% e evolução de +0,50 p.p. ao mês.
    return round(92.0 + (int(data_ref.month) - 1) * 0.5, 2)


def calcular_acuracia_movimentacao(source, hoje: date | None = None) -> list[dict]:
    hoje = hoje or _agora_local().date()
    df = _read_movimentacao(source)
    if df.empty:
        raise ValueError("A base MOVIMENTAÇÃO está vazia.")

    col_tesa = _localizar_coluna(
        df,
        {"TESA", "TM", "TIPO MOVIMENTACAO", "TIPO DE MOVIMENTACAO"},
    )
    col_data = _localizar_coluna(
        df,
        {"EMISSAO", "EMISSÃO", "DATA EMISSAO", "DATA DE EMISSAO", "DATA"},
    )

    work = df.copy()
    work["_data"] = _to_dates(work[col_data]).dt.normalize()
    work["_tesa"] = work[col_tesa].map(_normalizar_tesa)
    work = work[work["_data"].notna()].copy()
    if work.empty:
        raise ValueError("Nenhuma movimentação com data válida foi encontrada.")

    work["_periodo"] = work["_data"].dt.to_period("M")
    resultados: list[dict] = []

    for periodo, part in work.groupby("_periodo", sort=True):
        inicio = date(int(periodo.year), int(periodo.month), 1)
        competencia = _ultimo_dia_mes(inicio)
        periodo_atual = (periodo.year, periodo.month) == (hoje.year, hoje.month)

        if (periodo.year, periodo.month) > (hoje.year, hoje.month):
            continue

        if periodo_atual:
            fim = hoje - timedelta(days=1)
            if fim < inicio:
                continue
            status = "PARCIAL"
        else:
            fim = competencia
            status = "FECHADO"

        corte = part[
            (part["_data"] >= pd.Timestamp(inicio))
            & (part["_data"] <= pd.Timestamp(fim))
        ].copy()
        total = int(len(corte))
        if total <= 0:
            continue

        ajustes = int(corte["_tesa"].isin(AJUSTES_TESA).sum())
        regulares = total - ajustes
        acuracia = (regulares / total) * 100.0

        resultados.append({
            "competencia": competencia.isoformat(),
            "periodo_inicio": inicio.isoformat(),
            "periodo_fim": fim.isoformat(),
            "status_competencia": status,
            "valor": round(acuracia, 6),
            "meta": _meta_mensal(inicio),
            "movimentacoes_total": total,
            "ajustes_total": ajustes,
            "movimentacoes_regulares": regulares,
            "coluna_tesa": str(col_tesa),
            "coluna_data": str(col_data),
            "tesa_ajustes": sorted(AJUSTES_TESA),
            "logic_version": LOGIC_VERSION,
            "gerado_em": _agora_local().isoformat(),
        })

    if not resultados:
        raise ValueError("A base não possui competência elegível para cálculo.")
    return resultados


def _observacao_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def salvar_competencias_acuracia(resultados: list[dict]) -> list[dict]:
    client = get_client()
    saida = []

    for resultado in resultados:
        competencia = str(resultado["competencia"])
        status = str(resultado["status_competencia"]).upper()
        existentes = (
            client.table("almox_indicadores")
            .select("id,observacao")
            .in_("indicador", [
                "ACURÁCIA DE ESTOQUE",
                "ACURACIDADE DE ESTOQUE",
                "ACURÁCIDADE DE ESTOQUE",
                "ACURACIA DE ESTOQUE",
            ])
            .eq("competencia", competencia)
            .limit(5)
            .execute()
            .data
            or []
        )

        payload = {
            "competencia": competencia,
            "categoria": "OPERACIONAL",
            "indicador": INDICADOR,
            "valor": round(float(resultado["valor"]), 2),
            "meta": round(float(resultado["meta"]), 2),
            "unidade": "%",
            "observacao": json.dumps(
                {
                    "origem": "movimentacao_central_setta",
                    **resultado,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }

        if existentes:
            principal = existentes[0]
            atual = _observacao_dict(principal.get("observacao"))
            status_atual = str(atual.get("status_competencia") or "").upper()
            if status_atual == "FECHADO":
                saida.append({
                    "competencia": competencia,
                    "acao": "mantido_fechado",
                    **resultado,
                })
                continue
            client.table("almox_indicadores").update(payload).eq("id", principal["id"]).execute()
            acao = "fechado" if status == "FECHADO" else "atualizado"
        else:
            client.table("almox_indicadores").insert(payload).execute()
            acao = "fechado" if status == "FECHADO" else "salvo"

        try:
            client.table("almox_historico").insert({
                "tipo": "indicador_acuracia_estoque",
                "descricao": f"Acurácia {acao}: competência {competencia}",
                "dados": resultado,
            }).execute()
        except Exception:
            pass

        saida.append({
            "competencia": competencia,
            "acao": acao,
            **resultado,
        })

    return saida


@st.fragment
def render_alimentacao_acuracia(indicadores):
    with st.expander("ALIMENTAR · ACURÁCIA DE ESTOQUE", expanded=False):
        try:
            state = status_acuracia_central()
            meta = state.get("source") or {}
            st.caption(f"MOVIMENTAÇÃO: {rotulo_fonte(meta)}")
            if not state.get("ready"):
                st.warning("A base MOVIMENTAÇÃO ainda não está disponível na Central SETTA.")
                return

            with st.spinner("Carregando MOVIMENTAÇÃO e calculando a acurácia..."):
                bundle = carregar_movimentacao_central()
        except Exception as exc:
            st.error(f"Não foi possível carregar a base MOVIMENTAÇÃO: {exc}")
            return

        fingerprint = str(bundle.get("fingerprint") or "")
        source = bundle["movimentacao"]
        session_fp = st.session_state.get("acuracia_movimentacao_fingerprint")

        if session_fp != fingerprint:
            try:
                resultados = calcular_acuracia_movimentacao(source)
                gravados = salvar_competencias_acuracia(resultados)
                st.session_state["acuracia_movimentacao_resultados"] = gravados
                st.session_state["acuracia_movimentacao_fingerprint"] = fingerprint
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"Não foi possível calcular/gravar a acurácia: {exc}")
                return

        gravados = st.session_state.get("acuracia_movimentacao_resultados") or []
        if not gravados:
            st.info("A base está vinculada. Uma nova versão da MOVIMENTAÇÃO disparará a próxima apuração.")
            return

        ultimo = sorted(gravados, key=lambda x: x.get("competencia") or "")[-1]
        a, b, c, d = st.columns(4)
        a.metric("ACURÁCIA", f"{float(ultimo['valor']):.2f}%")
        b.metric("META", f"{float(ultimo['meta']):.2f}%")
        c.metric("AJUSTES", f"{int(ultimo['ajustes_total']):,}".replace(",", "."))
        d.metric("MOVIMENTAÇÕES", f"{int(ultimo['movimentacoes_total']):,}".replace(",", "."))

        st.caption(
            f"Competência {pd.to_datetime(ultimo['competencia']).strftime('%m/%Y')} · "
            f"{pd.to_datetime(ultimo['periodo_inicio']).strftime('%d/%m/%Y')} a "
            f"{pd.to_datetime(ultimo['periodo_fim']).strftime('%d/%m/%Y')} · "
            f"status {ultimo['status_competencia']} · "
            f"TESA/TM 020 e 520 classificados como AJUSTE."
        )

        conferencia = pd.DataFrame([
            {
                "Competência": pd.to_datetime(r["competencia"]).strftime("%m/%Y"),
                "Status": r["status_competencia"],
                "Movimentações": r["movimentacoes_total"],
                "Ajustes 020/520": r["ajustes_total"],
                "Acurácia (%)": round(float(r["valor"]), 2),
                "Meta (%)": round(float(r["meta"]), 2),
                "Ação": r["acao"],
            }
            for r in gravados
        ])
        st.dataframe(conferencia, use_container_width=True, hide_index=True)
