from __future__ import annotations

import os
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
from supabase import Client, create_client

PROJECT_URL = "https://cuixazpxkvniqldmmnth.supabase.co"

MODULOS = {
    "indicadores": "Alimentar Indicadores",
    "historico": "Histórico",
    "equipes": "Gestão de Equipes",
    "carreira": "Plano de Carreira",
    "configuracoes": "Configurações",
}

PERFIS_PADRAO = {
    "ADMINISTRADOR": {
        modulo: {"ver": True, "editar": True} for modulo in MODULOS
    },
    "GESTOR": {
        "indicadores": {"ver": True, "editar": True},
        "historico": {"ver": True, "editar": False},
        "equipes": {"ver": True, "editar": True},
        "carreira": {"ver": True, "editar": True},
        "configuracoes": {"ver": False, "editar": False},
    },
    "OPERACIONAL": {
        "indicadores": {"ver": True, "editar": True},
        "historico": {"ver": True, "editar": False},
        "equipes": {"ver": False, "editar": False},
        "carreira": {"ver": False, "editar": False},
        "configuracoes": {"ver": False, "editar": False},
    },
    "CONSULTA": {
        "indicadores": {"ver": True, "editar": False},
        "historico": {"ver": True, "editar": False},
        "equipes": {"ver": False, "editar": False},
        "carreira": {"ver": False, "editar": False},
        "configuracoes": {"ver": False, "editar": False},
    },
    "PERSONALIZADO": {modulo: {"ver": False, "editar": False} for modulo in MODULOS},
}

SESSION_KEYS = (
    "acesso_access_token",
    "acesso_refresh_token",
    "acesso_user_id",
    "acesso_email",
    "acesso_perfil",
)


def _secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
        if value is not None and str(value).strip():
            return str(value).strip().strip('"').strip("'")
    except Exception:
        pass
    value = os.getenv(name, default)
    return str(value or "").strip().strip('"').strip("'")


def criar_cliente_sessao() -> Client:
    url = _secret("SUPABASE_URL", PROJECT_URL)
    key = _secret("SUPABASE_KEY") or _secret("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY não configurados para autenticação.")
    return create_client(url, key)


def _salvar_tokens(response: Any) -> None:
    session = getattr(response, "session", None)
    user = getattr(response, "user", None)
    if session is not None:
        st.session_state.acesso_access_token = getattr(session, "access_token", "") or ""
        st.session_state.acesso_refresh_token = getattr(session, "refresh_token", "") or ""
    if user is not None:
        st.session_state.acesso_user_id = str(getattr(user, "id", "") or "")
        st.session_state.acesso_email = str(getattr(user, "email", "") or "")


def cliente_autenticado() -> Client | None:
    access_token = st.session_state.get("acesso_access_token")
    refresh_token = st.session_state.get("acesso_refresh_token")
    if not access_token or not refresh_token:
        return None
    try:
        client = criar_cliente_sessao()
        response = client.auth.set_session(access_token, refresh_token)
        _salvar_tokens(response)
        verified = client.auth.get_user()
        user = getattr(verified, "user", None)
        if user is None:
            return None
        st.session_state.acesso_user_id = str(getattr(user, "id", "") or "")
        st.session_state.acesso_email = str(getattr(user, "email", "") or "")
        return client
    except Exception:
        limpar_sessao()
        return None


def autenticar(email: str, senha: str) -> tuple[Client | None, str | None]:
    try:
        client = criar_cliente_sessao()
        response = client.auth.sign_in_with_password({"email": email.strip(), "password": senha})
        _salvar_tokens(response)
        verified = client.auth.get_user()
        user = getattr(verified, "user", None)
        if user is None:
            raise RuntimeError("Usuário não validado pelo Supabase Auth.")
        st.session_state.acesso_user_id = str(getattr(user, "id", "") or "")
        st.session_state.acesso_email = str(getattr(user, "email", "") or "")
        return client, None
    except Exception as exc:
        limpar_sessao()
        return None, str(exc)


