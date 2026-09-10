import base64
from io import BytesIO
import unittest
import threading

from PIL import Image, ImageDraw

from topic_image_generator import (
    GEMINI_IMAGE_ENDPOINT,
    GEMINI_IMAGE_MODEL,
    GEMINI_POSTER_MODEL,
    NVIDIA_IMAGE_ENDPOINT,
    build_contact_sheet_prompt,
    build_digest_illustration_requests,
    build_full_poster_prompt,
    generate_full_poster,
    generate_topic_images,
    split_contact_sheet,
)


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, encoded_image):
        self.encoded_image = encoded_image

    def json(self):
        return {"artifacts": [{"base64": self.encoded_image}]}

    def raise_for_status(self):
        return None


class _ErrorResponse:
    text = ""

    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message

    def json(self):
        return {
            "error": {
                "code": self.status_code,
                "message": self.message,
                "status": "INVALID_ARGUMENT",
            }
        }

    def raise_for_status(self):
        raise RuntimeError(self.message)


class _GeminiFakeResponse(_FakeResponse):
    def json(self):
        return {
            "candidates": [{
                "content": {
                    "parts": [{
                        "inlineData": {
                            "mimeType": "image/jpeg",
                            "data": self.encoded_image,
                        }
                    }]
                },
                "finishReason": "STOP",
            }]
        }


