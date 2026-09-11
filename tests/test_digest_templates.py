import unittest
from digest_templates import TEMPLATES, get_template
from topic_image_generator import build_full_poster_prompt


class TemplateTests(unittest.TestCase):
    def test_templates_change_design_not_content(self):
        digest = {'group_name': '测试群', 'date': '2026-09-11',
                  'topics': [{'title': '滑雪讨论', 'points': ['新雪季准备']} ]}
        prompts = []
        for key in TEMPLATES:
            prompt = build_full_poster_prompt(dict(digest, template_id=key))
            self.assertIn('测试群', prompt)
            self.assertIn('新雪季准备', prompt)
            prompts.append(prompt)
        self.assertEqual(len(set(prompts)), len(TEMPLATES))

    def test_unknown_template_falls_back(self):
        self.assertEqual(get_template('unknown'), TEMPLATES['handdrawn'])
        self.assertEqual(build_full_poster_prompt({'template_id': 'unknown'}),
                         build_full_poster_prompt({'template_id': 'handdrawn'}))
