# -*- coding: utf-8 -*-
"""使用 Gemini 或兼容的旧 NVIDIA 图片端点生成可切分的插画板。"""

import base64
from io import BytesIO
import time
from urllib.parse import quote
from PIL import Image
import requests


GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
GEMINI_POSTER_MODEL = "gemini-3-pro-image"
GEMINI_IMAGE_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1/models/"
    "{model}:generateContent"
)
NVIDIA_IMAGE_ENDPOINT = (
    "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
)
# Pollinations 免 key 免费图片：GET，提示词放在 URL 里，返回图片本体。
POLLINATIONS_ENDPOINT = "https://image.pollinations.ai/prompt/{prompt}"
CONTACT_SHEET_COLS = 4
CONTACT_SHEET_ROWS = 3
MAX_TOPIC_IMAGES = 12
POSTER_ASPECT_RATIO = "1:1"
POSTER_IMAGE_SIZE = "4K"
POSTER_MIN_SIDE = 1400


def _notify(callback, message):
    if callback:
        callback(message)


def _response_error_detail(response):
    """提取 Google/NVIDIA 返回的可读错误，同时避免把响应无限展开。"""
    try:
        data = response.json()
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            status = str(error.get("status") or "").strip()
            if status and message:
                return f"{status}: {message}"[:500]
            if message or status:
                return (message or status)[:500]
    return str(getattr(response, "text", "") or "").strip()[:500]


