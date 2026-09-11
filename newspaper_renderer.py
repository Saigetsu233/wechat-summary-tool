# -*- coding: utf-8 -*-
"""把结构化群聊摘要渲染为固定的手绘漫画信息图。"""

from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont, ImageOps

import platform_support


CANVAS_SIZE = (1536, 1536)
PAPER = "#FFFDF6"
NAVY = "#154F80"
NAVY_DARK = "#0B355F"
INK = "#17283D"
MUTED = "#526579"
WHITE = "#FFFFFF"
BLUE = "#1596D2"
CYAN = "#08AFC7"
PINK = "#EF4D78"
GREEN = "#12A36E"
ORANGE = "#F59B23"
PURPLE = "#7046C1"
YELLOW = "#F6C531"

SECTION_COLORS = (PINK, BLUE, GREEN, ORANGE, PURPLE, CYAN)
SECTION_PALES = ("#FFF0F5", "#EEF7FF", "#EDFBF5", "#FFF7E8", "#F5F0FF", "#ECFBFC")


def _font(size, bold=False, display=False):
    kind = "display" if display else ("bold" if bold else "regular")
    for candidate in platform_support.font_candidates(kind):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _clean(value):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _wrap(draw, text, font, max_width, max_lines):
    text = _clean(text)
    if not text:
        return []
    lines, current, consumed = [], "", 0
    for index, char in enumerate(text):
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            if char in "，。！？；：、”’）》】…" and lines:
                current += char
                consumed = index + 1
                continue
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
    if consumed < len(text) and lines:
        last = lines[-1].rstrip(" …")
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines[:max_lines]


def _draw_lines(draw, lines, xy, font, fill=INK, gap=6, bullet=False):
    x, y = xy
    bbox = draw.textbbox((0, 0), "中Ag", font=font)
    line_height = bbox[3] - bbox[1]
    for line in lines:
        if bullet:
            draw.ellipse((x, y + 10, x + 6, y + 16), fill=fill)
            draw.text((x + 16, y), line, font=font, fill=fill)
        else:
            draw.text((x, y), line, font=font, fill=fill)
        y += line_height + gap
    return y


def _draw_points(draw, points, xy, font, max_width, max_lines, fill=INK, gap=5):
    """按“一条要点一个圆点”绘制，折行只缩进不再补点。"""
    x, y = xy
    bbox = draw.textbbox((0, 0), "中Ag", font=font)
    line_height = bbox[3] - bbox[1]
    items = [str(point).strip() for point in points if str(point or "").strip()]
    remaining = max_lines
    per_point = max_lines if len(items) <= 1 else 2
    for point in items:
        if remaining <= 0:
            break
        for index, line in enumerate(
            _wrap(draw, point, font, max_width - 16, min(per_point, remaining))
        ):
            if index == 0:
                draw.ellipse((x, y + 7, x + 6, y + 13), fill=fill)
            draw.text((x + 16, y), line, font=font, fill=fill)
            y += line_height + gap
            remaining -= 1
    return y


def _points_of(item, fallback_keys=("summary", "reason")):
    points = item.get("points") if isinstance(item.get("points"), list) else []
    cleaned = [str(point).strip() for point in points if str(point or "").strip()]
    if cleaned:
        return cleaned
    for key in fallback_keys:
        text = str(item.get(key) or "").strip()
        if text:
            return [text]
    return []


def _outlined_round_rect(draw, box, radius, fill, outline, width=3, shadow=True):
    left, top, right, bottom = [int(v) for v in box]
    if shadow:
        draw.rounded_rectangle((left + 4, top + 5, right + 4, bottom + 5), radius=radius, fill="#D9E3E8")
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
    draw.rounded_rectangle((left + 2, top + 1, right - 2, bottom - 2), radius=max(3, radius - 2), outline=outline, width=1)


