"""Acabamento independente do Gestão Operacional, sem alterar lógica de dados."""
from __future__ import annotations


def apply_ui_refinements(st, *, primary: str = "#ffd43d", light: bool = False) -> None:
    # Regras intencionalmente limitadas à estrutura visual e controles gerais.
    ink = "#14211b" if light else "#f3f5f3"
    secondary = "#4c6356" if light else "#adb9b2"
    background = "#ffffff" if light else "#101714"
    border = "#d6ded7" if light else "#34413a"
    st.markdown(
        """
<style>
/* Gestão Operacional | refinamento de legibilidade, densidade e hierarquia */
html, body, [data-testid="stApp"] { -webkit-font-smoothing: antialiased; }
.main .block-container, [data-testid="stMainBlockContainer"] {
    max-width: 1680px !important;
    padding-left: clamp(16px, 2.6vw, 42px) !important;
    padding-right: clamp(16px, 2.6vw, 42px) !important;
    padding-bottom: 64px !important;
}
.hero {
    min-height: 104px !important;
    padding: 20px 24px !important;
    margin: 12px 0 22px !important;
    box-shadow: 0 8px 25px rgba(0,0,0,.08);
}
.hero h1 {
    font-size: clamp(24px, 1.95vw, 32px) !important;
    line-height: 1.16 !important;
    letter-spacing: -.3px !important;
    overflow-wrap: anywhere;
}
.hero p { font-size: 14px !important; line-height: 1.55 !important; }
.hero .period { padding: 11px 16px !important; font-size: 12px !important; letter-spacing: .25px; }
.section { font-size: 15px !important; margin-top: 22px !important; margin-bottom: 12px !important; }
.panel { border-radius: 14px !important; margin-top: 16px !important; padding: 18px !important; }
.sidebar-logo-section { margin-bottom: 15px !important; }
.sidebar-footer { font-size: 11px !important; line-height: 1.65 !important; }
[data-testid="stSidebar"] .stButton > button {
    min-height: 45px !important;
    height: 45px !important;
    font-size: 13px !important;
    border-radius: 10px !important;
    transition: background .15s ease, border-color .15s ease !important;
}
[data-testid="stSidebar"] .stButton > button:not([kind="primary"]):hover {
    background: rgba(255,212,61,.07) !important;
    border-color: rgba(255,212,61,.22) !important;
}
[data-testid="stButton"] button,
[data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button {
    min-height: 39px;
    font-size: 12px;
    font-weight: 750;
    border-radius: 10px;
    transition: filter .16s ease, border-color .16s ease, background .16s ease;
}
[data-testid="stButton"] button:hover,
[data-testid="stFormSubmitButton"] button:hover,
[data-testid="stDownloadButton"] button:hover { filter: brightness(1.05); }
button:focus-visible, input:focus-visible, select:focus-visible,
[role="button"]:focus-visible {
    outline: 2px solid __PRIMARY__ !important;
    outline-offset: 2px;
}
[data-testid="stExpander"] summary {
    font-size: 13px !important;
    font-weight: 700 !important;
    color: __INK__ !important;
}
[data-testid="stExpander"] details {
    border-color: __BORDER__ !important;
    border-radius: 11px !important;
}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border-radius: 12px !important;
    overflow: hidden;
}
.stCaption, [data-testid="stCaptionContainer"] {
    font-size: 12px !important;
    color: __SECONDARY__ !important;
}
@media (max-width: 900px) {
    .hero { gap: 12px !important; padding: 18px !important; }
}
@media (max-width: 640px) {
    .hero { flex-wrap: wrap !important; align-items: flex-start !important; }
    .hero h1 { font-size: 23px !important; }
    .hero .period { padding: 8px 12px !important; }
    .sidebar-logo-section { margin-top: 0 !important; }
}
@media (prefers-reduced-motion: reduce) {
    [data-testid="stButton"] button, [data-testid="stSidebar"] button { transition: none !important; }
}
</style>
        """.replace("__PRIMARY__", primary)
          .replace("__INK__", ink)
          .replace("__SECONDARY__", secondary)
          .replace("__BORDER__", border)
          .replace("__BACKGROUND__", background),
        unsafe_allow_html=True,
    )