def build_contact_sheet_prompt(topics):
    selected = topics[:MAX_TOPIC_IMAGES]
    prefix = (
        "Exact sheet of twelve separate crop-safe scenes arranged in four columns and three rows, "
        "cute hand-drawn Chinese internet infographic style, expressive chibi characters, "
        "thick slightly wobbly navy outlines, marker texture, pastel blue pink mint yellow, "
    )
    suffix = ", white background, empty speech bubbles, no text, no letters, no digits, no logos"
    available = max(24, (790 - len(prefix) - len(suffix)) // max(1, len(selected)) - 3)
    panels = []
    for topic in selected:
        title = str(topic.get("title") or "group chat").strip()
        summary = str(topic.get("summary") or "").strip()
        visual_prompt = str(topic.get("visual_prompt") or "").strip()
        subject = visual_prompt or f"{title}, {summary}"
        panels.append(subject[:available])
    return (prefix + ", ".join(panels) + suffix)[:800]


def build_single_scene_prompt(topic, placement="topic"):
    """为单一栏目生成有剧情、有关键物件的高细节手绘插画提示词。"""
    title = str(topic.get("title") or "group chat topic").strip()
    summary = str(topic.get("summary") or topic.get("reason") or "").strip()
    visual = str(topic.get("visual_prompt") or "").strip()
    subject = visual or f"{title}: {summary}"
    subject = subject[:460]
    role = "a lively group-chat participant portrait" if placement == "rank" else "a single editorial scene"
    gender = str(topic.get("gender") or "unspecified").strip().lower()
    portrait_constraint = ""
    if placement == "rank":
        portrait_constraint = {
            "female": " Depict the participant clearly as female; never change this to male.",
            "male": " Depict the participant clearly as male; never change this to female.",
        }.get(gender, " Use a gender-neutral character; never infer gender from a nickname.")
    return (
        "Create one polished standalone illustration for a Chinese group-chat daily newspaper. "
        f"It must be {role}, faithfully depicting this specific topic: {subject}."
        + portrait_constraint
        + " "
        f"Context for visual accuracy: {title} — {summary[:260]}. "
        "Do not make a collage, contact sheet, grid, dashboard, UI, or multiple panels. "
        "Show one clear main action and the concrete objects implied by the topic. "
        "Premium playful Chinese hand-drawn editorial illustration: expressive chibi characters, "
        "natural poses and faces, thick slightly imperfect dark-navy ink outlines, colored-pencil "
        "texture, soft watercolor shading, bright pastel blue pink mint yellow palette, small hand-drawn "
        "sparkles and empty speech bubbles. Composition must read clearly at small card size, with the "
        "subject large and centered. No text, letters, numbers, logos, watermarks, borders, or captions."
    )[:1150]


def build_free_scene_prompt(topic, placement="topic"):
    """免费图源（FLUX 系）专用：风格前置、反写实，尽量逼出卡通手绘感。

    免费模型对风格词更迟钝、对多主体场景理解更弱，所以把手绘风格放在最前，
    并明确列出画面主体，最后用反写实词压住它的写实倾向。
    """
    title = str(topic.get("title") or "group chat topic").strip()
    summary = str(topic.get("summary") or topic.get("reason") or "").strip()
    visual = str(topic.get("visual_prompt") or "").strip()
    subject = (visual or f"{title}: {summary}")[:360]
    # 实测：场景放前面才能保住每张的区分度，再用“扁平卡通、非照片”把写实压下去，
    # 是免费 FLUX 上“既贴题又有插画感”的平衡点（风格词过多会导致每张都画成同一个角色）。
    gender = str(topic.get("gender") or "unspecified").strip().lower()
    portrait = ""
    if placement == "rank":
        portrait = {
            "female": " Make the main character clearly female.",
            "male": " Make the main character clearly male.",
        }.get(gender, "")
    return (
        f"A colorful flat cartoon illustration, not a photo, not a 3D render: {subject}."
        + portrait
        + " Simple cel-shaded comic style, bold clean outlines, soft pastel colors, "
        "plain background, show all the characters and objects clearly, one clear action. "
        "No text, letters, numbers, logos, watermarks or borders."
    )[:900]


POSTER_LAYOUT_BRIEF = """\
READ THIS FIRST: everything before the content block is an instruction addressed to
you, and none of it may ever appear as text in the picture. Never letter words such as
canvas, header, main area, left column, right column, bottom band, footer, layout or
style onto the poster, and never letter any other English word that is not part of the
content block. The only words drawn on the poster are the Chinese strings listed in the
content block, together with the Latin fragments that appear inside those strings.

The page is one single square sheet. Divide it into four horizontal zones, top to bottom:
a header band (about 9% of the page height), a main area (about 58%), a bottom band
(about 29%) and a thin footer bar (about 4%). The bottom band spans the entire page
width, edge to edge - it is not nested inside the main area's right column. Keep outer
page margins tight and even; no large empty gaps anywhere.

At the top, draw a deep-navy rounded banner. Left: a white speech-bubble icon, then the
group name and the report title (页头标题) on ONE single line in a chunky rounded Chinese display
face (shrink the type as needed to keep it on one line), with the tagline line directly
underneath. Centre-right: the date in large type, and the message-count line just below
it. Far right, inside the banner: a chibi black cat mascot with a laptop and a mug, plus
one tiny hand-lettered sticker.

Below the banner, on the left, filling about 46% of the page width, draw a blue rounded frame titled
the overview heading (栏目1 标题) in a numbered pill header, the 导语 paragraph below it, then EXACTLY
{topic_count} topic card(s) laid out in {topic_grid}, sized so they fill the frame with no
empty slot. Every topic card carries its own pastel fill and thick colored border, a
filled circle with its number, the topic title in that card's accent color, its bullet
points as real bullets, and a small hand-drawn comic vignette that sits BESIDE or BELOW
the bullets without pushing them out - the vignette takes at most the lower 40% of the
card. Each vignette has chibi characters plus a speech balloon carrying that card's
气泡台词. Balloons and characters may gently overlap the card border.

Beside it, in the remaining width, draw at the top an orange rounded frame
titled 摸鱼大王评选 holding EXACTLY {rank_count} podium card(s) in one row. Make the cards
EQUAL width and EQUAL height - a flat row, not a stepped podium. Each has a circular
chibi portrait at the top, a small rank medallion, a crown or trophy for the first one,
the nickname in bold below the portrait, a colored ribbon banner with the title, and the
reason as bullets underneath.

Directly under that orange frame, draw a purple rounded frame titled 趣味成就颁发 with
EXACTLY {ach_count} white achievement card(s) in 2 columns. Each has a round hand-drawn
doodle icon on the left, the award name in bold, the nickname in brackets in the accent
color, the reason underneath, and on a few of them a tiny chibi figure or mini balloon
tucked into a corner.

Under everything above, running the full page width, draw three rounded frames side by side, titled 群聊骚话提取 (the
widest, about 36%), 明日话题展望 (about 28%) and 特别关注 (the rest). Each is a numbered
list of white rows with a colored number circle. Quote rows put the speaker attribution
right-aligned after the quote. The 明日话题展望 rows each get a tiny hand-drawn icon and
the 特别关注 rows get small hand-lettered warning stickers. Scatter a few stickers, mini
balloons and the cat mascot around these frames so the band feels drawn, not typeset.

At the very bottom, draw a thin deep-navy bar with the footer line centered.

Draw all of it in this style: premium cute Chinese hand-drawn infographic poster, like a well-made 小红书 group
digest. Thick slightly wobbly dark-navy ink outlines, colored-pencil and marker texture,
soft watercolor shading, warm off-white paper ground, bright pastel palette of blue, pink,
mint, orange, purple and yellow, hand-drawn sparkles and stars, expressive chibi
characters with real faces and natural poses, recurring black-cat mascot. Every frame,
badge, ribbon and bullet is drawn by hand rather than a flat vector shape.

Rules for the lettering, the most important part of the job:
- Render every Chinese string below EXACTLY as given: same characters, same order, nothing
  added, nothing dropped, nothing translated, no pinyin, no English captions, no emoji.
  Every glyph must be a real, correctly-formed Chinese character, never a look-alike.
- Set a floor on type size: no body text anywhere on the page may be smaller than the
  topic-card bullet text. The 骚话 rows carry the longest strings on the page, so give
  that frame the width and height its text needs at that size. If something does not fit,
  make its frame taller or let the wording run onto more lines - never shrink the type.
- Take extra care with dense multi-stroke characters: write each one with its correct
  components rather than an approximation that merely looks similar in outline.
- Text must be crisp, level, fully inside its card, and never clipped by a border or a
  drawing.
- Do not invent extra headings, page numbers, bylines, watermarks, logos or QR codes.
- If a card has no content given, leave it out rather than filling it with made-up text.
"""


def _poster_bullets(item):
    points = item.get("points") if isinstance(item.get("points"), list) else []
    cleaned = [str(point).strip() for point in points if str(point or "").strip()]
    if cleaned:
        return cleaned
    summary = str(item.get("summary") or item.get("reason") or "").strip()
    return [summary] if summary else []


def build_full_poster_prompt(digest):
    """把整份 digest 文案编成一次性整图海报提示词。"""
    lines = []
    group_name = str(digest.get("group_name") or "我们的群聊").strip()
    title_label = str(digest.get("title_label") or "群聊日报").strip()
    count_label = str(digest.get("count_label") or "今日群聊总结").strip()
    overview_label = str(digest.get("overview_label") or "今日群聊概览").strip()
    lines.append(f"页头群名：{group_name}")
    lines.append(f"页头标题：{title_label}")
    lines.append("页头副标语：— 各种话题一起聊 · 轻松摸鱼不孤单 —")
    lines.append(f"页头日期：{str(digest.get('date') or '').strip()}")
    lines.append(
        f"页头消息数行：{count_label} · {str(digest.get('message_count') or '0').strip()} 条消息"
    )

    lines.append("")
    lines.append(f"栏目1 标题：{overview_label}")
    lead = str(digest.get("lead") or digest.get("overview") or "").strip()
    if lead:
        lines.append(f"栏目1 导语：{lead}")
    topics = [item for item in (digest.get("topics") or []) if isinstance(item, dict)][:6]
    for index, topic in enumerate(topics, start=1):
        title = str(topic.get("title") or f"今日话题{index}").strip()
        lines.append(f"话题卡{index} 标题：{title}")
        for point in _poster_bullets(topic):
            lines.append(f"话题卡{index} 要点：{point}")
        bubble = str(topic.get("bubble") or "").strip()
        if bubble:
            lines.append(f"话题卡{index} 气泡台词：{bubble}")
        visual = str(topic.get("visual_prompt") or "").strip()
        if visual:
            lines.append(f"话题卡{index} 漫画画面（英文，仅供作画，不要写进画面）：{visual}")

    rankings = [
        item
        for item in (digest.get("mvp_rankings") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ][:3]
    lines.append("")
    lines.append("栏目2 标题：摸鱼大王评选")
    if rankings:
        for index, item in enumerate(rankings, start=1):
            default_title = "摸鱼大王" if index == 1 else f"摸鱼第 {index} 名"
            lines.append(f"人物卡{index} 昵称：{str(item.get('name')).strip()}")
            gender = str(item.get("gender") or "unspecified").strip().lower()
            if gender == "female":
                lines.append(f"人物卡{index} 人物形象：明确画成女性；不要根据昵称自行改成男性。")
            elif gender == "male":
                lines.append(f"人物卡{index} 人物形象：明确画成男性；不要根据昵称自行改成女性。")
            else:
                lines.append(f"人物卡{index} 人物形象：中性人物，不猜测或暗示性别。")
            lines.append(
                f"人物卡{index} 称号：{str(item.get('title') or default_title).strip()}"
            )
            for point in _poster_bullets(item):
                lines.append(f"人物卡{index} 理由：{point}")
        if len(rankings) < 3:
            lines.append(
                f"人物卡说明：今天只评出 {len(rankings)} 位，"
                f"请把这一栏排成 {len(rankings)} 张卡片并铺满整行，不要留空位、不要编造人名。"
            )
    else:
        lines.append("人物卡说明：今天没有可评选的人物，请省略这一栏并让上下栏目自然衔接。")

    achievements = [
        item for item in (digest.get("achievements") or []) if isinstance(item, dict)
    ][:6]
    lines.append("")
    lines.append("栏目3 标题：趣味成就颁发")
    for index, item in enumerate(achievements, start=1):
        award = str(item.get("award") or "今日成就").strip()
        name = str(item.get("name") or "").strip()
        reason = str(item.get("reason") or "").strip()
        lines.append(f"成就卡{index} 成就名：{award}")
        if name:
            lines.append(f"成就卡{index} 昵称：【{name}】")
        if reason:
            lines.append(f"成就卡{index} 说明：{reason}")
    if len(achievements) < 6:
        lines.append(
            f"成就卡说明：今天只有 {len(achievements)} 个成就，"
            "请按实际数量排版，不要凑满 6 张。"
        )

    quotes = [item for item in (digest.get("quotes") or []) if isinstance(item, dict)][:7]
    lines.append("")
    lines.append("栏目4 标题：群聊骚话提取")
    for index, item in enumerate(quotes, start=1):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        speaker = str(item.get("speaker") or "").strip()
        suffix = f" — {speaker}" if speaker else ""
        lines.append(f"骚话{index}：“{text}”{suffix}")

    lines.append("")
    lines.append("栏目5 标题：明日话题展望")
    tomorrow = digest.get("tomorrow_topics") if isinstance(digest.get("tomorrow_topics"), list) else []
    for index, item in enumerate([str(x).strip() for x in tomorrow if str(x or "").strip()][:5], start=1):
        lines.append(f"展望{index}：{item}")

    lines.append("")
    lines.append("栏目6 标题：特别关注")
    special = digest.get("special_notes") if isinstance(digest.get("special_notes"), list) else []
    for index, item in enumerate([str(x).strip() for x in special if str(x or "").strip()][:5], start=1):
        lines.append(f"关注{index}：{item}")

    lines.append("")
    lines.append("页脚：— 生活很卷，但摸鱼很快乐 —")

    topic_count = len(topics)
    grids = {
        1: "one full-width card",
        2: "one column of 2 cards",
        3: "one column of 3 cards",
        4: "a 2-column x 2-row grid",
        5: "a 2-column grid where the last card spans the full width",
        6: "a 2-column x 3-row grid",
    }
    brief = POSTER_LAYOUT_BRIEF.format(
        topic_count=topic_count or 1,
        topic_grid=grids.get(topic_count, "a 2-column grid"),
        rank_count=len(rankings) or 1,
        ach_count=len(achievements) or 1,
    )
    from digest_templates import template_layout_brief, get_template
    if get_template(digest.get('template_id')).get('brief'):
        brief = template_layout_brief(digest.get('template_id'))
    return (
        "You are an award-winning Chinese editorial illustrator and infographic designer.\n\n"
        + brief
        + "\nCONTENT TO TYPESET AND ILLUSTRATE (Chinese text is verbatim):\n"
        + "\n".join(lines)
    )


def _upscale_poster(image, min_side=POSTER_MIN_SIDE):
    """整图模式偶尔返回偏小的图，等比放大保证微信里字还看得清。"""
    if min(image.size) >= min_side:
        return image
    scale = min_side / float(min(image.size))
    target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(target, Image.Resampling.LANCZOS)


def generate_full_poster(digest, api_key, progress_callback=None,
                         request_fn=requests.post, cancel_event=None,
                         model=None, aspect_ratio=POSTER_ASPECT_RATIO,
                         image_size=POSTER_IMAGE_SIZE):
    """一次调用出整张手绘海报：文字与插画都由图片模型绘制。"""
    if not str(api_key or "").strip():
        raise ValueError("整图 AI 海报需要 Gemini API Key。")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("任务已取消。")
    _notify(progress_callback, "正在让图片模型直接绘制整张手绘海报（约 1～3 分钟）...")
    images = _generate_images_from_prompt(
        [digest],
        build_full_poster_prompt(digest),
        api_key,
        progress_callback=progress_callback,
        request_fn=request_fn,
        cancel_event=cancel_event,
        provider="gemini",
        model=model or GEMINI_POSTER_MODEL,
        split_sheet=False,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )
    if not images:
        raise RuntimeError("图片模型没有返回整张海报，请重试或改用本地排版模式。")
    return _upscale_poster(images[0])


def build_digest_illustration_requests(digest, detailed=False):
    """固定生成 12 格素材：六个话题、三个人物、三张栏目装饰。"""
    requests_list = []
    topics = digest.get("topics") if isinstance(digest.get("topics"), list) else []
    for topic in topics[:6]:
        if isinstance(topic, dict):
            requests_list.append({**topic, "_illustration_role": "topic"})
    while len(requests_list) < 6:
        # 精绘模式不为空栏目付费，但仍占位，保证人物插画落在固定索引 6~8。
        requests_list.append(
            {"_skip": True}
            if detailed
            else {"visual_prompt": "friends happily chatting about everyday life"}
        )

    rankings = (
        digest.get("mvp_rankings")
        if isinstance(digest.get("mvp_rankings"), list)
        else []
    )
    for index, item in enumerate(rankings[:3]):
        if not isinstance(item, dict):
            continue
        if detailed and not str(item.get("name") or "").strip():
            continue
        gender = str(item.get("gender") or "unspecified").strip().lower()
        visual_prompt = str(
            item.get("visual_prompt")
            or "cheerful award winner portrait holding a small trophy"
        ).strip()
        requests_list.append(
            {
                "visual_prompt": visual_prompt,
                "gender": gender,
                "_illustration_role": "rank",
            }
        )
    if detailed:
        while len(requests_list) < 9:
            requests_list.append({"_skip": True})
        return requests_list[:9]
    while len(requests_list) < 9:
        requests_list.append(
            {
                "visual_prompt": "cheerful award winner portrait holding a small trophy",
                "_illustration_role": "rank",
            }
        )
    requests_list.extend(
        [
            {"visual_prompt": "cute trophy and crown with joyful sparkles"},
            {"visual_prompt": "cheerful person making an announcement with a megaphone"},
            {"visual_prompt": "busy friendly group chat with colorful empty speech bubbles"},
        ]
    )
    return requests_list[:MAX_TOPIC_IMAGES]


def split_contact_sheet(image, count, cols=CONTACT_SHEET_COLS,
                        rows=CONTACT_SHEET_ROWS):
    """按固定网格切出前 count 张插画。"""
    count = max(0, min(int(count), cols * rows, MAX_TOPIC_IMAGES))
    if not count:
        return []
    source = image.convert("RGB")
    cell_w = source.width / cols
    cell_h = source.height / rows
    crops = []
    for index in range(count):
        row, col = divmod(index, cols)
        left = int(round(col * cell_w))
        top = int(round(row * cell_h))
        right = int(round((col + 1) * cell_w))
        bottom = int(round((row + 1) * cell_h))
        crops.append(source.crop((left, top, right, bottom)))
    return crops


def _extract_nvidia_image_bytes(response_data):
    artifacts = response_data.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        encoded = artifacts[0].get("base64") or artifacts[0].get("image")
        if encoded:
            return base64.b64decode(encoded)
        finish_reason = artifacts[0].get("finishReason")
        if finish_reason:
            raise RuntimeError(
                f"NVIDIA 图片模型未生成图片（{finish_reason}），请重试或关闭 AI 配图。"
            )
    encoded = response_data.get("image") or response_data.get("base64")
    if encoded:
        return base64.b64decode(encoded)
    raise RuntimeError("NVIDIA 图片接口没有返回可识别的图片数据。")


def _extract_gemini_image_bytes(response_data):
    candidates = response_data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        block_reason = (
            response_data.get("promptFeedback", {}).get("blockReason")
            if isinstance(response_data.get("promptFeedback"), dict)
            else None
        )
        detail = block_reason or "没有候选结果"
        raise RuntimeError(f"Gemini 图片模型未生成图片（{detail}）。")
    content = candidates[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline, dict):
                continue
            encoded = inline.get("data")
            if encoded:
                return base64.b64decode(encoded)
    reason = candidates[0].get("finishReason") or "响应中没有图片数据"
    raise RuntimeError(f"Gemini 图片模型未生成图片（{reason}）。")


def _generate_images_from_prompt(selected, prompt, api_key, progress_callback=None,
                                 request_fn=requests.post, cancel_event=None,
                                 provider="gemini", model=None, split_sheet=True,
                                 aspect_ratio="4:3", image_size="1K"):
    """执行一次图片请求；联系表模式可切片，单景模式返回整图。"""
    if provider == "gemini":
        selected_model = str(model or GEMINI_IMAGE_MODEL).strip()
        endpoint = GEMINI_IMAGE_ENDPOINT.format(
            model=quote(selected_model, safe="")
        )
        headers = {
            "x-goog-api-key": api_key.strip(),
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]},
            ],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                # 画幅必须走 imageConfig：responseFormat.image.aspectRatio 是枚举，
                # 传 "1:1" 这类字符串会被判 400，然后悄悄退回默认画幅。
                "imageConfig": {
                    "aspectRatio": aspect_ratio,
                    "imageSize": image_size,
                },
            },
        }
        # 整图海报要画满一页文字，比小插画慢得多，读超时按画布档位放宽。
        read_timeout = {"4K": 600, "2K": 420}.get(str(image_size).upper(), 180)
        timeout = (20, read_timeout)
        max_attempts = 3
    elif provider == "nvidia":
        endpoint = NVIDIA_IMAGE_ENDPOINT
        headers = {
                "Authorization": f"Bearer {api_key.strip()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
        }
        payload = {
            "cfg_scale": 1,
            "height": 768,
            "prompt": prompt,
            "samples": 1,
            "seed": 0,
            "steps": 4,
            "width": 1024,
        }
        timeout = (20, 120)
        max_attempts = 1
    else:
        raise ValueError(f"不支持的图片服务商：{provider}")

    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = request_fn(
                endpoint, headers=headers, json=payload, timeout=timeout
            )
        except requests.Timeout as exc:
            if provider == "gemini" and attempt < max_attempts:
                response = None
            else:
                raise RuntimeError(
                    f"{'Gemini' if provider == 'gemini' else 'NVIDIA'} "
                    f"图片模型等待超过 {timeout[1]} 秒，请稍后重试。"
                ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"连接图片模型失败：{exc}") from exc

        transient = (
            response is not None
            and response.status_code in (408, 429, 500, 502, 503, 504)
        )
        if provider != "gemini" or attempt >= max_attempts or not (
            response is None or transient
        ):
            break
        delay = 2 ** (attempt - 1)
        _notify(
            progress_callback,
            f"Gemini 图片服务暂时繁忙，{delay} 秒后重试...",
        )
        if cancel_event is not None:
            if cancel_event.wait(delay):
                raise RuntimeError("任务已取消。")
        else:
            time.sleep(delay)

    if response is None:
        raise RuntimeError("Gemini 图片请求没有收到响应，请稍后再试。")

    # Gemini REST 新旧后端曾使用过不同的图片配置字段。若服务明确表示
    # 不认识 responseFormat/imageSize，就按官方最简请求再试一次，让模型
    # 使用默认分辨率生成图片；这不会在鉴权、计费或地域错误时盲目重试。
    if provider == "gemini" and response.status_code == 400:
        detail = _response_error_detail(response)
        schema_markers = (
            "responseformat", "imageconfig", "imagesize",
            "aspectratio", "responsemodalities",
            "unknown name", "unknown field", "invalid json payload",
        )
        if any(marker in detail.lower() for marker in schema_markers):
            _notify(progress_callback, "Gemini 图片参数版本不兼容，正在使用兼容模式...")
            compatible_payload = {
                "contents": [
                    {"role": "user", "parts": [{"text": prompt}]},
                ],
            }
            try:
                response = request_fn(
                    endpoint,
                    headers=headers,
                    json=compatible_payload,
                    timeout=timeout,
                )
            except requests.Timeout as exc:
                raise RuntimeError(
                    "Gemini 图片兼容请求等待超时，请稍后重试。"
                ) from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"连接 Gemini 图片模型失败：{exc}") from exc

    if provider == "gemini":
        gemini_errors = {
            400: "Gemini 图片请求格式错误，请检查图片模型名。",
            401: "Gemini API Key 无效，请确认复制完整。",
            403: "Gemini 图片请求被拒绝，请确认项目已启用付费方案。",
            404: "Gemini 没有找到图片模型，请检查模型名。",
            429: "Gemini 图片模型达到频率或消费额度限制。",
            500: "Gemini 图片服务暂时异常，请稍后重试。",
            503: "Gemini 图片服务当前繁忙，请稍后重试。",
        }
        if response.status_code in gemini_errors:
            detail = _response_error_detail(response)
            suffix = f"\nGoogle 返回：{detail}" if detail else ""
            raise RuntimeError(gemini_errors[response.status_code] + suffix)
    else:
        if response.status_code in (401, 403):
            raise RuntimeError("NVIDIA 图片模型拒绝了 Key，请确认 Key 有权调用该模型。")
        if response.status_code == 402:
            raise RuntimeError("NVIDIA 图片模型额度不足或当前端点需要付费。")
        if response.status_code == 429:
            raise RuntimeError("NVIDIA 图片模型请求过于频繁，请稍后再试。")
    try:
        response.raise_for_status()
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("任务已取消。")
        response_data = response.json()
        raw_image = (
            _extract_gemini_image_bytes(response_data)
            if provider == "gemini"
            else _extract_nvidia_image_bytes(response_data)
        )
        with Image.open(BytesIO(raw_image)) as opened:
            sheet = opened.convert("RGB")
    except (OSError, ValueError, requests.RequestException) as exc:
        detail = response.text[:300] if response.text else str(exc)
        label = "Gemini" if provider == "gemini" else "NVIDIA"
        raise RuntimeError(f"{label} 图片模型返回异常：{detail}") from exc

    _notify(progress_callback, "AI 插画已生成，正在切分并排版...")
    return split_contact_sheet(sheet, len(selected)) if split_sheet else [sheet]


