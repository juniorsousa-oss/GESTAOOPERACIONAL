from __future__ import annotations

import pandas as pd

ALIASES = {
    "ACURACIDADE DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ACURÁCIDADE DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ACURÁCIA DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ACURACIA DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ENTREGAS NO PRAZO": "ENTREGAS NO PRAZO",
    "5S": "5S",
}

MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def normalizar_indicador(value):
    nome = str(value or "").strip()
    return ALIASES.get(nome.upper(), nome.upper())


def _ordenar_lancamentos(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    if "competencia" not in df.columns:
        return pd.DataFrame()
    df["_competencia"] = pd.to_datetime(df["competencia"], errors="coerce")
    df = df.dropna(subset=["_competencia"]).copy()
    if df.empty:
        return df
    if "created_at" in df.columns:
        df["_created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    elif "updated_at" in df.columns:
        df["_created_at"] = pd.to_datetime(df["updated_at"], errors="coerce", utc=True)
    else:
        df["_created_at"] = pd.NaT
    if "id" in df.columns:
        df["_id_ord"] = pd.to_numeric(df["id"], errors="coerce")
    else:
        df["_id_ord"] = pd.NA
    df["_seq"] = range(len(df))
    return df.sort_values(
        ["_competencia", "_created_at", "_id_ord", "_seq"],
        kind="stable",
        na_position="first",
    )


def consolidar_ultimo_por_mes(rows):
    """Retorna exatamente o último lançamento de cada mês, sem média."""
    df = _ordenar_lancamentos(rows)
    if df.empty:
        return []
    df["_periodo"] = df["_competencia"].dt.to_period("M")
    df = df.groupby("_periodo", sort=True, group_keys=False).tail(1)
    helpers = ["_competencia", "_created_at", "_id_ord", "_seq", "_periodo"]
    return df.drop(columns=[c for c in helpers if c in df.columns]).to_dict("records")


def meses_disponiveis(indicadores):
    df = _ordenar_lancamentos(indicadores)
    if df.empty:
        return []
    periodos = sorted(df["_competencia"].dt.to_period("M").unique(), reverse=True)
    return [str(p) for p in periodos]


def rotulo_mes(periodo):
    try:
        p = pd.Period(str(periodo), freq="M")
        return f"{MESES[p.month-1]}/{p.year}"
    except Exception:
        return str(periodo or "—")


def _periodo_corte(indicadores, modo="ATUAL", periodo=None):
    """Define o último mês que aparecerá na exportação."""
    if str(modo).upper().startswith("MÊS") or str(modo).upper().startswith("MES"):
        try:
            return pd.Period(str(periodo), freq="M")
        except Exception:
            return None

    df = _ordenar_lancamentos(indicadores)
    if df.empty:
        return None
    return df["_competencia"].dt.to_period("M").max()


def preparar_exportacao(indicadores, modo="ATUAL", periodo=None):
    """
    Prepara a série mensal para exportação.

    Regras:
    - ATUAL: janeiro até a última competência disponível do ano mais recente;
    - MÊS ESPECÍFICO: janeiro até o mês selecionado, inclusive;
    - nunca permite competência posterior ao corte escolhido;
    - em cada mês vale somente o último lançamento, nunca a média.
    """
    corte = _periodo_corte(indicadores, modo, periodo)
    if corte is None:
        return []

    modo_especifico = (
        str(modo).upper().startswith("MÊS")
        or str(modo).upper().startswith("MES")
    )

    grupos = {}
    for row in indicadores or []:
        nome = normalizar_indicador(row.get("indicador"))
        if not nome:
            continue
        item = dict(row)
        item["indicador"] = nome
        grupos.setdefault(nome, []).append(item)

    saida = []
    for nome, rows in grupos.items():
        mensais = consolidar_ultimo_por_mes(rows)
        for row in mensais:
            dt = pd.to_datetime(row.get("competencia"), errors="coerce")
            if pd.isna(dt):
                continue

            periodo_row = dt.to_period("M")

            # A exportação sempre fica restrita ao ano do corte e jamais
            # inclui uma competência posterior ao mês selecionado.
            if periodo_row.year != corte.year:
                continue
            if periodo_row > corte:
                continue

            item = dict(row)
            item["indicador"] = nome
            saida.append(item)

    saida.sort(
        key=lambda r: (
            normalizar_indicador(r.get("indicador")),
            pd.to_datetime(r.get("competencia"), errors="coerce"),
        )
    )

    # Trava defensiva adicional para MÊS ESPECÍFICO.
    if modo_especifico:
        validada = []
        for row in saida:
            dt = pd.to_datetime(row.get("competencia"), errors="coerce")
            if pd.isna(dt):
                continue
            pr = dt.to_period("M")
            if pr.year == corte.year and pr <= corte:
                validada.append(row)
        saida = validada

    return saida
