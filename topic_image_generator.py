# -*- coding: utf-8 -*-
"""使用 Gemini 或兼容的旧 NVIDIA 图片端点生成可切分的插画板。"""

import base64
from io import BytesIO
import time
from urllib.parse import quote
from PIL import Image
import requests


GEMINI_IMAGE_MODEL = "gemini-3.1-flash-image"
GEMINI_IMAGE_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1/models/"
    "{model}:generateContent"
)
NVIDIA_IMAGE_ENDPOINT = (
    "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
)
CONTACT_SHEET_COLS = 4
CONTACT_SHEET_ROWS = 3
MAX_TOPIC_IMAGES = 12


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
    return (
        "Create one polished standalone illustration for a Chinese group-chat daily newspaper. "
        f"It must be {role}, faithfully depicting this specific topic: {subject}. "
        f"Context for visual accuracy: {title} — {summary[:260]}. "
        "Do not make a collage, contact sheet, grid, dashboard, UI, or multiple panels. "
        "Show one clear main action and the concrete objects implied by the topic. "
        "Premium playful Chinese hand-drawn editorial illustration: expressive chibi characters, "
        "natural poses and faces, thick slightly imperfect dark-navy ink outlines, colored-pencil "
        "texture, soft watercolor shading, bright pastel blue pink mint yellow palette, small hand-drawn "
        "sparkles and empty speech bubbles. Composition must read clearly at small card size, with the "
        "subject large and centered. No text, letters, numbers, logos, watermarks, borders, or captions."
    )[:1150]


def build_digest_illustration_requests(digest, detailed=False):
    """固定生成 12 格素材：六个话题、三个人物、三张栏目装饰。"""
    requests_list = []
    topics = digest.get("topics") if isinstance(digest.get("topics"), list) else []
    for topic in topics[:6]:
        if isinstance(topic, dict):
            requests_list.append({**topic, "_illustration_role": "topic"})
    while not detailed and len(requests_list) < 6:
        requests_list.append(
            {"visual_prompt": "friends happily chatting about everyday life"}
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
        requests_list.append(
            {
                "visual_prompt": item.get("visual_prompt")
                or "cheerful award winner portrait holding a small trophy",
                "_illustration_role": "rank",
            }
        )
    if detailed:
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
                                 provider="gemini", model=None, split_sheet=True):
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
                "responseFormat": {
                    "image": {"aspectRatio": "4:3", "imageSize": "1K"}
                },
            },
        }
        timeout = (20, 180)
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
            "responseformat", "imagesize", "responsemodalities",
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


def generate_topic_images(topics, api_key, progress_callback=None,
                          request_fn=requests.post, cancel_event=None,
                          provider="gemini", model=None, mode="sheet"):
    """生成插画：sheet 只请求一次；detailed 为每个栏目单独绘制。"""
    selected = [item for item in topics if isinstance(item, dict)][:MAX_TOPIC_IMAGES]
    if not selected:
        return []
    if not str(api_key or "").strip():
        label = "Gemini" if provider == "gemini" else "NVIDIA"
        raise ValueError(f"AI 话题配图需要 {label} API Key。")
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("任务已取消。")
    if mode not in {"sheet", "detailed"}:
        raise ValueError(f"不支持的插画模式：{mode}")
    if mode == "detailed":
        if provider != "gemini":
            raise ValueError("精致插画模式目前仅支持 Gemini 图片模型。")
        illustrations = []
        total = len(selected)
        for index, item in enumerate(selected, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("任务已取消。")
            placement = str(item.get("_illustration_role") or "topic")
            _notify(progress_callback, f"正在精绘第 {index}/{total} 张栏目插画...")
            illustrations.extend(
                _generate_images_from_prompt(
                    [item],
                    build_single_scene_prompt(item, placement=placement),
                    api_key,
                    progress_callback=progress_callback,
                    request_fn=request_fn,
                    cancel_event=cancel_event,
                    provider=provider,
                    model=model,
                    split_sheet=False,
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
