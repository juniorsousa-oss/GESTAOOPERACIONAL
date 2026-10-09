from __future__ import annotations
from datetime import date
import base64
from collections import Counter, defaultdict
from io import BytesIO
import pandas as pd
from PIL import Image
import streamlit as st

# Padrão SETTA: tabelas responsivas ao total de registros exibidos nos filtros.
def _setta_table_height(data, requested=None):
    """Altura baseada nas linhas reais (DataFrame ou pandas.Styler)."""
    if isinstance(requested, str):
        return requested
    frame = getattr(data, "data", data)
    try:
        rows = len(frame)
    except (TypeError, ValueError, AttributeError):
        return requested if isinstance(requested, int) and requested >= 120 else None
    ceiling = max(120, requested) if isinstance(requested, int) and requested > 0 else 600
    return int(min(ceiling, max(120, 42 + 35 * (min(max(0, rows), 100) + 1))))


def _setta_dataframe(data, *args, **kwargs):
    height = _setta_table_height(data, kwargs.get("height"))
    if height is None:
        kwargs.pop("height", None)
    else:
        kwargs["height"] = height
    return st.dataframe(data, *args, **kwargs)


def _setta_data_editor(data, *args, **kwargs):
    if kwargs.get("num_rows") != "dynamic":
        height = _setta_table_height(data, kwargs.get("height"))
        if height is None:
            kwargs.pop("height", None)
        else:
            kwargs["height"] = height
    return st.data_editor(data, *args, **kwargs)


from supabase_client import get_client
from indicadores_dashboard import render_indicadores

st.set_page_config(page_title="GESTÃO | SETTA", page_icon="assets/mrp_setta_icon.png", layout="wide", initial_sidebar_state="expanded")

DEFAULT_CONFIG = {
    "tema":"Escuro (Padrão)", "cor_principal":"Amarelo (Padrão)", "estilo_botoes":"Amarelo",
    "logo_base64":None, "logo_name":None, "logo_mime":None, "logo_bg":"#ffffff",
    "textos":{}, "fontes":{}, "metas_equipes":{},
    "organograma":{"titulo":"ORGANOGRAMA DO ALMOXARIFADO","raiz_id":None,"pais":{}}
}
CORES={"Amarelo (Padrão)":"#ffd43d","Azul":"#1683ff","Verde":"#22c55e","Vermelho":"#ff4d4f","Roxo":"#8b5cf6","Laranja":"#ff922b","Ciano":"#22c7d6","Rosa":"#ec4899","Lima":"#a3e635"}
TEMAS=["Escuro (Padrão)","Claro","Automático"]
ESTILOS_BOTOES=["Amarelo","Colorido"]
TEXTOS_PADRAO={
    "menu_indicadores":"Alimentar Indicadores","menu_historico":"Histórico",
    "menu_equipes":"Gestão de Equipes","menu_carreira":"Plano de Carreira","menu_configuracoes":"Configurações",
    "titulo_indicadores":"Indicadores Operacionais","titulo_historico":"Histórico",
    "titulo_equipes":"Gestão de Equipes","titulo_carreira":"Plano de Carreira","titulo_configuracoes":"Configurações",
    "subtitulo_global":"Gestão operacional do almoxarifado","secao_dados_reais":"Dados reais do Supabase",
    "secao_indicadores":"Indicadores","secao_historico":"Histórico real","secao_colaboradores":"Colaboradores",
    "secao_equipes":"Equipes","secao_organograma":"Organograma","secao_configuracoes":"Configurações",
    "secao_plano":"Plano de carreira","mensagem_sem_indicadores":"A tabela almox_indicadores está conectada, mas ainda não possui lançamentos.",
    "diagnostico":"Diagnóstico","mensagem_indicadores":"Consulta do banco real habilitada.",
    "mensagem_carreira":"Módulo preparado para receber níveis, competências, metas e trilhas de desenvolvimento.",
    "rodape_linha1":"Gestão Operacional","rodape_linha2":"SETTA • Streamlit + Supabase",
    "rodape_linha3":"Gestão Almoxarifado • Streamlit + Supabase • migração em andamento"
}
FONTES_PADRAO={"menu":"Arial","titulo":"Arial","subtitulo":"Arial"}
FONTES=["Arial","Verdana","Trebuchet MS","Georgia","Courier New","Times New Roman"]

def carregar_configuracoes():
    try:
        resultado=get_client().table("almox_app_state").select("estado").eq("id","global").limit(1).execute()
        if resultado.data:
            estado=resultado.data[0].get("estado") or {}
            config={**DEFAULT_CONFIG,**{k:v for k,v in estado.items() if k in DEFAULT_CONFIG}}
            config["textos"]={**TEXTOS_PADRAO,**(estado.get("textos") or {})}
            config["fontes"]={**FONTES_PADRAO,**(estado.get("fontes") or {})}
            config["metas_equipes"]={**(estado.get("metas_equipes") or {})}
            config["organograma"]={**DEFAULT_CONFIG["organograma"],**(estado.get("organograma") or {})}
            config["organograma"]["pais"]={str(k):str(v) for k,v in (config["organograma"].get("pais") or {}).items()}
            return config
    except Exception as e:
        st.session_state.config_erro=str(e)
    return {**DEFAULT_CONFIG,"textos":TEXTOS_PADRAO.copy(),"fontes":FONTES_PADRAO.copy(),"metas_equipes":{},"organograma":DEFAULT_CONFIG["organograma"].copy()}

def salvar_configuracoes(config):
    try:
        return bool(get_client().table("almox_app_state").upsert({"id":"global","estado":config},on_conflict="id").execute().data)
    except Exception:
        return False