def solicitar_acesso(nome: str, email: str, senha: str) -> tuple[bool, str]:
    try:
        client = criar_cliente_sessao()
        response = client.auth.sign_up(
            {
                "email": email.strip(),
                "password": senha,
                "options": {"data": {"nome": nome.strip()}},
            }
        )
        session = getattr(response, "session", None)
        if session is None:
            return True, "Cadastro criado. Confirme o e-mail e depois faça login para solicitar a liberação."
        _salvar_tokens(response)
        perfil = obter_ou_criar_perfil(client, nome_padrao=nome)
        if perfil:
            return True, "Cadastro criado. A solicitação de acesso foi enviada ao administrador."
        return True, "Cadastro criado. Faça login novamente para concluir a solicitação de acesso."
    except Exception as exc:
        return False, str(exc)


def obter_ou_criar_perfil(client: Client, nome_padrao: str = "") -> dict[str, Any] | None:
    user_id = str(st.session_state.get("acesso_user_id") or "")
    email = str(st.session_state.get("acesso_email") or "")
    if not user_id or not email:
        return None

    result = (
        client.table("almox_usuarios_acesso")
        .select("user_id,email,nome,perfil,ativo,permissoes")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if rows:
        perfil = rows[0]
        st.session_state.acesso_perfil = perfil
        return perfil

    nome = (nome_padrao or email.split("@", 1)[0]).strip()
    payload = {
        "user_id": user_id,
        "email": email,
        "nome": nome,
        "perfil": "PENDENTE",
        "ativo": False,
        "permissoes": {},
    }
    try:
        created = client.table("almox_usuarios_acesso").insert(payload).execute()
        rows = created.data or []
        perfil = rows[0] if rows else payload
        st.session_state.acesso_perfil = perfil
        return perfil
    except Exception:
        return None


def permissao(perfil: dict[str, Any] | None, modulo: str, acao: str = "ver") -> bool:
    if not perfil or not perfil.get("ativo"):
        return False
    if str(perfil.get("perfil") or "").upper() == "ADMINISTRADOR":
        return True
    permissoes = perfil.get("permissoes") or {}
    item = permissoes.get(modulo) or {}
    return bool(item.get(acao, False))


def modulos_visiveis(perfil: dict[str, Any] | None) -> list[str]:
    return [modulo for modulo in MODULOS if permissao(perfil, modulo, "ver")]


def limpar_sessao() -> None:
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)



def _habilitar_autofill_login() -> None:
    """Marca os campos de login para gerenciadores de senha do navegador/iOS."""
    components.html(
        """
        <script>
        (() => {
          const doc = window.parent.document;
          const apply = () => {
            const box = doc.querySelector('div[data-testid="stForm"]');
            if (!box) return false;
            const inputs = Array.from(box.querySelectorAll('input'));
            const user = inputs.find((el) => el.type !== 'password');
            const pass = inputs.find((el) => el.type === 'password');

            if (user) {
              user.setAttribute('autocomplete', 'username');
              user.setAttribute('name', 'username');
              user.setAttribute('autocapitalize', 'none');
              user.setAttribute('spellcheck', 'false');
            }
            if (pass) {
              pass.setAttribute('autocomplete', 'current-password');
              pass.setAttribute('name', 'password');
            }

            const htmlForm = box.querySelector('form') || box.closest('form');
            if (htmlForm) htmlForm.setAttribute('autocomplete', 'on');
            return Boolean(user && pass);
          };

          apply();
          let attempts = 0;
          const timer = setInterval(() => {
            attempts += 1;
            if (apply() || attempts > 30) clearInterval(timer);
          }, 100);

          const observer = new MutationObserver(() => apply());
          observer.observe(doc.body, {childList: true, subtree: true});
          setTimeout(() => observer.disconnect(), 5000);
        })();
        </script>
        """,
        height=0,
        width=0,
    )

