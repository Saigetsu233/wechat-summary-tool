# -*- coding: utf-8 -*-
"""将结构化的群聊摘要本地渲染为单页报纸杂志风 PNG。"""

from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont, ImageOps


CANVAS_SIZE = (1440, 2400)
PAPER = "#FFF9F0"
INK = "#182033"
MUTED = "#6A6874"
ACCENT = "#F04F5F"
PURPLE = "#6C5CE7"
CYAN = "#14B8A6"
SUN = "#FFC857"
RULE = "#252838"
CARD = "#F2EDFF"
PASTELS = ("#FFE3E6", "#DDF8F3", "#EEE9FF")


def _font(size, bold=False):
    windows_fonts = Path("C:/Windows/Fonts")
    candidates = (
        [windows_fonts / "msyhbd.ttc", windows_fonts / "simhei.ttf"]
        if bold
        else [windows_fonts / "msyh.ttc", windows_fonts / "simsun.ttc"]
    )
    candidates.extend(
        [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _clean(value):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _wrap(draw, text, font, max_width, max_lines):
    """按实际像素宽度换行，超出行数时以省略号收尾。"""
    text = _clean(text)
    if not text:
        return []
    lines = []
    current = ""
    consumed = 0
    for index, char in enumerate(text):
        if current and char in "，。！？；：、”’）》】…":
            current += char
            consumed = index + 1
            continue
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current.rstrip())
            current = char.lstrip()
            if len(lines) >= max_lines:
                consumed = index
                break
        else:
            current = candidate
        consumed = index + 1
    if len(lines) < max_lines and current:
        lines.append(current.rstrip())
    truncated = consumed < len(text)
    if truncated and lines:
        last = lines[-1].rstrip(" …")
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines[:max_lines]


def _draw_lines(draw, lines, xy, font, fill, line_gap=12):
    x, y = xy
    bbox = draw.textbbox((0, 0), "中Ag", font=font)
    line_height = bbox[3] - bbox[1]
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + line_gap
    return y


def _draw_sparkles(draw, x, y, color=SUN, scale=1.0):
    """用矢量小星星做装饰，不依赖系统 emoji 字体。"""
    size = int(12 * scale)
    draw.polygon(
        [(x, y - size), (x + 4, y - 4), (x + size, y), (x + 4, y + 4),
         (x, y + size), (x - 4, y + 4), (x - size, y), (x - 4, y - 4)],
        fill=color,
    )
    small = max(4, int(size * 0.45))
    draw.polygon(
        [(x + size + 13, y + 12 - small), (x + size + 16, y + 9),
         (x + size + 13 + small, y + 12), (x + size + 16, y + 15),
         (x + size + 13, y + 12 + small), (x + size + 10, y + 15),
         (x + size + 13 - small, y + 12), (x + size + 10, y + 9)],
        fill=color,
    )


def _draw_face_sticker(draw, x, y, radius=34, sunglasses=False):
    """绘制报刊剪贴画风笑脸贴纸。"""
    draw.ellipse(
        (x - radius - 5, y - radius + 6, x + radius + 5, y + radius + 16),
        fill="#D8D0C4",
    )
    draw.ellipse(
        (x - radius - 6, y - radius - 6, x + radius + 6, y + radius + 6),
        fill="white",
    )
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=SUN, outline=RULE, width=3,
    )
    if sunglasses:
        draw.rounded_rectangle(
            (x - 24, y - 13, x - 3, y + 2), radius=4, fill=RULE
        )
        draw.rounded_rectangle(
            (x + 3, y - 13, x + 24, y + 2), radius=4, fill=RULE
        )
        draw.line((x - 3, y - 7, x + 3, y - 7), fill=RULE, width=3)
    else:
        draw.ellipse((x - 17, y - 12, x - 10, y - 3), fill=RULE)
        draw.ellipse((x + 10, y - 12, x + 17, y - 3), fill=RULE)
    draw.arc((x - 20, y - 2, x + 20, y + 25), 12, 168, fill=RULE, width=4)


def _section_label(draw, x, y, number, title, width):
    number_font = _font(25, bold=True)
    title_font = _font(27, bold=True)
    color = (ACCENT, CYAN, PURPLE)[(number - 1) % 3]
    draw.rounded_rectangle((x, y, x + 42, y + 42), radius=8, fill=color)
    draw.text((x + 10, y + 5), str(number), font=number_font, fill="white")
    draw.rounded_rectangle(
        (x + 51, y - 2, x + width, y + 45), radius=10,
        fill=PASTELS[(number - 1) % len(PASTELS)],
    )
    draw.text((x + 56, y + 4), _clean(title), font=title_font, fill=INK)
    draw.line((x, y + 56, x + width, y + 56), fill=color, width=3)
    return y + 70


