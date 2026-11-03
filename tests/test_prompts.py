from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.prompts import (
    PromptConfigError,
    get_prompt,
    prompt_catalog_path,
    render_prompt,
)


class PromptCatalogTests(unittest.TestCase):
    def test_default_catalog_contains_every_model_prompt(self) -> None:
        keys = (
            "translation.default_system",
            "translation.ad_detection_user",
            "translation.translate_user",
            "translation.compress_user",
            "content_analysis.structure_system",
            "content_analysis.structure_user",
            "content_analysis.video_type_system",
            "content_analysis.video_type_user",
            "content_analysis.representative_shot_label",
            "content_analysis.visual_shots_system",
            "content_analysis.visual_shots_user",
            "content_analysis.visual_shot_context",
            "animation.system",
            "animation.user",
            "animation.retry",
            "animation.style",
            "visual_planner.system",
            "music.system",
            "music.user",
            "music.retry",
        )
        for key in keys:
            with self.subTest(key=key):
                self.assertTrue(get_prompt(key).strip())

    def test_render_replaces_only_named_double_brace_placeholders(self) -> None:
        rendered = render_prompt(
            "translation.ad_detection_user",
            required=("payload_json",),
            payload_json='{"segments":[]}',
        )
        self.assertIn('{"segments":[]}', rendered)
        self.assertIn('{"ad_ranges":', rendered)
        self.assertNotIn("{{payload_json}}", rendered)

    def test_required_placeholder_cannot_be_removed_from_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prompts.json"
            path.write_text(
                json.dumps({"version": 1, "prompts": {"sample": "No input"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"RU_EN_DUBBER_PROMPTS_FILE": str(path)}):
                with self.assertRaises(PromptConfigError):
                    render_prompt(
                        "sample",
                        required=("payload_json",),
                        payload_json="{}",
                    )

    def test_environment_can_select_an_editable_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "custom.json"
            path.write_text(
                json.dumps({
                    "version": 1,
                    "prompts": {"custom": {"message": "Hello {{name}}"}},
                }),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"RU_EN_DUBBER_PROMPTS_FILE": str(path)}):
                self.assertEqual(prompt_catalog_path(), path.resolve())
                self.assertEqual(
                    render_prompt("custom.message", required=("name",), name="editor"),
                    "Hello editor",
                )

    def test_invalid_json_fails_with_prompt_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "broken.json"
            path.write_text("{not valid JSON", encoding="utf-8")
            with patch.dict(os.environ, {"RU_EN_DUBBER_PROMPTS_FILE": str(path)}):
                with self.assertRaisesRegex(PromptConfigError, "cannot load prompt catalog"):
                    get_prompt("translation.default_system")

    def test_model_call_sites_do_not_embed_prompt_prose(self) -> None:
        source_root = Path(__file__).parents[1] / "src" / "ru_en_dubber"
        call_sites = (
            source_root / "translation" / "qwen.py",
            source_root / "content" / "analysis.py",
            source_root / "visuals" / "animations.py",
            source_root / "visuals" / "planner.py",
            source_root / "music" / "stage.py",
        )
        for path in call_sites:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("/no_think", text)
                self.assertNotIn("You are ", text)
                self.assertNotIn("Return JSON only", text)


if __name__ == "__main__":
    unittest.main()
