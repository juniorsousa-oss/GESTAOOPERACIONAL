from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from supabase_client import get_client
from indicadores_historico import salvar_snapshot_otif
from indicadores_api import carregar_otif_central, rotulo_fonte, status_otif_central, source_frame

INDICADOR = "ENTREGAS NO PRAZO"
TZ_APP = ZoneInfo("America/Sao_Paulo")
LOGIC_VERSION = "2026-10-06-otif-almox-mensal-v7"
TOL = 1e-9


def _agora_local():
    return datetime.now(TZ_APP)


def _ultimo_dia_mes(data_ref: date) -> date:
    return date(data_ref.year, data_ref.month, monthrange(data_ref.year, data_ref.month)[1])


def _mes_anterior(data_ref: date) -> date:
    return date(data_ref.year, data_ref.month, 1) - timedelta(days=1)


def _status_registro_mensal(competencia: date) -> str | None:
    try:
        rows = (
            get_client().table("almox_indicadores")
            .select("observacao")
            .eq("indicador", INDICADOR)
            .eq("competencia", competencia.isoformat())
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return None
        obs = rows[0].get("observacao")
        if isinstance(obs, str):
            try:
                obs = json.loads(obs)
            except Exception:
                obs = {}
        return str((obs or {}).get("status_competencia") or "").upper() or None
    except Exception:
        return None


def _norm_code(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    s = re.sub(r"[^A-Za-z0-9]", "", s).upper()
    if s.isdigit():
        try:
            return str(int(s))
        except Exception:
            pass
    return s


def _norm_project(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return re.sub(r"\s+", "", s)


def _to_dates(series):
    try:
        return pd.to_datetime(series, dayfirst=True, errors="coerce", format="mixed")
    except TypeError:
        return pd.to_datetime(series, dayfirst=True, errors="coerce")


def _num(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "")
    if not s or s.lower() == "nan":
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def _num_series(series):
    return series.map(_num).clip(lower=0)


def _file_hash(uploaded):
    # Arquivos manuais chegam como UploadedFile/BytesIO; pela Central SETTA,
    # fontes brutas chegam em SETTA_SOURCE_V1 e o Relatório MRP como DataFrame.
    if isinstance(uploaded, pd.DataFrame):
        data = uploaded.to_json(
            orient="table",
            date_format="iso",
            force_ascii=False,
            index=False,
        ).encode("utf-8")
    elif isinstance(uploaded, dict) and str(uploaded.get("format") or "") == "SETTA_SOURCE_V1":
        data = json.dumps(
            uploaded,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    else:
        data = uploaded.getvalue()
    return hashlib.sha256(data).hexdigest(), len(data)


def _read_excel(uploaded, **kwargs):
    if isinstance(uploaded, dict) and str(uploaded.get("format") or "") == "SETTA_SOURCE_V1":
        return source_frame(uploaded, **kwargs)
    return pd.read_excel(BytesIO(uploaded.getvalue()), engine="openpyxl", **kwargs)


def _parse_action(action):
    text = "" if action is None or (isinstance(action, float) and pd.isna(action)) else str(action)
    out = {"Estoque": 0.0, "Pré Nota": 0.0, "P.C.": 0.0, "Fabricação": 0.0, "S.C.": 0.0}
    patterns = [
        ("Estoque", r"Estoque\s+([\d.,]+)"),
        ("Pré Nota", r"Pr[eé]\s*Nota\s+([\d.,]+)"),
        ("P.C.", r"P\.C\.\s+([\d.,]+)"),
        ("Fabricação", r"Fabrica[cç][aã]o\s+([\d.,]+)"),
        ("S.C.", r"S\.C\.\s+([\d.,]+)"),
    ]
    for label, pattern in patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            out[label] += _num(m.group(1))
    # Pré Nota é subconjunto do P.C.; nunca somar os dois.
    # MAX preserva a classificação quando a Ação detalha apenas um dos estágios,
    # sem inflar a quantidade de compra quando ambos aparecem.
    out["Compra"] = max(out["Pré Nota"], out["P.C."])
    out["Externo"] = out["Compra"] + out["Fabricação"] + out["S.C."]
    return out


def _fmt_date(value):
    ts = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(ts) else ts.strftime("%d/%m/%Y")


def _build_mrp(mrp_file):
    # A Central SETTA publica diretamente a Demanda_Projeto como JSON/GZIP.
    # O modo manual continua aceitando o Excel completo do MRP.
    if isinstance(mrp_file, pd.DataFrame):
        raw = mrp_file.copy()
        rename = {
            "Produto": "Código",
            "Semana de Atendimento": "Semana Atendimento",
            "Necessidade": "Necessidade MRP",
            "Estoque": "Estoque atual MRP",
            "Ação": "Ação MRP",
        }
        raw = raw.rename(columns=rename)
        expected = [
            "Projeto", "Código", "Semana Atendimento", "Necessidade MRP",
            "Estoque atual MRP", "Pré Nota", "P.C.", "Fabricação", "S.C.", "Ação MRP",
        ]
        missing = [col for col in expected if col not in raw.columns]
        if missing:
            raise ValueError(
                "Relatório MRP da Central não possui as colunas esperadas: " + ", ".join(missing)
            )
        raw = raw[expected].copy()
    else:
        # Terceira aba: Demanda_Projeto. A e B formam a chave Projeto_Código.
        raw = _read_excel(mrp_file, sheet_name=2, usecols="A,B,G,H,I,J,K,L,M,N", dtype=str)
        raw.columns = [
            "Projeto", "Código", "Semana Atendimento", "Necessidade MRP", "Estoque atual MRP",
            "Pré Nota", "P.C.", "Fabricação", "S.C.", "Ação MRP",
        ]
    raw["Projeto"] = raw["Projeto"].map(_norm_project)
    raw["Código normalizado"] = raw["Código"].map(_norm_code)
    raw["Projeto_Código"] = raw["Projeto"] + "_" + raw["Código normalizado"]
    for col in ["Necessidade MRP", "Estoque atual MRP", "Pré Nota", "P.C.", "Fabricação", "S.C."]:
        raw[col] = _num_series(raw[col])

    parsed = raw["Ação MRP"].map(_parse_action)
    raw["Ação Estoque"] = parsed.map(lambda x: x["Estoque"])
    raw["Ação Pré Nota"] = parsed.map(lambda x: x["Pré Nota"])
    raw["Ação P.C."] = parsed.map(lambda x: x["P.C."])
    raw["Ação Compra"] = parsed.map(lambda x: x["Compra"])
    raw["Ação Fabricação"] = parsed.map(lambda x: x["Fabricação"])
    raw["Ação S.C."] = parsed.map(lambda x: x["S.C."])
    raw["Ação Externa"] = parsed.map(lambda x: x["Externo"])

    def join_unique(s):
        vals = []
        for v in s:
            txt = "" if pd.isna(v) else str(v).strip()
            if txt and txt not in vals:
                vals.append(txt)
        return " | ".join(vals)

    mrp = raw.groupby("Projeto_Código", as_index=False).agg({
        "Projeto": "first",
        "Código normalizado": "first",
        "Semana Atendimento": join_unique,
        "Necessidade MRP": "sum",
        "Estoque atual MRP": "max",
        "Pré Nota": "sum",
        "P.C.": "sum",
        "Fabricação": "sum",
        "S.C.": "sum",
        "Ação MRP": join_unique,
        "Ação Estoque": "sum",
        "Ação Pré Nota": "sum",
        "Ação P.C.": "sum",
        "Ação Compra": "sum",
        "Ação Fabricação": "sum",
        "Ação S.C.": "sum",
        "Ação Externa": "sum",
    })
    return raw, mrp


def _allocate_external_to_requests(solicitacoes, mrp_lookup):
    """Distribui apenas a parcela pendente classificada como externa no MRP.

    O atendimento é naturalmente FIFO: o que tende a permanecer pendente é a demanda mais nova.
    Por isso, a alocação de causa externa percorre solicitações pendentes da mais nova para a mais antiga.
    Solicitações já inelegíveis por data absorvem a causa externa primeiro, evitando dupla exclusão no On Time Almox.
    """
    df = solicitacoes.copy()
    for col in ["Qtd Excluída Compra", "Qtd Excluída Fabricação", "Qtd Excluída S.C."]:
        df[col] = 0.0

    for key, idxs in df.groupby("Projeto_Código").groups.items():
        if key not in mrp_lookup.index:
            continue
        mr = mrp_lookup.loc[key]
        pools = {
            "Qtd Excluída Compra": max(_num(mr.get("Ação Compra", 0)), 0.0),
            "Qtd Excluída Fabricação": max(_num(mr.get("Ação Fabricação", 0)), 0.0),
            "Qtd Excluída S.C.": max(_num(mr.get("Ação S.C.", 0)), 0.0),
        }
        total_pool = sum(pools.values())
        if total_pool <= TOL:
            continue

        part = df.loc[list(idxs)].copy()
        part = part[~part["Tipo II"] & (part["Qtd Pendente Atual"] > TOL)]
        if part.empty:
            continue
        part["_prioridade"] = part["Elegível Inicial OnTime Almox"].map({False: 0, True: 1})
        # Dentro de cada faixa, a pendência mais nova é tratada primeiro.
        part = part.sort_values(["_prioridade", "Data Solicitação"], ascending=[True, False], na_position="first")

        for ridx, row in part.iterrows():
            falta = float(row["Qtd Pendente Atual"])
            if falta <= TOL:
                continue
            pool_total = sum(pools.values())
            if pool_total <= TOL:
                break
            alocar = min(falta, pool_total)
            # Preserva o mix de causas do Ação ao repartir uma parcela menor que o pool.
            snapshot = pools.copy()
            snap_total = sum(snapshot.values())
            remaining = alocar
            keys = list(snapshot)
            for pos, cat in enumerate(keys):
                if pos == len(keys) - 1:
                    q = remaining
                else:
                    q = alocar * snapshot[cat] / snap_total if snap_total > TOL else 0.0
                    q = min(q, remaining)
                q = min(q, pools[cat])
                df.at[ridx, cat] += q
                pools[cat] -= q
                remaining -= q
            if remaining > 1e-6:
                # Ajuste residual por arredondamento.
                for cat in keys:
                    if pools[cat] > TOL:
                        q = min(remaining, pools[cat])
                        df.at[ridx, cat] += q
                        pools[cat] -= q
                        remaining -= q
                        if remaining <= TOL:
                            break

    df["Qtd Excluída Causa Externa"] = (
        df["Qtd Excluída Compra"] + df["Qtd Excluída Fabricação"] + df["Qtd Excluída S.C."]
    )
    return df


def calcular_entregas_v2(
    relatorio_file,
    for022_file,
    cadastro_file,
    mrp_file,
    data_registro: date | None = None,
    competencia_ref: date | None = None,
):
    data_registro = data_registro or _agora_local().date()
    competencia_ref = competencia_ref or data_registro
    periodo_inicio = date(competencia_ref.year, competencia_ref.month, 1)
    competencia = _ultimo_dia_mes(competencia_ref)

    mes_ref = (competencia_ref.year, competencia_ref.month)
    mes_atual = (data_registro.year, data_registro.month)
    if mes_ref > mes_atual:
        raise ValueError("Não é possível calcular uma competência futura.")
    if mes_ref < mes_atual:
        periodo_fim = competencia
        status_competencia = "FECHADO"
    else:
        periodo_fim = data_registro if data_registro == competencia else data_registro - timedelta(days=1)
        status_competencia = "FECHADO" if periodo_fim == competencia else "PARCIAL"

    if periodo_fim < periodo_inicio:
        raise ValueError("Ainda não há dia encerrado para calcular a competência atual.")
    inicio_ts = pd.Timestamp(periodo_inicio)
    fim_ts = pd.Timestamp(periodo_fim)

    # 1) DATA CM — FOR-022, primeira aba. Se a OP repetir, usa a MAIOR data dentro do período analisado.
    cron = _read_excel(for022_file, sheet_name=0, usecols="A,V", dtype=str)
    cron.columns = ["Projeto", "Data CM"]
    cron["Projeto"] = cron["Projeto"].map(_norm_project)
    cron["Data CM"] = _to_dates(cron["Data CM"]).dt.normalize()
    cron_periodo = cron[
        cron["Projeto"].ne("") & cron["Data CM"].between(inicio_ts, fim_ts, inclusive="both")
    ].copy()
    datas_projeto = (
        cron_periodo.groupby("Projeto", as_index=False)["Data CM"].max()
        .sort_values(["Data CM", "Projeto"])
    )
    if datas_projeto.empty:
        raise ValueError(
            f"Nenhuma OP com Data CM entre {periodo_inicio.strftime('%d/%m/%Y')} e {periodo_fim.strftime('%d/%m/%Y')} na FOR-022."
        )
    cm_map = datas_projeto.set_index("Projeto")["Data CM"].to_dict()

    # 2) CADASTRO — cabeçalho na linha 2; Tipo II é totalmente desconsiderado dos indicadores.
    cad = _read_excel(cadastro_file, sheet_name=0, header=1, usecols="B,D", dtype=str)
    cad.columns = ["Código", "Tipo"]
    cad["Código normalizado"] = cad["Código"].map(_norm_code)
    cad["Tipo"] = cad["Tipo"].fillna("").astype(str).str.strip().str.upper()
    cad = cad[cad["Código normalizado"].ne("")].drop_duplicates("Código normalizado", keep="last")
    tipo_map = cad.set_index("Código normalizado")["Tipo"].to_dict()

    # 3) MRP — terceira aba, usada para causa raiz da pendência. Pré Nota e P.C. são a mesma família: COMPRA.
    mrp_raw, mrp = _build_mrp(mrp_file)
    mrp_lookup = mrp.set_index("Projeto_Código")

    # 4) RELATÓRIO GERAL — fonte mestre. Preserva todas as colunas para a auditoria.
    rel_raw = _read_excel(relatorio_file, sheet_name="Geral")
    if rel_raw.shape[1] < 16:
        raise ValueError("O Relatório Geral não possui as colunas esperadas até P.")
    rel = pd.DataFrame({
        "Projeto": rel_raw.iloc[:, 0],
        "Código": rel_raw.iloc[:, 3],
        "Data Solicitação": rel_raw.iloc[:, 6],
        "Qtd Necessária": rel_raw.iloc[:, 8],
        "Qtd Atendida": rel_raw.iloc[:, 9],
        "Data Separação": rel_raw.iloc[:, 13],
        "Data Conferência": rel_raw.iloc[:, 15],
    })
    rel.insert(0, "Linha Relatório Geral", range(2, len(rel) + 2))
    rel["Projeto"] = rel["Projeto"].map(_norm_project)
    rel["Código normalizado"] = rel["Código"].map(_norm_code)
    rel["Projeto_Código"] = rel["Projeto"] + "_" + rel["Código normalizado"]
    rel["Qtd Necessária"] = _num_series(rel["Qtd Necessária"])
    rel["Qtd Atendida"] = _num_series(rel["Qtd Atendida"])
    rel["Qtd Atendida Efetiva"] = rel[["Qtd Necessária", "Qtd Atendida"]].min(axis=1)
    rel["Data Solicitação"] = _to_dates(rel["Data Solicitação"]).dt.normalize()
    rel["Data Separação"] = _to_dates(rel["Data Separação"]).dt.normalize()
    rel["Data Conferência"] = _to_dates(rel["Data Conferência"]).dt.normalize()
    rel["Data CM"] = rel["Projeto"].map(cm_map)
    rel["Tipo"] = rel["Código normalizado"].map(tipo_map).fillna("")
    rel["Tipo II"] = rel["Tipo"].eq("II")

    # Macro: todas as linhas ligadas às OPs cuja maior Data CM válida está dentro do período.
    macro = rel[rel["Data CM"].notna()].copy()
    if macro.empty:
        raise ValueError("Nenhuma solicitação do Relatório Geral foi vinculada às OPs programadas no período.")

    # Anexa diagnóstico atual do MRP à linha original sem usá-lo como fonte mestre.
    mrp_cols = [
        "Projeto_Código", "Necessidade MRP", "Estoque atual MRP", "Pré Nota", "P.C.", "Fabricação", "S.C.",
        "Ação MRP", "Ação Estoque", "Ação Compra", "Ação Fabricação", "Ação S.C.", "Ação Externa",
    ]
    macro = macro.merge(mrp[mrp_cols], on="Projeto_Código", how="left", validate="many_to_one")
    for c in ["Necessidade MRP", "Estoque atual MRP", "Pré Nota", "P.C.", "Fabricação", "S.C.",
              "Ação Estoque", "Ação Compra", "Ação Fabricação", "Ação S.C.", "Ação Externa"]:
        macro[c] = pd.to_numeric(macro[c], errors="coerce").fillna(0.0)
    macro["Ação MRP"] = macro["Ação MRP"].fillna("")

    # Solicitação: mesmo Projeto + Código + Data de Solicitação é uma única solicitação; quantidades são somadas.
    date_key = macro["Data Solicitação"].dt.strftime("%Y-%m-%d").fillna("")
    macro["Grupo Solicitação"] = macro["Projeto_Código"] + "_" + date_key
    sem_data = macro["Data Solicitação"].isna()
    macro.loc[sem_data, "Grupo Solicitação"] = (
        macro.loc[sem_data, "Projeto_Código"] + "_SEM_DATA_LINHA_" + macro.loc[sem_data, "Linha Relatório Geral"].astype(str)
    )

    macro["Solicitada antes da CM"] = macro["Data Solicitação"].notna() & (macro["Data Solicitação"] < macro["Data CM"])
    macro["Separada no prazo"] = macro["Data Separação"].notna() & (macro["Data Separação"] <= macro["Data CM"])
    macro["Conferida no prazo"] = macro["Data Conferência"].notna() & (macro["Data Conferência"] <= macro["Data CM"])
    macro["Qtd OnTime Separação Linha"] = macro["Qtd Atendida Efetiva"].where(
        (~macro["Tipo II"]) & macro["Solicitada antes da CM"] & macro["Separada no prazo"], 0.0
    )
    macro["Qtd OnTime Conferência Linha"] = macro["Qtd Atendida Efetiva"].where(
        (~macro["Tipo II"]) & macro["Solicitada antes da CM"] & macro["Conferida no prazo"], 0.0
    )

    def join_lines(s):
        return ", ".join(str(int(x)) for x in s if pd.notna(x))

    solicit = macro.groupby("Grupo Solicitação", as_index=False).agg({
        "Projeto": "first",
        "Código normalizado": "first",
        "Projeto_Código": "first",
        "Data Solicitação": "first",
        "Data CM": "first",
        "Tipo": "first",
        "Tipo II": "first",
        "Qtd Necessária": "sum",
        "Qtd Atendida Efetiva": "sum",
        "Qtd OnTime Separação Linha": "sum",
        "Qtd OnTime Conferência Linha": "sum",
        "Data Separação": ["min", "max"],
        "Data Conferência": ["min", "max"],
        "Linha Relatório Geral": join_lines,
        "Necessidade MRP": "first",
        "Estoque atual MRP": "first",
        "Ação MRP": "first",
        "Ação Estoque": "first",
        "Ação Compra": "first",
        "Ação Fabricação": "first",
        "Ação S.C.": "first",
        "Ação Externa": "first",
    })
    solicit.columns = [
        "Grupo Solicitação", "Projeto", "Código", "Projeto_Código", "Data Solicitação", "Data CM", "Tipo", "Tipo II",
        "Qtd Necessária", "Qtd Atendida", "Qtd OnTime Separação", "Qtd OnTime Conferência",
        "Separação mais cedo", "Separação mais tarde", "Conferência mais cedo", "Conferência mais tarde",
        "Linhas Origem", "Necessidade MRP", "Estoque atual MRP", "Ação MRP", "Ação Estoque", "Ação Compra",
        "Ação Fabricação", "Ação S.C.", "Ação Externa",
    ]
    solicit["Qtd Atendida"] = solicit[["Qtd Necessária", "Qtd Atendida"]].min(axis=1)
    solicit["Qtd OnTime Separação"] = solicit[["Qtd Necessária", "Qtd OnTime Separação"]].min(axis=1)
    solicit["Qtd OnTime Conferência"] = solicit[["Qtd Necessária", "Qtd OnTime Conferência"]].min(axis=1)
    solicit["Qtd Pendente Atual"] = (solicit["Qtd Necessária"] - solicit["Qtd Atendida"]).clip(lower=0)
    solicit["Solicitada antes da CM"] = solicit["Data Solicitação"].notna() & (solicit["Data Solicitação"] < solicit["Data CM"])
    solicit["Elegível Inicial OnTime Almox"] = (~solicit["Tipo II"]) & solicit["Solicitada antes da CM"]

    # Causa externa atual (compra = Pré Nota + P.C.; além de Fabricação e S.C.) é aplicada apenas à pendência.
    solicit = _allocate_external_to_requests(solicit, mrp_lookup)
    solicit["Qtd Elegível OnTime Almox"] = (
        solicit["Qtd Necessária"].where(solicit["Elegível Inicial OnTime Almox"], 0.0)
        - solicit["Qtd Excluída Causa Externa"].where(solicit["Elegível Inicial OnTime Almox"], 0.0)
    ).clip(lower=0)
    solicit["Qtd OnTime Almox"] = solicit[["Qtd OnTime Separação", "Qtd Elegível OnTime Almox"]].min(axis=1)
    solicit["Qtd OnTime Almox Simulado Entrega"] = solicit[["Qtd OnTime Conferência", "Qtd Elegível OnTime Almox"]].min(axis=1)

    # Global: somente Tipo II sai totalmente. Solicitação tardia e causa externa continuam explicando perdas do processo.
    solicit["Qtd Base Global"] = solicit["Qtd Necessária"].where(~solicit["Tipo II"], 0.0)
    solicit["Qtd OnTime Global"] = solicit["Qtd OnTime Separação"].where(~solicit["Tipo II"], 0.0)
    solicit["Qtd OnTime Global Simulado Entrega"] = solicit["Qtd OnTime Conferência"].where(~solicit["Tipo II"], 0.0)

    def status_row(r):
        if r["Tipo II"]:
            return "FORA DO INDICADOR · TIPO II"
        if pd.isna(r["Data Solicitação"]):
            return "FORA DO ON TIME ALMOX · SEM DATA DE SOLICITAÇÃO"
        if not r["Solicitada antes da CM"]:
            return "FORA DO ON TIME ALMOX · SOLICITAÇÃO NO DIA/APÓS CM"
        ext = r["Qtd Excluída Causa Externa"]
        eleg = r["Qtd Elegível OnTime Almox"]
        if ext > TOL and eleg <= TOL:
            return "FORA DO ON TIME ALMOX · CAUSA EXTERNA"
        if r["Qtd OnTime Almox"] + TOL >= eleg:
            return "ON TIME ALMOX"
        if r["Qtd Atendida"] > r["Qtd OnTime Separação"] + TOL:
            return "PERDA ALMOX · SEPARAÇÃO APÓS CM"
        if r["Qtd Pendente Atual"] > TOL and r["Ação Estoque"] > TOL:
            return "PERDA ALMOX · PENDENTE COM ESTOQUE"
        if r["Qtd Pendente Atual"] > TOL:
            return "PERDA ALMOX · PENDENTE SEM CAUSA EXTERNA"
        return "PERDA ALMOX · NÃO ATENDIDA NO PRAZO"

    solicit["Classificação OnTime"] = solicit.apply(status_row, axis=1)

    # IN FULL — independentemente da data. Tipo II sai de tudo. Causas externas pendentes saem apenas do In Full Almox.
    proj_rows = []
    for projeto, g in solicit.groupby("Projeto"):
        non_ii = g[~g["Tipo II"]]
        global_req = float(non_ii["Qtd Necessária"].sum())
        global_att = float(non_ii["Qtd Atendida"].sum())
        global_pend = max(global_req - global_att, 0.0)
        ext = float(non_ii["Qtd Excluída Causa Externa"].sum())
        almox_req = max(global_req - ext, 0.0)
        almox_att = min(global_att, almox_req)
        almox_pend = max(almox_req - almox_att, 0.0)
        proj_rows.append({
            "Projeto": projeto,
            "Data CM": g["Data CM"].max(),
            "Qtd Global": global_req,
            "Qtd Atendida Global": global_att,
            "Qtd Pendente Global": global_pend,
            "Completo Global": "SIM" if global_req > TOL and global_pend <= TOL else "NÃO",
            "Qtd Excluída Externa In Full Almox": ext,
            "Qtd Elegível In Full Almox": almox_req,
            "Qtd Atendida In Full Almox": almox_att,
            "Qtd Pendente In Full Almox": almox_pend,
            "Projeto Elegível In Full Almox": "SIM" if almox_req > TOL else "NÃO",
            "Completo Almox": "SIM" if almox_req > TOL and almox_pend <= TOL else "NÃO",
        })
    projetos = pd.DataFrame(proj_rows)

    # Métricas principais.
    global_base = float(solicit["Qtd Base Global"].sum())
    global_ontime = float(solicit["Qtd OnTime Global"].sum())
    global_ontime_sim = float(solicit["Qtd OnTime Global Simulado Entrega"].sum())
    almox_base = float(solicit["Qtd Elegível OnTime Almox"].sum())
    almox_ontime = float(solicit["Qtd OnTime Almox"].sum())
    almox_ontime_sim = float(solicit["Qtd OnTime Almox Simulado Entrega"].sum())

    ontime_global_pct = global_ontime / global_base * 100.0 if global_base > TOL else 0.0
    ontime_global_sim_pct = global_ontime_sim / global_base * 100.0 if global_base > TOL else 0.0
    ontime_almox_pct = almox_ontime / almox_base * 100.0 if almox_base > TOL else 0.0
    ontime_almox_sim_pct = almox_ontime_sim / almox_base * 100.0 if almox_base > TOL else 0.0

    glob_proj = projetos[projetos["Qtd Global"] > TOL]
    alm_proj = projetos[projetos["Projeto Elegível In Full Almox"].eq("SIM")]
    infull_global_pct = (glob_proj["Completo Global"].eq("SIM").mean() * 100.0) if not glob_proj.empty else 0.0
    infull_almox_pct = (alm_proj["Completo Almox"].eq("SIM").mean() * 100.0) if not alm_proj.empty else 0.0
    otif_global_pct = ontime_global_pct * infull_global_pct / 100.0
    otif_almox_pct = ontime_almox_pct * infull_almox_pct / 100.0

    # Funil e causas — quantidades, sem esconder o macro.
    macro_qtd = float(macro["Qtd Necessária"].sum())
    tipo_ii_qtd = float(solicit.loc[solicit["Tipo II"], "Qtd Necessária"].sum())
    late_qtd = float(solicit.loc[(~solicit["Tipo II"]) & (~solicit["Solicitada antes da CM"]), "Qtd Necessária"].sum())
    ext_early = float(solicit.loc[solicit["Elegível Inicial OnTime Almox"], "Qtd Excluída Causa Externa"].sum())
    causas = pd.DataFrame([
        ["Macro do período", macro_qtd],
        ["Fora do indicador · Tipo II", tipo_ii_qtd],
        ["Global · base após Tipo II", global_base],
        ["Fora On Time Almox · solicitação no dia/após CM ou sem data", late_qtd],
        ["Fora On Time Almox · Compra (P.C.; Pré Nota já contida)", float(solicit.loc[solicit["Elegível Inicial OnTime Almox"], "Qtd Excluída Compra"].sum())],
        ["Fora On Time Almox · Fabricação", float(solicit.loc[solicit["Elegível Inicial OnTime Almox"], "Qtd Excluída Fabricação"].sum())],
        ["Fora On Time Almox · S.C.", float(solicit.loc[solicit["Elegível Inicial OnTime Almox"], "Qtd Excluída S.C."].sum())],
        ["Base elegível On Time Almox", almox_base],
        ["Atendida no prazo Almox", almox_ontime],
        ["Perda On Time Almox", max(almox_base - almox_ontime, 0.0)],
    ], columns=["Etapa / Causa", "Quantidade"])

    # Material — visão consolidada para confrontar Relatório Geral x MRP.
    materiais = solicit.groupby(["Projeto", "Código", "Projeto_Código"], as_index=False).agg({
        "Qtd Necessária": "sum", "Qtd Atendida": "sum", "Qtd Pendente Atual": "sum",
        "Qtd Excluída Compra": "sum", "Qtd Excluída Fabricação": "sum", "Qtd Excluída S.C.": "sum",
        "Qtd Elegível OnTime Almox": "sum", "Qtd OnTime Almox": "sum",
        "Necessidade MRP": "first", "Estoque atual MRP": "first", "Ação MRP": "first",
        "Ação Estoque": "first", "Ação Compra": "first", "Ação Fabricação": "first", "Ação S.C.": "first",
    })
    materiais["Diferença Pendência Relatório x Necessidade MRP"] = materiais["Qtd Pendente Atual"] - materiais["Necessidade MRP"]

    # Volta a classificação consolidada para cada linha original da auditoria.
    append_cols = [
        "Grupo Solicitação", "Qtd Excluída Compra", "Qtd Excluída Fabricação", "Qtd Excluída S.C.",
        "Qtd Excluída Causa Externa", "Qtd Elegível OnTime Almox", "Qtd OnTime Almox",
        "Qtd OnTime Almox Simulado Entrega", "Classificação OnTime",
    ]
    macro = macro.merge(solicit[append_cols], on="Grupo Solicitação", how="left", validate="many_to_one")
    macro["AUD_Camada 1 · Período"] = "DENTRO DO PERÍODO"
    macro["AUD_Camada 2 · Tipo"] = macro["Tipo II"].map({True: "EXCLUÍDO · TIPO II", False: "MATERIAL ENTREGÁVEL"})
    macro["AUD_Camada 3 · Solicitação"] = macro["Solicitada antes da CM"].map({True: "ANTES DA CM", False: "NO DIA/APÓS CM OU SEM DATA"})
    macro["AUD_Camada 4 · Causa atual"] = macro["Ação MRP"].replace("", "SEM PENDÊNCIA/SEM REGISTRO NO MRP")
    macro["AUD_Camada 5 · Elegibilidade On Time Almox"] = macro["Qtd Elegível OnTime Almox"].gt(TOL).map({True: "ELEGÍVEL", False: "NÃO ELEGÍVEL"})
    macro["AUD_Camada 6 · Resultado"] = macro["Classificação OnTime"]

    # Preserva as colunas originais do Relatório Geral para a aba FUNIL_GERAL.
    audit_idx = macro["Linha Relatório Geral"].astype(int) - 2
    funil = rel_raw.iloc[audit_idx].copy().reset_index(drop=True)
    funil.insert(0, "Linha Relatório Geral", macro["Linha Relatório Geral"].reset_index(drop=True))
    audit_extra = macro[[
        "Projeto_Código", "Grupo Solicitação", "Data CM", "Tipo", "Tipo II", "Solicitada antes da CM",
        "Separada no prazo", "Conferida no prazo", "Necessidade MRP", "Estoque atual MRP", "Ação MRP",
        "Qtd Excluída Compra", "Qtd Excluída Fabricação", "Qtd Excluída S.C.", "Qtd Excluída Causa Externa",
        "Qtd Elegível OnTime Almox", "Qtd OnTime Almox", "Qtd OnTime Almox Simulado Entrega",
        "AUD_Camada 1 · Período", "AUD_Camada 2 · Tipo", "AUD_Camada 3 · Solicitação",
        "AUD_Camada 4 · Causa atual", "AUD_Camada 5 · Elegibilidade On Time Almox", "AUD_Camada 6 · Resultado",
    ]].reset_index(drop=True)
    funil = pd.concat([funil, audit_extra], axis=1)

    hashes = {}
    for key, f in [("relatorio", relatorio_file), ("for022", for022_file), ("cadastro", cadastro_file), ("mrp", mrp_file)]:
        h, size = _file_hash(f)
        hashes[f"{key}_nome"] = getattr(f, "name", key)
        hashes[f"{key}_sha256"] = h
        hashes[f"{key}_tamanho"] = size

    return {
        "data_registro": data_registro.isoformat(),
        "competencia": competencia.isoformat(),
        "status_competencia": status_competencia,
        "periodo_inicio": periodo_inicio.isoformat(),
        "periodo_fim": periodo_fim.isoformat(),
        "gerado_em": _agora_local().isoformat(),
        "logic_version": LOGIC_VERSION,
        "projetos_programados": int(datas_projeto["Projeto"].nunique()),
        "linhas_macro": int(len(macro)),
        "solicitacoes_consolidadas": int(len(solicit)),
        "macro_qtd": macro_qtd,
        "tipo_ii_qtd": tipo_ii_qtd,
        "global_base_qtd": global_base,
        "almox_base_ontime_qtd": almox_base,
        "almox_ontime_qtd": almox_ontime,
        "global_ontime_qtd": global_ontime,
        "ontime_almox_pct": ontime_almox_pct,
        "ontime_almox_sim_pct": ontime_almox_sim_pct,
        "infull_almox_pct": infull_almox_pct,
        "otif_almox_pct": otif_almox_pct,
        "ontime_global_pct": ontime_global_pct,
        "ontime_global_sim_pct": ontime_global_sim_pct,
        "infull_global_pct": infull_global_pct,
        "otif_global_pct": otif_global_pct,
        "projetos_infull_almox": int(len(alm_proj)),
        "projetos_completos_almox": int(alm_proj["Completo Almox"].eq("SIM").sum()) if not alm_proj.empty else 0,
        "projetos_infull_global": int(len(glob_proj)),
        "projetos_completos_global": int(glob_proj["Completo Global"].eq("SIM").sum()) if not glob_proj.empty else 0,
        "qtd_solicitacao_tardia": late_qtd,
        "qtd_externa_early": ext_early,
        "qtd_compra_early": float(solicit.loc[solicit["Elegível Inicial OnTime Almox"], "Qtd Excluída Compra"].sum()),
        "qtd_fabricacao_early": float(solicit.loc[solicit["Elegível Inicial OnTime Almox"], "Qtd Excluída Fabricação"].sum()),
        "qtd_sc_early": float(solicit.loc[solicit["Elegível Inicial OnTime Almox"], "Qtd Excluída S.C."].sum()),
        "funil_geral": funil.to_dict("records"),
        "solicitacoes": solicit.to_dict("records"),
        "projetos": projetos.to_dict("records"),
        "materiais_mrp": materiais.to_dict("records"),
        "causas": causas.to_dict("records"),
        "datas_projeto": datas_projeto.to_dict("records"),
        **hashes,
    }


def _meta_mensal(data_ref: date) -> float:
    """Meta mensal oficial do OTIF Almox. Janeiro = 25,60% e +2,60 p.p. por mês."""
    return round(25.60 + (int(data_ref.month) - 1) * 2.60, 2)

def _excel_safe(df):
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.tz_localize(None) if getattr(out[c].dt, "tz", None) is not None else out[c]
    return out


@st.cache_data(show_spinner=False, max_entries=6)
def _excel_auditoria(resultado, meta):
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    output = BytesIO()
    resumo = [
        ["AUDITORIA · OTIF ALMOXARIFADO", ""],
        ["Data de registro", _fmt_date(resultado["data_registro"])],
        ["Período", f"{_fmt_date(resultado['periodo_inicio'])} a {_fmt_date(resultado['periodo_fim'])}"],
        ["Versão da lógica", resultado["logic_version"]],
        ["", ""],
        ["ON TIME ALMOX (%)", resultado["ontime_almox_pct"]],
        ["IN FULL ALMOX (%)", resultado["infull_almox_pct"]],
        ["OTIF ALMOX (%)", resultado["otif_almox_pct"]],
        ["Meta (%)", meta],
        ["Base elegível On Time Almox (qtd)", resultado["almox_base_ontime_qtd"]],
        ["Atendida no prazo Almox (qtd)", resultado["almox_ontime_qtd"]],
        ["Projetos completos Almox", f"{resultado['projetos_completos_almox']} / {resultado['projetos_infull_almox']}"],
        ["", ""],
        ["VISÃO GLOBAL · ON TIME (%)", resultado["ontime_global_pct"]],
        ["VISÃO GLOBAL · IN FULL (%)", resultado["infull_global_pct"]],
        ["VISÃO GLOBAL · OTIF (%)", resultado["otif_global_pct"]],
        ["On Time simulado pela conferência · Almox (%)", resultado["ontime_almox_sim_pct"]],
        ["On Time simulado pela conferência · Global (%)", resultado["ontime_global_sim_pct"]],
        ["", ""],
        ["Projetos programados", resultado["projetos_programados"]],
        ["Linhas macro", resultado["linhas_macro"]],
        ["Solicitações consolidadas", resultado["solicitacoes_consolidadas"]],
        ["Quantidade macro", resultado["macro_qtd"]],
        ["Quantidade Tipo II excluída", resultado["tipo_ii_qtd"]],
        ["Quantidade solicitação tardia", resultado["qtd_solicitacao_tardia"]],
        ["Quantidade compra excluída do Almox", resultado["qtd_compra_early"]],
        ["Quantidade fabricação excluída do Almox", resultado["qtd_fabricacao_early"]],
        ["Quantidade S.C. excluída do Almox", resultado["qtd_sc_early"]],
        ["", ""],
        ["Relatório Geral", resultado["relatorio_nome"]],
        ["SHA-256 Relatório Geral", resultado["relatorio_sha256"]],
        ["FOR-022", resultado["for022_nome"]],
        ["SHA-256 FOR-022", resultado["for022_sha256"]],
        ["Cadastro", resultado["cadastro_nome"]],
        ["SHA-256 Cadastro", resultado["cadastro_sha256"]],
        ["MRP", resultado["mrp_nome"]],
        ["SHA-256 MRP", resultado["mrp_sha256"]],
    ]
    metodologia = [
        ["ETAPA", "REGRA DE NEGÓCIO"],
        ["Período", "Competência mensal. Mês encerrado usa do dia 1 ao último dia; mês atual usa do dia 1 ao último dia encerrado e fecha definitivamente no último dia do mês."],
        ["Data CM", "FOR-022, aba Datas esperadas: OP em A e Separação em V. Se a OP repetir, usa a maior data que esteja dentro do período analisado."],
        ["Fonte mestre", "Relatório Geral. A auditoria preserva as linhas originais e adiciona as camadas de tratamento."],
        ["Consolidação", "Mesmo Projeto + Código + Data de Solicitação representa uma solicitação; as quantidades das linhas são somadas."],
        ["Tipo II", "Excluído totalmente de On Time e In Full, tanto Global quanto Almox."],
        ["Solicitação tardia", "Solicitada no dia da Data CM ou depois: permanece na visão Global, mas sai do On Time Almox."],
        ["On Time oficial", "Usa Data de Separação, comparando somente a data, sem hora."],
        ["On Time simulado", "Usa Data de Conferência apenas como leitura paralela para futura migração."],
        ["Causa raiz", "MRP, 3ª aba Demanda_Projeto. A coluna Ação define a parcela pendente prevista por Estoque, Compra, Fabricação ou S.C."],
        ["Pré Nota", "É subconjunto do P.C. e não é somada a ele. Para a causa COMPRA, usa-se uma única quantidade: o maior valor identificado entre P.C. e Pré Nota na Ação, evitando dupla contagem."],
        ["Elegibilidade On Time Almox", "Não Tipo II + solicitação anterior à CM. Da pendência atual, retira a parcela explicitamente coberta por Compra, Fabricação ou S.C. no MRP."],
        ["Perda Almox", "Quantidade elegível que não foi separada até a Data CM. Pendência em estoque permanece responsabilidade do Almox."],
        ["On Time Almox", "Quantidade elegível atendida até a Data CM / quantidade elegível total."],
        ["In Full Global", "Projeto com 100% da quantidade entregável (não Tipo II) atendida, independentemente da data."],
        ["In Full Almox", "Projeto completo em toda a quantidade sob responsabilidade do Almox; pendências externas de Compra, Fabricação e S.C. são retiradas da base Almox."],
        ["OTIF Almox", "ON TIME ALMOX (%) × IN FULL ALMOX (%) / 100."],
        ["Meta mensal", "Janeiro = 25,60%; acréscimo fixo de 2,60 pontos percentuais a cada mês. A meta é automática e não editável na alimentação."],
        ["Visão Global", "Diagnóstico separado. Não altera o indicador principal do Almox; serve para explicar as perdas do processo."],
    ]

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(resumo).to_excel(writer, sheet_name="RESUMO", index=False, header=False)
        _excel_safe(pd.DataFrame(resultado["funil_geral"])).to_excel(writer, sheet_name="FUNIL_GERAL", index=False)
        _excel_safe(pd.DataFrame(resultado["solicitacoes"])).to_excel(writer, sheet_name="SOLICITACOES", index=False)
        _excel_safe(pd.DataFrame(resultado["projetos"])).to_excel(writer, sheet_name="PROJETOS", index=False)
        _excel_safe(pd.DataFrame(resultado["materiais_mrp"])).to_excel(writer, sheet_name="MATERIAIS_MRP", index=False)
        _excel_safe(pd.DataFrame(resultado["causas"])).to_excel(writer, sheet_name="CAUSAS", index=False)
        _excel_safe(pd.DataFrame(resultado["datas_projeto"])).to_excel(writer, sheet_name="DATAS_CM", index=False)
        pd.DataFrame(metodologia).to_excel(writer, sheet_name="METODOLOGIA", index=False, header=False)

        wb = writer.book
        dark = PatternFill("solid", fgColor="1F2937")
        yellow = PatternFill("solid", fgColor="FFD43D")
        soft = PatternFill("solid", fgColor="FFF4BF")
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
                vals = [str(ws.cell(r, col).value or "") for r in range(1, min(ws.max_row, 150) + 1)]
                ws.column_dimensions[get_column_letter(col)].width = min(max(max((len(v) for v in vals), default=8) + 2, 10), 42)
        ws = wb["RESUMO"]
        ws.merge_cells("A1:B1")
        ws["A1"].fill = yellow
        ws["A1"].font = Font(size=14, bold=True, color="111111")
        ws["A1"].alignment = Alignment(horizontal="center")
        ws.column_dimensions["A"].width = 52
        ws.column_dimensions["B"].width = 82
        for r in range(2, ws.max_row + 1):
            ws.cell(r, 1).font = Font(bold=True)
            ws.cell(r, 1).border = border
            ws.cell(r, 2).border = border
        for r in list(range(6, 13)) + list(range(14, 19)):
            ws.cell(r, 1).fill = soft
            ws.cell(r, 2).fill = soft
        ws = wb["METODOLOGIA"]
        ws.column_dimensions["A"].width = 31
        ws.column_dimensions["B"].width = 115
        for cell in ws[1]:
            cell.fill = dark
            cell.font = white
        for row in ws.iter_rows():
            for cell in row:
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    output.seek(0)
    return output.getvalue()


def _salvar(resultado, meta):
    client = get_client()
    competencia = str(resultado["competencia"])
    status_competencia = str(resultado.get("status_competencia") or "PARCIAL").upper()
    audit_keys = [
        "logic_version", "data_registro", "competencia", "status_competencia", "periodo_inicio", "periodo_fim",
        "projetos_programados", "linhas_macro", "solicitacoes_consolidadas", "macro_qtd", "tipo_ii_qtd",
        "global_base_qtd", "almox_base_ontime_qtd", "almox_ontime_qtd", "ontime_almox_pct",
        "infull_almox_pct", "otif_almox_pct", "ontime_global_pct", "infull_global_pct", "otif_global_pct",
        "ontime_almox_sim_pct", "ontime_global_sim_pct", "projetos_infull_almox", "projetos_completos_almox",
        "projetos_infull_global", "projetos_completos_global", "qtd_solicitacao_tardia", "qtd_compra_early",
        "qtd_fabricacao_early", "qtd_sc_early", "relatorio_nome", "relatorio_sha256", "for022_nome",
        "for022_sha256", "cadastro_nome", "cadastro_sha256", "mrp_nome", "mrp_sha256",
    ]
    audit = {k: resultado[k] for k in audit_keys}
    payload = {
        "competencia": competencia,
        "categoria": "OPERACIONAL",
        "indicador": INDICADOR,
        "valor": round(float(resultado["otif_almox_pct"]), 2),
        "meta": round(float(meta), 2),
        "unidade": "%",
        "observacao": json.dumps({"origem": "otif_almox_mensal_v7", **audit}, ensure_ascii=False, separators=(",", ":")),
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }
    existente = (
        client.table("almox_indicadores").select("id,observacao").eq("indicador", INDICADOR)
        .eq("competencia", competencia).limit(1).execute().data or []
    )

    if existente:
        obs_atual = existente[0].get("observacao")
        if isinstance(obs_atual, str):
            try:
                obs_atual = json.loads(obs_atual)
            except Exception:
                obs_atual = {}
        status_atual = str((obs_atual or {}).get("status_competencia") or "").upper()
        if status_atual == "FECHADO":
            return "mantido_fechado", None
        client.table("almox_indicadores").update(payload).eq("id", existente[0]["id"]).execute()
        acao = "fechado" if status_competencia == "FECHADO" else "atualizado"
    else:
        client.table("almox_indicadores").insert(payload).execute()
        acao = "fechado" if status_competencia == "FECHADO" else "salvo"

    try:
        client.table("almox_historico").insert({
            "tipo": "indicador_otif_almox_auditoria",
            "descricao": f"OTIF Almox {acao}: competência {_fmt_date(competencia)}",
            "dados": audit,
        }).execute()
    except Exception:
        pass

    snapshot_versao = None
    if status_competencia == "FECHADO":
        snapshot_versao = salvar_snapshot_otif(resultado, meta)
    return acao, snapshot_versao

@st.fragment
def render_alimentacao_entregas_v2(indicadores):
    hoje = _agora_local().date()
    competencia_atual = _ultimo_dia_mes(hoje)
    periodo_fim_atual = hoje if hoje == competencia_atual else hoje - timedelta(days=1)

    with st.expander("ALIMENTAR · ENTREGAS NO PRAZO / OTIF ALMOX", expanded=False):
        st.caption(
            f"Competência atual: {competencia_atual.strftime('%d/%m/%Y')} · "
            f"Período em acompanhamento: 01/{hoje.strftime('%m/%Y')} a {periodo_fim_atual.strftime('%d/%m/%Y')} · "
            "o mês é atualizado com as bases e fica travado quando fecha."
        )

        fonte = st.radio(
            "FONTE DOS DADOS",
            ["CENTRAL SETTA · API", "ARQUIVOS MANUAIS"],
            horizontal=True,
            key="otif_fonte_dados",
        )

        relatorio = for022 = cadastro = mrp = None
        fingerprint_base = ""

        if fonte == "CENTRAL SETTA · API":
            try:
                state = status_otif_central()
                src = state.get("sources") or {}
                der = state.get("derived") or {}
                st.caption(
                    " · ".join([
                        f"RELATÓRIO GERAL: {rotulo_fonte(src.get('relatorio_geral'))}",
                        f"FOR022: {rotulo_fonte(src.get('for022'))}",
                        f"CADASTROS: {rotulo_fonte(src.get('cadastros'))}",
                        f"MRP: {rotulo_fonte(der.get('relatorio_mrp'))}",
                    ])
                )
                if not state.get("ready"):
                    st.warning("A Central SETTA ainda não possui todas as quatro bases necessárias para o OTIF.")
                    return

                with st.spinner("Carregando bases atualizadas da Central SETTA..."):
                    bundle = carregar_otif_central()
                relatorio = bundle["relatorio"]
                for022 = bundle["for022"]
                cadastro = bundle["cadastro"]
                mrp = bundle["mrp"]
                fingerprint_base = str(bundle.get("fingerprint") or "")
                st.success("Bases carregadas automaticamente pela API da Central SETTA.")
            except Exception as exc:
                st.error(f"Não foi possível carregar as bases pela API: {exc}")
                st.caption("Use ARQUIVOS MANUAIS como contingência enquanto a integração estiver indisponível.")
                return
        else:
            c1, c2 = st.columns(2)
            with c1:
                relatorio = st.file_uploader("RELATÓRIO GERAL", type=["xlsx", "xlsm"], key="otif_relatorio")
                cadastro = st.file_uploader("CADASTROS", type=["xlsx", "xlsm", "xltx"], key="otif_cadastro")
            with c2:
                for022 = st.file_uploader("SEN-PCP-FOR-022 · PLANEJAMENTO MACRO", type=["xlsx", "xlsm"], key="otif_for022")
                mrp = st.file_uploader("MRP · RELATÓRIOS COMPLETOS", type=["xlsx", "xlsm"], key="otif_mrp")
            if any(x is None for x in (relatorio, for022, cadastro, mrp)):
                st.info("Envie as quatro bases para gerar a apuração em camadas.")
                return
            fingerprint_base = hashlib.sha256(
                relatorio.getvalue() + for022.getvalue() + cadastro.getvalue() + mrp.getvalue()
            ).hexdigest()

        anterior_ref = _mes_anterior(hoje)
        competencia_anterior = _ultimo_dia_mes(anterior_ref)
        if _status_registro_mensal(competencia_anterior) != "FECHADO":
            fechamento_fp = hashlib.sha256(
                (LOGIC_VERSION + "|FECHAMENTO|" + fonte + "|" + fingerprint_base + "|" + competencia_anterior.isoformat()).encode("utf-8")
            ).hexdigest()
            if st.session_state.get("otif_fechamento_fp") != fechamento_fp:
                try:
                    with st.spinner(f"Fechando competência {competencia_anterior.strftime('%m/%Y')}..."):
                        resultado_anterior = calcular_entregas_v2(
                            relatorio, for022, cadastro, mrp, hoje, anterior_ref
                        )
                        meta_anterior = _meta_mensal(anterior_ref)
                        acao_anterior, snap_anterior = _salvar(resultado_anterior, meta_anterior)
                    st.session_state["otif_fechamento_fp"] = fechamento_fp
                    st.session_state["otif_fechamento_msg"] = (
                        f"Competência {competencia_anterior.strftime('%m/%Y')} {acao_anterior}: "
                        f"01/{competencia_anterior.strftime('%m/%Y')} a {competencia_anterior.strftime('%d/%m/%Y')} · "
                        f"OTIF {resultado_anterior['otif_almox_pct']:.2f}%"
                        + (f" · snapshot v{snap_anterior}" if snap_anterior else "")
                    )
                except Exception as exc:
                    st.warning(f"Não foi possível fechar automaticamente {competencia_anterior.strftime('%m/%Y')}: {exc}")

        if st.session_state.get("otif_fechamento_msg"):
            st.info(st.session_state["otif_fechamento_msg"])

        m1, m2 = st.columns([1, 1])
        with m1:
            st.text_input(
                "Competência mensal",
                value=competencia_atual.strftime("%d/%m/%Y"),
                disabled=True,
                key="otif_competencia",
            )
        with m2:
            meta = _meta_mensal(hoje)
            st.number_input("Meta OTIF Almox (%)", min_value=0.0, max_value=100.0, value=float(meta), step=0.1, disabled=True, key="otif_meta")
            st.caption(f"Meta automática do mês: {meta:.2f}% · evolução mensal de +2,60 p.p.")

        fingerprint = hashlib.sha256(
            (LOGIC_VERSION + "|ATUAL|" + fonte + "|" + fingerprint_base + "|" + hoje.isoformat()).encode("utf-8")
        ).hexdigest()
        if st.session_state.get("otif_fingerprint") != fingerprint:
            try:
                with st.spinner("Aplicando o funil de elegibilidade e atualizando a competência mensal..."):
                    resultado = calcular_entregas_v2(relatorio, for022, cadastro, mrp, hoje, hoje)
                    acao_atual, snapshot_atual = _salvar(resultado, meta)
                st.session_state["otif_resultado"] = resultado
                st.session_state["otif_fingerprint"] = fingerprint
                st.session_state["otif_auto_save"] = (
                    f"Competência {competencia_atual.strftime('%m/%Y')} {acao_atual} automaticamente · "
                    f"período {pd.to_datetime(resultado['periodo_inicio']).strftime('%d/%m/%Y')} a "
                    f"{pd.to_datetime(resultado['periodo_fim']).strftime('%d/%m/%Y')}"
                    + (f" · snapshot v{snapshot_atual}" if snapshot_atual else "")
                )
            except Exception as exc:
                st.session_state.pop("otif_resultado", None)
                st.error(f"Não foi possível calcular/registrar automaticamente: {exc}")
                return

        resultado = st.session_state.get("otif_resultado")
        if not resultado:
            return

        if st.session_state.get("otif_auto_save"):
            st.success(st.session_state["otif_auto_save"])

        st.markdown("#### INDICADOR DO ALMOXARIFADO")
        a, b, c, d = st.columns(4)
        a.metric("ON TIME ALMOX", f"{resultado['ontime_almox_pct']:.2f}%")
        b.metric("IN FULL ALMOX", f"{resultado['infull_almox_pct']:.2f}%")
        c.metric("OTIF ALMOX", f"{resultado['otif_almox_pct']:.2f}%")
        d.metric("BASE ELEGÍVEL", f"{resultado['almox_base_ontime_qtd']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        st.caption(
            f"Status da competência: {resultado['status_competencia']} · "
            f"On Time oficial usa Data de Separação sem horas. Simulação pela Data de Conferência: {resultado['ontime_almox_sim_pct']:.2f}%. "
            f"In Full: {resultado['projetos_completos_almox']} de {resultado['projetos_infull_almox']} projetos elegíveis completos."
        )

        with st.expander("VISÃO GLOBAL · DIAGNÓSTICO DO PROCESSO", expanded=False):
            g1, g2, g3 = st.columns(3)
            g1.metric("ON TIME GLOBAL", f"{resultado['ontime_global_pct']:.2f}%")
            g2.metric("IN FULL GLOBAL", f"{resultado['infull_global_pct']:.2f}%")
            g3.metric("OTIF GLOBAL", f"{resultado['otif_global_pct']:.2f}%")
            st.caption(
                f"Esta visão não altera o indicador do Almox. Ela explica a diferença causada por solicitação tardia, compra, fabricação, S.C. e demais fatores do processo. "
                f"On Time global simulado pela conferência: {resultado['ontime_global_sim_pct']:.2f}%."
            )

        st.markdown("#### FUNIL DE ELEGIBILIDADE")
        st.dataframe(pd.DataFrame(resultado["causas"]), use_container_width=True, hide_index=True)
        with st.expander("CONFERÊNCIA POR PROJETO · IN FULL"):
            st.dataframe(pd.DataFrame(resultado["projetos"]), use_container_width=True, hide_index=True)
        with st.expander("SOLICITAÇÕES CONSOLIDADAS · ON TIME"):
            st.dataframe(pd.DataFrame(resultado["solicitacoes"]), use_container_width=True, hide_index=True)
        with st.expander("MATERIAIS · CONFRONTO RELATÓRIO GERAL x MRP"):
            st.dataframe(pd.DataFrame(resultado["materiais_mrp"]), use_container_width=True, hide_index=True)

        excel_bytes = _excel_auditoria(resultado, meta)
        st.download_button(
            "EXPORTAR AUDITORIA · EXCEL",
            excel_bytes,
            file_name=f"auditoria_otif_almox_{competencia_atual.isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="otif_excel",
        )
        st.caption(
            "Gravação automática ativa: durante o mês, o mesmo registro da competência é atualizado. "
            "Ao fechar o mês, ele recebe status FECHADO e não pode mais ser sobrescrito pela rotina automática."
        )

