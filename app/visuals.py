"""Генератор диаграмм и документов для владельца.

Диаграмма: центральный блок + слова/пункты, разложенные по блокам.
Документ: DOCX с заголовком и списком.
"""
import os
import re
import time

from PIL import Image, ImageDraw, ImageFont

from . import config

try:
    from docx import Document
    from docx.shared import Pt
    HAS_DOCX = True
except Exception:  # noqa: BLE001
    HAS_DOCX = False

VISUAL_DIR = os.path.join(config.DATA_DIR, "visuals")
os.makedirs(VISUAL_DIR, exist_ok=True)


def _font_path():
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _font(size, bold=False):
    path = _font_path()
    try:
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def parse_text(text):
    """Заголовок + пункты. Понимает строки, запятые, точки с запятой, •."""
    raw = (text or "").strip()
    if not raw:
        return "Схема", []
    # сохраняем строки: каждая строка — отдельный блок
    parts = [re.sub(r"[ \t]+", " ", line).strip(" -•\t")
             for line in raw.splitlines() if line.strip(" -•\t")]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"[;•]+", raw) if p.strip(" -•\t")]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r",|\.\s+", raw) if p.strip()]
    if not parts:
        parts = [raw]
    title = parts[0]
    items = parts[1:]
    if not items and len(title.split()) > 6:
        # длинную фразу режем на слова-блоки
        items = title.split()
        title = "Схема"
    if not items:
        items = [title]
    # чистим и ограничиваем
    clean = []
    for item in items:
        item = re.sub(r"^(сделай|создай|построй|нарисуй)\s+", "", item, flags=re.I).strip()
        if item and item.lower() not in [x.lower() for x in clean]:
            clean.append(item[:60])
    return title[:80], clean[:10]


def _wrap(draw, text, font, width):
    words = text.split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=font)[2] <= width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines[:3]


def _block(draw, xy, text, fill, outline, font):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=18, fill=fill, outline=outline, width=3)
    lines = _wrap(draw, text, font, (x2 - x1) - 26)
    total = len(lines) * (font.size + 6)
    ty = y1 + (y2 - y1 - total) // 2
    for line in lines:
        tw = draw.textbbox((0, 0), line, font=font)[2]
        draw.text((x1 + (x2 - x1 - tw) // 2, ty), line, font=font, fill=(245, 248, 255))
        ty += font.size + 6


def diagram(text, caption=None):
    """Сделать блок-схему/майнд-карту и вернуть путь PNG."""
    title, items = parse_text(text)
    W, H = 1600, 1050
    img = Image.new("RGB", (W, H), (12, 17, 30))
    draw = ImageDraw.Draw(img)
    # фон
    for y in range(H):
        shade = int(12 + 18 * y / H)
        draw.line([(0, y), (W, y)], fill=(shade, shade + 4, shade + 14))
    f_title = _font(34, True)
    f_item = _font(24, True)
    f_tag = _font(18)

    cx, cy = W // 2, H // 2
    center = (cx - 260, cy - 85, cx + 260, cy + 85)
    draw.line([(cx, cy), (cx, cy)], fill=(0, 0, 0))
    colors = [
        ((34, 88, 160), (92, 160, 235)), ((34, 120, 96), (92, 205, 160)),
        ((120, 78, 160), (185, 135, 235)), ((160, 92, 40), (235, 160, 92)),
        ((150, 50, 90), (230, 110, 150)), ((50, 100, 130), (110, 190, 225)),
    ]
    n = len(items)
    positions = []
    cols = 2 if n > 1 else 1
    per_col = (n + cols - 1) // cols
    left = [i for i in range(n) if i % 2 == 0]
    right = [i for i in range(n) if i % 2 == 1]
    # координаты блоков вокруг центра
    for side, indexes in ((-1, left), (1, right)):
        for row, idx in enumerate(indexes):
            if side < 0:
                bx = cx - 640
            else:
                bx = cx + 340
            by = cy - (len(indexes) - 1) * 90 + row * 180 - 75
            positions.append((idx, (bx, by, bx + 300, by + 120)))
    # линии
    for idx, box in positions:
        x1, y1, x2, y2 = box
        draw.line([(cx, cy), ((x1 + x2) // 2, (y1 + y2) // 2)], fill=(80, 105, 145), width=4)
    # центральный блок
    _block(draw, center, title, (28, 45, 75), (120, 170, 235), f_title)
    # боковые блоки
    for idx, box in positions:
        fill, outline = colors[idx % len(colors)]
        _block(draw, box, items[idx], fill, outline, f_item)
    # подпись
    draw.text((30, H - 45), "Ева · Aetherlyn · «Eva ии помощница»",
              font=f_tag, fill=(150, 170, 200))
    path = os.path.join(VISUAL_DIR, "diagram_%d.png" % int(time.time() * 1000))
    img.save(path, "PNG")
    return path, (caption or title)


def document(text, caption=None):
    """Сделать DOCX-документ из текста и вернуть путь."""
    title, items = parse_text(text)
    if HAS_DOCX:
        doc = Document()
        doc.add_heading(title, level=1)
        for item in items:
            p = doc.add_paragraph(item, style="List Bullet")
            for run in p.runs:
                run.font.size = Pt(12)
        # исходный текст целиком, если он длиннее списка
        source = (text or "").strip()
        if source and len(source) > 80:
            doc.add_paragraph("")
            doc.add_paragraph(source[:5000])
        path = os.path.join(VISUAL_DIR, "document_%d.docx" % int(time.time() * 1000))
        doc.save(path)
        return path, (caption or title)
    # запасной вариант — текстовый файл
    path = os.path.join(VISUAL_DIR, "document_%d.txt" % int(time.time() * 1000))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write((text or "").strip())
    return path, (caption or title)


def chart(text, caption=None):
    """Простая столбчатая диаграмма из строк вида «слово: число»."""
    rows = re.findall(r"([^\n:;]+?)\s*[:=]\s*(\d+(?:[.,]\d+)?)", text or "")
    if len(rows) < 2:
        return diagram(text, caption)
    labels = [r[0].strip()[:20] for r in rows][:8]
    values = [float(r[1].replace(",", ".")) for r in rows][:8]
    W, H = 1200, 700
    img = Image.new("RGB", (W, H), (15, 20, 34))
    draw = ImageDraw.Draw(img)
    f = _font(22, True)
    f_small = _font(18)
    margin, base = 80, H - 110
    maxv = max(values) or 1
    bar_w = min(110, (W - 2 * margin) // max(len(values), 1) - 25)
    for i, (label, value) in enumerate(zip(labels, values)):
        x = margin + i * (bar_w + 35)
        h = int((base - 80) * value / maxv)
        draw.rounded_rectangle((x, base - h, x + bar_w, base), radius=10,
                               fill=(70, 130, 210), outline=(130, 190, 255))
        draw.text((x, base - h - 30), str(value), font=f_small, fill=(230, 240, 255))
        draw.text((x, base + 15), label, font=f_small, fill=(180, 200, 225))
    draw.text((30, 25), (caption or "Диаграмма")[:60], font=f, fill=(230, 240, 255))
    draw.text((30, H - 45), "Ева · Aetherlyn · «Eva ии помощница»",
              font=f_small, fill=(150, 170, 200))
    path = os.path.join(VISUAL_DIR, "chart_%d.png" % int(time.time() * 1000))
    img.save(path, "PNG")
    return path, (caption or "Диаграмма")