class TopicImageGeneratorTests(unittest.TestCase):
    def _sheet(self):
        image = Image.new("RGB", (400, 300), "white")
        draw = ImageDraw.Draw(image)
        for index in range(12):
            row, column = divmod(index, 4)
            color = ((index * 37) % 255, (index * 67) % 255, (index * 97) % 255)
            draw.rectangle(
                (column * 100, row * 100, (column + 1) * 100, (row + 1) * 100),
                fill=color,
            )
        return image

    def test_prompt_requests_fixed_grid_without_text(self):
        prompt = build_contact_sheet_prompt(
            [{"title": "雪板选购", "summary": "比较硬度与板型"}]
        )
        self.assertIn("four columns and three rows", prompt)
        self.assertLessEqual(len(prompt), 800)
        self.assertIn("雪板选购", prompt)

    def test_split_contact_sheet_returns_requested_cells(self):
        crops = split_contact_sheet(self._sheet(), 12)
        self.assertEqual(len(crops), 12)
        self.assertEqual(crops[0].size, (100, 100))

    def test_digest_always_builds_fixed_twelve_panel_sheet(self):
        requests = build_digest_illustration_requests(
            {
                "topics": [{"visual_prompt": "snowboard trip"}],
                "mvp_rankings": [{"visual_prompt": "winner portrait"}],
            }
        )
        self.assertEqual(len(requests), 12)
        self.assertEqual(requests[0]["visual_prompt"], "snowboard trip")
        self.assertEqual(requests[6]["visual_prompt"], "winner portrait")

    def test_generate_topic_images_calls_nvidia_once(self):
        buffer = BytesIO()
        self._sheet().save(buffer, format="JPEG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return _FakeResponse(encoded)

        images = generate_topic_images(
            [
                {"title": "雪板", "summary": "装备讨论"},
                {"title": "行程", "summary": "周末出发"},
            ],
            "nvapi-test-key",
            request_fn=fake_request,
            provider="nvidia",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], NVIDIA_IMAGE_ENDPOINT)
        self.assertEqual(calls[0][1]["json"]["height"], 768)
        self.assertEqual(calls[0][1]["timeout"], (20, 120))
        self.assertEqual(len(images), 2)

    def test_generate_topic_images_uses_gemini_native_image_api(self):
        buffer = BytesIO()
        self._sheet().save(buffer, format="JPEG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return _GeminiFakeResponse(encoded)

        images = generate_topic_images(
            [{"title": "雪板", "summary": "装备讨论"}],
            "gemini-test-key",
            request_fn=fake_request,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][0],
            GEMINI_IMAGE_ENDPOINT.format(model=GEMINI_IMAGE_MODEL),
        )
        self.assertEqual(
            calls[0][1]["headers"]["x-goog-api-key"], "gemini-test-key"
        )
        generation = calls[0][1]["json"]["generationConfig"]
        self.assertEqual(generation["responseModalities"], ["TEXT", "IMAGE"])
        self.assertEqual(generation["imageConfig"]["aspectRatio"], "4:3")
        self.assertEqual(calls[0][1]["timeout"], (20, 180))
        self.assertEqual(len(images), 1)

    def test_detailed_mode_draws_each_topic_as_its_own_scene(self):
        buffer = BytesIO()
        self._sheet().save(buffer, format="JPEG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return _GeminiFakeResponse(encoded)

        images = generate_topic_images(
            [
                {"title": "雪板选购", "summary": "比较板型与硬度", "visual_prompt": "a rider comparing two snowboards"},
                {"title": "周末出发", "summary": "确认集合时间", "visual_prompt": "friends packing a car for a ski trip"},
            ],
            "gemini-test-key",
            request_fn=fake_request,
            mode="detailed",
        )

        self.assertEqual(len(images), 2)
        self.assertEqual(len(calls), 2)
        first_prompt = calls[0][1]["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("standalone illustration", first_prompt)
        self.assertIn("snowboards", first_prompt)
        self.assertNotIn("four columns and three rows", first_prompt)

    def test_gemini_schema_error_retries_with_minimal_payload(self):
        buffer = BytesIO()
        self._sheet().save(buffer, format="JPEG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return _ErrorResponse(
                    400, "Invalid JSON payload received. Unknown name imageConfig"
                )
            return _GeminiFakeResponse(encoded)

        images = generate_topic_images(
            [{"title": "雪板"}], "test-key", request_fn=fake_request
        )

        self.assertEqual(len(images), 1)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("generationConfig", calls[1][1]["json"])

    def test_gemini_error_includes_google_detail(self):
        with self.assertRaisesRegex(RuntimeError, "Only available to billed users"):
            generate_topic_images(
                [{"title": "雪板"}],
                "test-key",
                request_fn=lambda *args, **kwargs: _ErrorResponse(
                    400, "Only available to billed users"
                ),
            )

    def test_cancelled_image_request_does_not_reach_network(self):
        cancel_event = threading.Event()
        cancel_event.set()
        calls = []
        with self.assertRaisesRegex(RuntimeError, "任务已取消"):
            generate_topic_images(
                [{"title": "雪板"}],
                "nvapi-test-key",
                request_fn=lambda *args, **kwargs: calls.append((args, kwargs)),
                cancel_event=cancel_event,
            )
        self.assertEqual(calls, [])


POSTER_DIGEST = {
    "group_name": "摸鱼研究所",
    "date": "2026-09-09",
    "message_count": "1782",
    "lead": "今天从抗压心态聊到 AI 办公。",
    "topics": [
        {
            "title": "职场抗压",
            "points": ["萝卜狗纠结辞职", "群友劝他降低预期"],
            "bubble": "先躺平再说",
            "visual_prompt": "tired worker at a desk",
        },
        {"title": "办公AI实测", "summary": "吐槽 AI 做演示文稿错漏百出。"},
    ],
    "mvp_rankings": [
        {"name": "红鲤躺平日记", "title": "硬核抗压首席工友", "reason": "全天高频输出。"},
        {"name": "", "title": "不该上榜", "reason": "没有名字。"},
    ],
    "achievements": [{"award": "物理抗压大师", "name": "红鲤躺平日记", "reason": "坚如磐石。"}],
    "quotes": [{"speaker": "rin", "text": "给多少钱出多少力。"}],
    "tomorrow_topics": ["AI 工具准确率"],
    "special_notes": ["偏方不可信，请就医。"],
}


class FullPosterPromptTests(unittest.TestCase):
    def test_prompt_carries_every_section_verbatim(self):
        prompt = build_full_poster_prompt(POSTER_DIGEST)
        for expected in (
            "摸鱼研究所",
            "今日群聊总结 · 1782 条消息",
            "今天从抗压心态聊到 AI 办公。",
            "萝卜狗纠结辞职",
            "先躺平再说",
            "红鲤躺平日记",
            "物理抗压大师",
            "给多少钱出多少力。",
            "AI 工具准确率",
            "偏方不可信，请就医。",
            "— 生活很卷，但摸鱼很快乐 —",
        ):
            self.assertIn(expected, prompt)
        self.assertIn("EXACTLY as given", prompt)

    def test_prompt_counts_match_real_content(self):
        prompt = build_full_poster_prompt(POSTER_DIGEST)
        # 版式说明是折行文本，比较前先把换行压平。
        flat = " ".join(prompt.split())
        self.assertIn("EXACTLY 2 topic card(s)", flat)
        # 没有昵称的候选人不占位，也不许模型编人名。
        self.assertIn("EXACTLY 1 podium card(s)", flat)
        self.assertIn("不要编造人名", prompt)
        self.assertIn("EXACTLY 1 white achievement card(s)", flat)
        self.assertNotIn("不该上榜", prompt)

    def test_topic_summary_becomes_a_bullet_when_points_missing(self):
        prompt = build_full_poster_prompt(POSTER_DIGEST)
        self.assertIn("话题卡2 要点：吐槽 AI 做演示文稿错漏百出。", prompt)


class FullPosterGenerationTests(unittest.TestCase):
    def _encoded_poster(self, size=(1600, 1600)):
        buffer = BytesIO()
        Image.new("RGB", size, "white").save(buffer, format="JPEG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def test_generate_full_poster_requests_one_square_2k_image(self):
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            return _GeminiFakeResponse(self._encoded_poster())

        poster = generate_full_poster(
            POSTER_DIGEST, "gemini-test-key", request_fn=fake_request
        )
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertIn(GEMINI_POSTER_MODEL, url)
        image_config = kwargs["json"]["generationConfig"]["imageConfig"]
        self.assertEqual(image_config["aspectRatio"], "1:1")
        self.assertEqual(image_config["imageSize"], "4K")
        # 整页文字比小插画慢，读超时必须放宽。
        self.assertGreaterEqual(kwargs["timeout"][1], 600)
        self.assertEqual(poster.size, (1600, 1600))

    def test_small_poster_is_upscaled_for_readable_text(self):
        poster = generate_full_poster(
            POSTER_DIGEST,
            "gemini-test-key",
            request_fn=lambda url, **kwargs: _GeminiFakeResponse(
                self._encoded_poster((700, 700))
            ),
        )
        self.assertGreaterEqual(min(poster.size), 1400)

    def test_generate_full_poster_requires_a_key(self):
        with self.assertRaisesRegex(ValueError, "Gemini API Key"):
            generate_full_poster(POSTER_DIGEST, "  ")

    def test_generate_full_poster_honours_cancellation(self):
        cancel_event = threading.Event()
        cancel_event.set()
        calls = []
        with self.assertRaisesRegex(RuntimeError, "任务已取消"):
            generate_full_poster(
                POSTER_DIGEST,
                "gemini-test-key",
                request_fn=lambda *args, **kwargs: calls.append(kwargs),
                cancel_event=cancel_event,
            )
        self.assertEqual(calls, [])


class DetailedModeSlotTests(unittest.TestCase):
    def test_detailed_mode_keeps_portrait_indexes_stable(self):
        requests = build_digest_illustration_requests(
            {
                "topics": [{"visual_prompt": "snowboard trip"}],
                "mvp_rankings": [{"name": "雪友A", "visual_prompt": "winner portrait"}],
            },
            detailed=True,
        )
        self.assertEqual(len(requests), 9)
        # 空话题位只占位不出图，人物插画仍落在索引 6。
        self.assertTrue(requests[1]["_skip"])
        self.assertEqual(requests[6]["visual_prompt"], "winner portrait")

    def test_detailed_mode_skips_placeholders_without_drawing(self):
        calls = []
        buffer = BytesIO()
        Image.new("RGB", (256, 256), "white").save(buffer, format="JPEG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

        images = generate_topic_images(
            [{"visual_prompt": "one real scene"}, {"_skip": True}],
            "gemini-test-key",
            request_fn=lambda url, **kwargs: (
                calls.append(url) or _GeminiFakeResponse(encoded)
            ),
            provider="gemini",
            mode="detailed",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(images), 2)
        self.assertIsNone(images[1])


if __name__ == "__main__":
    unittest.main()
