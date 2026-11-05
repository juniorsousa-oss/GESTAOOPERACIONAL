"""Regressões rápidas de desempenho do Streamlit Gestão Operacional.

Sem Supabase e sem credenciais: verifica que operações de alto custo não
voltem a ocorrer a cada interação do dashboard ou a cada tecla do login.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    result = (ROOT / path).read_text(encoding="utf-8")
    ast.parse(result, filename=path)
    return result


login = source("controle_acesso.py")
app = source("streamlit_app_original.py")
dashboard = source("indicadores_dashboard.py")
accuracy = source("indicadores_acuracia.py")
otif = source("indicadores_entregas_v2.py")
history = source("indicadores_historico.py")

# Credenciais só na sessão do usuário, nunca em um cache de processo.
assert "st.session_state.get(\"_acesso_cliente_cached\")" in login
assert "AUTH_VERIFY_SECONDS = 20.0" in login
assert "PERFIL_VERIFY_SECONDS = 15.0" in login
assert "st.session_state.get(\"_acesso_perfil_cache_user_id\")" in login
assert 'st.session_state.pop(key, None)' in login
assert '@st.cache_data(ttl=600, max_entries=1, show_spinner=False)' in login
assert 'image = carregar_imagem_login()' in login

# Dashboard precisa ler poucas colunas e limitar consultas em reruns.
assert '@st.cache_data(ttl=15, max_entries=2, show_spinner=False)' in app
assert 'def indicadores_dashboard_atuais():' in app
assert '.select("id,competencia,categoria,indicador,valor,meta,unidade,observacao")' in app
assert "indicadores=indicadores_dashboard_atuais()" in app
assert 'on_click=_alterar_visao_indicador' in dashboard

# st.expander(expanded=False) sozinho NÃO atrasa a execução do seu corpo.
# Cada módulo deve proteger a chamada cara com um toggle explícito.
for code, gate, expensive in (
    (accuracy, 'key="acuracia_apuracao_ativa"', 'state = status_acuracia_central()'),
    (otif, 'key="otif_apuracao_ativa"', 'state = status_otif_central()'),
    (history, 'key="otif_historico_ativo"', 'snapshots = listar_snapshots_otif()'),
):
    assert gate in code, gate
    assert code.index(gate) < code.index(expensive), expensive

print("GESTAO_OPERACIONAL_PERFORMANCE_CONTRACT_OK")