def _paste_topic_image(canvas, source, box, accent):
    """把图片模型生成的插画裁成圆角杂志缩略图。"""
    left, top, right, bottom = box
    size = (right - left, bottom - top)
    fitted = ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=16, fill=255)
    shadow_box = (left + 6, top + 7, right + 6, bottom + 7)
    ImageDraw.Draw(canvas).rounded_rectangle(shadow_box, radius=16, fill="#D8D0C5")
    canvas.paste(fitted, (left, top), mask)
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius=16, outline=accent, width=3)


def render_newspaper(digest, output_path, topic_images=None):
    """把日报字段与可选 AI 插画渲染成一张长版 PNG。"""
    image = Image.new("RGB", CANVAS_SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    topic_images = topic_images if isinstance(topic_images, (list, tuple)) else []
    width, height = CANVAS_SIZE
    margin = 76

    masthead_font = _font(55, bold=True)
    small_caps = _font(20, bold=True)
    meta_font = _font(22)
    headline_font = _font(42, bold=True)
    deck_font = _font(22)
    body_font = _font(20)
    topic_font = _font(25, bold=True)
    sidebar_title = _font(27, bold=True)
    tiny_font = _font(18)

    # 背景纸张纹理与报头
    for dot_y in range(36, height - 90, 44):
        for dot_x in range(30, width - 20, 44):
            draw.ellipse((dot_x, dot_y, dot_x + 2, dot_y + 2), fill="#EDE6DA")
    draw.rectangle((0, 0, width, 24), fill=INK)
    draw.rectangle((0, 24, width * 0.42, 31), fill=ACCENT)
    draw.rectangle((width * 0.42, 24, width * 0.72, 31), fill=SUN)
    draw.rectangle((width * 0.72, 24, width, 31), fill=CYAN)
    draw.rounded_rectangle((margin, 52, margin + 225, 88), radius=18, fill=INK)
    draw.text((margin + 15, 57), "CHATROOM DAILY", font=small_caps, fill="white")
    draw.text((margin, 86), "群 聊 日 报", font=masthead_font, fill=INK)
    _draw_sparkles(draw, margin + 352, 122, color=ACCENT, scale=1.25)
    draw.rounded_rectangle(
        (margin + 440, 104, margin + 676, 143), radius=19,
        fill="#DDF8F3", outline=CYAN, width=2,
    )
    draw.text(
        (margin + 458, 110), "今日份 · 有点东西",
        font=_font(18, bold=True), fill=INK,
    )
    date_text = _clean(digest.get("date")) or "TODAY"
    group_text = _clean(digest.get("group_name")) or "未命名群聊"
    count_text = _clean(digest.get("message_count")) or "0"
    right_meta = f"{date_text}\n{group_text}  |  {count_text} 条消息"
    draw.multiline_text(
        (width - margin, 67),
        right_meta,
        font=meta_font,
        fill=MUTED,
        anchor="ra",
        spacing=10,
    )
    draw.line((margin, 184, width - margin, 184), fill=RULE, width=5)
    draw.line((margin, 197, width - margin, 197), fill=PURPLE, width=2)

    # 主标题和导语
    headline_lines = _wrap(
        draw,
        digest.get("headline") or "今日群聊，重点都在这里",
        headline_font,
        width - margin * 2 - 95,
        2,
    )
    y = _draw_lines(draw, headline_lines, (margin, 236), headline_font, INK, 12)
    if topic_images and isinstance(topic_images[0], Image.Image):
        _paste_topic_image(
            image, topic_images[0],
            (width - margin - 86, 232, width - margin, 316), PURPLE,
        )
    deck_lines = _wrap(
        draw,
        digest.get("lead") or digest.get("overview") or "今日群聊摘要。",
        deck_font,
        width - margin * 2,
        3,
    )
    y = _draw_lines(draw, deck_lines, (margin, y + 20), deck_font, MUTED, 12)
    divider_y = max(y + 28, 448)
    draw.line((margin, divider_y, width - margin, divider_y), fill=RULE, width=3)
    draw.rounded_rectangle(
        (margin, divider_y - 15, margin + 158, divider_y + 15),
        radius=15, fill=ACCENT,
    )
    draw.text(
        (margin + 16, divider_y - 12), "TODAY'S PICKS",
        font=_font(16, bold=True), fill="white",
    )

    # 高密度彩色信息图布局：左侧话题卡，右侧人物与成就榜。
    gutter = 36
    left_width = 820
    right_x = margin + left_width + gutter
    right_width = width - margin - right_x
    content_top = divider_y + 32
    draw.rounded_rectangle(
        (margin - 12, content_top - 12, margin + left_width + 12, height - 108),
        radius=24, fill="#F8F5FF", outline="#CDC6F7", width=2,
    )

    topics = digest.get("topics") if isinstance(digest.get("topics"), list) else []
    topics = [item for item in topics if isinstance(item, dict)][:10]
    if not topics:
        topics = [{"title": "今日重点", "summary": digest.get("overview", "暂无内容") }]

    topic_gap = 16
    topic_card_width = (left_width - topic_gap) // 2
    topic_card_height = 338
    for index, topic in enumerate(topics, start=1):
        row, column = divmod(index - 1, 2)
        card_x = margin + column * (topic_card_width + topic_gap)
        card_y = content_top + row * (topic_card_height + topic_gap)
        colors = (ACCENT, "#2F80ED", CYAN, "#F59E0B", PURPLE)
        pales = ("#FFF0F2", "#EAF4FF", "#E9FBF7", "#FFF6DD", "#F0EDFF")
        color = colors[(index - 1) % len(colors)]
        pale = pales[(index - 1) % len(pales)]
        draw.rounded_rectangle(
            (card_x, card_y, card_x + topic_card_width,
             card_y + topic_card_height),
            radius=20, fill=pale, outline=color, width=3,
        )
        draw.ellipse(
            (card_x + 16, card_y + 14, card_x + 62, card_y + 60), fill=color
        )
        draw.text(
            (card_x + 39, card_y + 37), str(index),
            font=_font(22, bold=True), fill="white", anchor="mm",
        )
        card_title_font = _font(22, bold=True)
        title_lines = _wrap(
            draw, topic.get("title") or f"重点 {index}",
            card_title_font, topic_card_width - 88, 2,
        )
        _draw_lines(
            draw, title_lines, (card_x + 74, card_y + 18),
            card_title_font, color, 5,
        )
        illustration = topic_images[index - 1] if index <= len(topic_images) else None
        text_x = card_x + 18
        text_y = card_y + 78
        text_width = topic_card_width - 36
        if isinstance(illustration, Image.Image):
            thumb_box = (
                card_x + topic_card_width - 168,
                card_y + topic_card_height - 156,
                card_x + topic_card_width - 18,
                card_y + topic_card_height - 18,
            )
            _paste_topic_image(image, illustration, thumb_box, color)
            text_width = topic_card_width - 198
        card_body_font = _font(18)
        body_lines = _wrap(
            draw,
            topic.get("summary") or "",
            card_body_font,
            text_width,
            8,
        )
        _draw_lines(draw, body_lines, (text_x, text_y), card_body_font, INK, 8)

    # MVP 卡片
    right_y = content_top
    mvp = digest.get("mvp") if isinstance(digest.get("mvp"), dict) else {}
    mvp_name = _clean(mvp.get("name")) or "神秘群友"
    mvp_title = _clean(mvp.get("title"))
    reason_lines = _wrap(
        draw, mvp.get("reason") or "今日份的存在感已经拉满。",
        tiny_font, right_width - 48, 5,
    )
    mvp_card_height = max(300, 224 + len(reason_lines) * 29)
    draw.rounded_rectangle(
        (right_x + 8, right_y + 9,
         right_x + right_width + 8, right_y + mvp_card_height + 9),
        radius=20, fill="#D9D2C7",
    )
    draw.rounded_rectangle(
        (right_x, right_y, right_x + right_width, right_y + mvp_card_height),
        radius=20, fill=CARD, outline=PURPLE, width=3,
    )
    draw.text((right_x + 24, right_y + 22), "PERSON OF THE DAY", font=tiny_font, fill=ACCENT)
    draw.text((right_x + 24, right_y + 58), "今日 MVP", font=sidebar_title, fill=INK)
    draw.text((right_x + 24, right_y + 112), mvp_name, font=topic_font, fill=ACCENT)
    _draw_sparkles(draw, right_x + right_width - 52, right_y + 48, color=SUN, scale=1.0)
    if mvp_title:
        title_lines = _wrap(draw, mvp_title, tiny_font, right_width - 48, 1)
        _draw_lines(draw, title_lines, (right_x + 24, right_y + 154), tiny_font, MUTED, 8)
    _draw_lines(draw, reason_lines, (right_x + 24, right_y + 194), tiny_font, INK, 10)
    right_y += mvp_card_height + 34

    # 趣味成就
    draw.text((right_x, right_y), "趣味成就", font=sidebar_title, fill=INK)
    draw.line((right_x, right_y + 48, right_x + right_width, right_y + 48), fill=CYAN, width=5)
    right_y += 72
    achievements = (
        digest.get("achievements")
        if isinstance(digest.get("achievements"), list)
        else []
    )
    achievements = [item for item in achievements if isinstance(item, dict)][:10]
    for achievement_index, achievement in enumerate(achievements):
        award = _clean(achievement.get("award")) or "今日成就"
        name = _clean(achievement.get("name")) or "群友"
        bullet_color = (ACCENT, CYAN, PURPLE)[achievement_index % 3]
        draw.ellipse((right_x, right_y + 7, right_x + 13, right_y + 20), fill=bullet_color)
        title_lines = _wrap(
            draw, f"{award} | {name}", tiny_font, right_width - 30, 2
        )
        right_y = _draw_lines(
            draw, title_lines, (right_x + 28, right_y), tiny_font, INK, 8
        )
        reason_lines = _wrap(
            draw, achievement.get("reason") or "", tiny_font, right_width - 28, 2
        )
        right_y = _draw_lines(
            draw, reason_lines, (right_x + 28, right_y + 3), tiny_font, MUTED, 8
        )
        right_y += 13
        if right_y > height - 780:
            break

    # 今日金句
    quote = digest.get("quote") if isinstance(digest.get("quote"), dict) else {}
    quote_y = right_y + 22
    quote_bottom = min(quote_y + 220, height - 520)
    draw.rounded_rectangle(
        (right_x, quote_y, right_x + right_width, quote_bottom),
        radius=18, fill="#FFF0D4", outline=ACCENT, width=3,
    )
    draw.text((right_x + 22, quote_y + 20), "今日金句", font=topic_font, fill=ACCENT)
    quote_lines = _wrap(
        draw,
        "「" + _clean(quote.get("text") or "今天也是很有节目效果的一天。") + "」",
        tiny_font,
        right_width - 44,
        4,
    )
    quote_text_y = _draw_lines(
        draw, quote_lines, (right_x + 22, quote_y + 68), tiny_font, INK, 11
    )
    speaker = _clean(quote.get("speaker"))
    if speaker:
        speaker_lines = _wrap(draw, f"—— {speaker}", tiny_font, right_width - 44, 1)
        _draw_lines(
            draw,
            speaker_lines,
            (right_x + 22, min(quote_text_y + 18, quote_bottom - 42)),
            tiny_font,
            MUTED,
            8,
        )

    # 页脚
    if len(topic_images) > 1 and isinstance(topic_images[-1], Image.Image):
        _paste_topic_image(
            image, topic_images[-1],
            (right_x + right_width - 76, quote_y + 14,
             right_x + right_width - 16, quote_y + 74), ACCENT,
        )

    # 用同一批 AI 插画组成小型画报，填充侧栏并强化参考图的漫画感。
    gallery_images = [item for item in topic_images[:4] if isinstance(item, Image.Image)]
    gallery_y = quote_bottom + 30
    if gallery_images and gallery_y < height - 250:
        draw.text((right_x, gallery_y), "今日画报", font=sidebar_title, fill=INK)
        draw.line(
            (right_x, gallery_y + 46, right_x + right_width, gallery_y + 46),
            fill=PURPLE, width=5,
        )
        gallery_y += 68
        tile_gap = 12
        tile_width = (right_width - tile_gap) // 2
        available_height = height - 126 - gallery_y
        tile_height = max(90, (available_height - tile_gap) // 2)
        for gallery_index, illustration in enumerate(gallery_images):
            gallery_row, gallery_column = divmod(gallery_index, 2)
            tile_x = right_x + gallery_column * (tile_width + tile_gap)
            tile_y = gallery_y + gallery_row * (tile_height + tile_gap)
            _paste_topic_image(
                image,
                illustration,
                (tile_x, tile_y, tile_x + tile_width, tile_y + tile_height),
                (ACCENT, CYAN, PURPLE, "#F59E0B")[gallery_index],
            )
    draw.line((margin, height - 86, width - margin, height - 86), fill=RULE, width=3)
    draw.text((margin, height - 66), "AI 提炼 · 本地排版 · 原文归属以聊天记录为准", font=tiny_font, fill=MUTED)
    draw.text((width - margin, height - 66), "01", font=tiny_font, fill=ACCENT, anchor="ra")

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    return output
