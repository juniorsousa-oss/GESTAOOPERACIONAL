from __future__ import annotations

import base64
import unicodedata
from datetime import datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageDraw, ImageFont
import streamlit as st

GROUPS = ["ACURÁCIA DE ESTOQUE", "ENTREGAS NO PRAZO", "5S", "PRODUTIVIDADE"]
ALIASES = {
    "ACURACIDADE DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ACURÁCIDADE DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ACURÁCIA DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ACURACIA DE ESTOQUE": "ACURÁCIA DE ESTOQUE",
    "ENTREGAS NO PRAZO": "ENTREGAS NO PRAZO",
    "5S": "5S",
    "PRODUTIVIDADE": "PRODUTIVIDADE",
}

# Sempre que o desenho da imagem mudar, altere esta versão para invalidar o cache.
EXPORT_LAYOUT_VERSION = "print-official-a4-v9-periodo-corte"
PRINT_STANDARD = "A4_EXECUTIVO_V1"

LAST_INDICADORES = []
_ORIGINAL_DOWNLOAD_BUTTON = st.download_button

# Paleta aprovada: clara, alto contraste e adequada para impressão.
BG = "#FFFFFF"
PANEL = "#FFFFFF"
CARD = "#FFFFFF"
BORDER = "#CBD5E1"
GRID = "#E2E8F0"
TEXT = "#0F172A"
MUTED = "#475569"
YELLOW = "#FFC21C"
YELLOW_DARK = "#D89E00"
META = "#1E293B"
GREEN = "#149447"
RED = "#C62828"
STATUS_NEUTRAL_BG = "#F1F5F9"
STATUS_OK_BG = "#D1FAE5"
STATUS_BAD_BG = "#FEE2E2"


def _font(size, bold=False):
    """Fonte TrueType robusta para manter a escala tipográfica em produção.

    O Streamlit Cloud pode não possuir o caminho DejaVu usado localmente.
    Quando isso acontecia, Pillow caía na fonte bitmap padrão e ignorava
    completamente os tamanhos definidos no layout.
    """
    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "DejaVuSans-Bold.ttf",
        "LiberationSans-Bold.ttf",
    ]
    regular_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
    ]
    for candidate in (bold_candidates if bold else regular_candidates):
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue

    try:
        from pathlib import Path
        import reportlab
        bundled = Path(reportlab.__file__).parent / "fonts" / ("VeraBd.ttf" if bold else "Vera.ttf")
        return ImageFont.truetype(str(bundled), size=size)
    except (ImportError, OSError):
        pass

    # Pillow recente permite escalar a fonte padrão. Isso evita voltar ao
    # bitmap minúsculo mesmo em ambientes sem fontes de sistema conhecidas.
    try:
        return ImageFont.load_default(size=size)
    except TypeError as exc:
        raise RuntimeError("Fonte escalável indisponível. Verifique a instalação do ReportLab.") from exc


def _norm_text(value):
    """Normaliza strings para NFC antes da renderização pelo Pillow."""
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value))


def _text(draw, xy, value, *args, **kwargs):
    return draw.text(xy, _norm_text(value), *args, **kwargs)


def _pct(v):
    try:
        return f"{float(v):.2f}%".replace(".", ",")
    except Exception:
        return "—"


def _pct_signed(v):
    try:
        return f"{float(v):+.2f}%".replace(".", ",")
    except Exception:
        return "—"


