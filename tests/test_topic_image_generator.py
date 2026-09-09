import base64
from io import BytesIO
import unittest

from PIL import Image, ImageDraw

from topic_image_generator import (
    NVIDIA_IMAGE_ENDPOINT,
    build_contact_sheet_prompt,
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
        crops = split_contact_sheet(self._sheet(), 10)
        self.assertEqual(len(crops), 10)
        self.assertEqual(crops[0].size, (100, 100))

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
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], NVIDIA_IMAGE_ENDPOINT)
        self.assertEqual(len(images), 2)


if __name__ == "__main__":
    unittest.main()
