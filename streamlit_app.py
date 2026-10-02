from pathlib import Path
# DEPLOY_VERSION: login-mrp-1to1-v2
import importlib
import sys
import re

# Garante que atualizacoes dos modulos sejam carregadas em cada execucao do app,
# mesmo quando o ambiente do Streamlit reaproveita o mesmo processo Python.
for _module_name in (
    "indicadores_dashboard",
    "indicadores_pdf",
    "indicadores_regras",
    "indicadores_entregas_v2",
    "indicadores_historico",
    "controle_acesso",
):
    sys.modules.pop(_module_name, None)
importlib.invalidate_caches()

# Executa a versão original do aplicativo aplicando otimizações e camadas de
# controle de acesso sem reescrever a base funcional preservada.
_original = Path(__file__).with_name("streamlit_app_original.py")
_source = _original.read_text(encoding="utf-8")

# -----------------------------------------------------------------------------
# Controle de acesso
# O mesmo usuário do Supabase Auth pode ser reutilizado em vários aplicativos.
# As permissões abaixo são específicas do Gestão Operacional.
# -----------------------------------------------------------------------------
_import_anchor = "from indicadores_dashboard import render_indicadores\n"
_access_import = (
    "from controle_acesso import render_login, permissao, render_admin_usuarios, render_usuario_sidebar\n"
)
if _import_anchor not in _source:
    raise RuntimeError("Ponto de importação do controle de acesso não encontrado.")
_source = _source.replace(_import_anchor, _import_anchor + _access_import, 1)

_page_config_anchor = (
    'st.set_page_config(page_title="GESTÃO | SETTA", page_icon="assets/mrp_setta_icon.png", '
    'layout="wide", initial_sidebar_state="expanded")\n'
)
if _page_config_anchor not in _source:
    raise RuntimeError("Configuração principal do Streamlit não encontrada.")
_page_config_login = 'st.set_page_config(page_title="GESTÃO | SETTA", page_icon="setta_favicon.png", layout="wide")\n'
_source = _source.replace(
    _page_config_anchor,
    _page_config_login + "\n_acesso_client,_acesso_perfil=render_login()\n",
    1,
)

_old_paginas = 'paginas_ids=["dashboard","indicadores","historico","equipes","configuracoes"]'
_new_paginas = (
    'paginas_ids=[_id for _id in ["dashboard","indicadores","historico","equipes","configuracoes"] '
    'if permissao(_acesso_perfil,_id,"ver")]'
)
if _old_paginas not in _source:
    raise RuntimeError("Lista de páginas do menu não encontrada.")
_source = _source.replace(_old_paginas, _new_paginas, 1)

_old_carreira = 'if st.session_state.pagina=="carreira":st.session_state.pagina="equipes"\nwith st.sidebar:'
_new_carreira = '''if st.session_state.pagina=="carreira":st.session_state.pagina="equipes"
if not paginas_ids:
    st.error("Seu usuário não possui nenhuma aba liberada neste aplicativo.")
    st.stop()
if st.session_state.pagina not in paginas_ids:
    st.session_state.pagina=paginas_ids[0]
with st.sidebar:'''
if _old_carreira not in _source:
    raise RuntimeError("Ponto de inicialização do menu lateral não encontrado.")
_source = _source.replace(_old_carreira, _new_carreira, 1)

_logo_anchor = "    st.markdown(f'<div class=\"sidebar-logo-section\"><div class=\"sidebar-logo-wrap\" style=\"--logo-bg:{logo_bg};--logo-border:{logo_border};\">{logo_html}</div></div>',unsafe_allow_html=True)\n"
if _logo_anchor not in _source:
    raise RuntimeError("Ponto da logo lateral não encontrado.")
_source = _source.replace(
    _logo_anchor,
    _logo_anchor + "    render_usuario_sidebar(_acesso_perfil)\n",
    1,
)

_pagina_anchor = "pagina=st.session_state.pagina\n"
_pagina_guard = '''pagina=st.session_state.pagina
if not permissao(_acesso_perfil,pagina,"ver"):
    st.error("Você não possui permissão para acessar esta área.")
    st.stop()
'''
if _pagina_anchor not in _source:
    raise RuntimeError("Ponto de seleção da página não encontrado.")
_source = _source.replace(_pagina_anchor, _pagina_guard, 1)

_config_anchor = '''elif pagina=="configuracoes":
    st.markdown(f'<div class="section">{txt("secao_configuracoes")}</div>',unsafe_allow_html=True);st.write("As configurações ficam salvas no Supabase e são carregadas novamente quando o aplicativo abre.")
'''
_config_replacement = '''elif pagina=="configuracoes":
    st.markdown(f'<div class="section">{txt("secao_configuracoes")}</div>',unsafe_allow_html=True);st.write("As configurações ficam salvas no Supabase e são carregadas novamente quando o aplicativo abre.")
    if str((_acesso_perfil or {}).get("perfil") or "").upper()=="ADMINISTRADOR":
        render_admin_usuarios(_acesso_client,_acesso_perfil)
        st.markdown("---")
'''
if _config_anchor not in _source:
    raise RuntimeError("Tela de configurações não encontrada para integração de usuários.")
_source = _source.replace(_config_anchor, _config_replacement, 1)

