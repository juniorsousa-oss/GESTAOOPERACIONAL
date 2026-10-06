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
LOGIC_VERSION = "2026-10-06-acuracia-tm-s2-usuario-auditoria-v9"
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
    # Layout oficial do ANALÍTICO:
    # A = CODIGO | G = ARMZ | H = SALDO EM ESTOQUE
    df = _read_source(source, header=1)
    if df.empty:
        raise ValueError("A base ANALÍTICO está vazia.")
    if df.shape[1] < 8:
        raise ValueError("ANALÍTICO sem as colunas mínimas esperadas até H.")

    col_codigo = df.columns[0]
    col_armazem = df.columns[6]
    col_saldo = df.columns[7]

    work = df.copy()
    work["_codigo"] = work[col_codigo].map(_normalizar_codigo)
    work["_armazem"] = work[col_armazem].fillna("").astype(str).str.strip().str.upper()
    work["_saldo"] = work[col_saldo].map(_num)
    work = work[
        work["_codigo"].ne("")
        & work["_armazem"].eq("S2")
    ].copy()

    por_codigo = work.groupby("_codigo", as_index=False)["_saldo"].sum()
    com_saldo = por_codigo[por_codigo["_saldo"] > 0].copy()

    descricao_por_codigo = (
        work.assign(_descricao=work[df.columns[3]].fillna("").astype(str).str.strip())
        .groupby("_codigo", as_index=False)["_descricao"]
        .first()
    )
    base_auditoria = (
        com_saldo.merge(descricao_por_codigo, on="_codigo", how="left")
        .rename(columns={"_codigo": "CODIGO", "_descricao": "DESCRICAO", "_saldo": "SALDO_S2"})
        [["CODIGO", "DESCRICAO", "SALDO_S2"]]
        .sort_values("CODIGO")
        .reset_index(drop=True)
    )

    return {
        "materiais_s2_com_saldo": int(com_saldo["_codigo"].nunique()),
        "linhas_analitico_s2": int(len(work)),
        "codigos_analitico_s2_distintos": int(por_codigo["_codigo"].nunique()),
        "coluna_codigo": str(col_codigo),
        "coluna_armazem": str(col_armazem),
        "coluna_saldo": str(col_saldo),
        "escopo_s2": "ANALÍTICO · ARMZ=S2 · saldo > 0 após consolidação por código",
        "base_s2_auditoria": base_auditoria.to_dict("records"),
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
        raise ValueError("Nenhum código distinto com saldo positivo foi encontrado no S2.")

    # Layout padrão da MOVIMENTAÇÃO validado:
    # C = TM | J = EMISSAO | K = USUARIO | L = ARMAZEM
    mov = _read_source(movimentacao_source, header=0)
    if mov.empty or mov.shape[1] < 12:
        raise ValueError(
            "A fonte MOVIMENTAÇÃO da Central não está no layout padrão esperado "
            "(C=TM, J=EMISSAO, K=USUARIO, L=ARMAZEM)."
        )

    col_tm = mov.columns[2]
    col_data = mov.columns[9]
    col_usuario = mov.columns[10]
    col_armazem = mov.columns[11]

    if _compact(col_tm) not in {"TM", "TESA", "TES"}:
        raise ValueError(
            f"Fonte MOVIMENTAÇÃO incompatível: coluna C deveria ser TM e veio '{col_tm}'."
        )
    if "EMISSAO" not in _compact(col_data) and "DATA" not in _compact(col_data):
        raise ValueError(
            f"Fonte MOVIMENTAÇÃO incompatível: coluna J deveria ser EMISSAO e veio '{col_data}'."
        )
    if "USUARIO" not in _compact(col_usuario):
        raise ValueError(
            f"Fonte MOVIMENTAÇÃO incompatível: coluna K deveria ser USUARIO e veio '{col_usuario}'."
        )
    if "ARMAZEM" not in _compact(col_armazem) and _compact(col_armazem) != "ARMZ":
        raise ValueError(
            f"Fonte MOVIMENTAÇÃO incompatível: coluna L deveria ser ARMAZEM e veio '{col_armazem}'."
        )

    mov_audit = mov.copy()
    mov_audit["_tm"] = mov_audit[col_tm].map(_normalizar_tesa)
    mov_audit["_data"] = _to_dates(mov_audit[col_data]).dt.normalize()
    mov_audit["_usuario"] = mov_audit[col_usuario].fillna("").astype(str).str.strip()
    mov_audit["_usuario_norm"] = mov_audit["_usuario"].str.upper()
    mov_audit["_armazem"] = mov_audit[col_armazem].fillna("").astype(str).str.strip().str.upper()

    candidatos = mov_audit[
        mov_audit["_tm"].isin(AJUSTES_TESA)
        & mov_audit["_armazem"].eq("S2")
        & mov_audit["_data"].notna()
    ].copy()

    api_excluidos = candidatos[candidatos["_usuario_norm"].eq("API")].copy()
    work = candidatos[
        candidatos["_usuario"].ne("")
        & candidatos["_usuario_norm"].ne("API")
    ].copy()

    if work.empty:
        raise ValueError(
            "Nenhuma movimentação válida encontrada: precisa ser TM 020/520, "
            "ARMAZEM S2 e USUARIO preenchido diferente de API."
        )

    work["_periodo"] = work["_data"].dt.to_period("M")
    api_excluidos["_periodo"] = api_excluidos["_data"].dt.to_period("M")
    resultados: list[dict] = []

    for periodo, part in work.groupby("_periodo", sort=True):
        inicio = date(int(periodo.year), int(periodo.month), 1)
        competencia = _ultimo_dia_mes(inicio)
        mes = (int(periodo.year), int(periodo.month))
        mes_atual = (hoje.year, hoje.month)
        if mes > mes_atual:
            continue

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

        ajustes = int(len(part))
        ajustes_020 = int(part["_tm"].eq("020").sum())
        ajustes_520 = int(part["_tm"].eq("520").sum())

        api_periodo = api_excluidos[api_excluidos["_periodo"].eq(periodo)].copy()
        if mes == mes_atual:
            api_periodo = api_periodo[api_periodo["_data"] <= pd.Timestamp(fim)].copy()

        percentual_ajuste = (ajustes / materiais_base) * 100.0
        acuracia = 100.0 - percentual_ajuste

        ajuste_auditoria = part[
            [c for c in mov.columns if c in part.columns]
        ].copy()
        ajuste_auditoria["REGRA_TM"] = part["_tm"].values
        ajuste_auditoria["REGRA_ARMAZEM"] = part["_armazem"].values
        ajuste_auditoria["REGRA_USUARIO"] = part["_usuario"].values
        ajuste_auditoria["CLASSIFICACAO_AUDITORIA"] = "AJUSTE VALIDO"

        api_auditoria = api_periodo[
            [c for c in mov.columns if c in api_periodo.columns]
        ].copy()
        api_auditoria["REGRA_TM"] = api_periodo["_tm"].values
        api_auditoria["REGRA_ARMAZEM"] = api_periodo["_armazem"].values
        api_auditoria["REGRA_USUARIO"] = api_periodo["_usuario"].values
        api_auditoria["CLASSIFICACAO_AUDITORIA"] = "EXCLUIDO · USUARIO API"

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
            "ajustes_020": ajustes_020,
            "ajustes_520": ajustes_520,
            "movimentacoes_api_excluidas": int(len(api_periodo)),
            "ajustes_validos_auditoria": ajuste_auditoria.to_dict("records"),
            "api_excluidos_auditoria": api_auditoria.to_dict("records"),
            "coluna_tm": str(col_tm),
            "coluna_data_movimentacao": str(col_data),
            "coluna_usuario_movimentacao": str(col_usuario),
            "coluna_armazem_movimentacao": str(col_armazem),
            "regra_usuario": "USUARIO preenchido e diferente de API",
            "armazem_movimentacao": "S2",
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

        resumo_persistencia = {
            k: v for k, v in resultado.items()
            if k not in {"base_s2_auditoria", "ajustes_validos_auditoria", "api_excluidos_auditoria"}
        }
        payload = {
            "competencia": competencia,
            "categoria": "OPERACIONAL",
            "indicador": INDICADOR,
            "valor": round(float(resultado["valor"]), 2),
            "meta": round(float(resultado["meta"]), 2),
            "unidade": "%",
            "observacao": json.dumps(
                {
                    "origem": "analitico_s2_mais_tm_020_520_s2_usuario_humano",
                    **resumo_persistencia,
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
                "dados": resumo_persistencia,
            }).execute()
        except Exception:
            pass

        saida.append({
            "competencia": competencia,
            "acao": acao,
            **resultado,
        })

    return saida


def _excel_safe_acuracia(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            try:
                out[col] = out[col].dt.tz_localize(None)
            except Exception:
                pass
    return out


@st.cache_data(show_spinner=False, max_entries=8)
def _excel_auditoria_acuracia(resultado: dict) -> bytes:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    output = BytesIO()

    resumo = [
        ["AUDITORIA · ACURÁCIA DE ESTOQUE", ""],
        ["Competência", pd.to_datetime(resultado["competencia"]).strftime("%m/%Y")],
        ["Status", resultado["status_competencia"]],
        ["Período", f"{pd.to_datetime(resultado['periodo_inicio']).strftime('%d/%m/%Y')} a {pd.to_datetime(resultado['periodo_fim']).strftime('%d/%m/%Y')}"],
        ["Versão da lógica", resultado["logic_version"]],
        ["", ""],
        ["Materiais distintos S2 com saldo > 0", resultado["materiais_s2_com_saldo"]],
        ["Ajustes válidos TM 020/520", resultado["ajustes_020_520"]],
        ["TM 020 válidos", resultado.get("ajustes_020", 0)],
        ["TM 520 válidos", resultado.get("ajustes_520", 0)],
        ["Movimentações API excluídas", resultado.get("movimentacoes_api_excluidas", 0)],
        ["% de ajuste", resultado["percentual_ajuste"]],
        ["Acurácia (%)", resultado["valor"]],
        ["Meta (%)", resultado["meta"]],
        ["", ""],
        ["Fórmula % ajuste", "Ajustes válidos / materiais distintos S2 com saldo positivo × 100"],
        ["Fórmula acurácia", "100% - % de ajuste"],
        ["Regra usuário", resultado.get("regra_usuario", "USUARIO diferente de API")],
        ["Regra estoque", resultado.get("escopo_s2", "")],
        ["Coluna TM", resultado.get("coluna_tm", "")],
        ["Coluna emissão", resultado.get("coluna_data_movimentacao", "")],
        ["Coluna usuário", resultado.get("coluna_usuario_movimentacao", "")],
        ["Coluna armazém", resultado.get("coluna_armazem_movimentacao", "")],
    ]

    metodologia = [
        ["ETAPA", "REGRA DE NEGÓCIO"],
        ["Base de estoque", "Usar o relatório ANALÍTICO e filtrar ARMZ = S2."],
        ["Material elegível", "Consolidar por código e considerar somente códigos distintos cuja soma do SALDO EM ESTOQUE seja maior que zero."],
        ["Movimentações de ajuste", "Usar somente TM 020 e TM 520."],
        ["Armazém da movimentação", "Somente ARMAZEM = S2."],
        ["Usuário", "Somente USUARIO preenchido e diferente de API. Movimentações com USUARIO = API são automáticas e ficam excluídas."],
        ["Período", "Competência mensal. Mês encerrado usa do dia 1 ao último dia; mês corrente usa até o último dia encerrado."],
        ["% de ajuste", "Quantidade de ajustes válidos / quantidade de materiais distintos com saldo positivo no S2 × 100."],
        ["Acurácia", "100% - % de ajuste."],
        ["Meta", "AGO/2026 = 95,50%; acréscimo contínuo de 0,50 ponto percentual por competência."],
    ]

    base_df = pd.DataFrame(resultado.get("base_s2_auditoria") or [])
    validos_df = pd.DataFrame(resultado.get("ajustes_validos_auditoria") or [])
    api_df = pd.DataFrame(resultado.get("api_excluidos_auditoria") or [])

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(resumo).to_excel(writer, sheet_name="RESUMO", index=False, header=False)
        _excel_safe_acuracia(base_df).to_excel(writer, sheet_name="BASE_S2", index=False)
        _excel_safe_acuracia(validos_df).to_excel(writer, sheet_name="AJUSTES_VALIDOS", index=False)
        _excel_safe_acuracia(api_df).to_excel(writer, sheet_name="API_EXCLUIDOS", index=False)
        pd.DataFrame(metodologia).to_excel(writer, sheet_name="METODOLOGIA", index=False, header=False)

        wb = writer.book
        dark = PatternFill("solid", fgColor="1F2937")
        yellow = PatternFill("solid", fgColor="FFD43D")
        white = Font(color="FFFFFF", bold=True)
        thin = Side(style="thin", color="D1D5DB")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for ws in wb.worksheets:
            ws.sheet_view.showGridLines = False
            ws.freeze_panes = "A2"
            if ws.title not in ("RESUMO", "METODOLOGIA") and ws.max_row >= 1:
                for cell in ws[1]:
                    cell.fill = dark
                    cell.font = white
                    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                    cell.border = border
                ws.auto_filter.ref = ws.dimensions
            for row in ws.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if ws.title not in ("RESUMO", "METODOLOGIA"):
                        cell.border = border
            for col in range(1, ws.max_column + 1):
                vals = [str(ws.cell(r, col).value or "") for r in range(1, min(ws.max_row, 200) + 1)]
                width = min(max(max((len(v) for v in vals), default=8) + 2, 10), 42)
                ws.column_dimensions[get_column_letter(col)].width = width

        ws = wb["RESUMO"]
        ws.merge_cells("A1:B1")
        ws["A1"].fill = yellow
        ws["A1"].font = Font(size=14, bold=True, color="111111")
        ws["A1"].alignment = Alignment(horizontal="center")
        ws.column_dimensions["A"].width = 48
        ws.column_dimensions["B"].width = 86
        for r in range(2, ws.max_row + 1):
            ws.cell(r, 1).font = Font(bold=True)
            ws.cell(r, 1).border = border
            ws.cell(r, 2).border = border

        ws = wb["METODOLOGIA"]
        for cell in ws[1]:
            cell.fill = dark
            cell.font = white
            cell.border = border
        for r in range(2, ws.max_row + 1):
            ws.cell(r, 1).font = Font(bold=True)
            ws.cell(r, 1).border = border
            ws.cell(r, 2).border = border
        ws.column_dimensions["A"].width = 34
        ws.column_dimensions["B"].width = 110

    return output.getvalue()


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
            f"Ajustes: {int(ultimo['ajustes_020_520']):,} movimentações TM 020/520 do S2 com USUARIO diferente de API · "
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

        excel_bytes = _excel_auditoria_acuracia(ultimo)
        st.download_button(
            "EXPORTAR AUDITORIA · EXCEL",
            excel_bytes,
            file_name=f"auditoria_acuracia_estoque_{ultimo['competencia']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="acuracia_excel",
        )
        st.caption(
            "A auditoria exporta a base S2 consolidada, os ajustes válidos, "
            "as movimentações API excluídas e a metodologia usada no cálculo."
        )

