# -*- coding: utf-8 -*-
"""使用 NVIDIA 图片模型为日报话题生成一张可切分的插画板。"""

import base64
from io import BytesIO
from PIL import Image
import requests


NVIDIA_IMAGE_ENDPOINT = (
    "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
)
CONTACT_SHEET_COLS = 4
CONTACT_SHEET_ROWS = 3
MAX_TOPIC_IMAGES = 12


def _notify(callback, message):
    if callback:
        callback(message)


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


def build_digest_illustration_requests(digest):
    """固定生成 12 格素材：六个话题、三个人物、三张栏目装饰。"""
    requests_list = []
    topics = digest.get("topics") if isinstance(digest.get("topics"), list) else []
    for topic in topics[:6]:
        if isinstance(topic, dict):
            requests_list.append(topic)
    while len(requests_list) < 6:
        requests_list.append(
            {"visual_prompt": "friends happily chatting about everyday life"}
        )

    rankings = (
        digest.get("mvp_rankings")
        if isinstance(digest.get("mvp_rankings"), list)
        else []
    )
    for index in range(3):
        item = rankings[index] if index < len(rankings) and isinstance(rankings[index], dict) else {}
        requests_list.append(
            {
                "visual_prompt": item.get("visual_prompt")
                or "cheerful award winner portrait holding a small trophy"
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


def _extract_image_bytes(response_data):
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


def generate_topic_images(topics, api_key, progress_callback=None,
                          request_fn=requests.post):
    """一次生成联系表并裁出最多十二张手绘插画。"""
    selected = [item for item in topics if isinstance(item, dict)][:MAX_TOPIC_IMAGES]
    if not selected:
        return []
    if not str(api_key or "").strip():
        raise ValueError("AI 话题配图需要 NVIDIA API Key。")

    _notify(progress_callback, f"正在让图片模型绘制 {len(selected)} 张话题插画...")
    payload = {
        "cfg_scale": 1,
        "height": 768,
        "prompt": build_contact_sheet_prompt(selected),
        "samples": 1,
        "seed": 0,
        "steps": 4,
        "width": 1024,
    }
    try:
        response = request_fn(
            NVIDIA_IMAGE_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(20, 300),
        )
    except requests.Timeout as exc:
        raise RuntimeError("NVIDIA 图片模型响应超时，请稍后重试。") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"连接 NVIDIA 图片模型失败：{exc}") from exc

    if response.status_code in (401, 403):
        raise RuntimeError("NVIDIA 图片模型拒绝了 Key，请确认 Key 有权调用该模型。")
    if response.status_code == 402:
        raise RuntimeError("NVIDIA 图片模型额度不足或当前端点需要付费。")
    if response.status_code == 429:
        raise RuntimeError("NVIDIA 图片模型请求过于频繁，请稍后再试。")
    try:
        response.raise_for_status()
        raw_image = _extract_image_bytes(response.json())
        with Image.open(BytesIO(raw_image)) as opened:
            sheet = opened.convert("RGB")
    except (ValueError, requests.RequestException) as exc:
        detail = response.text[:300] if response.text else str(exc)
        raise RuntimeError(f"NVIDIA 图片模型返回异常：{detail}") from exc

    _notify(progress_callback, "AI 插画已生成，正在切分并排版...")
    return split_contact_sheet(sheet, len(selected))
