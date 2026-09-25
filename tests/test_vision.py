"""视觉识别的测试。

全部 mock —— 不联网、不花用户的钱、不需要 API key。
真机验证过的行为（空正文、编造的 id）都在这留下回归用例。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bambu_doctor import vision
from bambu_doctor.knowledge import KnowledgeBase
from bambu_doctor.vision import VisionError, build_prompt, identify, parse_reply

KB = KnowledgeBase.load()


class TestPrompt(unittest.TestCase):
    def test_lists_visible_symptoms(self):
        prompt = build_prompt(KB)
        self.assertIn("thin-stringing", prompt)
        self.assertIn("spaghetti", prompt)

    def test_excludes_symptoms_photos_cannot_show(self):
        """尺寸偏差照片看不出来——不该让模型去猜。"""
        prompt = build_prompt(KB)
        for hidden in vision.NOT_VISIBLE:
            self.assertNotIn(hidden, prompt)

    def test_demands_json_and_allows_unknown(self):
        prompt = build_prompt(KB)
        self.assertIn("unknown", prompt)
        self.assertIn("observed", prompt)
        self.assertIn("JSON", prompt)

    def test_forbids_giving_advice(self):
        """AI 只做感知，不给建议——这是 DESIGN.md 定的硬约束。"""
        self.assertIn("不要给怎么修的建议", build_prompt(KB))


class TestParseReply(unittest.TestCase):
    def test_plain_json(self):
        sighting = parse_reply(
            '{"symptom": "thin-stringing", "observed": ["细丝"], "confidence": "high"}', KB
        )
        self.assertEqual(sighting.symptom_id, "thin-stringing")
        self.assertEqual(sighting.observed, ["细丝"])
        self.assertEqual(sighting.confidence, "high")
        self.assertTrue(sighting.is_known)

    def test_fenced_json(self):
        text = '```json\n{"symptom": "spaghetti", "observed": []}\n```'
        self.assertEqual(parse_reply(text, KB).symptom_id, "spaghetti")

    def test_with_leading_prose(self):
        text = '好的，我看了照片：\n{"symptom": "blobs", "observed": ["颗粒"]}\n就这些。'
        self.assertEqual(parse_reply(text, KB).symptom_id, "blobs")

    def test_invented_id_becomes_unknown(self):
        """模型编一个知识库里没有的 id —— 必须作废，不能带进推理。"""
        sighting = parse_reply('{"symptom": "layer-shift-9000", "observed": ["错层"]}', KB)
        self.assertEqual(sighting.symptom_id, "unknown")
        self.assertFalse(sighting.is_known)
        self.assertIn("layer-shift-9000", sighting.note)

    def test_bogus_confidence_downgraded(self):
        self.assertEqual(
            parse_reply('{"symptom": "blobs", "confidence": "非常确定"}', KB).confidence,
            "low",
        )

    def test_invalid_alternative_dropped(self):
        self.assertEqual(
            parse_reply('{"symptom": "blobs", "alternative": "nope"}', KB).alternative, ""
        )

    def test_missing_symptom_is_unknown(self):
        self.assertEqual(parse_reply("{}", KB).symptom_id, "unknown")

    def test_observed_string_coerced_to_list(self):
        self.assertEqual(
            parse_reply('{"symptom": "blobs", "observed": "一根丝"}', KB).observed, ["一根丝"]
        )

    def test_no_json_raises(self):
        with self.assertRaises(VisionError):
            parse_reply("我觉得这张照片没问题", KB)

    def test_broken_json_raises(self):
        with self.assertRaises(VisionError):
            parse_reply('{"symptom": ', KB)


def _response(content: str):
    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 42}}
            ).encode("utf-8")

    return Fake()


class TestIdentify(unittest.TestCase):
    @mock.patch("bambu_doctor.vision.urllib.request.urlopen")
    def test_success(self, fake):
        fake.return_value = _response('{"symptom": "thin-stringing", "observed": ["细丝"]}')
        sighting = identify(b"fakebytes", KB, api_key="k")
        self.assertEqual(sighting.symptom_id, "thin-stringing")
        self.assertEqual(sighting.model, vision.DEFAULT_MODEL)

    @mock.patch("bambu_doctor.vision.urllib.request.urlopen")
    def test_empty_content_raises(self, fake):
        """实测过的坑：token 被吃光时接口返回 200，但正文是空的。
        这种情况绝不能当成"模型说没问题"。"""
        fake.return_value = _response("")
        with self.assertRaises(VisionError) as ctx:
            identify(b"fakebytes", KB, api_key="k")
        self.assertIn("空正文", str(ctx.exception))

    @mock.patch("bambu_doctor.vision.urllib.request.urlopen")
    def test_payload_is_openai_compatible_multimodal(self, fake):
        fake.return_value = _response('{"symptom": "blobs"}')
        identify(b"fakebytes", KB, api_key="k", mime="image/png")
        sent = json.loads(fake.call_args[0][0].data.decode("utf-8"))
        content = sent["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(
            content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        )
        # 关思考 + 给足 max_tokens（否则正文会被思考挤没）
        self.assertEqual(sent["reasoning_effort"], "none")
        self.assertGreaterEqual(sent["max_tokens"], 800)

    @mock.patch("bambu_doctor.vision.urllib.request.urlopen")
    def test_auth_header_uses_bearer(self, fake):
        fake.return_value = _response('{"symptom": "blobs"}')
        identify(b"fakebytes", KB, api_key="secret-key")
        request = fake.call_args[0][0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")

    def test_empty_image_raises(self):
        with self.assertRaises(VisionError) as ctx:
            identify(b"", KB, api_key="k")
        self.assertIn("空的", str(ctx.exception))

    def test_oversized_image_raises(self):
        with self.assertRaises(VisionError) as ctx:
            identify(b"x" * (vision.MAX_IMAGE_BYTES + 1), KB, api_key="k")
        self.assertIn("太大", str(ctx.exception))

    def test_no_key_raises_with_hint(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(vision, "_dotenv_candidates", return_value=[]):
                with self.assertRaises(VisionError) as ctx:
                    identify(b"x", KB)
        self.assertIn("API key", str(ctx.exception))


class TestApiKeySources(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {"BAMBU_DOCTOR_API_KEY": "from-env"}):
            self.assertEqual(vision.api_key_from_env(), "from-env")

    def test_fallback_to_dotenv_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# 注释\nDEEPSEEK_API_KEY=from-file\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(vision, "_dotenv_candidates", return_value=[path]):
                    self.assertEqual(vision.api_key_from_env(), "from-file")

    def test_quoted_value_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('DEEPSEEK_API_KEY="quoted-key"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(vision, "_dotenv_candidates", return_value=[path]):
                    self.assertEqual(vision.api_key_from_env(), "quoted-key")

    def test_no_key_anywhere_returns_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(vision, "_dotenv_candidates", return_value=[]):
                self.assertEqual(vision.api_key_from_env(), "")


if __name__ == "__main__":
    unittest.main()
