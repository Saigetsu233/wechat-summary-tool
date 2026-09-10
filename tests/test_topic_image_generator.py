import base64
from io import BytesIO
import unittest
import threading

from PIL import Image, ImageDraw

from topic_image_generator import (
    GEMINI_IMAGE_ENDPOINT,
    GEMINI_IMAGE_MODEL,
    NVIDIA_IMAGE_ENDPOINT,
    build_contact_sheet_prompt,
    build_digest_illustration_requests,
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
        self.assertEqual(
            generation["responseFormat"]["image"]["aspectRatio"], "4:3"
        )
        self.assertEqual(calls[0][1]["timeout"], (20, 180))
        self.assertEqual(len(images), 1)

    def test_gemini_schema_error_retries_with_minimal_payload(self):
        buffer = BytesIO()
        self._sheet().save(buffer, format="JPEG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return _ErrorResponse(
                    400, "Invalid JSON payload received. Unknown name responseFormat"
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


if __name__ == "__main__":
    unittest.main()