def render_login() -> tuple[Client | None, dict[str, Any] | None]:
    client = cliente_autenticado()
    if client is not None:
        perfil = obter_ou_criar_perfil(client)
        if perfil and perfil.get("ativo"):
            return client, perfil
        st.warning("Seu acesso está aguardando liberação do administrador.")
        if st.button("Sair", key="acesso_logout_pendente"):
            limpar_sessao()
            st.rerun()
        st.stop()

    # Reaproveita a mesma imagem de login configurada no MRP para manter
    # identidade visual consistente entre os aplicativos.
    image = ""
    try:
        publico = criar_cliente_sessao()
        resultado = (
            publico.table("mrp_app_config")
            .select("login_image_data")
            .eq("id", 1)
            .limit(1)
            .execute()
        )
        if resultado.data:
            image = str(resultado.data[0].get("login_image_data") or "")
    except Exception:
        image = ""

    image_html = (
        f'<div class="setta-login-image"><img src="{image}" /></div>'
        if image
        else '<div class="setta-login-image setta-login-image-empty"><div>Setta</div></div>'
    )

    st.markdown(
        f"""
        <style>
          .stApp {{ background:#ffffff !important; color:#202020 !important; }}
          [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebar"] {{ display:none !important; }}
          .block-container {{ max-width:100% !important; min-height:100vh !important; height:100vh !important; padding:0 !important; margin:0 !important; position:relative !important; overflow:hidden !important; }}

          /* PROPORÇÕES IDÊNTICAS AO LOGIN DO MRP */
          .setta-login-wrap {{
            position:fixed !important;
            left:50% !important;
            top:50% !important;
            transform:translate(-50%,-50%) !important;
            width:400px !important;
            height:500px !important;
            margin:0 !important;
            padding:0 !important;
            box-sizing:border-box !important;
            background:#fff !important;
            border:1px solid rgba(0,0,0,.13) !important;
            border-radius:26px !important;
            box-shadow:0 18px 48px rgba(0,0,0,.16) !important;
            overflow:visible !important;
            z-index:10 !important;
          }}

          .setta-login-image {{
            width:100% !important;
            height:140px !important;
            transform:none !important;
            overflow:hidden !important;
            background:transparent !important;
            display:flex !important;
            align-items:center !important;
            justify-content:center !important;
            padding:18px 28px 4px !important;
            box-sizing:border-box !important;
          }}
          .setta-login-image img {{
            width:auto !important;
            height:auto !important;
            max-width:220px !important;
            max-height:82px !important;
            object-fit:contain !important;
            object-position:center !important;
            display:block !important;
          }}
          .setta-login-image-empty {{
            display:flex !important;
            align-items:center !important;
            justify-content:center !important;
          }}
          .setta-login-image-empty div {{
            font-size:3.4rem !important;
            font-weight:850 !important;
            font-style:italic !important;
            line-height:1 !important;
            color:#111 !important;
          }}

          .setta-login-heading {{
            width:100% !important;
            height:66px !important;
            transform:none !important;
            margin:0 !important;
            padding:0 28px !important;
            box-sizing:border-box !important;
            display:flex !important;
            align-items:flex-start !important;
            justify-content:center !important;
            text-align:center !important;
          }}
          .setta-login-title {{
            color:#111 !important;
            font-size:18px !important;
            line-height:24px !important;
            font-weight:800 !important;
            margin:0 !important;
            padding:0 !important;
            text-align:center !important;
          }}

          div[data-testid="InputInstructions"],
          div[data-testid="stTextInput"] div[data-testid="InputInstructions"] {{
            display:none !important;
          }}

          div[data-testid="stForm"] {{
            position:fixed !important;
            left:50% !important;
            top:calc(50% + 60px) !important;
            transform:translate(-50%,-50%) !important;
            width:300px !important;
            height:auto !important;
            margin:0 !important;
            padding:0 !important;
            box-sizing:border-box !important;
            background:transparent !important;
            border:0 !important;
            border-radius:0 !important;
            box-shadow:none !important;
            z-index:20 !important;
          }}
          div[data-testid="stForm"] label {{
            color:#202020 !important;
            font-size:12px !important;
            line-height:17px !important;
            font-weight:650 !important;
            margin-bottom:5px !important;
          }}
          div[data-testid="stForm"] [data-testid="stTextInput"] {{
            width:100% !important;
            margin-bottom:3px !important;
          }}

          div[data-testid="stForm"] [data-baseweb="input"] {{
            width:100% !important;
            height:35px !important;
            min-height:35px !important;
            display:flex !important;
            align-items:center !important;
            box-sizing:border-box !important;
            border:2px solid #050505 !important;
            border-radius:8px !important;
            background:#f0f2f6 !important;
            box-shadow:none !important;
            overflow:hidden !important;
            color:#202020 !important;
          }}
          div[data-testid="stForm"] [data-baseweb="base-input"] {{
            width:100% !important;
            height:35px !important;
            min-height:35px !important;
            border:0 !important;
            outline:0 !important;
            box-shadow:none !important;
            background:#f0f2f6 !important;
            color:#202020 !important;
          }}
          div[data-testid="stForm"] [data-baseweb="input"] > div {{
            height:35px !important;
            min-height:35px !important;
            display:flex !important;
            align-items:center !important;
            box-sizing:border-box !important;
            background:#f0f2f6 !important;
            color:#202020 !important;
          }}
          div[data-testid="stForm"] input {{
            width:100% !important;
            height:35px !important;
            min-height:35px !important;
            line-height:normal !important;
            padding:0 12px !important;
            margin:0 !important;
            box-sizing:border-box !important;
            border:0 !important;
            outline:none !important;
            box-shadow:none !important;
            border-radius:8px !important;
            background:#f0f2f6 !important;
            color:#202020 !important;
            -webkit-text-fill-color:#202020 !important;
            font-size:13px !important;
          }}
          div[data-testid="stForm"] [data-testid="stTextInput"] [data-baseweb="input"] button {{
            width:38px !important;
            min-width:38px !important;
            height:35px !important;
            min-height:35px !important;
            flex:0 0 38px !important;
            margin:0 !important;
            padding:0 !important;
            position:static !important;
            top:auto !important;
            border:0 !important;
            border-radius:0 !important;
            background:transparent !important;
            color:#050505 !important;
            box-shadow:none !important;
            display:flex !important;
            align-items:center !important;
            justify-content:center !important;
            box-sizing:border-box !important;
          }}
          div[data-testid="stForm"] [data-testid="stTextInput"] [data-baseweb="input"] button:hover {{
            background:transparent !important;
            border:0 !important;
            color:#050505 !important;
          }}
          div[data-testid="stForm"] [data-testid="stTextInput"] [data-baseweb="input"] button svg {{
            width:17px !important;
            height:17px !important;
            margin:0 !important;
            display:block !important;
          }}
          div[data-testid="stForm"] input::placeholder {{
            color:#a6adb8 !important;
            opacity:1 !important;
          }}
          div[data-testid="stForm"] input:focus {{
            outline:none !important;
            border:0 !important;
            box-shadow:none !important;
            background:#f0f2f6 !important;
            color:#202020 !important;
            -webkit-text-fill-color:#202020 !important;
          }}
          div[data-testid="stForm"] input:-webkit-autofill,
          div[data-testid="stForm"] input:-webkit-autofill:hover,
          div[data-testid="stForm"] input:-webkit-autofill:focus {{
            -webkit-box-shadow:0 0 0 1000px #f0f2f6 inset !important;
            -webkit-text-fill-color:#202020 !important;
          }}

          div[data-testid="stForm"] [data-testid="stFormSubmitButton"] button {{
            position:static !important;
            width:100% !important;
            height:50px !important;
            min-height:50px !important;
            margin:10px 0 0 !important;
            padding:0 !important;
            box-sizing:border-box !important;
            border-radius:10px !important;
            background:#050505 !important;
            border:1px solid #050505 !important;
            color:#fff !important;
            font-size:13px !important;
            font-weight:750 !important;
            display:flex !important;
            align-items:center !important;
            justify-content:center !important;
          }}
          div[data-testid="stForm"] [data-testid="stFormSubmitButton"] button:hover {{
            background:#171717 !important;
            border-color:#171717 !important;
          }}
          div[data-testid="stForm"] [data-testid="stFormSubmitButton"] button p {{
            color:#fff !important;
          }}

          div[data-testid="stAlert"] {{
            position:fixed !important;
            left:50% !important;
            top:calc(50% + 270px) !important;
            transform:translateX(-50%) !important;
            width:400px !important;
            box-sizing:border-box !important;
            z-index:30 !important;
          }}

          @media (max-width:480px) {{
            .setta-login-wrap {{
              width:360px !important;
              height:500px !important;
            }}
            div[data-testid="stForm"] {{
              width:312px !important;
            }}
            div[data-testid="stAlert"] {{
              width:360px !important;
            }}
          }}
        </style>
        <div class="setta-login-wrap">
          {image_html}
          <div class="setta-login-heading">
            <div class="setta-login-title">GESTÃO OPERACIONAL | SETTA</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("form_login_gestao"):
        email = st.text_input("Usuário", placeholder="Digite seu usuário", key="acesso_email_login")
        senha = st.text_input("Senha", type="password", placeholder="Digite sua senha", key="acesso_senha_login")
        entrar = st.form_submit_button("ENTRAR", use_container_width=True)

    _habilitar_autofill_login()

    if entrar:
        client, erro = autenticar(email, senha)
        if erro:
            st.error("Não foi possível entrar. Verifique usuário e senha.")
        elif client is not None:
            obter_ou_criar_perfil(client)
            st.rerun()

    st.stop()

def _normalizar_permissoes(perfil_nome: str, atual: dict[str, Any] | None = None) -> dict[str, Any]:
    perfil_nome = str(perfil_nome or "PERSONALIZADO").upper()
    if perfil_nome in PERFIS_PADRAO and perfil_nome != "PERSONALIZADO":
        return {k: dict(v) for k, v in PERFIS_PADRAO[perfil_nome].items()}
    atual = atual or {}
    return {
        modulo: {
            "ver": bool((atual.get(modulo) or {}).get("ver", False)),
            "editar": bool((atual.get(modulo) or {}).get("editar", False)),
        }
        for modulo in MODULOS
    }



def convidar_usuario(
    client: Client,
    nome: str,
    email: str,
    perfil: str,
    ativo: bool,
    permissoes: dict[str, Any],
) -> tuple[bool, str]:
    try:
        access_token = str(st.session_state.get("acesso_access_token") or "")
        if not access_token:
            return False, "Sua sessão expirou. Entre novamente antes de criar um usuário."

        response = client.functions.invoke(
            "almox-convidar-usuario",
            invoke_options={
                "headers": {"Authorization": f"Bearer {access_token}"},
                "body": {
                    "nome": nome.strip(),
                    "email": email.strip().lower(),
                    "perfil": perfil,
                    "ativo": bool(ativo),
                    "permissoes": permissoes,
                },
            },
        )

        data = getattr(response, "data", response)
        if isinstance(data, bytes):
            import json
            data = json.loads(data.decode("utf-8"))
        elif isinstance(data, str):
            import json
            data = json.loads(data)

        if isinstance(data, dict):
            if data.get("ok"):
                return True, str(data.get("message") or "Convite enviado com sucesso.")
            if data.get("error"):
                return False, str(data.get("error"))
        return True, "Convite enviado com sucesso."
    except Exception as exc:
        mensagem = str(exc)
        if "already" in mensagem.lower() and "registered" in mensagem.lower():
            mensagem = "Este e-mail já possui uma conta no Supabase Auth. Ajuste as permissões dele na lista de usuários."
        return False, mensagem

def render_admin_usuarios(client: Client, perfil_logado: dict[str, Any]) -> None:
    if str(perfil_logado.get("perfil") or "").upper() != "ADMINISTRADOR":
        st.error("Apenas administradores podem gerenciar usuários e acessos.")
        return

    st.markdown("### Usuários e Acessos")
    st.caption("Crie novos acessos e defina o perfil e as permissões de cada usuário neste aplicativo.")

    with st.expander("+ NOVO USUÁRIO", expanded=False):
        st.caption("O usuário receberá um convite por e-mail para ativar a conta no Supabase Auth.")
        with st.form("form_novo_usuario_gestao", clear_on_submit=True):
            c_nome, c_email = st.columns(2)
            with c_nome:
                novo_nome = st.text_input("Nome", key="novo_usuario_nome")
            with c_email:
                novo_email = st.text_input("E-mail", key="novo_usuario_email")

            novo_perfil = st.selectbox(
                "Perfil",
                ["ADMINISTRADOR", "GESTOR", "OPERACIONAL", "CONSULTA", "PERSONALIZADO"],
                index=2,
                key="novo_usuario_perfil",
            )
            novo_ativo = st.toggle(
                "Liberar acesso assim que o convite for aceito",
                value=True,
                key="novo_usuario_ativo",
            )

            if novo_perfil == "PERSONALIZADO":
                st.markdown("**Permissões por aba**")
                novas_permissoes = {}
                for modulo, rotulo in MODULOS.items():
                    c1, c2, c3 = st.columns([2.2, 1, 1])
                    with c1:
                        st.write(rotulo)
                    with c2:
                        ver = st.checkbox("Visualizar", key=f"novo_{modulo}_ver")
                    with c3:
                        editar = st.checkbox("Editar", key=f"novo_{modulo}_editar")
                    if editar:
                        ver = True
                    novas_permissoes[modulo] = {"ver": bool(ver), "editar": bool(editar)}
            else:
                novas_permissoes = _normalizar_permissoes(novo_perfil)
                st.caption("As permissões padrão desse perfil serão aplicadas automaticamente.")

            enviar_convite = st.form_submit_button(
                "ENVIAR CONVITE",
                use_container_width=True,
                type="primary",
            )

        if enviar_convite:
            if not novo_nome.strip():
                st.error("Informe o nome do usuário.")
            elif "@" not in novo_email or "." not in novo_email.split("@")[-1]:
                st.error("Informe um e-mail válido.")
            else:
                ok, mensagem = convidar_usuario(
                    client,
                    novo_nome,
                    novo_email,
                    novo_perfil,
                    novo_ativo,
                    novas_permissoes,
                )
                if ok:
                    st.success(mensagem)
                    st.rerun()
                else:
                    st.error(f"Não foi possível criar o usuário: {mensagem}")

    st.markdown("#### Usuários cadastrados")

    response = (
        client.table("almox_usuarios_acesso")
        .select("user_id,email,nome,perfil,ativo,permissoes,criado_em,atualizado_em")
        .order("criado_em", desc=False)
        .execute()
    )
    usuarios = response.data or []
    if not usuarios:
        st.info("Nenhum usuário cadastrado.")
        return

    for idx, usuario in enumerate(usuarios):
        titulo = usuario.get("nome") or usuario.get("email") or "Usuário"
        situacao = "ATIVO" if usuario.get("ativo") else "PENDENTE / BLOQUEADO"
        with st.expander(f"{titulo} · {situacao}", expanded=not bool(usuario.get("ativo"))):
            st.caption(usuario.get("email") or "")
            key_base = f"usr_{usuario.get('user_id')}_{idx}"

            perfil_atual = str(usuario.get("perfil") or "PENDENTE").upper()
            opcoes = ["ADMINISTRADOR", "GESTOR", "OPERACIONAL", "CONSULTA", "PERSONALIZADO"]
            if perfil_atual not in opcoes:
                perfil_atual = "CONSULTA"
            perfil_escolhido = st.selectbox(
                "Perfil",
                opcoes,
                index=opcoes.index(perfil_atual),
                key=f"{key_base}_perfil",
            )
            ativo = st.toggle("Acesso ativo", value=bool(usuario.get("ativo")), key=f"{key_base}_ativo")

            base = _normalizar_permissoes(
                perfil_escolhido,
                usuario.get("permissoes") if perfil_escolhido == "PERSONALIZADO" else None,
            )
            permissoes = {}
            st.markdown("**Permissões por aba**")
            for modulo, rotulo in MODULOS.items():
                c1, c2, c3 = st.columns([2.2, 1, 1])
                with c1:
                    st.write(rotulo)
                with c2:
                    ver = st.checkbox(
                        "Visualizar",
                        value=bool((base.get(modulo) or {}).get("ver")),
                        key=f"{key_base}_{modulo}_ver",
                        disabled=perfil_escolhido != "PERSONALIZADO",
                    )
                with c3:
                    editar = st.checkbox(
                        "Editar",
                        value=bool((base.get(modulo) or {}).get("editar")),
                        key=f"{key_base}_{modulo}_editar",
                        disabled=perfil_escolhido != "PERSONALIZADO",
                    )
                if editar:
                    ver = True
                permissoes[modulo] = {"ver": bool(ver), "editar": bool(editar)}

            if perfil_escolhido != "PERSONALIZADO":
                permissoes = _normalizar_permissoes(perfil_escolhido)

            if st.button("Salvar acesso", key=f"{key_base}_salvar", type="primary"):
                client.table("almox_usuarios_acesso").update(
                    {
                        "perfil": perfil_escolhido,
                        "ativo": ativo,
                        "permissoes": permissoes,
                    }
                ).eq("user_id", usuario.get("user_id")).execute()
                st.success("Permissões atualizadas.")
                st.rerun()


def render_usuario_sidebar(perfil: dict[str, Any]) -> None:
    nome = perfil.get("nome") or perfil.get("email") or "Usuário"
    perfil_nome = str(perfil.get("perfil") or "").replace("_", " ")
    st.markdown(
        f"<div style='margin:8px 4px 12px;padding:10px;border:1px solid #2c3933;border-radius:10px;'>"
        f"<div style='font-size:12px;font-weight:800'>{nome}</div>"
        f"<div style='font-size:10px;color:#9aa39f;margin-top:3px'>{perfil_nome}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if st.button("Sair", key="acesso_logout_sidebar", use_container_width=True):
        limpar_sessao()
        st.rerun()
