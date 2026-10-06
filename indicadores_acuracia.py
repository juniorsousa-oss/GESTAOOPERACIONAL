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
    carregar_acuracia_central,
    rotulo_fonte,
    source_frame,
    status_acuracia_central,
)
from supabase_client import get_client


INDICADOR = "ACURÁCIA DE ESTOQUE"
TZ_APP = ZoneInfo("America/Sao_Paulo")
LOGIC_VERSION = "2026-10-06-acuracia-analitico-ajustes-s2-v6"
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


def _read_source(source, header: int = 0) -> pd.DataFrame:
    if isinstance(source, dict) and str(source.get("format") or "") == "SETTA_SOURCE_V1":
        return source_frame(source, sheet_name=0, header=header, dtype=str)
    if hasattr(source, "seek"):
        source.seek(0)
    return pd.read_excel(source, sheet_name=0, header=header, dtype=str)


def _compact(value) -> str:
    return re.sub(r"[^A-Z0-9]+", "", _normalizar_cabecalho(value))


def _localizar_coluna(df: pd.DataFrame, aliases: set[str], obrigatoria: bool = True) -> str | None:
    norm = {_normalizar_cabecalho(col): col for col in df.columns}
    compact = {_compact(col): col for col in df.columns}

    for alias in aliases:
        key = _normalizar_cabecalho(alias)
        if key in norm:
            return norm[key]
        ckey = _compact(alias)
        if ckey in compact:
            return compact[ckey]

    # Variações comuns do Protheus: T.M., Cod. Armazem, Data Movimentação etc.
    alias_compact = {_compact(alias) for alias in aliases}
    for ckey, original in compact.items():
        if "TESA" in alias_compact and "TESA" in ckey:
            return original
        if "TM" in alias_compact and ckey in {"TM", "TIPOMOV", "TIPOMOVIMENTO", "TIPOMOVIMENTACAO"}:
            return original
        if "ARMAZEM" in alias_compact and "ARMAZEM" in ckey:
            return original
        if any(x.startswith("DATA") or "EMISSAO" in x for x in alias_compact):
            if "EMISSAO" in ckey or ("DATA" in ckey and ("MOV" in ckey or ckey == "DATA")):
                return original

    if obrigatoria:
        raise ValueError(
            "Não foi possível localizar uma das colunas esperadas: "
            + ", ".join(sorted(aliases))
        )
    return None