def _generate_pollinations_image(prompt, cancel_event=None, get_fn=None):
    """Pollinations 免 key 出图：GET 拿图片本体，失败返回 None。"""
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("任务已取消。")
    get_fn = get_fn or requests.get
    url = POLLINATIONS_ENDPOINT.format(prompt=quote(prompt[:900], safe=""))
    params = {"width": 768, "height": 768, "nologo": "true", "model": "flux"}
    try:
        response = get_fn(url, params=params, timeout=(20, 120))
        response.raise_for_status()
        with Image.open(BytesIO(response.content)) as opened:
            image = opened.convert("RGB")
    except (requests.RequestException, OSError, ValueError):
        return None
    # Pollinations 免费版会在底部打 pollinations.ai 水印，裁掉底部一条即可。
    width, height = image.size
    return image.crop((0, 0, width, int(height * 0.94)))


# 免费图片来源：不需要 key 的走 Pollinations，NVIDIA 免费端点需要免费 key。
_FREE_IMAGE_PROVIDERS = {"pollinations", "nvidia"}


def generate_topic_images(topics, api_key, progress_callback=None,
                          request_fn=requests.post, cancel_event=None,
                          provider="gemini", model=None, mode="sheet",
                          get_fn=None):
    """生成插画：sheet 只请求一次；detailed / 免费来源为每个栏目单独绘制。"""
    selected = [item for item in topics if isinstance(item, dict)][:MAX_TOPIC_IMAGES]
    if not selected:
        return []
    # Pollinations 免 key；其它来源需要各自的 key。
    if provider != "pollinations" and not str(api_key or "").strip():
        label = {"gemini": "Gemini", "nvidia": "NVIDIA"}.get(provider, provider)
        raise ValueError(f"AI 话题配图需要 {label} API Key。")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("任务已取消。")
    if mode not in {"sheet", "detailed"}:
        raise ValueError(f"不支持的插画模式：{mode}")

    # 免费来源画不了整齐的 12 宫格联系表，一律逐格单独画（每张免费/低成本）。
    per_panel = mode == "detailed" or provider in _FREE_IMAGE_PROVIDERS
    if per_panel:
        illustrations = []
        total = sum(1 for item in selected if not item.get("_skip"))
        drawn = 0
        for item in selected:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("任务已取消。")
            if item.get("_skip"):
                illustrations.append(None)
                continue
            placement = str(item.get("_illustration_role") or "topic")
            drawn += 1
            _notify(progress_callback, f"正在绘制第 {drawn}/{total} 张栏目插画...")
            # 免费图源用风格前置的提示词逼卡通感；Gemini 用原本的高细节提示词。
            if provider in _FREE_IMAGE_PROVIDERS:
                scene_prompt = build_free_scene_prompt(item, placement=placement)
            else:
                scene_prompt = build_single_scene_prompt(item, placement=placement)
            if provider == "pollinations":
                illustrations.append(
                    _generate_pollinations_image(
                        scene_prompt, cancel_event=cancel_event, get_fn=get_fn
                    )
                )
            else:
                illustrations.extend(
                    _generate_images_from_prompt(
                        [item], scene_prompt, api_key,
                        progress_callback=progress_callback,
                        request_fn=request_fn, cancel_event=cancel_event,
                        provider=provider, model=model, split_sheet=False,
                    )
                )
        return illustrations

    _notify(progress_callback, f"正在让图片模型绘制 {len(selected)} 张话题插画...")
    return _generate_images_from_prompt(
        selected,
        build_contact_sheet_prompt(selected),
        api_key,
        progress_callback=progress_callback,
        request_fn=request_fn,
        cancel_event=cancel_event,
        provider=provider,
        model=model,
        split_sheet=True,
    )