def _month(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        meses = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
        return f"{meses[d.month-1]}/{d.year}"
    except Exception:
        return str(value or "—")[:16].upper()


def _groups(indicadores):
    groups = {name: [] for name in GROUPS}
    for row in indicadores or []:
        raw = str(row.get("indicador") or "").strip()
        key = ALIASES.get(raw.upper(), raw.upper())
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    for name in groups:
        groups[name].sort(key=lambda r: str(r.get("competencia") or ""))
    return groups


def _value_meta_diff(row):
    try:
        value = float(row.get("valor")) if row and row.get("valor") is not None else None
    except Exception:
        value = None
    try:
        meta = float(row.get("meta")) if row and row.get("meta") is not None else None
    except Exception:
        meta = None
    diff = value - meta if value is not None and meta is not None else None
    return value, meta, diff


def _rounded(draw, box, fill, outline=BORDER, radius=18, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _center(draw, text, box, font, fill):
    text = _norm_text(text)
    x1, y1, x2, y2 = box
    bb = draw.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    _text(draw, 
        ((x1 + x2 - tw) / 2, (y1 + y2 - th) / 2 - 2),
        text,
        font=font,
        fill=fill,
    )


def _status(diff):
    if diff is None:
        return "SEM HISTÓRICO", MUTED, STATUS_NEUTRAL_BG
    if diff >= 0:
        return "ACIMA DA META", GREEN, STATUS_OK_BG
    return "ABAIXO DA META", RED, STATUS_BAD_BG


def _logo_from_config():
    """Retorna a mesma logo configurada no aplicativo/menu lateral."""
    try:
        cfg = st.session_state.get("config") or {}
        return cfg.get("logo_base64"), cfg.get("logo_mime")
    except Exception:
        return None, None


def _load_logo(logo_base64=None, logo_mime=None):
    if not logo_base64:
        logo_base64, logo_mime = _logo_from_config()
    if not logo_base64:
        return None
    # O upload atual do app trabalha com imagem raster; SVG fica sem renderização
    # na exportação até existir um conversor SVG no ambiente.
    if str(logo_mime or "").lower() == "image/svg+xml":
        return None
    try:
        raw = base64.b64decode(logo_base64)
        logo = Image.open(BytesIO(raw)).convert("RGBA")
        return logo
    except Exception:
        return None


def _draw_logo(canvas, logo_base64=None, logo_mime=None, box=None):
    """Desenha a logo real no canto superior direito preservando proporção."""
    logo = _load_logo(logo_base64, logo_mime)
    if logo is None:
        return False

    # Caixa reservada conforme esboço aprovado.
    box = box or (1325, 38, 1692, 132)
    x1, y1, x2, y2 = box
    max_w, max_h = x2 - x1, y2 - y1
    lw, lh = logo.size
    if not lw or not lh:
        return False

    scale = min(max_w / lw, max_h / lh)
    nw, nh = max(1, int(lw * scale)), max(1, int(lh * scale))
    logo = logo.resize((nw, nh), Image.Resampling.LANCZOS)
    px = x2 - nw
    py = y1 + (max_h - nh) // 2
    canvas.paste(logo, (px, py), logo)
    return True


def _fit_font(draw, text, size, max_width, bold=False):
    """Ajusta pelo tamanho real dos glifos, inclusive para novos indicadores."""
    text = _norm_text(text)
    font = _font(size, bold)
    while size > 24 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
        size -= 2
        font = _font(size, bold)
    return font


def gerar_imagem_indicador(nome, rows, indice, logo_base64=None, logo_mime=None):
    """Arte A4 paisagem, renderizada nativamente a 300 dpi."""
    from math import ceil, isfinite
    from zoneinfo import ZoneInfo

    W, H = 3508, 2480
    im = Image.new("RGB", (W, H), BG)
    im.info["dpi"] = (300, 300)
    d = ImageDraw.Draw(im)
    left, right = 140, W - 140  # Margem de aproximadamente 12 mm.
    rows = list(rows or [])
    latest = rows[-1] if rows else None
    value, meta, diff = _value_meta_diff(latest)
    status, status_color, status_bg = _status(diff)
    if value is not None and meta is None:
        status = "SEM META"
    elif diff == 0:
        status = "NA META"
    selected_month = _month(latest.get("competencia")) if latest else "Sem lançamento"

    # Cabeçalho com área independente para a marca.
    d.rounded_rectangle((left, 122, left + 12, 278), radius=6, fill=YELLOW)
    _text(d, (left + 42, 116), "GESTÃO OPERACIONAL  /  INDICADORES", font=_font(32, True), fill=MUTED)
    title = f"{indice:02d} · {nome}"
    title_font = _fit_font(d, title, 88, 2420, True)
    _text(d, (left + 42, 170), title, font=title_font, fill=TEXT)
    if rows:
        period = f"JAN/{str(latest.get('competencia'))[:4]} até {selected_month}"
    else:
        period = "Sem histórico no período selecionado"
    _text(d, (left + 42, 278), f"Fechamento mensal  •  {period}", font=_font(42), fill=MUTED)
    _draw_logo(im, logo_base64, logo_mime, box=(2790, 120, right, 300))
    d.line((left, 360, right, 360), fill=BORDER, width=2)

    # Os números são o foco; as quatro caixas têm a mesma malha e respiro.
    gap = 28
    card_width = (right - left - 3 * gap) / 4
    top, bottom = 416, 788
    difference = f"{diff:+.2f}".replace(".", ",") if diff is not None else "—"
    cards = [
        ("RESULTADO FINAL", _pct(value), selected_month, TEXT),
        ("META DO PERÍODO", _pct(meta), "Referência do fechamento", TEXT),
        ("DIFERENÇA", difference, "Pontos percentuais • resultado − meta", status_color),
        ("STATUS", status, selected_month, status_color),
    ]
    for i, (label, main, sub, color) in enumerate(cards):
        x = left + i * (card_width + gap)
        end = x + card_width
        _rounded(d, (x, top, end, bottom), BG, radius=20, width=2)
        _text(d, (x + 38, top + 36), label, font=_font(34, True), fill=MUTED)
        if i == 3:
            # Badge executivo: destaca o status sem competir com os KPIs numéricos.
            badge = (x + 34, top + 112, end - 34, top + 238)
            _rounded(d, badge, status_bg, outline=None, radius=36, width=0)
            status_font = _fit_font(d, main, 48, card_width - 116, True)
            _center(d, main, badge, status_font, color)
        else:
            f = _fit_font(d, main, 116, card_width - 76, True)
            _text(d, (x + 38, top + 112), main, font=f, fill=color)
        sub_font = _fit_font(d, sub, 32, card_width - 76)
        _text(d, (x + 38, bottom - 72), sub, font=sub_font, fill=MUTED)

    # Gráfico amplo; legenda na mesma linha do título.
    _text(d, (left, 865), "Evolução mensal do indicador", font=_font(58, True), fill=TEXT)
    _text(d, (left, 940), "Último fechamento válido de cada mês", font=_font(38), fill=MUTED)
    d.rectangle((right - 640, 898, right - 592, 924), fill=YELLOW)
    _text(d, (right - 570, 886), "Resultado", font=_font(36), fill=TEXT)
    d.line((right - 300, 910, right - 220, 910), fill=META, width=4)
    d.ellipse((right - 266, 904, right - 254, 916), fill=BG, outline=META, width=3)
    _text(d, (right - 192, 886), "Meta", font=_font(36), fill=TEXT)

    plot_l, plot_r = left + 145, right - 22
    plot_t, plot_b = 1100, 2100
    plot_h, plot_w = plot_b - plot_t, plot_r - plot_l
    parsed = [_value_meta_diff(row) for row in rows]
    finite_values = [v for pair in parsed for v in pair[:2] if v is not None and isfinite(v)]
    high = max([100.0] + finite_values)
    tick_step = max(20, ceil(high / 100) * 20)
    tick_max = ceil(high / tick_step) * tick_step
    scale_max = tick_max * 1.12
    def ypos(v):
        return plot_b - plot_h * max(0, v) / scale_max
    for tick in range(0, int(tick_max) + 1, tick_step):
        y = ypos(tick)
        d.line((plot_l, y, plot_r, y), fill=GRID, width=2)
        _text(d, (plot_l - 28, y), f"{tick}%", anchor="rm", font=_font(34), fill=MUTED)

    if not rows:
        _center(d, "Nenhum lançamento no período selecionado", (plot_l, plot_t, plot_r, plot_b), _font(44), MUTED)
    else:
        step = plot_w / len(rows)
        bar_width = min(156, step * .52)
        points, labels = [], []
        for i, (row, (val, met, _)) in enumerate(zip(rows, parsed)):
            x = plot_l + (i + .5) * step
            if val is not None and isfinite(val):
                top_y = ypos(val)
                if val > 0:
                    d.rectangle((x - bar_width / 2, top_y, x + bar_width / 2, plot_b), fill=YELLOW)
                labels.append((x, top_y, _pct(val)))
            else:
                labels.append((x, plot_b, "—"))
            points.append((x, ypos(met)) if met is not None and isfinite(met) else None)
            label = _month(row.get("competencia"))
            month, _, year = label.partition("/")
            _text(d, (x, plot_b + 52), month, anchor="mt", font=_font(40, True), fill=TEXT)
            _text(d, (x, plot_b + 108), year, anchor="mt", font=_font(32), fill="#334155")
        # Ausência de meta interrompe a linha, em vez de inventar uma meta zero.
        for a, b in zip(points, points[1:]):
            if a is not None and b is not None:
                d.line((a, b), fill=META, width=4)
        for point in points:
            if point:
                x, y = point
                d.ellipse((x - 7, y - 7, x + 7, y + 7), fill=BG, outline=META, width=4)
        # Rótulos sobre fundo branco permanecem legíveis perto da linha de meta.
        for x, y, label in labels:
            f = _fit_font(d, label, 42, step - 12, True)
            box = d.textbbox((x, y - 24), label, font=f, anchor="mb")
            d.rectangle((box[0] - 8, box[1] - 5, box[2] + 8, box[3] + 5), fill=BG)
            _text(d, (x, y - 24), label, font=f, fill=TEXT, anchor="mb")

    d.line((left, 2300, right, 2300), fill=BORDER, width=2)
    now = datetime.now(ZoneInfo("America/Sao_Paulo"))
    _text(d, (left, 2334), f"GESTÃO OPERACIONAL  •  {now:%d/%m/%Y às %H:%M}", font=_font(32), fill="#334155")
    _text(d, (right, 2334), "FECHAMENTO MENSAL", anchor="rt", font=_font(32, True), fill="#334155")
    return im


def gerar_imagens_zip(indicadores, logo_base64=None, logo_mime=None):
    if not logo_base64:
        logo_base64, logo_mime = _logo_from_config()

    groups = _groups(indicadores)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        for idx, (name, rows) in enumerate(groups.items(), 1):
            img = gerar_imagem_indicador(
                name,
                rows,
                idx,
                logo_base64=logo_base64,
                logo_mime=logo_mime,
            )
            png = BytesIO()
            img.save(png, format="PNG", optimize=True, dpi=(300, 300))
            safe = (
                name.lower()
                .replace(" ", "_")
                .replace("á", "a")
                .replace("ã", "a")
                .replace("é", "e")
                .replace("í", "i")
                .replace("ó", "o")
                .replace("ú", "u")
                .replace("ç", "c")
            )
            zf.writestr(f"{idx:02d}_{safe}.png", png.getvalue())
    return buffer.getvalue()


def gerar_pdf_indicadores(indicadores, logo_base64=None, logo_mime=None) -> bytes:
    global LAST_INDICADORES
    LAST_INDICADORES = list(indicadores or [])
    return gerar_imagens_zip(
        LAST_INDICADORES,
        logo_base64=logo_base64,
        logo_mime=logo_mime,
    )


def _patched_download_button(label, data=None, file_name=None, mime=None, **kwargs):
    if str(label).upper().strip() == "EXPORTAR PDF":
        return _ORIGINAL_DOWNLOAD_BUTTON(
            "EXPORTAR IMAGENS",
            data=gerar_imagens_zip(LAST_INDICADORES),
            file_name="indicadores_operacionais_imagens.zip",
            mime="application/zip",
            **kwargs,
        )
    return _ORIGINAL_DOWNLOAD_BUTTON(label, data=data, file_name=file_name, mime=mime, **kwargs)


st.download_button = _patched_download_button