def _num(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text or text.upper() == "NAN":
        return 0.0
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return 0.0


def _normalizar_codigo(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip().upper()
    if not text or text == "NAN":
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    return re.sub(r"\s+", "", text)


def _base_s2_analitico(source) -> dict:
    # O relatório ANALÍTICO consumido pela Central/Inventário já é a origem
    # de estoque do S2. O layout oficial é:
    # A = código, D = descrição, H = saldo/quantidade, K = valor.
    # Não tentamos inferir "armazém" por nomes genéricos como LOCAL, pois isso
    # pode capturar campos que não representam o depósito e zerar a base.
    df = _read_source(source, header=1)
    if df.empty:
        raise ValueError("A base ANALÍTICO está vazia.")
    if df.shape[1] < 8:
        raise ValueError("ANALÍTICO sem as colunas mínimas esperadas até H.")

    col_codigo = df.columns[0]   # A
    col_saldo = df.columns[7]    # H

    work = df.copy()
    work["_codigo"] = work[col_codigo].map(_normalizar_codigo)
    work["_saldo"] = work[col_saldo].map(_num)
    work = work[work["_codigo"].ne("")].copy()

    por_codigo = work.groupby("_codigo", as_index=False)["_saldo"].sum()
    com_saldo = por_codigo[por_codigo["_saldo"] > 0].copy()

    return {
        "materiais_s2_com_saldo": int(com_saldo["_codigo"].nunique()),
        "linhas_analitico_consideradas": int(len(work)),
        "codigos_analitico_distintos": int(por_codigo["_codigo"].nunique()),
        "coluna_codigo": str(col_codigo),
        "coluna_saldo": str(col_saldo),
        "coluna_armazem": "",
        "escopo_s2": "ANALÍTICO oficial do S2 · código A · saldo H",
    }

def _meta_mensal(data_ref: date) -> float:
    # Referência validada: AGO/2026 = 95,50%.
    # A partir daí a meta cresce continuamente +0,50 p.p. por competência,
    # inclusive na virada do ano.
    base = date(2026, 8, 1)
    meses = (data_ref.year - base.year) * 12 + (data_ref.month - base.month)
    return round(95.50 + meses * 0.50, 2)


def calcular_acuracia_estoque(
    analitico_source,
    movimentacao_source,
    hoje: date | None = None,
    movimentacao_nome: str = "",
) -> list[dict]:
    hoje = hoje or _agora_local().date()
    base = _base_s2_analitico(analitico_source)
    materiais_base = int(base["materiais_s2_com_saldo"])
    if materiais_base <= 0:
        raise ValueError("Nenhum código distinto com saldo positivo foi encontrado no armazém S2.")

    # O Protheus pode deslocar o cabeçalho e também variar TESA como T.M./TM.
    # Procuramos nas primeiras linhas até encontrar simultaneamente tipo de
    # movimentação e armazém, evitando depender de uma posição fixa.
    mov = pd.DataFrame()
    col_tesa = None
    col_armazem_mov = None
    header_mov = None
    for header_idx in range(0, 7):
        tentativa = _read_source(movimentacao_source, header=header_idx)
        if tentativa.empty:
            continue
        tentativa_tesa = _localizar_coluna(
            tentativa,
            {"TESA", "TM", "T.M.", "T.M", "TES", "TIPO MOV", "TIPO MOV.", "TIPO MOVIMENTO", "TIPO MOVIMENTACAO", "TIPO DE MOVIMENTACAO"},
            obrigatoria=False,
        )
        tentativa_armazem = _localizar_coluna(
            tentativa,
            {
                "ARMAZEM", "ARMAZÉM", "LOCAL", "COD ARMAZEM", "CÓD ARMAZÉM",
                "COD. ARMAZEM", "COD. ARMAZÉM", "ARMAZEM ORIGEM", "ARMAZÉM ORIGEM",
                "LOCAL ORIGEM",
            },
            obrigatoria=False,
        )
        if tentativa_tesa is not None and tentativa_armazem is not None:
            mov = tentativa
            col_tesa = tentativa_tesa
            col_armazem_mov = tentativa_armazem
            header_mov = header_idx
            break

    if mov.empty:
        raise ValueError("A base MOVIMENTAÇÃO está vazia.")
    if col_tesa is None:
        raise ValueError("Não foi possível localizar a coluna TESA/T.M./TM na MOVIMENTAÇÃO.")
    if col_armazem_mov is None:
        raise ValueError("Não foi possível localizar a coluna de armazém na MOVIMENTAÇÃO para aplicar o filtro S2.")

    col_data = _localizar_coluna(
        mov,
        {"EMISSAO", "EMISSÃO", "DATA EMISSAO", "DATA DE EMISSAO", "DATA", "DT MOVIMENTACAO", "DATA MOVIMENTACAO"},
        obrigatoria=False,
    )

    work = mov.copy()
    work["_tesa"] = work[col_tesa].map(_normalizar_tesa)
    work["_armazem_mov"] = work[col_armazem_mov].fillna("").astype(str).str.strip().str.upper()
    work = work[
        work["_tesa"].isin(AJUSTES_TESA)
        & work["_armazem_mov"].eq("S2")
    ].copy()
    if work.empty:
        raise ValueError("Nenhuma movimentação TESA/TM 020 ou 520 do armazém S2 foi encontrada.")

    # Se houver data, apuramos cada competência existente no próprio relatório.
    # Se a MOVIMENTAÇÃO vier sem data, ela é considerada o relatório do mês
    # indicado pelo próprio arquivo/fonte e será tratada como competência atual.
    if col_data is not None:
        work["_data"] = _to_dates(work[col_data]).dt.normalize()
        work = work[work["_data"].notna()].copy()
        if work.empty:
            raise ValueError("As movimentações 020/520 não possuem datas válidas.")
        work["_periodo"] = work["_data"].dt.to_period("M")
        grupos = list(work.groupby("_periodo", sort=True))
    else:
        nome = str(movimentacao_nome or "")
        match = re.search(r"(20\d{2})[-_](0?[1-9]|1[0-2])", nome)
        if match:
            ano_ref = int(match.group(1))
            mes_ref = int(match.group(2))
            periodo_ref = pd.Period(f"{ano_ref:04d}-{mes_ref:02d}", freq="M")
        else:
            periodo_ref = pd.Period(hoje, freq="M")
        grupos = [(periodo_ref, work)]

    resultados: list[dict] = []
    for periodo, part in grupos:
        inicio = date(int(periodo.year), int(periodo.month), 1)
        competencia = _ultimo_dia_mes(inicio)
        mes = (int(periodo.year), int(periodo.month))
        mes_atual = (hoje.year, hoje.month)
        if mes > mes_atual:
            continue

        if col_data is not None:
            if mes == mes_atual:
                fim = hoje - timedelta(days=1)
                if fim < inicio:
                    continue
                part = part[part["_data"] <= pd.Timestamp(fim)].copy()
                status = "PARCIAL"
            else:
                fim = competencia
                part = part[part["_data"] <= pd.Timestamp(fim)].copy()
                status = "FECHADO"
        else:
            fim = hoje - timedelta(days=1) if mes == mes_atual else competencia
            status = "PARCIAL" if mes == mes_atual else "FECHADO"

        ajustes = int(len(part))
        percentual_ajuste = (ajustes / materiais_base) * 100.0
        acuracia = 100.0 - percentual_ajuste

        resultados.append({
            "competencia": competencia.isoformat(),
            "periodo_inicio": inicio.isoformat(),
            "periodo_fim": fim.isoformat(),
            "status_competencia": status,
            "valor": round(acuracia, 6),
            "percentual_ajuste": round(percentual_ajuste, 6),
            "meta": _meta_mensal(inicio),
            "materiais_s2_com_saldo": materiais_base,
            "ajustes_020_520": ajustes,
            "cabecalho_movimentacao_linha": int(header_mov) + 1 if header_mov is not None else None,
            "coluna_tesa": str(col_tesa),
            "coluna_armazem_movimentacao": str(col_armazem_mov),
            "armazem_movimentacao": "S2",
            "coluna_data_movimentacao": str(col_data or ""),
            "tesa_ajustes": sorted(AJUSTES_TESA),
            "logic_version": LOGIC_VERSION,
            "gerado_em": _agora_local().isoformat(),
            **base,
        })

    if not resultados:
        raise ValueError("A MOVIMENTAÇÃO não possui competência elegível para cálculo.")
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
                    "origem": "analitico_s2_mais_movimentacao_020_520",
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
            fontes = state.get("sources") or {}
            st.caption(
                " · ".join([
                    f"ANALÍTICO: {rotulo_fonte(fontes.get('analitico'))}",
                    f"MOVIMENTAÇÃO: {rotulo_fonte(fontes.get('movimentacao'))}",
                ])
            )
            if not state.get("ready"):
                st.warning("ANALÍTICO e MOVIMENTAÇÃO precisam estar disponíveis na Central SETTA.")
                return

            with st.spinner("Carregando ANALÍTICO + MOVIMENTAÇÃO e calculando a acurácia..."):
                bundle = carregar_acuracia_central()
        except Exception as exc:
            st.error(f"Não foi possível carregar as bases da acurácia: {exc}")
            return

        fingerprint = LOGIC_VERSION + "|" + str(bundle.get("fingerprint") or "")
        session_fp = st.session_state.get("acuracia_estoque_fingerprint")

        if session_fp != fingerprint:
            try:
                mov_meta = ((bundle.get("status") or {}).get("sources") or {}).get("movimentacao") or {}
                resultados = calcular_acuracia_estoque(
                    bundle["analitico"],
                    bundle["movimentacao"],
                    movimentacao_nome=str(mov_meta.get("last_file_name") or ""),
                )
                gravados = salvar_competencias_acuracia(resultados)
                st.session_state["acuracia_estoque_resultados"] = gravados
                st.session_state["acuracia_estoque_fingerprint"] = fingerprint
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(f"Não foi possível calcular/gravar a acurácia: {exc}")
                return

        gravados = st.session_state.get("acuracia_estoque_resultados") or []
        if not gravados:
            st.info("As bases estão vinculadas. Uma nova versão do ANALÍTICO ou da MOVIMENTAÇÃO disparará nova apuração.")
            return

        ultimo = sorted(gravados, key=lambda x: x.get("competencia") or "")[-1]
        a, b, c, d = st.columns(4)
        a.metric("ACURÁCIA", f"{float(ultimo['valor']):.2f}%")
        b.metric("% DE AJUSTE", f"{float(ultimo['percentual_ajuste']):.2f}%")
        c.metric("MATERIAIS S2", f"{int(ultimo['materiais_s2_com_saldo']):,}".replace(",", "."))
        d.metric("AJUSTES 020/520", f"{int(ultimo['ajustes_020_520']):,}".replace(",", "."))

        st.caption(
            f"Base: {int(ultimo['materiais_s2_com_saldo']):,} códigos distintos com saldo positivo no S2 · "
            f"Ajustes: {int(ultimo['ajustes_020_520']):,} movimentações TESA/TM 020 ou 520 também do armazém S2 · "
            f"Acurácia = 100% − ({int(ultimo['ajustes_020_520'])} ÷ {int(ultimo['materiais_s2_com_saldo'])} × 100)."
            .replace(",", ".")
        )
        st.caption(
            f"Competência {pd.to_datetime(ultimo['competencia']).strftime('%m/%Y')} · "
            f"{pd.to_datetime(ultimo['periodo_inicio']).strftime('%d/%m/%Y')} a "
            f"{pd.to_datetime(ultimo['periodo_fim']).strftime('%d/%m/%Y')} · "
            f"status {ultimo['status_competencia']} · {ultimo.get('escopo_s2') or ''}"
        )

        conferencia = pd.DataFrame([
            {
                "Competência": pd.to_datetime(r["competencia"]).strftime("%m/%Y"),
                "Status": r["status_competencia"],
                "Materiais S2 c/ saldo": r["materiais_s2_com_saldo"],
                "Ajustes 020/520": r["ajustes_020_520"],
                "% Ajuste": round(float(r["percentual_ajuste"]), 2),
                "Acurácia (%)": round(float(r["valor"]), 2),
                "Meta (%)": round(float(r["meta"]), 2),
                "Ação": r["acao"],
            }
            for r in gravados
        ])
        st.dataframe(conferencia, use_container_width=True, hide_index=True)

