"""Regressão estática do favicon central e do refinamento de indicadores.

Executa sem credenciais e sem alterar bancos, arquivos de clientes ou sessões.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def read(filename: str) -> str:
    content = (ROOT / filename).read_text(encoding="utf-8")
    ast.parse(content, filename=filename)
    return content


app = read("streamlit_app.py")
original = read("streamlit_app_original.py")
visual = read("setta_visual.py")
ui = read("ui_refinements.py")
dashboard = read("indicadores_dashboard.py")
access = read("controle_acesso.py")

assert 'page_icon=centralized_page_icon()' in app
assert "from setta_visual import centralized_page_icon" in app
assert "from ui_refinements import apply_ui_refinements" in app
assert "apply_ui_refinements(st, primary=PRIMARY, light=IS_LIGHT)" in app
assert "@st.cache_data(ttl=120,show_spinner=False)\\ndef rows(" in app
assert "@st.cache_data(ttl=120,show_spinner=False)\ndef rows(" in original

assert "visual_get" in visual and '"setta_global"' in visual
assert "favicon_data" in visual and "ttl=300" in visual
assert "setta_favicon.png" in visual
assert "def centralized_page_icon():" in visual
assert "def apply_ui_refinements(" in ui

assert "with st.container(border=True):" in dashboard
assert "ind-chart-head" in dashboard
assert "st.markdown('</div>'" not in dashboard
assert ".ind-kpi-label{font-size:11px" in dashboard
assert "axis_max = max(105.0" in dashboard

# Autofill nativo precisa declarar que é uma senha existente, não cadastro novo.
assert 'key="acesso_email_login", autocomplete="username"' in access
assert 'key="acesso_senha_login", autocomplete="current-password"' in access
assert "_habilitar_autofill_login" not in access
assert "components.html(" not in access
assert "sign_in_with_password" in access

# Nada na melhoria deve modificar lógica de acesso ou as rotinas de cálculo.
assert "from controle_acesso import render_login" in app
assert "render_alimentacao_acuracia(rows)" in dashboard
assert "render_alimentacao_entregas_v2(rows)" in dashboard
print("GESTAOOPERACIONAL_UI_FAVICON_REGRESSION_OK")