@st.cache_data(show_spinner=False)
def detectar_cor_fundo_logo(logo_b64,logo_mime=None):
    if not logo_b64:return "#ffffff"
    try:
        if (logo_mime or "").lower()=="image/svg+xml":return "#ffffff"
        imagem=Image.open(BytesIO(base64.b64decode(logo_b64))).convert("RGBA");imagem.thumbnail((160,160))
        pixels=[(r,g,b) for r,g,b,a in imagem.getdata() if a>=220]
        if not pixels:return "#ffffff"
        agrupadas=[((r//16)*16,(g//16)*16,(b//16)*16) for r,g,b in pixels]
        return "#{:02x}{:02x}{:02x}".format(*Counter(agrupadas).most_common(1)[0][0])
    except Exception:return "#ffffff"

if "config_carregada" not in st.session_state:
    st.session_state.config=carregar_configuracoes();st.session_state.config_carregada=True
config=st.session_state.config
config["textos"]={**TEXTOS_PADRAO,**(config.get("textos") or {})}
config["fontes"]={**FONTES_PADRAO,**(config.get("fontes") or {})}
config["metas_equipes"]={**(config.get("metas_equipes") or {})}
config["organograma"]={**DEFAULT_CONFIG["organograma"],**(config.get("organograma") or {})}
config["organograma"]["pais"]={str(k):str(v) for k,v in (config["organograma"].get("pais") or {}).items()}
textos=config["textos"];fontes=config["fontes"]
def txt(k):return textos.get(k,TEXTOS_PADRAO.get(k,k))
PRIMARY=CORES.get(config.get("cor_principal"),CORES["Amarelo (Padrão)"])
IS_LIGHT=config.get("tema")=="Claro"
if IS_LIGHT: APP_BG,SIDEBAR_BG,TEXT,MUTED,PANEL,BORDER,INPUT_BG="#f4f6f5","#ffffff","#18201d","#68736e","#ffffff","#d7ded9","#f8faf9"
else: APP_BG,SIDEBAR_BG,TEXT,MUTED,PANEL,BORDER,INPUT_BG="#0b0f0e","#090c0b","#f4f5f4","#9aa39f","#101513","#35403b","#101513"

st.markdown(f"""
<style>
#MainMenu,footer{{visibility:hidden}}
header[data-testid="stHeader"]{{display:block!important;height:32px!important;min-height:32px!important;background:#090c0b!important;border-bottom:1px solid #1f2925!important}}
[data-testid="stToolbar"]{{display:flex!important;align-items:center!important;justify-content:flex-end!important;height:32px!important;background:#090c0b!important}}
button[data-testid="stSidebarCollapseButton"]{{display:flex!important;visibility:visible!important;pointer-events:auto!important}}
section[data-testid="stSidebar"]{{background:{SIDEBAR_BG}!important;border-right:1px solid {BORDER};width:260px!important;min-width:260px!important;max-width:260px!important;flex:0 0 260px!important;overflow:hidden!important}}
section[data-testid="stSidebar"]>div:first-child{{width:260px!important;min-width:260px!important;max-width:260px!important;box-sizing:border-box!important;padding:0 9px 16px;overflow:hidden!important}}
section[data-testid="stSidebar"][aria-expanded="false"]{{width:0!important;min-width:0!important;max-width:0!important;flex-basis:0!important}}
.stApp{{background:{APP_BG};color:{TEXT}}}.block-container{{max-width:1500px;padding:8px 34px 50px}}
.hero{{display:flex;justify-content:space-between;align-items:center;gap:24px;margin:10px 0 24px;padding:20px 22px;border:1px solid {BORDER};border-radius:16px;background:linear-gradient(145deg,{PANEL},#0d1210);box-sizing:border-box;min-height:112px}}.hero h1{{margin:0;font-family:{fontes["titulo"]},sans-serif;font-size:30px;line-height:1.1;font-weight:900;color:{TEXT};text-transform:uppercase;letter-spacing:.3px}}.hero p,.muted{{font-family:{fontes["subtitulo"]},sans-serif;color:{MUTED}}}.hero p{{margin:8px 0 0;font-size:14px;line-height:1.4}}.period{{background:{PRIMARY};color:#111;padding:12px 18px;border-radius:10px;font-weight:900;text-transform:uppercase;white-space:nowrap;font-size:14px}}
.section{{color:{PRIMARY};font-size:14px;font-weight:900;letter-spacing:1px;text-transform:uppercase;margin:22px 0 10px}}
.panel{{background:linear-gradient(145deg,{PANEL},#0d1210);border:1px solid {BORDER};border-radius:15px;padding:18px;margin-top:14px}}
.notice{{padding:12px 14px;border-left:3px solid {PRIMARY};background:{PANEL};color:{MUTED};border-radius:7px;font-size:11px}}
div[data-testid="stMetric"]{{background:linear-gradient(145deg,{PANEL},#0d1210);border:1px solid {BORDER};padding:15px;border-radius:12px}}
[data-testid="stButton"] button[kind="primary"]{{color:#111!important;background:{PRIMARY}!important;border-color:{PRIMARY}!important}}[data-testid="stButton"] button[kind="primary"] p{{color:#111!important}}
/* SIDEBAR: somente nomes, sem ícones, e 20px menos altura/espacamento */
[data-testid="stSidebar"] .stButton{{margin:0!important}}
[data-testid="stSidebar"] .stButton>button{{width:100%;min-height:42px!important;height:42px!important;border-radius:12px;border:1px solid transparent;background:transparent;color:{TEXT}!important;font-family:{fontes["menu"]},sans-serif;font-size:14px;font-weight:900;text-align:left;padding:0 14px 0 18px;box-shadow:none;position:relative}}
[data-testid="stSidebar"] .stButton>button p{{color:inherit!important;margin:0!important;text-align:left!important;width:100%!important;display:block!important}} [data-testid="stSidebar"] .stButton>button>div{{width:100%!important;justify-content:flex-start!important}}
[data-testid="stSidebar"] div.st-key-menu_dashboard button p::before,[data-testid="stSidebar"] div.st-key-menu_indicadores button p::before,[data-testid="stSidebar"] div.st-key-menu_historico button p::before,[data-testid="stSidebar"] div.st-key-menu_equipes button p::before,[data-testid="stSidebar"] div.st-key-menu_carreira button p::before,[data-testid="stSidebar"] div.st-key-menu_configuracoes button p::before{{display:none!important;content:none!important}}
[data-testid="stSidebar"] div.st-key-menu_dashboard button::after,[data-testid="stSidebar"] div.st-key-menu_indicadores button::after,[data-testid="stSidebar"] div.st-key-menu_historico button::after,[data-testid="stSidebar"] div.st-key-menu_equipes button::after,[data-testid="stSidebar"] div.st-key-menu_carreira button::after,[data-testid="stSidebar"] div.st-key-menu_configuracoes button::after{{content:"›";position:absolute;right:15px;top:50%;transform:translateY(-50%);font-size:24px;color:#9aa39f}}
[data-testid="stSidebar"] div.st-key-menu_dashboard button[kind="primary"]::after,[data-testid="stSidebar"] div.st-key-menu_indicadores button[kind="primary"]::after,[data-testid="stSidebar"] div.st-key-menu_historico button[kind="primary"]::after,[data-testid="stSidebar"] div.st-key-menu_equipes button[kind="primary"]::after,[data-testid="stSidebar"] div.st-key-menu_carreira button[kind="primary"]::after,[data-testid="stSidebar"] div.st-key-menu_configuracoes button[kind="primary"]::after{{color:{PRIMARY}}}
[data-testid="stSidebar"] .stButton>button[kind="primary"]{{background:rgba(255,212,61,.085)!important;color:{PRIMARY}!important;border-left:4px solid {PRIMARY}}}
.sidebar-logo-section{{width:100%;display:flex;flex-direction:column;align-items:center;margin:-24px 0 8px;padding:0 0 9px;border-bottom:1px solid {BORDER}}}.sidebar-logo-wrap{{width:190px;height:82px;display:flex;justify-content:center;align-items:center;background:var(--logo-bg);border:1px solid var(--logo-border);border-radius:12px;overflow:hidden}}.sidebar-logo-img{{display:block;width:100%;height:100%;object-fit:contain;margin:auto}}.sidebar-logo-placeholder{{width:176px;height:68px;display:flex;align-items:center;justify-content:center;text-align:center;color:#6b7280;background:#fff;border-radius:8px;font-size:11px;line-height:1.4}}.sidebar-footer{{margin:12px 5px 0;padding-top:12px;border-top:1px solid {BORDER};color:{MUTED};font-size:10px;line-height:1.6}}
.ge-tabs-note{{color:#a4ada8;font-size:14px;margin:0 0 16px 2px;line-height:1.45}}
.ge-kpi{{background:linear-gradient(145deg,#151b18,#0d1110);border:1px solid #34413b;border-radius:15px;padding:19px 21px;min-height:112px}}.ge-kpi .label{{font-size:12px;color:#9ba49f;text-transform:uppercase;letter-spacing:1px;font-weight:800}}.ge-kpi .value{{font-size:36px;line-height:1.05;font-weight:900;color:#f4f5f4;margin-top:9px}}.ge-kpi .sub{{font-size:12px;color:#ffd43d;margin-top:8px}}
/* IMAGEM 02: títulos dos dois painéis sem balões desproporcionais */
.ge-overview-panel{{background:linear-gradient(145deg,#111714,#0c100f);border:1px solid #34413b;border-radius:15px;padding:9px 18px 7px;margin-top:16px}}
.ge-panel-title{{font-size:14px;font-weight:900;color:#f3f5f4;text-transform:uppercase;letter-spacing:.7px;margin:0 0 6px}}
.ge-mini{{display:flex;align-items:center;gap:13px;padding:10px 0;border-bottom:1px solid #26302c}}.ge-mini:last-child{{border-bottom:0}}.ge-mini .rank{{width:31px;height:31px;border-radius:9px;background:#ffd43d;color:#111;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:900;flex:0 0 31px}}.ge-mini .main{{flex:1;min-width:0}}.ge-mini .name{{font-size:14px;font-weight:800;color:#f4f5f4}}.ge-mini .desc{{font-size:12px;color:#929c97;margin-top:3px}}
.ge-bar{{height:8px;background:#202925;border-radius:99px;overflow:hidden;margin-top:8px}}.ge-bar span{{display:block;height:100%;background:#ffd43d;border-radius:99px}}
.ge-alert{{padding:15px 17px;border-radius:12px;border:1px solid #5b4a13;background:#211c0b;color:#ddd4a7;font-size:13px;line-height:1.5}}
.ge-team-card,.ge-person-card{{background:linear-gradient(145deg,#111714,#0c100f);border:1px solid #34413b;border-radius:16px}}.ge-team-card{{padding:23px;margin-top:14px;position:relative;overflow:hidden}}.ge-team-head{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}.ge-team-title{{font-size:24px;font-weight:900;color:#f4f5f4;margin:0;line-height:1.2}}.ge-team-title span{{color:#ffd43d}}.ge-team-status{{font-size:11px;font-weight:900;border:1px solid #4a5a52;border-radius:999px;padding:7px 11px;color:#ffd43d;white-space:nowrap}}.ge-team-objective{{color:#aab2ae;font-size:14px;line-height:1.6;margin-top:10px;max-width:1050px}}.ge-team-grid{{display:grid;grid-template-columns:1.2fr 1fr 1.6fr;gap:14px;margin-top:20px}}.ge-info-box{{background:#0a0e0d;border:1px solid #26312c;border-radius:12px;padding:16px}}.ge-info-label{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#8b9690;font-weight:900}}.ge-info-value{{font-size:14px;color:#f0f2f1;font-weight:800;margin-top:7px}}.ge-task{{font-size:12px;color:#c8cecb;padding:8px 0;border-bottom:1px solid #202824;line-height:1.4}}.ge-task:last-child{{border-bottom:0}}.ge-member{{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px solid #202824}}.ge-member:last-child{{border-bottom:0}}.ge-member img,.ge-member .ge-avatar-fallback{{flex:0 0 40px}}.ge-member-name{{font-size:13px;font-weight:800;color:#f0f2f1}}.ge-member-role{{font-size:11px;color:#929c97;margin-top:3px}}.ge-empty{{color:#929c97;font-size:12px;padding:10px 0}}
.ge-person-card{{padding:17px;text-align:center;min-height:250px;margin-bottom:12px;position:relative;transition:.15s;border:1px solid #34413b}}.ge-person-card.selected{{border:2px solid #ffd43d;box-shadow:0 0 0 2px rgba(255,212,61,.10)}}.ge-person-photo{{width:94px;height:94px;margin:1px auto 12px;border-radius:50%;padding:5px;border:2px solid #ffd43d;background:#171d19;box-shadow:0 0 0 4px rgba(255,212,61,.08)}}.ge-person-photo img,.ge-person-photo .ge-avatar-fallback{{width:80px!important;height:80px!important;border-radius:50%;display:block;object-fit:cover}}.ge-avatar-img{{border-radius:50%;object-fit:cover;display:block}}.ge-avatar-fallback{{border-radius:50%;background:#26302b;color:#ffd43d;display:flex;align-items:center;justify-content:center;font-weight:900}}.ge-person-name{{font-size:14px;font-weight:900;color:#f4f5f4;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.25}}.ge-person-role{{font-size:11px;color:#ffd43d;font-weight:800;margin-top:7px;min-height:28px;line-height:1.35}}.ge-person-team{{font-size:11px;color:#9aa39f;margin-top:5px;min-height:28px;line-height:1.35}}.ge-person-status{{font-size:11px;color:#9aa39f;margin-top:11px;line-height:1.3}}
/* Cartão clicável */

.ge-admissoes-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;align-items:start}}.ge-admissao-card{{text-align:center;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-start}}.ge-admissao-photo{{height:70px;display:flex;align-items:center;justify-content:center}}.ge-admissao-name{{height:30px;display:flex;align-items:center;justify-content:center;width:100%;font-size:12px;font-weight:900;color:#f4f5f4;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.ge-admissao-date{{height:18px;font-size:11px;color:#9aa39f}}.ge-admissoes-panel{{padding:16px 18px 14px}}.ge-admissoes-panel .ge-panel-title{{margin-bottom:8px}}.org-focus-head{{margin:4px 0 12px}}.org-focus-title{{font-size:22px;font-weight:900;color:#f4f5f4;text-transform:uppercase}}.org-focus-sub{{font-size:12px;color:#8f9994;margin-top:4px}}.org-main{{min-height:620px;background:linear-gradient(145deg,#111714,#0b0f0e);border:1px solid #35403b;border-radius:18px;padding:26px 22px 34px;box-shadow:0 14px 40px rgba(0,0,0,.18);overflow:hidden}}.org-main .org-title{{font-size:20px;font-weight:900;letter-spacing:.8px;text-transform:uppercase;margin:0 0 28px;color:#f4f5f4;text-align:center}}.org-main .org-tree{{width:100%;overflow-x:auto;overflow-y:hidden;padding:10px 18px 28px;box-sizing:border-box}}.org-main .org-node{{width:238px;min-height:154px;box-sizing:border-box;padding:16px 14px 14px;border:1px solid #3b4741;border-radius:16px;background:linear-gradient(160deg,#18201c,#0d1210);display:flex;flex-direction:column;align-items:center;justify-content:flex-start;position:relative;box-shadow:0 8px 24px rgba(0,0,0,.20);transition:transform .16s,border-color .16s,box-shadow .16s}}.org-main .org-node:hover{{transform:translateY(-2px);border-color:#ffd43d;box-shadow:0 12px 28px rgba(0,0,0,.28)}}.org-main .org-node.root{{border:2px solid #ffd43d;background:linear-gradient(160deg,#20271f,#111613);box-shadow:0 0 0 5px rgba(255,212,61,.08),0 12px 30px rgba(0,0,0,.25)}}.org-main .org-node img,.org-main .org-node .ge-avatar-fallback{{width:66px!important;height:66px!important;border-radius:50%;object-fit:cover;flex:0 0 66px;border:2px solid #ffd43d;box-shadow:0 0 0 4px rgba(255,212,61,.07)}}.org-main .org-node strong{{display:block;max-width:205px;margin-top:11px;font-size:13px;line-height:1.3;font-weight:900;color:#f4f5f4;text-transform:uppercase;text-align:center;white-space:normal}}.org-main .org-node small{{display:block;max-width:205px;margin-top:5px;font-size:10px;line-height:1.35;font-weight:800;color:#aeb7b2;text-transform:uppercase;text-align:center}}.org-main .org-children{{display:flex;justify-content:center;align-items:flex-start;gap:30px;position:relative;padding-top:34px;margin-top:0}}.org-main .org-children::before{{content:"";position:absolute;top:0;left:50%;height:17px;border-left:2px solid #4a5750}}.org-main .org-child{{position:relative;display:flex;flex-direction:column;align-items:center;min-width:238px;padding-top:17px}}.org-main .org-child::before{{content:"";position:absolute;top:0;left:50%;width:100%;border-top:2px solid #4a5750}}.org-main .org-child:first-child::before{{left:50%;width:50%}}.org-main .org-child:last-child::before{{left:0;width:50%}}.org-main .org-child:only-child::before{{display:none}}.org-main .org-child>.org-connector{{position:absolute;top:0;left:50%;height:17px;border-left:2px solid #4a5750}}.org-main .org-connector{{height:20px;border-left:2px solid #4a5750;margin:0 auto}}div[data-testid="stExpander"]{{border:1px solid #34413b!important;border-radius:12px!important;background:#0d1210!important;margin-top:14px}}
.ge-colab-card{{background:linear-gradient(145deg,#111714,#0c100f);border:1px solid #35403b;border-radius:16px;padding:16px 14px 12px;min-height:278px;display:flex;flex-direction:column;align-items:center;box-sizing:border-box;box-shadow:0 8px 22px rgba(0,0,0,.14)}}.ge-colab-card.selected{{border:2px solid #ffd43d;box-shadow:0 0 0 4px rgba(255,212,61,.08),0 10px 25px rgba(0,0,0,.22)}}.ge-colab-photo{{width:92px;height:92px;border-radius:50%;object-fit:cover;border:2px solid #ffd43d;box-shadow:0 0 0 5px rgba(255,212,61,.07);margin:2px auto 13px;display:block}}.ge-colab-photo-fallback{{width:92px;height:92px;border-radius:50%;border:2px solid #ffd43d;background:#26302b;color:#ffd43d;display:flex;align-items:center;justify-content:center;font-size:25px;font-weight:900;margin:2px auto 13px;box-shadow:0 0 0 5px rgba(255,212,61,.07)}}.ge-colab-name{{font-size:13px;font-weight:900;color:#f4f5f4;text-transform:uppercase;text-align:center;line-height:1.25;min-height:33px;display:flex;align-items:center;justify-content:center}}.ge-colab-role{{font-size:11px;font-weight:800;color:#ffd43d;text-transform:uppercase;text-align:center;line-height:1.3;min-height:30px;margin-top:5px;display:flex;align-items:center;justify-content:center}}.ge-colab-team{{font-size:10px;color:#a3ada8;text-transform:uppercase;text-align:center;line-height:1.3;min-height:28px;margin-top:4px;display:flex;align-items:center;justify-content:center}}.ge-colab-meta{{font-size:10px;color:#7f8b85;text-align:center;margin-top:5px;min-height:16px}}.ge-colab-select button{{height:34px!important;min-height:34px!important;border-radius:9px!important;margin-top:10px!important;font-size:10px!important;font-weight:900!important;text-transform:uppercase!important;padding:0 10px!important}}.ge-colab-select button[kind="primary"]{{box-shadow:0 0 0 2px rgba(255,212,61,.14)!important}}

.ge-click-card{{width:100%;border:1px solid #34413b!important;border-radius:16px!important;background:linear-gradient(145deg,#111714,#0c100f)!important;padding:12px!important;text-align:center!important;min-height:250px!important;color:#f4f5f4!important}}
.ge-click-card:hover{{border-color:#ffd43d!important}}
.ge-click-card p{{white-space:pre-line!important;font-weight:900!important}}
.org-wrap{{background:linear-gradient(145deg,#111714,#0c100f);border:1px solid #34413b;border-radius:16px;padding:24px;overflow:auto;min-height:560px}}
.org-title{{text-align:center;color:#ffd43d;font-weight:900;font-size:16px;text-transform:uppercase;margin-bottom:24px}}.org-tree{{display:flex;flex-direction:column;align-items:center;min-width:max-content;padding:8px 24px 30px}}
.org-node{{width:190px;background:#121816;border:1px solid #4b5852;border-radius:12px;padding:10px;text-align:center;box-shadow:0 8px 22px rgba(0,0,0,.16)}}.org-node.root{{border:2px solid #ffd43d}}.org-node img,.org-node .fallback{{width:58px;height:58px;border-radius:50%;object-fit:cover;border:2px solid #ffd43d;margin:0 auto 7px;display:block}}.org-node .fallback{{background:#26302b;color:#ffd43d;display:flex;align-items:center;justify-content:center;font-weight:900}}.org-node strong{{display:block;color:#f4f5f4;font-size:12px;text-transform:uppercase}}.org-node small{{display:block;color:#9aa39f;font-size:10px;margin-top:4px}}.org-connector{{height:22px;border-left:2px solid #626d67}}.org-children{{display:flex;gap:28px;align-items:flex-start;justify-content:center;position:relative}}.org-children::before{{content:"";position:absolute;top:0;left:calc(10%);right:calc(10%);border-top:2px solid #626d67}}.org-child{{display:flex;flex-direction:column;align-items:center;position:relative}}.org-child::before{{content:"";height:18px;border-left:2px solid #626d67}}.org-grandchildren{{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin-top:12px}}
.meta-card{{background:#0d1210;border:1px solid #29332f;border-radius:12px;padding:14px;margin-top:10px}}.meta-head{{display:flex;justify-content:space-between;gap:10px;align-items:center}}.meta-name{{font-size:13px;font-weight:900;color:#f4f5f4;text-transform:uppercase}}.meta-value{{font-size:13px;font-weight:900;color:#ffd43d}}.meta-progress{{height:7px;background:#202925;border-radius:99px;overflow:hidden;margin-top:9px}}.meta-progress span{{display:block;height:100%;background:#ffd43d;border-radius:99px}}
[data-testid="stTabs"] button{{font-size:14px!important;font-weight:800!important;padding:10px 16px!important}}
@media(max-width:900px){{.ge-team-grid{{grid-template-columns:1fr}}.org-children{{gap:14px}}.org-children::before{{display:none}}}}

/* FIX LAYOUT V2 */
[data-testid="stVerticalBlockBorderWrapper"]{{min-height:0!important;height:auto!important;align-self:flex-start!important}}
[data-testid="stVerticalBlockBorderWrapper"]>div{{min-height:0!important;height:auto!important}}
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlock"]{{min-height:0!important;height:auto!important}}
.ge-colab-card{{width:100%!important;min-height:0!important;height:auto!important;margin:0!important;padding:0!important;border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;overflow:visible!important}}
.ge-colab-photo-wrap{{width:96px!important;height:96px!important;margin:0 auto 12px!important;display:flex!important;align-items:center!important;justify-content:center!important;border:2px solid #ffd43d!important;border-radius:50%!important;background:#171d19!important;overflow:hidden!important;box-shadow:0 0 0 5px rgba(255,212,61,.07)!important}}
.ge-colab-photo-wrap img,.ge-colab-photo-wrap .ge-avatar-img,.ge-colab-photo-wrap .ge-avatar-fallback{{width:92px!important;height:92px!important;min-width:92px!important;min-height:92px!important;border-radius:50%!important;object-fit:cover!important;display:block!important}}
.ge-colab-name,.ge-colab-role,.ge-colab-team,.ge-colab-meta{{width:100%!important;text-align:center!important;white-space:normal!important;overflow-wrap:anywhere!important;display:flex!important;align-items:center!important;justify-content:center!important}}
.ge-colab-name{{min-height:36px!important;font-size:13px!important;line-height:1.25!important;font-weight:900!important;color:#f4f5f4!important;text-transform:uppercase!important}}
.ge-colab-role{{min-height:30px!important;margin-top:4px!important;font-size:11px!important;line-height:1.3!important;font-weight:900!important;color:#ffd43d!important;text-transform:uppercase!important}}
.ge-colab-team{{min-height:28px!important;margin-top:3px!important;font-size:10px!important;line-height:1.3!important;color:#a3ada8!important;text-transform:uppercase!important}}
.ge-colab-meta{{min-height:16px!important;margin-top:4px!important;font-size:10px!important;line-height:1.3!important;color:#7f8b85!important}}
.ge-colab-select{{width:100%!important;margin-top:9px!important}}.ge-colab-select button{{width:100%!important;height:34px!important;min-height:34px!important;border-radius:9px!important;font-size:10px!important;font-weight:900!important;text-transform:uppercase!important}}

.org-wrap{{width:100%!important;box-sizing:border-box!important;background:linear-gradient(145deg,#111714,#0b0f0e)!important;border:1px solid #34413b!important;border-radius:18px!important;padding:22px!important;min-height:0!important;height:auto!important;box-shadow:0 14px 40px rgba(0,0,0,.18)!important;overflow:hidden!important}}
.org-wrap .org-title{{font-size:18px!important;line-height:1.25!important;font-weight:900!important;color:#ffd43d!important;text-align:center!important;text-transform:uppercase!important;margin:0 0 10px!important}}
.org-wrap .org-title-sub{{font-size:11px!important;line-height:1.35!important;color:#8f9994!important;text-align:center!important;margin:0 0 18px!important}}
.org-tree{{display:flex!important;flex-direction:column!important;align-items:center!important;width:max-content!important;min-width:100%!important;padding:8px 24px 30px!important;box-sizing:border-box!important}}
.org-node{{width:190px!important;min-width:190px!important;min-height:112px!important;box-sizing:border-box!important;padding:10px 9px 9px!important;border:1px solid #3b4741!important;border-radius:12px!important;background:#121816!important;display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:flex-start!important;position:relative!important;overflow:hidden!important}}
.org-node.root{{border:2px solid #ffd43d!important;background:#18201c!important}}
.org-node img,.org-node .ge-avatar-fallback,.org-node .fallback{{width:52px!important;height:52px!important;min-width:52px!important;min-height:52px!important;border-radius:50%!important;object-fit:cover!important;border:2px solid #ffd43d!important;display:block!important;margin:0 auto 6px!important}}
.org-node strong{{display:block!important;width:100%!important;max-width:170px!important;margin:0!important;font-size:9px!important;line-height:1.22!important;font-weight:900!important;color:#f4f5f4!important;text-transform:uppercase!important;text-align:center!important;white-space:normal!important;overflow-wrap:anywhere!important}}
.org-node small{{display:block!important;width:100%!important;max-width:170px!important;margin:4px 0 0!important;font-size:8px!important;line-height:1.25!important;font-weight:800!important;color:#aeb7b2!important;text-transform:uppercase!important;text-align:center!important;white-space:normal!important;overflow-wrap:anywhere!important}}
.org-connector{{height:24px!important;width:2px!important;background:#4a5750!important;border:0!important;margin:0!important;flex:0 0 24px!important}}
.org-children{{display:flex!important;flex-direction:row!important;align-items:flex-start!important;justify-content:center!important;gap:26px!important;width:max-content!important;min-width:0!important;position:relative!important;padding-top:30px!important;margin:0!important}}
.org-children::before{{content:""!important;position:absolute!important;top:14px!important;left:95px!important;right:95px!important;height:2px!important;border:0!important;background:#4a5750!important}}
.org-child{{width:190px!important;min-width:190px!important;position:relative!important;display:flex!important;flex-direction:column!important;align-items:center!important;padding-top:14px!important;box-sizing:border-box!important}}
.org-child::before{{content:""!important;position:absolute!important;top:-16px!important;left:50%!important;width:2px!important;height:16px!important;background:#4a5750!important;border:0!important;transform:translateX(-50%)!important}}
.org-child>.org-connector{{display:none!important}}
.org-child>.org-children{{margin-top:0!important}}
.org-children .org-child:only-child::before{{display:block!important}}
@media(max-width:900px){{.org-children{{gap:12px!important}}.org-children::before{{left:95px!important;right:95px!important}}}}

</style>
""",unsafe_allow_html=True)

@st.cache_data(ttl=120,show_spinner=False)
def rows(table,limit=500,order=None,desc=True,columns="*"):
    q=get_client().table(table).select(columns)
    if order:q=q.order(order,desc=desc)
    return q.limit(limit).execute().data or []
def df(data):return pd.DataFrame(data) if data else pd.DataFrame()

def foto_html(c,tamanho=72):
    if not c:return '<div class="ge-avatar-fallback">?</div>'
    b64=c.get("foto_base64");mime=c.get("foto_mime") or "image/jpeg"
    if b64:return f'<img class="ge-avatar-img" style="width:{tamanho}px;height:{tamanho}px" src="data:{mime};base64,{b64}">'
    nome=(c.get("nome") or "?").strip();return f'<div class="ge-avatar-fallback" style="width:{tamanho}px;height:{tamanho}px">{nome[:1].upper()}</div>'

def nome_curto(nome):
    p=[x for x in (nome or "").split() if x];return " ".join(p[:2]) if p else "Sem nome"

def registrar_historico(tipo,descricao,dados):
    try:get_client().table("almox_historico").insert({"tipo":tipo,"descricao":descricao,"dados":dados}).execute()
    except Exception:pass
    finally:rows.clear()

def save_global_config():
    ok=salvar_configuracoes(config)
    if ok:st.session_state.config=config
    return ok

def vincular_colaborador_a_equipe(cid,eid,nome=None):
    dados={"equipe_id":eid,"equipe_atual":nome}
    resultado=get_client().table("almox_colaboradores").update(dados).eq("id",cid).execute()
    rows.clear()
    return resultado

if "pagina" not in st.session_state:st.session_state.pagina="equipes"
paginas_ids=["indicadores","historico","equipes","configuracoes"]
if st.session_state.pagina=="carreira":st.session_state.pagina="equipes"
with st.sidebar:
    logo_b64=config.get("logo_base64")
    if logo_b64:
        logo_bg=detectar_cor_fundo_logo(logo_b64,config.get("logo_mime"));logo_border="#555555"
        logo_html=f'<img class="sidebar-logo-img" src="data:{config.get("logo_mime") or "image/png"};base64,{logo_b64}" alt="Logo SETTA">'
    else:logo_bg="#101513" if not IS_LIGHT else "#ffffff";logo_border="#e5e7eb";logo_html='<div class="sidebar-logo-placeholder">SUA LOGO AQUI<br>Configure em Configurações</div>'
    st.markdown(f'<div class="sidebar-logo-section"><div class="sidebar-logo-wrap" style="--logo-bg:{logo_bg};--logo-border:{logo_border};">{logo_html}</div></div>',unsafe_allow_html=True)
    for _id in paginas_ids:
        ativo=st.session_state.pagina==_id
        if st.button(txt("menu_"+_id).upper(),use_container_width=True,type="primary" if ativo else "secondary",key=f"menu_{_id}"):
            st.session_state.pagina=_id;st.rerun()
    st.markdown(f"<div class='sidebar-footer'>{txt('rodape_linha1')}<br>{txt('rodape_linha2')}</div>",unsafe_allow_html=True)

pagina=st.session_state.pagina
MESES_PT=["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"]
competencia_atual=f"{MESES_PT[date.today().month-1]}/{date.today().strftime('%y')}".upper()
st.markdown(f'<div class="hero"><div><h1>{txt("titulo_"+pagina)}</h1><p>{txt("subtitulo_global")}</p></div><div class="period">{competencia_atual}</div></div>',unsafe_allow_html=True)

try:get_client();conectado=True
except Exception:conectado=False
if not conectado:st.error("Supabase ainda não está configurado no ambiente do Streamlit.");st.stop()

if pagina=="indicadores":
    indicadores=rows("almox_indicadores",order="competencia")
    render_indicadores(indicadores)
elif pagina=="historico":
    st.markdown(f'<div class="section">{txt("secao_historico")}</div>',unsafe_allow_html=True);_setta_dataframe(df(rows("almox_historico",order="criado_em")),use_container_width=True,hide_index=True)

elif pagina=="equipes":
    client=get_client();equipes_raw=rows("almox_equipes",order="nome",desc=False);colaboradores_raw=rows("almox_colaboradores",order="nome",desc=False)
    ativos_equipes=[x for x in equipes_raw if x.get("ativo",True)];ativos_colaboradores=[x for x in colaboradores_raw if x.get("ativo",True)]
    nomes_colab={str(x.get("id")):x for x in colaboradores_raw};nomes_eq={str(x.get("id")):x.get("nome","") for x in equipes_raw}
    k1,k2,k3,k4=st.columns(4)
    k1.markdown(f'<div class="ge-kpi"><div class="label">Equipes ativas</div><div class="value">{len(ativos_equipes)}</div><div class="sub">Estrutura operacional</div></div>',unsafe_allow_html=True)
    k2.markdown(f'<div class="ge-kpi"><div class="label">Colaboradores ativos</div><div class="value">{len(ativos_colaboradores)}</div><div class="sub">Base atual</div></div>',unsafe_allow_html=True)
    k3.markdown(f'<div class="ge-kpi"><div class="label">Colaboradores alocados</div><div class="value">{sum(1 for x in ativos_colaboradores if x.get("equipe_id"))}</div><div class="sub">Com equipe definida</div></div>',unsafe_allow_html=True)
    k4.markdown(f'<div class="ge-kpi"><div class="label">Sem equipe</div><div class="value">{sum(1 for x in ativos_colaboradores if not x.get("equipe_id"))}</div><div class="sub">Aguardando alocação</div></div>',unsafe_allow_html=True)

    tab_geral,tab_organograma,tab_equipes,tab_colaboradores,tab_carreira=st.tabs(["VISÃO GERAL","ORGANOGRAMA","EQUIPES","COLABORADORES",txt("titulo_carreira").upper()])

    @st.dialog("Criar equipe")
    def dialog_nova_equipe():
        with st.form("dialog_form_nova_equipe",clear_on_submit=True):
            nome=st.text_input("Nome da equipe *",placeholder="Ex.: Abastecimento de Produção");objetivo=st.text_area("Objetivo",height=90);resp_opts={"Nenhum":None}
            for c in ativos_colaboradores:resp_opts[f"{c.get('nome','')} — {c.get('funcao') or 'Sem função'}"]=c.get("id")
            resp=st.selectbox("Responsável",list(resp_opts.keys()));tarefas=st.text_area("Tarefas principais — uma por linha",height=90);ok=st.form_submit_button("CRIAR EQUIPE",type="primary",use_container_width=True)
        if ok:
            nome=nome.strip()
            if not nome:st.error("Informe o nome da equipe.");return
            if any((x.get("nome") or "").strip().casefold()==nome.casefold() and x.get("ativo",True) for x in equipes_raw):st.error("Já existe uma equipe ativa com esse nome.");return
            try:
                novo=client.table("almox_equipes").insert({"nome":nome,"objetivo":objetivo.strip() or None,"tarefas":[x.strip() for x in tarefas.splitlines() if x.strip()],"responsavel_id":resp_opts[resp],"ativo":True}).execute().data
                if novo:
                    eid=novo[0].get("id");rid=resp_opts[resp]
                    if rid:vincular_colaborador_a_equipe(rid,eid,nome)
                    registrar_historico("equipe_criada",f"Equipe criada: {nome}",{"equipe_id":eid,"nome":nome,"responsavel_id":rid});st.success(f"Equipe '{nome}' criada com sucesso.");st.rerun()
            except Exception as e:st.error(f"Erro ao criar equipe: {e}")

    @st.dialog("Cadastrar colaborador")
    def dialog_novo_colaborador():
        equipes_opts={x.get("nome",""):x.get("id") for x in ativos_equipes}
        with st.form("dialog_form_novo_colaborador",clear_on_submit=True):
            c1,c2=st.columns(2)
            with c1:nome=st.text_input("Nome completo *");mat=st.text_input("Matrícula");func=st.text_input("Função / cargo")
            with c2:data_adm=st.date_input("Data de admissão",value=None,format="DD/MM/YYYY");eq_label=st.selectbox("Equipe",["Sem equipe"]+list(equipes_opts.keys()));foto=st.file_uploader("Foto do colaborador *",type=["png","jpg","jpeg","webp"])
            ok=st.form_submit_button("CADASTRAR COLABORADOR",type="primary",use_container_width=True)
        if ok:
            nome=nome.strip();mat=mat.strip() or None
            if not nome:st.error("Informe o nome do colaborador.");return
            if mat and any((x.get("matricula") or "").strip().casefold()==mat.casefold() for x in colaboradores_raw):st.error("Essa matrícula já está cadastrada.");return
            if foto is None:st.error("Inclua a foto do colaborador.");return
            if foto.size>2*1024*1024:st.error("A foto deve ter no máximo 2 MB.");return
            try:
                eqid=equipes_opts.get(eq_label);b64=base64.b64encode(foto.getvalue()).decode("ascii")
                novo=client.table("almox_colaboradores").insert({"nome":nome,"matricula":mat,"funcao":func.strip() or None,"data_admissao":data_adm.isoformat() if data_adm else None,"equipe_id":eqid,"equipe_atual":eq_label if eqid else None,"foto_base64":b64,"foto_mime":foto.type or "image/jpeg","foto_nome":foto.name,"ativo":True}).execute().data
                if novo:registrar_historico("colaborador_criado",f"Colaborador criado: {nome}",{"colaborador_id":novo[0].get("id"),"nome":nome,"equipe_id":eqid});st.success(f"Colaborador '{nome}' cadastrado com sucesso.");st.rerun()
            except Exception as e:st.error(f"Erro ao cadastrar colaborador: {e}")

    @st.dialog("Metas da equipe")
    def dialog_metas_equipe(equipe):
        eid=str(equipe.get("id"));metas=list(config.get("metas_equipes",{}).get(eid,[]))
        st.caption(f"Equipe: {equipe.get('nome','')}. Cadastre metas mensuráveis para acompanhar a execução.")
        if metas:
            for i,m in enumerate(metas):
                a,b,c=st.columns([2,1,1]);a.write(f"**{m.get('nome','Meta')}**");b.write(f"Meta: **{m.get('valor','—')} {m.get('unidade','')}**");c.write(f"Prazo: **{m.get('prazo','—')}**")
                if st.button("EXCLUIR",key=f"del_meta_{eid}_{i}"):
                    metas.pop(i);config["metas_equipes"][eid]=metas;save_global_config();st.rerun()
        with st.form(f"form_meta_{eid}"):
            st.markdown("**NOVA META**");a,b,c=st.columns([2,1,1]);nome=a.text_input("Descrição da meta");valor=b.number_input("Valor",min_value=0.0,step=1.0);unidade=c.text_input("Unidade",placeholder="% / pedidos / horas");prazo=st.date_input("Prazo",value=date.today(),format="DD/MM/YYYY");ok=st.form_submit_button("ADICIONAR META",type="primary",use_container_width=True)
        if ok:
            if not nome.strip():st.error("Informe a descrição da meta.");return
            metas.append({"nome":nome.strip(),"valor":valor,"unidade":unidade.strip(),"prazo":prazo.strftime("%d/%m/%Y"),"criada_em":date.today().isoformat()});config["metas_equipes"][eid]=metas
            if save_global_config():st.success("Meta adicionada.");st.rerun()
            else:st.error("Não foi possível salvar a meta.")

    with tab_geral:
        st.markdown('<div class="ge-tabs-note">Visão consolidada da estrutura do almoxarifado, equipes, pessoas e distribuição atual.</div>',unsafe_allow_html=True);a,b=st.columns([1.15,.85])
        max_equipes=max([sum(1 for c in ativos_colaboradores if str(c.get("equipe_id"))==str(e.get("id"))) for e in ativos_equipes] or [1])
        with a:
            st.markdown('<div class="ge-overview-panel"><div class="ge-panel-title">ESTRUTURA DAS EQUIPES</div>',unsafe_allow_html=True)
            for idx,e in enumerate(ativos_equipes,1):
                n=sum(1 for c in ativos_colaboradores if str(c.get("equipe_id"))==str(e.get("id")));tarefas=len(e.get("tarefas") or []);pct_bar=round(n/max_equipes*100,1) if max_equipes else 0
                st.markdown(f'<div class="ge-mini"><div class="rank">{idx:02d}</div><div class="main"><div class="name">{e.get("nome","")}</div><div class="desc">{n} colaborador(es) · {tarefas} tarefa(s)</div><div class="ge-bar"><span style="width:{pct_bar}%"></span></div></div></div>',unsafe_allow_html=True)
            if not ativos_equipes:st.markdown('<div class="ge-empty">Nenhuma equipe ativa cadastrada.</div>',unsafe_allow_html=True)
            st.markdown('</div>',unsafe_allow_html=True)
        with b:
            funcoes=Counter((c.get("funcao") or "Sem função").strip().upper() for c in ativos_colaboradores);max_func=max(funcoes.values() or [1])
            st.markdown('<div class="ge-overview-panel"><div class="ge-panel-title">RESUMO DE PESSOAS</div>',unsafe_allow_html=True)
            for f,n in sorted(funcoes.items(),key=lambda x:(-x[1],x[0]))[:7]:
                pct_bar=round(n/max_func*100,1);st.markdown(f'<div class="ge-mini"><div class="main"><div class="name">{f}</div><div class="desc">{n} colaborador(es)</div><div class="ge-bar"><span style="width:{pct_bar}%"></span></div></div></div>',unsafe_allow_html=True)
            st.markdown('</div>',unsafe_allow_html=True)
        sem=[c for c in ativos_colaboradores if not c.get("equipe_id")]
        if sem:st.markdown(f'<div class="ge-alert"><b>Alocação pendente:</b> {len(sem)} colaborador(es) ativo(s) ainda estão sem equipe definida.</div>',unsafe_allow_html=True)
        recentes=sorted([c for c in ativos_colaboradores if c.get("data_admissao")],key=lambda x:x.get("data_admissao") or "",reverse=True)[:5]
        cards=[]
        for c in recentes:
            dt=pd.to_datetime(c.get("data_admissao"),errors="coerce")
            data_txt=dt.strftime("%d/%m/%Y") if not pd.isna(dt) else "—"
            cards.append(f'<div class="ge-admissao-card"><div class="ge-admissao-photo">{foto_html(c,60)}</div><div class="ge-admissao-name">{nome_curto(c.get("nome"))}</div><div class="ge-admissao-date">{data_txt}</div></div>')
        if cards:st.markdown('<div class="panel ge-admissoes-panel"><div class="ge-panel-title">ADMISSÕES MAIS RECENTES</div><div class="ge-admissoes-grid">'+''.join(cards)+'</div></div>',unsafe_allow_html=True)

    with tab_equipes:
        st.markdown('<div class="ge-tabs-note">Cada equipe aparece como um painel operacional, com objetivo, responsável, tarefas, integrantes e metas.</div>',unsafe_allow_html=True)
        if st.button("CRIAR EQUIPE",type="primary",use_container_width=True,key="abrir_nova_equipe"):dialog_nova_equipe()
        filtro=st.text_input("Buscar equipe",placeholder="Digite parte do nome...",key="busca_equipe");exib=[x for x in equipes_raw if filtro.strip().casefold() in (x.get("nome") or "").casefold()]
        for e in exib:
            eid=str(e.get("id"));membros=[c for c in ativos_colaboradores if str(c.get("equipe_id"))==eid];resp=nomes_colab.get(str(e.get("responsavel_id")));metas=config.get("metas_equipes",{}).get(eid,[])
            resp_html=f'{foto_html(resp,48)}<div><div class="ge-member-name">{resp.get("nome","")}</div><div class="ge-member-role">{resp.get("funcao") or "Sem função"}</div></div>' if resp else '<div class="ge-empty">Nenhum responsável definido.</div>'
            tarefas=''.join(f'<div class="ge-task">{t}</div>' for t in (e.get("tarefas") or [])) or '<div class="ge-empty">Nenhuma tarefa cadastrada.</div>'
            membros_html=''.join(f'<div class="ge-member">{foto_html(c,40)}<div><div class="ge-member-name">{c.get("nome","")}</div><div class="ge-member-role">{c.get("funcao") or "Sem função"}</div></div></div>' for c in membros) or '<div class="ge-empty">Nenhum colaborador associado a esta equipe.</div>'
            st.markdown(f'<div class="ge-team-card"><div class="ge-team-head"><div><div class="ge-team-title"><span>●</span> {e.get("nome","")}</div><div class="ge-team-objective">{e.get("objetivo") or "Objetivo não informado."}</div></div><div class="ge-team-status">{"ATIVA" if e.get("ativo",True) else "INATIVA"}</div></div><div class="ge-team-grid"><div class="ge-info-box"><div class="ge-info-label">Responsável</div><div style="margin-top:10px;display:flex;align-items:center;gap:11px">{resp_html}</div></div><div class="ge-info-box"><div class="ge-info-label">Integrantes</div><div class="ge-info-value">{len(membros)} colaborador(es)</div><div class="ge-info-label" style="margin-top:14px">Tarefas</div><div class="ge-info-value">{len(e.get("tarefas") or [])} atividade(s)</div></div><div class="ge-info-box"><div class="ge-info-label">Tarefas principais</div>{tarefas}</div></div><div class="ge-info-box" style="margin-top:14px"><div class="ge-info-label">EQUIPE</div>{membros_html}</div></div>',unsafe_allow_html=True)
            a,b,c,d=st.columns(4)
            with a:
                if st.button("ADICIONAR COLABORADOR",key=f"add_{eid}",use_container_width=True):
                    disponiveis=[c for c in ativos_colaboradores if str(c.get("equipe_id"))!=eid]
                    opts={f"{c.get('nome','')} — {c.get('matricula') or 'sem matrícula'}":c.get("id") for c in disponiveis}
                    with st.popover(f"ADICIONAR COLABORADORES — {e.get('nome','')}"):
                        with st.form(f"form_add_{eid}"):
                            sel=st.multiselect("Colaboradores",list(opts.keys()));ok=st.form_submit_button("ADICIONAR",type="primary",use_container_width=True)
                        if ok:
                            if not sel:st.warning("Selecione pelo menos um colaborador.")
                            else:
                                for label in sel:vincular_colaborador_a_equipe(opts[label],eid,e.get("nome"))
                                registrar_historico("colaboradores_equipe",f"Colaboradores adicionados: {e.get('nome','')}",{"equipe_id":eid,"colaborador_ids":[opts[x] for x in sel]});st.rerun()
            with b:
                if st.button("METAS DA EQUIPE",key=f"metas_{eid}",use_container_width=True):dialog_metas_equipe(e)
            with c:
                if st.button("DEFINIR RESPONSÁVEL",key=f"resp_{eid}",use_container_width=True):
                    opts={"Nenhum":None};
                    for col in ativos_colaboradores:opts[f"{col.get('nome','')} — {col.get('funcao') or 'Sem função'}"]=col.get("id")
                    with st.popover(f"RESPONSÁVEL — {e.get('nome','')}"):
                        with st.form(f"form_resp_{eid}"):
                            labels=list(opts.keys());atual=e.get("responsavel_id");idx=[str(x) for x in opts.values()].index(str(atual)) if atual is not None and str(atual) in [str(x) for x in opts.values()] else 0;escolha=st.selectbox("Responsável",labels,index=idx);ok=st.form_submit_button("VINCULAR RESPONSÁVEL",type="primary",use_container_width=True)
                        if ok:
                            rid=opts[escolha];client.table("almox_equipes").update({"responsavel_id":rid}).eq("id",eid).execute()
                            if rid:vincular_colaborador_a_equipe(rid,eid,e.get("nome"))
                            registrar_historico("equipe_responsavel",f"Responsável definido: {e.get('nome','')}",{"equipe_id":eid,"responsavel_id":rid});st.rerun()
            with d:
                if st.button("EDITAR EQUIPE",key=f"edit_{eid}",use_container_width=True):st.session_state["editar_equipe_id"]=eid;st.rerun()
            if metas:
                for m in metas:st.markdown(f'<div class="meta-card"><div class="meta-head"><div class="meta-name">META · {m.get("nome","")}</div><div class="meta-value">{m.get("valor","—")} {m.get("unidade","")}</div></div><div class="muted" style="font-size:11px;margin-top:5px">Prazo: {m.get("prazo","—")}</div></div>',unsafe_allow_html=True)
            if st.session_state.get("editar_equipe_id")==eid:
                with st.form(f"form_edit_{eid}"):
                    nome_e=st.text_input("Nome",value=e.get("nome") or "");obj_e=st.text_area("Objetivo",value=e.get("objetivo") or "",height=90);tarefas_e=st.text_area("Tarefas — uma por linha",value="\n".join(e.get("tarefas") or []),height=90);ok=st.form_submit_button("SALVAR ALTERAÇÕES",type="primary")
                if ok:
                    try:
                        client.table("almox_equipes").update({"nome":nome_e.strip(),"objetivo":obj_e.strip() or None,"tarefas":[x.strip() for x in tarefas_e.splitlines() if x.strip()]}).eq("id",eid).execute();st.session_state.pop("editar_equipe_id",None);st.rerun()
                    except Exception as ex:st.error(f"Erro ao editar equipe: {ex}")

    with tab_colaboradores:
        st.markdown('<div class="ge-tabs-note">Clique diretamente no cartão do colaborador para selecioná-lo. A seleção controla as ações abaixo.</div>',unsafe_allow_html=True)
        if st.button("CADASTRAR COLABORADOR",type="primary",use_container_width=True,key="abrir_novo_colaborador"):dialog_novo_colaborador()
        cf1,cf2=st.columns([2,1]);
        with cf1:busca=st.text_input("Buscar colaborador",placeholder="Nome, matrícula ou função...",key="busca_colab")
        with cf2:status=st.selectbox("Status",["Todos","Ativos","Inativos"],key="filtro_status_colab")
        filtrados=[]
        for c in colaboradores_raw:
            texto=" ".join([str(c.get("nome") or ""),str(c.get("matricula") or ""),str(c.get("funcao") or "")]).casefold();ok=status=="Todos" or (status=="Ativos" and c.get("ativo",True)) or (status=="Inativos" and not c.get("ativo",True))
            if busca.strip().casefold() in texto and ok:filtrados.append(c)
        if filtrados:
            selected_id=st.session_state.get("colab_selecionado")
            for base in range(0,len(filtrados),5):
                cols=st.columns(5,gap="medium")
                for col,c in zip(cols,filtrados[base:base+5]):
                    cid=str(c.get("id"));selecionado=selected_id==cid
                    nome=(c.get("nome") or "Sem nome").upper()
                    func=(c.get("funcao") or "SEM FUNÇÃO").upper()
                    equipe=nomes_eq.get(str(c.get("equipe_id")),c.get("equipe_atual") or "SEM EQUIPE")
                    status_txt="ATIVO" if c.get("ativo",True) else "INATIVO"
                    matricula=str(c.get("matricula") or "SEM MATRÍCULA")
                    with col:
                        with st.container(border=True):
                            card_cls="ge-colab-card selected" if selecionado else "ge-colab-card"
                            st.markdown(f'<div class="{card_cls}"><div class="ge-colab-photo-wrap">{foto_html(c,92)}</div><div class="ge-colab-name">{nome}</div><div class="ge-colab-role">{func}</div><div class="ge-colab-team">{equipe.upper()}</div><div class="ge-colab-meta">{status_txt} · {matricula}</div></div>',unsafe_allow_html=True)
                            if st.button("SELECIONADO" if selecionado else "SELECIONAR",key=f"card_colab_{cid}",use_container_width=True,type="primary" if selecionado else "secondary"):
                                st.session_state.colab_selecionado=cid
                                st.rerun()
        else:
            st.info("Nenhum colaborador encontrado.")
        escolhido=next((c for c in filtrados if str(c.get("id"))==str(st.session_state.get("colab_selecionado"))),None)
        if escolhido:
            cid=str(escolhido.get("id"));a,b=st.columns(2)
            with a:
                if st.button("EDITAR COLABORADOR",key=f"editar_colab_{cid}",use_container_width=True):st.session_state["editar_colaborador_id"]=cid;st.rerun()
            with b:
                if st.button("INATIVAR COLABORADOR" if escolhido.get("ativo",True) else "REATIVAR COLABORADOR",key=f"status_colab_{cid}",use_container_width=True):
                    client.table("almox_colaboradores").update({"ativo":not escolhido.get("ativo",True)}).eq("id",cid).execute();registrar_historico("colaborador_status",f"Status alterado: {escolhido.get('nome','')}",{"colaborador_id":cid,"ativo":not escolhido.get("ativo",True)});st.rerun()
            if st.session_state.get("editar_colaborador_id")==cid:
                with st.form(f"form_editar_colab_{cid}"):
                    a,b=st.columns(2)
                    with a:nome_e=st.text_input("Nome completo",value=escolhido.get("nome") or "");mat_e=st.text_input("Matrícula",value=escolhido.get("matricula") or "");func_e=st.text_input("Função / cargo",value=escolhido.get("funcao") or "")
                    with b:
                        try:data_val=pd.to_datetime(escolhido.get("data_admissao")).date() if escolhido.get("data_admissao") else None
                        except Exception:data_val=None
                        data_e=st.date_input("Data de admissão",value=data_val,format="DD/MM/YYYY",key=f"data_edit_{cid}");eqs={"Sem equipe":None};eqs.update({x.get("nome",""):x.get("id") for x in ativos_equipes});labels=list(eqs.keys());atual=escolhido.get("equipe_id");idx=labels.index(nomes_eq.get(str(atual),"Sem equipe")) if nomes_eq.get(str(atual),"Sem equipe") in labels else 0;eq_e=st.selectbox("Equipe",labels,index=idx,key=f"eq_edit_{cid}");foto_e=st.file_uploader("Substituir foto",type=["png","jpg","jpeg","webp"],key=f"foto_edit_{cid}")
                    ok=st.form_submit_button("SALVAR COLABORADOR",type="primary")
                if ok:
                    mat_e=mat_e.strip() or None
                    if not nome_e.strip():st.error("O nome é obrigatório.")
                    elif mat_e and any(str(x.get("id"))!=cid and (x.get("matricula") or "").strip().casefold()==mat_e.casefold() for x in colaboradores_raw):st.error("Essa matrícula já está cadastrada para outro colaborador.")
                    else:
                        dados={"nome":nome_e.strip(),"matricula":mat_e,"funcao":func_e.strip() or None,"data_admissao":data_e.isoformat() if data_e else None,"equipe_id":eqs[eq_e],"equipe_atual":eq_e if eqs[eq_e] else None}
                        if foto_e is not None:
                            if foto_e.size>2*1024*1024:st.error("A foto deve ter no máximo 2 MB.");st.stop()
                            dados.update({"foto_base64":base64.b64encode(foto_e.getvalue()).decode("ascii"),"foto_mime":foto_e.type or "image/jpeg","foto_nome":foto_e.name})
                        client.table("almox_colaboradores").update(dados).eq("id",cid).execute();registrar_historico("colaborador_editado",f"Colaborador editado: {nome_e.strip()}",{"colaborador_id":cid,"nome":nome_e.strip()});st.session_state.pop("editar_colaborador_id",None);st.rerun()

    with tab_organograma:
        st.markdown('<div class="org-focus-head"><div class="org-focus-title">ORGANOGRAMA</div><div class="org-focus-sub">Estrutura hierárquica visual do almoxarifado.</div></div>',unsafe_allow_html=True)
        people=ativos_colaboradores
        ids=[str(c.get("id")) for c in people]
        byid={str(c.get("id")):c for c in people}
        orgcfg=config.get("organograma",{})
        saved_parents={str(k):str(v) for k,v in (orgcfg.get("pais") or {}).items()}
        saved_parents={k:v for k,v in saved_parents.items() if k in ids and v in ids and k!=v}
        saved_root=str(orgcfg.get("raiz_id")) if str(orgcfg.get("raiz_id")) in ids else (ids[0] if ids else "")
        if people:
            def org_node(c,isroot=False):
                photo=foto_html(c,52)
                cls=" root" if isroot else ""
                return f'<div class="org-node{cls}">{photo}<strong>{c.get("nome","")}</strong><small>{c.get("funcao") or "Sem função"}</small></div>'
            def build_children(parents,root_id):
                children=defaultdict(list)
                for cid in ids:
                    if cid==root_id: continue
                    pid=parents.get(cid,"")
                    if pid and pid in ids and pid!=cid: children[pid].append(cid)
                    else: children[root_id].append(cid)
                for pid in children: children[pid].sort(key=lambda x:(byid[x].get("nome") or "").upper())
                return children
            def render_children(pid,children):
                kids=children.get(pid,[])
                if not kids: return ""
                return '<div class="org-children">'+''.join(f'<div class="org-child">{org_node(byid[kid])}{render_children(kid,children)}</div>' for kid in kids)+'</div>'
            children=build_children(saved_parents,saved_root)
            tree_html=org_node(byid[saved_root],True)+'<div class="org-connector"></div>'+render_children(saved_root,children)
            st.markdown(f'<div class="org-wrap org-main"><div class="org-title">{orgcfg.get("titulo") or "ORGANOGRAMA DO ALMOXARIFADO"}</div><div class="org-title-sub">Visualização hierárquica por níveis e subordinados.</div><div class="org-tree">{tree_html}</div></div>',unsafe_allow_html=True)
            with st.expander("⚙ AJUSTAR ORGANOGRAMA",expanded=False):
                root_opts={f"{c.get('nome','')} — {(c.get('funcao') or 'Sem função').upper()}":str(c.get('id')) for c in people}
                root_labels=list(root_opts.keys());root_index=list(root_opts.values()).index(saved_root) if saved_root in root_opts.values() else 0
                titulo_org=st.text_input("Título",value=orgcfg.get("titulo") or "ORGANOGRAMA DO ALMOXARIFADO",key="org_titulo_ajuste")
                root_label=st.selectbox("Responsável / raiz",root_labels,index=root_index,key="org_root_ajuste")
                draft_root=str(root_opts[root_label]);st.caption("Defina o superior imediato de cada colaborador. A visualização principal mostra somente o organograma.")
                novas_pais={}
                for c in people:
                    cid=str(c.get("id"));options={"NÍVEL 1 — RAIZ":""}
                    for superior in people:
                        sid=str(superior.get("id"))
                        if sid!=cid:options[f"{nome_curto(superior.get('nome'))} — {(superior.get('funcao') or 'Sem função').upper()}"]=sid
                    atual=saved_parents.get(cid,"");vals=list(options.values());idx=vals.index(atual) if atual in vals else 0
                    escolha=st.selectbox(f"{c.get('nome','')} · {c.get('funcao') or 'Sem função'}",list(options.keys()),index=idx,key=f"org_parent_ajuste_{cid}")
                    novas_pais[cid]=options[escolha]
                def has_cycle(start_id,parents):
                    seen=set();cur=start_id
                    while cur:
                        if cur in seen:return True
                        seen.add(cur);cur=parents.get(cur,"")
                    return False
                for cid in list(novas_pais):
                    if cid==draft_root: novas_pais[cid]=""
                    elif has_cycle(cid,novas_pais): novas_pais[cid]=""
                if st.button("SALVAR ORGANOGRAMA",type="primary",use_container_width=True,key="salvar_org_ajuste"):
                    config["organograma"]={"titulo":titulo_org.strip() or "ORGANOGRAMA DO ALMOXARIFADO","raiz_id":draft_root,"pais":novas_pais}
                    if save_global_config(): registrar_historico("organograma_atualizado","Organograma atualizado",{"raiz_id":draft_root,"colaboradores":len(people)});st.rerun()
                    else: st.error("Não foi possível salvar o organograma.")
        else: st.info("Cadastre colaboradores ativos para montar o organograma.")
    with tab_carreira:
        st.markdown(f'<div class="section">{txt("secao_plano")}</div>',unsafe_allow_html=True);st.info(txt("mensagem_carreira"))
elif pagina=="configuracoes":
    st.markdown(f'<div class="section">{txt("secao_configuracoes")}</div>',unsafe_allow_html=True);st.write("As configurações ficam salvas no Supabase e são carregadas novamente quando o aplicativo abre.")
    st.markdown("### Personalização de textos e fontes");novo_textos=textos.copy();novo_fontes=fontes.copy();a,b=st.columns(2)
    with a:
        for _id,_rot in [("indicadores","Alimentar Indicadores"),("historico","Histórico"),("equipes","Gestão de Equipes"),("configuracoes","Configurações")]:novo_textos["menu_"+_id]=st.text_input("Menu: "+_rot,value=textos["menu_"+_id],key="edit_menu_"+_id)
        for _id,_rot in [("indicadores","Alimentar Indicadores"),("historico","Histórico"),("equipes","Gestão de Equipes"),("carreira","Plano de Carreira"),("configuracoes","Configurações")]:novo_textos["titulo_"+_id]=st.text_input("Título: "+_rot,value=textos["titulo_"+_id],key="edit_titulo_"+_id)
    with b:
        novo_textos["subtitulo_global"]=st.text_input("Subtítulo principal",value=textos["subtitulo_global"],key="edit_subtitulo")
        fi={x:i for i,x in enumerate(FONTES)};novo_fontes["menu"]=st.selectbox("Fonte dos menus",FONTES,index=fi.get(fontes.get("menu"),0));novo_fontes["titulo"]=st.selectbox("Fonte dos títulos",FONTES,index=fi.get(fontes.get("titulo"),0));novo_fontes["subtitulo"]=st.selectbox("Fonte dos subtítulos",FONTES,index=fi.get(fontes.get("subtitulo"),0))
    if st.button("SALVAR TEXTOS E FONTES",type="primary",use_container_width=True):
        config["textos"]=novo_textos;config["fontes"]=novo_fontes
        if save_global_config():st.success("Textos e fontes salvos.");st.rerun()
        else:st.error("Não foi possível salvar.")
    st.markdown("---");st.markdown("### Aparência");a,b=st.columns(2)
    with a:
        tema=st.selectbox("Tema",TEMAS,index=TEMAS.index(config.get("tema",TEMAS[0])));cor=st.selectbox("Cor principal",list(CORES.keys()),index=list(CORES.keys()).index(config.get("cor_principal","Amarelo (Padrão)")));estilo=st.radio("Estilo dos botões",ESTILOS_BOTOES,index=ESTILOS_BOTOES.index(config.get("estilo_botoes","Amarelo")))
        if st.button("SALVAR CONFIGURAÇÕES",type="primary",use_container_width=True):
            config.update({"tema":tema,"cor_principal":cor,"estilo_botoes":estilo})
            if save_global_config():st.success("Configurações salvas.");st.rerun()
    with b:
        arquivo=st.file_uploader("Logo da empresa",type=["png","jpg","jpeg","svg"])
        if arquivo is not None and arquivo.size<=2*1024*1024:
            config.update({"logo_base64":base64.b64encode(arquivo.getvalue()).decode("ascii"),"logo_name":arquivo.name,"logo_mime":arquivo.type or "image/png"})
            if save_global_config():st.success("Logo salva.");st.rerun()
        elif arquivo is not None:st.error("A logo deve ter no máximo 2 MB.")
        if config.get("logo_base64"):st.markdown(f'<div class="panel"><img src="data:{config.get("logo_mime") or "image/png"};base64,{config["logo_base64"]}" style="max-width:100%;max-height:210px;object-fit:contain"></div>',unsafe_allow_html=True)
        else:st.info("Nenhuma logo configurada.")

st.markdown(f"<br><div class='muted'>{txt('rodape_linha3')}</div>",unsafe_allow_html=True)