# -----------------------------------------------------------------------------
# Fotos de colaboradores
# Antes a tela de equipes fazia SELECT * e transferia cerca de 20 MB de fotos
# Base64 em cada leitura. Agora a listagem usa somente thumbnails pequenas.
# Para registros antigos ainda sem thumbnail, ela é criada automaticamente na
# primeira visualização e passa a ser reutilizada nas próximas consultas.
# -----------------------------------------------------------------------------
_photo_block = re.compile(
    r"def df\(data\):return pd\.DataFrame\(data\) if data else pd\.DataFrame\(\)\n\n"
    r"def foto_html\(c,tamanho=72\):\n"
    r".*?\n"
    r"def nome_curto\(nome\):",
    re.S,
)

_photo_replacement = '''def df(data):return pd.DataFrame(data) if data else pd.DataFrame()

def criar_thumb_base64(raw,max_px=180):
    if not raw:return ""
    try:
        imagem=Image.open(BytesIO(raw)).convert("RGB")
        imagem.thumbnail((max_px,max_px))
        saida=BytesIO()
        imagem.save(saida,format="JPEG",quality=72,optimize=True)
        return base64.b64encode(saida.getvalue()).decode("ascii")
    except Exception:
        return ""

@st.cache_data(ttl=86400,show_spinner=False)
def carregar_foto_visual(colaborador_id,atualizado_em=""):
    try:
        resultado=get_client().table("almox_colaboradores").select("foto_base64,foto_mime").eq("id",colaborador_id).limit(1).execute()
        if not resultado.data:return "", "image/jpeg"
        registro=resultado.data[0]
        original=registro.get("foto_base64") or ""
        mime=registro.get("foto_mime") or "image/jpeg"
        if not original:return "", mime
        try:
            thumb=criar_thumb_base64(base64.b64decode(original))
        except Exception:
            thumb=""
        if thumb:
            try:get_client().table("almox_colaboradores").update({"foto_thumb_base64":thumb}).eq("id",colaborador_id).execute()
            except Exception:pass
            return thumb, "image/jpeg"
        return original, mime
    except Exception:
        return "", "image/jpeg"

def foto_html(c,tamanho=72):
    if not c:return '<div class="ge-avatar-fallback">?</div>'
    b64=c.get("foto_thumb_base64") or ""
    mime="image/jpeg" if b64 else (c.get("foto_mime") or "image/jpeg")
    if not b64 and c.get("id"):
        b64,mime=carregar_foto_visual(str(c.get("id")),str(c.get("atualizado_em") or ""))
    if b64:return f'<img class="ge-avatar-img" style="width:{tamanho}px;height:{tamanho}px" src="data:{mime};base64,{b64}">'
    nome=(c.get("nome") or "?").strip();return f'<div class="ge-avatar-fallback" style="width:{tamanho}px;height:{tamanho}px">{nome[:1].upper()}</div>'

def nome_curto(nome):'''

_source, _n = _photo_block.subn(_photo_replacement, _source, count=1)
if _n != 1:
    raise RuntimeError("Bloco de fotos do Gestão Almoxarifado não encontrado.")

_old_team_query='colaboradores_raw=rows("almox_colaboradores",order="nome",desc=False)'
_new_team_query='colaboradores_raw=rows("almox_colaboradores",order="nome",desc=False,columns="id,nome,funcao,equipe_atual,ativo,criado_em,atualizado_em,matricula,data_admissao,equipe_id,foto_thumb_base64,foto_mime,foto_nome")'
if _old_team_query not in _source:
    raise RuntimeError("Consulta de colaboradores não encontrada para otimização.")
_source=_source.replace(_old_team_query,_new_team_query,1)

_old_new_photo='eqid=equipes_opts.get(eq_label);b64=base64.b64encode(foto.getvalue()).decode("ascii")'
_new_new_photo='eqid=equipes_opts.get(eq_label);_foto_bytes=foto.getvalue();b64=base64.b64encode(_foto_bytes).decode("ascii");thumb=criar_thumb_base64(_foto_bytes)'
if _old_new_photo not in _source:
    raise RuntimeError("Cadastro de foto não encontrado para otimização.")
_source=_source.replace(_old_new_photo,_new_new_photo,1)
_source=_source.replace('"foto_base64":b64,"foto_mime":foto.type or "image/jpeg"','"foto_base64":b64,"foto_thumb_base64":thumb,"foto_mime":foto.type or "image/jpeg"',1)

_old_edit='dados.update({"foto_base64":base64.b64encode(foto_e.getvalue()).decode("ascii"),"foto_mime":foto_e.type or "image/jpeg","foto_nome":foto_e.name})'
_new_edit='_foto_e_bytes=foto_e.getvalue();dados.update({"foto_base64":base64.b64encode(_foto_e_bytes).decode("ascii"),"foto_thumb_base64":criar_thumb_base64(_foto_e_bytes),"foto_mime":foto_e.type or "image/jpeg","foto_nome":foto_e.name})'
if _old_edit not in _source:
    raise RuntimeError("Edição de foto não encontrada para otimização.")
_source=_source.replace(_old_edit,_new_edit,1)

exec(compile(_source,"streamlit_app_original.py","exec"),globals(),globals())
