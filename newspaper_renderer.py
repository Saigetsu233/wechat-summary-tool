# -*- coding: utf-8 -*-
"""将结构化的群聊摘要本地渲染为单页报纸杂志风 PNG。"""

from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont


CANVAS_SIZE = (1440, 1680)
PAPER = "#F4F0E6"
INK = "#171717"
MUTED = "#68645D"
ACCENT = "#B62D2D"
RULE = "#262626"
CARD = "#E9E3D6"


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


def _section_label(draw, x, y, number, title, width):
    number_font = _font(25, bold=True)
    title_font = _font(27, bold=True)
    draw.rectangle((x, y, x + 42, y + 42), fill=ACCENT)
    draw.text((x + 10, y + 5), str(number), font=number_font, fill=PAPER)
    draw.text((x + 56, y + 4), _clean(title), font=title_font, fill=INK)
    draw.line((x, y + 54, x + width, y + 54), fill=RULE, width=2)
    return y + 70


def render_newspaper(digest, output_path):
    """把日报字段渲染成固定 3:4 的单张 PNG。"""
    image = Image.new("RGB", CANVAS_SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    width, height = CANVAS_SIZE
    margin = 76

    masthead_font = _font(61, bold=True)
    small_caps = _font(22, bold=True)
    meta_font = _font(24)
    headline_font = _font(52, bold=True)
    deck_font = _font(27)
    body_font = _font(25)
    topic_font = _font(29, bold=True)
    sidebar_title = _font(30, bold=True)
    tiny_font = _font(21)

    # 报头
    draw.rectangle((0, 0, width, 24), fill=INK)
    draw.text((margin, 58), "CHATROOM DAILY", font=small_caps, fill=ACCENT)
    draw.text((margin, 86), "群 聊 日 报", font=masthead_font, fill=INK)
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
    draw.line((margin, 184, width - margin, 184), fill=RULE, width=6)
    draw.line((margin, 197, width - margin, 197), fill=RULE, width=1)

    # 主标题和导语
    headline_lines = _wrap(
        draw,
        digest.get("headline") or "今日群聊，重点都在这里",
        headline_font,
        width - margin * 2,
        2,
    )
    y = _draw_lines(draw, headline_lines, (margin, 236), headline_font, INK, 12)
    deck_lines = _wrap(
        draw,
        digest.get("lead") or digest.get("overview") or "今日群聊摘要。",
        deck_font,
        width - margin * 2,
        3,
    )
    y = _draw_lines(draw, deck_lines, (margin, y + 20), deck_font, MUTED, 12)
    divider_y = max(y + 28, 462)
    draw.line((margin, divider_y, width - margin, divider_y), fill=RULE, width=3)

    # 两栏杂志式布局
    gutter = 44
    left_width = 790
    right_x = margin + left_width + gutter
    right_width = width - margin - right_x
    content_top = divider_y + 32
    draw.line(
        (right_x - gutter // 2, content_top, right_x - gutter // 2, height - 108),
        fill="#B5AEA2",
        width=2,
    )

    topics = digest.get("topics") if isinstance(digest.get("topics"), list) else []
    topics = [item for item in topics if isinstance(item, dict)][:4]
    if not topics:
        topics = [{"title": "今日重点", "summary": digest.get("overview", "暂无内容") }]

    left_y = content_top
    topic_body_limits = [5, 5, 4, 4]
    for index, topic in enumerate(topics, start=1):
        left_y = _section_label(
            draw,
            margin,
            left_y,
            index,
            topic.get("title") or f"重点 {index}",
            left_width,
        )
        body_lines = _wrap(
            draw,
            topic.get("summary") or "",
            body_font,
            left_width,
            topic_body_limits[min(index - 1, len(topic_body_limits) - 1)],
        )
        left_y = _draw_lines(draw, body_lines, (margin, left_y), body_font, INK, 12)
        left_y += 26
        if left_y > height - 180:
            break

    # MVP 卡片
    right_y = content_top
    draw.rectangle(
        (right_x, right_y, right_x + right_width, right_y + 300),
        fill=CARD,
        outline=RULE,
        width=2,
    )
    draw.text((right_x + 24, right_y + 22), "PERSON OF THE DAY", font=tiny_font, fill=ACCENT)
    draw.text((right_x + 24, right_y + 58), "今日 MVP", font=sidebar_title, fill=INK)
    mvp = digest.get("mvp") if isinstance(digest.get("mvp"), dict) else {}
    mvp_name = _clean(mvp.get("name")) or "神秘群友"
    mvp_title = _clean(mvp.get("title"))
    draw.text((right_x + 24, right_y + 112), mvp_name, font=topic_font, fill=ACCENT)
    if mvp_title:
        title_lines = _wrap(draw, mvp_title, tiny_font, right_width - 48, 1)
        _draw_lines(draw, title_lines, (right_x + 24, right_y + 154), tiny_font, MUTED, 8)
    reason_lines = _wrap(
        draw, mvp.get("reason") or "今日份的存在感已经拉满。",
        tiny_font, right_width - 48, 4,
    )
    _draw_lines(draw, reason_lines, (right_x + 24, right_y + 194), tiny_font, INK, 10)
    right_y += 332

    # 趣味成就
    draw.text((right_x, right_y), "趣味成就", font=sidebar_title, fill=INK)
    draw.line((right_x, right_y + 48, right_x + right_width, right_y + 48), fill=ACCENT, width=4)
    right_y += 72
    achievements = (
        digest.get("achievements")
        if isinstance(digest.get("achievements"), list)
        else []
    )
    achievements = [item for item in achievements if isinstance(item, dict)][:4]
    for achievement in achievements:
        award = _clean(achievement.get("award")) or "今日成就"
        name = _clean(achievement.get("name")) or "群友"
        draw.ellipse((right_x, right_y + 7, right_x + 13, right_y + 20), fill=ACCENT)
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
        right_y += 20
        if right_y > height - 430:
            break

    # 今日金句
    quote = digest.get("quote") if isinstance(digest.get("quote"), dict) else {}
    quote_y = max(right_y + 22, height - 400)
    draw.rectangle(
        (right_x, quote_y, right_x + right_width, height - 126),
        outline=ACCENT,
        width=3,
    )
    draw.text((right_x + 22, quote_y + 20), "今日金句", font=topic_font, fill=ACCENT)
    quote_lines = _wrap(
        draw,
        "「" + _clean(quote.get("text") or "今天也是很有节目效果的一天。") + "」",
        body_font,
        right_width - 44,
        5,
    )
    quote_text_y = _draw_lines(
        draw, quote_lines, (right_x + 22, quote_y + 72), body_font, INK, 13
    )
    speaker = _clean(quote.get("speaker"))
    if speaker:
        speaker_lines = _wrap(draw, f"—— {speaker}", tiny_font, right_width - 44, 1)
        _draw_lines(
            draw,
            speaker_lines,
            (right_x + 22, min(quote_text_y + 18, height - 166)),
            tiny_font,
            MUTED,
            8,
        )

    # 页脚
    draw.line((margin, height - 86, width - margin, height - 86), fill=RULE, width=3)
    draw.text((margin, height - 66), "AI 提炼 · 本地排版 · 原文归属以聊天记录为准", font=tiny_font, fill=MUTED)
    draw.text((width - margin, height - 66), "01", font=tiny_font, fill=ACCENT, anchor="ra")

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    return output
