"""Regressão da necessidade do novo Relatório Geral SETTA."""
from pathlib import Path
import ast

import pandas as pd

codigo = Path("indicadores_entregas_v2.py").read_text(encoding="utf-8")
arvore = ast.parse(codigo)
alvo = next(n for n in arvore.body if isinstance(n, ast.FunctionDef)
            and n.name == "normalizar_necessidade_relatorio_geral")
alvo.decorator_list = []
escopo = {"pd": pd, "TOL": 1e-9}
exec(compile(ast.Module(body=[alvo], type_ignores=[]), "ot_if_necessidade.py", "exec"), escopo)

linhas = pd.DataFrame([
    {"Projeto_Código": "26001001001_00060140", "Data Solicitação": pd.Timestamp("2026-08-19"), "Qtd Necessária": 12.0, "Qtd Atendida": 4.0},
    {"Projeto_Código": "26001001001_00060140", "Data Solicitação": pd.Timestamp("2026-08-19"), "Qtd Necessária": 12.0, "Qtd Atendida": 8.0},
    {"Projeto_Código": "26001001001_00700217", "Data Solicitação": pd.Timestamp("2026-08-19"), "Qtd Necessária": 5.0, "Qtd Atendida": 2.0},
    {"Projeto_Código": "26001001002_00060140", "Data Solicitação": pd.Timestamp("2026-09-22"), "Qtd Necessária": 2.0, "Qtd Atendida": 1.0},
    {"Projeto_Código": "26001001002_00060140", "Data Solicitação": pd.Timestamp("2026-09-23"), "Qtd Necessária": 4.0, "Qtd Atendida": 4.0},
    {"Projeto_Código": "26001001002_00060140", "Data Solicitação": pd.Timestamp("2026-09-23"), "Qtd Necessária": 4.0, "Qtd Atendida": 0.0},
])
res = escopo["normalizar_necessidade_relatorio_geral"](linhas)
assert res["Qtd Necessária"].tolist() == [12.0, 0.0, 5.0, 2.0, 4.0, 0.0]
assert res["Qtd Atendida"].tolist() == [4.0, 8.0, 2.0, 1.0, 4.0, 0.0]
assert res["Necessidade divergente na origem"].tolist() == [False]*6
assert res.groupby("Projeto_Código")["Qtd Necessária"].sum()["26001001002_00060140"] == 6.0
assert linhas["Qtd Necessária"].tolist() == [12,12,5,2,4,4]

invalido = linhas.copy()
invalido.loc[5, "Qtd Necessária"] = 5.0
try:
    escopo["normalizar_necessidade_relatorio_geral"](invalido)
except ValueError as exc:
    assert "mesma OP/produto/data" in str(exc)
else:
    raise AssertionError("Necessidades diferentes na mesma data devem ser bloqueadas")
assert "rel_raw.iloc[:, 8]" not in codigo
assert '"Qtd Necessária": "Qtd. necessária"' in codigo
print("GESTAO_OPERACIONAL_RELATORIO_GERAL_NECESSIDADE_UNICA_OK")