def _section_header(draw, box, number, title, color):
    left, top, right, bottom = box
    _outlined_round_rect(draw, box, 20, color, NAVY_DARK, width=3, shadow=False)
    draw.ellipse((left + 10, top + 7, left + 60, bottom - 7), fill=WHITE, outline=NAVY_DARK, width=3)
    draw.text((left + 35, (top + bottom) // 2), str(number), font=_font(31, True), fill=color, anchor="mm")
    draw.text((left + 72, (top + bottom) // 2 - 1), title, font=_font(31, True, True), fill=WHITE, anchor="lm")


def _sparkles(draw, x, y, color=YELLOW):
    draw.polygon([(x, y - 12), (x + 4, y - 4), (x + 12, y), (x + 4, y + 4), (x, y + 12), (x - 4, y + 4), (x - 12, y), (x - 4, y - 4)], fill=color)
    draw.ellipse((x + 18, y + 10, x + 27, y + 19), fill=color)


def _paste_sticker(canvas, source, box, accent, circular=False):
    if not isinstance(source, Image.Image):
        return
    left, top, right, bottom = [int(v) for v in box]
    size = (right - left, bottom - top)
    fitted = ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    mask_draw = ImageDraw.Draw(mask)
    if circular:
        mask_draw.ellipse((0, 0, size[0] - 1, size[1] - 1), fill=255)
    else:
        mask_draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=16, fill=255)
    border = ImageDraw.Draw(canvas)
    if circular:
        border.ellipse((left - 5, top - 5, right + 5, bottom + 5), fill=WHITE, outline=accent, width=4)
    else:
        border.rounded_rectangle((left - 5, top - 5, right + 5, bottom + 5), radius=19, fill=WHITE, outline=accent, width=4)
    canvas.paste(fitted, (left, top), mask)


def _draw_chat_icon(draw, x, y):
    draw.rounded_rectangle((x, y, x + 58, y + 42), radius=14, fill=WHITE)
    draw.polygon([(x + 12, y + 38), (x + 9, y + 55), (x + 27, y + 42)], fill=WHITE)
    for offset in (17, 30, 43):
        draw.ellipse((x + offset - 3, y + 18, x + offset + 3, y + 24), fill=NAVY)


def _topic_card(canvas, draw, topic, sticker, box, index):
    left, top, right, bottom = box
    color = SECTION_COLORS[(index - 1) % len(SECTION_COLORS)]
    pale = SECTION_PALES[(index - 1) % len(SECTION_PALES)]
    _outlined_round_rect(draw, box, 18, pale, color, width=3, shadow=False)
    draw.ellipse((left + 12, top + 10, left + 59, top + 57), fill=color, outline=WHITE, width=2)
    draw.text((left + 35, top + 33), str(index), font=_font(27, True), fill=WHITE, anchor="mm")
    title_font = _font(24, True)
    title_lines = _wrap(draw, topic.get("title") or f"今日话题 {index}", title_font, right - left - 82, 2)
    body_top = _draw_lines(draw, title_lines, (left + 68, top + 16), title_font, color, 4)
    if isinstance(sticker, Image.Image):
        _paste_sticker(canvas, sticker, (right - 124, bottom - 112, right - 12, bottom - 12), color)
        text_width = right - left - 150
    else:
        text_width = right - left - 28
    body_font = _font(18)
    body_top = max(body_top + 6, top + 67)
    max_lines = max(2, (bottom - 16 - body_top) // 23)
    _draw_points(draw, _points_of(topic), (left + 16, body_top), body_font, text_width, min(6, max_lines))
    _sparkles(draw, right - 142, bottom - 46, color)


def _rank_card(canvas, draw, item, sticker, box, rank):
    left, top, right, bottom = box
    colors = ("#E5A70D", "#6E9BC6", "#C87843")
    pale = ("#FFF8DA", "#F2F8FF", "#FFF2E8")[rank - 1]
    color = colors[rank - 1]
    _outlined_round_rect(draw, box, 18, pale, color, width=3, shadow=False)
    if item.get("_placeholder"):
        draw.ellipse(
            ((left + right) // 2 - 45, top + 44, (left + right) // 2 + 45, top + 134),
            fill=WHITE, outline=color, width=3,
        )
        draw.text(((left + right) // 2, top + 88), "☆", font=_font(48, True), fill=color, anchor="mm")
        draw.text(((left + right) // 2, top + 144), "本日留空", font=_font(24, True), fill=INK, anchor="ma")
        ribbon_y = top + 190
        draw.rounded_rectangle((left + 14, ribbon_y, right - 14, ribbon_y + 39), radius=10, fill="#9AA9B8")
        draw.text(((left + right) // 2, ribbon_y + 19), "不凑数", font=_font(18, True), fill=WHITE, anchor="mm")
        reason_font = _font(16)
        _draw_lines(
            draw,
            _wrap(draw, "只给当天有明确贡献的群友上榜。", reason_font, right - left - 28, 3),
            (left + 14, ribbon_y + 53), reason_font, MUTED, 4, bullet=True,
        )
        return
    image_size = min(116, right - left - 38)
    image_left = (left + right - image_size) // 2
    _paste_sticker(canvas, sticker, (image_left, top + 22, image_left + image_size, top + 22 + image_size), color, circular=True)
    draw.ellipse((image_left - 7, top + 10, image_left + 35, top + 52), fill=YELLOW, outline=NAVY_DARK, width=3)
    draw.text((image_left + 14, top + 31), str(rank), font=_font(24, True), fill=NAVY_DARK, anchor="mm")
    name = _clean(item.get("name"))
    draw.text(((left + right) // 2, top + 154), name, font=_font(25, True), fill=INK, anchor="ma")
    title = _clean(item.get("title")) or ("摸鱼大王" if rank == 1 else f"摸鱼第 {rank} 名")
    ribbon_y = top + 190
    draw.rounded_rectangle((left + 14, ribbon_y, right - 14, ribbon_y + 39), radius=10, fill=color)
    draw.text(((left + right) // 2, ribbon_y + 19), title, font=_font(18, True), fill=WHITE, anchor="mm")
    reason_font = _font(16)
    _draw_points(
        draw,
        _points_of(item, ("reason",)) or ["今日贡献稳定，节目效果在线。"],
        (left + 14, ribbon_y + 53), reason_font, right - left - 28, 4,
    )


def _achievement_card(canvas, draw, item, sticker, box, index):
    left, top, right, bottom = box
    color = SECTION_COLORS[index % len(SECTION_COLORS)]
    _outlined_round_rect(draw, box, 16, WHITE, "#B8C5E8", width=2, shadow=False)
    _paste_sticker(canvas, sticker, (left + 12, top + 14, left + 82, top + 84), color, circular=True)
    award_font = _font(18, True)
    _draw_lines(draw, _wrap(draw, item.get("award") or "今日成就", award_font, right - left - 108, 1), (left + 96, top + 13), award_font, PURPLE, 2)
    name = _clean(item.get("name"))
    if name:
        draw.text((left + 96, top + 43), f"【{name}】", font=_font(15, True), fill=color)
    reason_font = _font(14)
    _draw_lines(draw, _wrap(draw, item.get("reason") or "今日表现非常在线。", reason_font, right - left - 108, 3), (left + 96, top + 69), reason_font, INK, 2)


def _numbered_list(draw, items, box, color, max_items, quote_mode=False):
    left, top, right, bottom = box
    body_font = _font(16)
    row_height = max(37, (bottom - top) // max(1, max_items))
    for index, item in enumerate(items[:max_items], start=1):
        row_y = top + (index - 1) * row_height
        draw.rounded_rectangle((left, row_y, right, row_y + row_height - 5), radius=10, fill=WHITE)
        draw.ellipse((left + 8, row_y + 6, left + 40, row_y + 38), fill=color)
        draw.text((left + 24, row_y + 22), str(index), font=_font(18, True), fill=WHITE, anchor="mm")
        if isinstance(item, dict):
            text, speaker = _clean(item.get("text")), _clean(item.get("speaker"))
        else:
            text, speaker = _clean(item), ""
        if quote_mode and text:
            text = f"“{text}”"
        speaker_font = _font(14)
        # 按实际宽度让位，长昵称不再压住金句正文。
        speaker_width = (
            int(draw.textlength(f"— {speaker}", font=speaker_font)) + 18 if speaker else 0
        )
        max_text_width = right - left - 70 - speaker_width
        _draw_lines(draw, _wrap(draw, text, body_font, max_text_width, 2), (left + 51, row_y + 8), body_font, INK, 2)
        if speaker:
            draw.text((right - 10, row_y + 12), f"— {speaker}", font=speaker_font, fill=MUTED, anchor="ra")


def save_poster_image(poster, output_path):
    """保存整图模式下由图片模型直接绘制的海报，不再叠加本地排版。"""
    if not isinstance(poster, Image.Image):
        raise ValueError("整图海报数据无效。")
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    poster.convert("RGB").save(output, format="PNG", optimize=True)
    return output


def render_newspaper(digest, output_path, topic_images=None):
    """渲染固定 1:1 手绘漫画日报，栏目位置不会随模型输出漂移。"""
    image = Image.new("RGB", CANVAS_SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    images = list(topic_images) if isinstance(topic_images, (list, tuple)) else []
    width, height = CANVAS_SIZE

    draw.rectangle((0, 0, width, 126), fill=NAVY)
    draw.rectangle((0, 112, width, 126), fill=NAVY_DARK)
    _draw_chat_icon(draw, 22, 29)
    group_name = _clean(digest.get("group_name")) or "我们的群聊"
    title_font = _font(48, True, True)
    _draw_lines(draw, _wrap(draw, f"{group_name}  群聊日报", title_font, 980, 1), (101, 23), title_font, WHITE, 0)
    draw.text((108, 82), "—  各种话题一起聊 · 轻松摸鱼不孤单  —", font=_font(19, True), fill=WHITE)
    draw.text((1512, 24), _clean(digest.get("date")) or "TODAY", font=_font(27, True), fill=WHITE, anchor="ra")
    draw.text((1512, 67), f"今日群聊总结 · {_clean(digest.get('message_count')) or '0'} 条消息", font=_font(17), fill=WHITE, anchor="ra")
    _sparkles(draw, 1450, 97, WHITE)

    margin, gap = 16, 14
    top, upper_bottom = 140, 1094
    left_right, right_left = 712, 726

    _outlined_round_rect(draw, (margin, top, left_right, upper_bottom), 18, "#F0FAFF", BLUE, width=3, shadow=False)
    _section_header(draw, (margin + 1, top + 1, left_right - 1, top + 60), 1, "今日群聊概览", BLUE)
    lead_font = _font(17)
    _draw_lines(draw, _wrap(draw, digest.get("lead") or digest.get("overview") or "今日群聊精彩纷呈。", lead_font, 656, 3), (margin + 18, top + 72), lead_font, INK, 4)
    topics = [item for item in (digest.get("topics") or []) if isinstance(item, dict)][:6]
    cards_top, card_gap = top + 150, 12
    card_w = (left_right - margin - 3 * card_gap) // 2
    card_h = (upper_bottom - cards_top - 4 * card_gap) // 3
    for index, topic in enumerate(topics):
        row, col = divmod(index, 2)
        x = margin + card_gap + col * (card_w + card_gap)
        y = cards_top + row * (card_h + card_gap)
        _topic_card(image, draw, topic, images[index] if index < len(images) else None, (x, y, x + card_w, y + card_h), index + 1)

    rank_bottom = 554
    _outlined_round_rect(draw, (right_left, top, width - margin, rank_bottom), 18, "#FFF9E3", ORANGE, width=3, shadow=False)
    _section_header(draw, (right_left + 1, top + 1, width - margin - 1, top + 60), 2, "摸鱼大王评选", ORANGE)
    rankings = [item for item in (digest.get("mvp_rankings") or []) if isinstance(item, dict)][:3]
    if not rankings:
        rankings = [digest.get("mvp") if isinstance(digest.get("mvp"), dict) else {}]
    # 只画当天真的评出来的人，不再用“本日留空”占位卡凑满一行。
    rankings = [item for item in rankings if _clean(item.get("name"))] or [{"_placeholder": True}]
    rank_gap = 10
    rank_slots = len(rankings)
    # 卡片宽度始终按三人位算，人少时整组居中，避免出现一张超宽空卡。
    rank_w = (width - margin - right_left - 4 * rank_gap) // 3
    rank_span = rank_slots * rank_w + (rank_slots - 1) * rank_gap
    rank_start = right_left + max(rank_gap, (width - margin - right_left - rank_span) // 2)
    for index, item in enumerate(rankings):
        x = rank_start + index * (rank_w + rank_gap)
        image_index = 6 + index
        sticker = images[image_index] if image_index < len(images) else None
        _rank_card(image, draw, item, sticker, (x, top + 74, x + rank_w, rank_bottom - 12), index + 1)

    ach_top = rank_bottom + gap
    _outlined_round_rect(draw, (right_left, ach_top, width - margin, upper_bottom), 18, "#F5F1FF", PURPLE, width=3, shadow=False)
    _section_header(draw, (right_left + 1, ach_top + 1, width - margin - 1, ach_top + 60), 3, "趣味成就颁发", PURPLE)
    achievements = [item for item in (digest.get("achievements") or []) if isinstance(item, dict)][:6]
    ach_gap = 10
    ach_w = (width - margin - right_left - 3 * ach_gap) // 2
    ach_h = (upper_bottom - (ach_top + 72) - 4 * ach_gap) // 3
    for index, item in enumerate(achievements):
        row, col = divmod(index, 2)
        x = right_left + ach_gap + col * (ach_w + ach_gap)
        y = ach_top + 72 + row * (ach_h + ach_gap)
        image_index = 9 + (index % 3)
        _achievement_card(image, draw, item, images[image_index] if image_index < len(images) else None, (x, y, x + ach_w, y + ach_h), index)

    bottom_top, bottom_bottom = 1108, 1508
    widths = (520, 430, 540)
    xs = (margin, margin + widths[0] + gap, margin + widths[0] + gap + widths[1] + gap)
    specs = ((4, "群聊骚话提取", CYAN), (5, "明日话题展望", GREEN), ("★", "特别关注", PINK))
    for x, section_w, spec in zip(xs, widths, specs):
        _outlined_round_rect(draw, (x, bottom_top, x + section_w, bottom_bottom), 18, SECTION_PALES[1], spec[2], width=3, shadow=False)
        _section_header(draw, (x + 1, bottom_top + 1, x + section_w - 1, bottom_top + 60), spec[0], spec[1], spec[2])

    quotes = [item for item in (digest.get("quotes") or []) if isinstance(item, dict)][:7]
    if not quotes:
        quotes = [digest.get("quote") if isinstance(digest.get("quote"), dict) else {}]
    tomorrow = digest.get("tomorrow_topics") if isinstance(digest.get("tomorrow_topics"), list) else []
    special = digest.get("special_notes") if isinstance(digest.get("special_notes"), list) else []
    _numbered_list(draw, quotes, (xs[0] + 10, bottom_top + 72, xs[0] + widths[0] - 10, bottom_bottom - 12), CYAN, 7, quote_mode=True)
    _numbered_list(draw, tomorrow, (xs[1] + 10, bottom_top + 72, xs[1] + widths[1] - 10, bottom_bottom - 12), GREEN, 5)
    _numbered_list(draw, special, (xs[2] + 10, bottom_top + 72, xs[2] + widths[2] - 10, bottom_bottom - 12), PINK, 5)

    draw.rectangle((0, 1518, width, height), fill=NAVY)
    draw.text((width // 2, 1527), "—  生活很卷，但摸鱼很快乐  —", font=_font(15, True), fill=WHITE, anchor="ma")

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    return output
