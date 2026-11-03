from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.translation import QwenTranslationStage


class QwenTranslationTests(unittest.TestCase):
    def test_starts_multimodal_server_with_vision_projector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.gguf"
            projector = root / "mmproj.gguf"
            model.write_bytes(b"model")
            projector.write_bytes(b"projector")
            stage = QwenTranslationStage(
                server="llama-server", model=model, vision_projector=projector
            )
            process = Mock()
            with patch("subprocess.Popen", return_value=process) as popen:
                returned, log = stage._start_server(
                    "llama-server", model, 12345, root / "server.log"
                )
                log.close()

            command = popen.call_args.args[0]
            self.assertIs(returned, process)
            self.assertEqual(command[command.index("--mmproj") + 1], str(projector.resolve()))

    def test_writes_real_translations_and_replaces_fake_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_dir = root / "job"
            job_dir.mkdir()
            server = root / "llama-server.exe"
            model = root / "qwen.gguf"
            server.write_bytes(b"server")
            model.write_bytes(b"model")
            manifest = Manifest(
                job=Job(id="job", source_file="source.mp4"),
                segments=[
                    Segment(
                        id=1,
                        start=0.0,
                        end=2.0,
                        ru="Привет.",
                        en="Old",
                        tts_file="tts/000001.wav",
                        tts_duration=1.5,
                    ),
                    Segment(id=2, start=2.1, end=4.5, ru="Как дела?"),
                ],
                completed_stages=[
                    "transcription",
                    "fake_translation",
                    "tts",
                    "premiere_export",
                ],
                artifacts={
                    "tts_report": "tts.json",
                    "tts_segments": "tts",
                    "premiere_export": "premiere-export/premiere-export.json",
                    "premiere_xml": "premiere-export/premiere_sequence.xml",
                },
                metadata={
                    "translation_provider": "fake",
                    "tts": {"provider": "old"},
                    "premiere_export": {"episode_count": 1},
                },
            )
            process = Mock()
            log_file = io.BytesIO()
            stage = QwenTranslationStage(server=str(server), model=model)

            with (
                patch.object(stage, "_start_server", return_value=(process, log_file)),
                patch.object(stage, "_wait_until_ready"),
                patch.object(stage, "_stop_server"),
                patch.object(
                    stage,
                    "_translate_segments",
                    return_value={1: "Hello.", 2: "How are you?"},
                ),
            ):
                result = stage.run(manifest, job_dir)

            self.assertEqual(result.segments[0].en, "Hello.")
            self.assertEqual(result.segments[1].en, "How are you?")
            self.assertNotIn("fake_translation", result.completed_stages)
            self.assertNotIn("tts", result.completed_stages)
            self.assertNotIn("premiere_export", result.completed_stages)
            self.assertNotIn("translation_provider", result.metadata)
            self.assertNotIn("tts", result.metadata)
            self.assertNotIn("premiere_export", result.metadata)
            self.assertNotIn("tts_report", result.artifacts)
            self.assertNotIn("premiere_xml", result.artifacts)
            self.assertIsNone(result.segments[0].tts_file)
            self.assertIsNone(result.segments[0].tts_duration)
            self.assertEqual(result.metadata["translation"]["provider"], "qwen")
            self.assertEqual(result.artifacts["translation"], "translation.json")
            saved = json.loads((job_dir / "translation.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["segments"][1]["en"], "How are you?")

    def test_retries_overlong_lines_with_compression_prompt(self) -> None:
        segment = Segment(id=1, start=0.0, end=1.0, ru="Очень длинная фраза.")
        stage = QwenTranslationStage(server="llama-server", model=Path("model.gguf"))
        with patch.object(
            stage,
            "_request_translations",
            side_effect=[
                {1: "This translation is much too long to fit."},
                {1: "Too long."},
            ],
        ) as request:
            result = stage._translate_segments([segment], 12345)

        self.assertEqual(result[1], "Too long.")
        self.assertEqual(request.call_count, 2)

    def test_parses_json_even_when_wrapped_in_markdown(self) -> None:
        content = '```json\n{"translations":[{"id":7,"en":"All good."}]}\n```'
        self.assertEqual(
            QwenTranslationStage._parse_response(content),
            {7: "All good."},
        )

    def test_repairs_trailing_commas_in_model_json(self) -> None:
        content = '{"translations":[{"id":7,"en":"All good.",}],}'
        self.assertEqual(QwenTranslationStage._parse_response(content), {7: "All good."})

    def test_parses_ad_range_using_transcript_segment_boundaries(self) -> None:
        segments = [
            Segment(id=10, start=60.0, end=64.0, ru="А теперь партнёр видео."),
            Segment(id=11, start=64.1, end=70.0, ru="Используйте промокод TEST."),
        ]
        content = json.dumps(
            {
                "ad_ranges": [
                    {
                        "start_id": 10,
                        "end_id": 11,
                        "confidence": 0.97,
                        "brand": "Example",
                        "reason": "promo code",
                    }
                ]
            }
        )

        ranges = QwenTranslationStage._parse_ad_response(content, segments)

        self.assertEqual(ranges[0]["start"], 60.0)
        self.assertEqual(ranges[0]["end"], 70.0)
        self.assertEqual(ranges[0]["start_segment_id"], 10)
        self.assertEqual(ranges[0]["brand"], "Example")

    def test_expands_ad_start_to_repeated_brand_introduction(self) -> None:
        segments = [
            Segment(id=9, start=57.1, end=63.4, ru="Основная тема."),
            Segment(id=10, start=63.5, end=74.1, ru="На помощь приходит GoLogin."),
            Segment(id=11, start=74.2, end=86.7, ru="GoLogin защищает данные."),
            Segment(id=12, start=87.0, end=100.4, ru="Ссылка в описании."),
        ]
        content = json.dumps(
            {
                "ad_ranges": [
                    {
                        "start_id": 12,
                        "end_id": 12,
                        "confidence": 0.95,
                        "kind": "paid_sponsor",
                        "brand": "GoLogin",
                    }
                ]
            }
        )

        ranges = QwenTranslationStage._parse_ad_response(content, segments)

        self.assertEqual(ranges[0]["start_segment_id"], 10)
        self.assertEqual(ranges[0]["start"], 63.5)

    def test_splits_mixed_segment_at_word_level_when_removing_ad(self) -> None:
        segments = [
            Segment(
                id=1,
                start=0.0,
                end=4.0,
                ru="Промокод TEST а теперь продолжим обзор",
            )
        ]
        words = {
            1: [
                {"word": "Промокод", "start": 0.0, "end": 0.6},
                {"word": "TEST", "start": 0.7, "end": 1.0},
                {"word": "а", "start": 1.2, "end": 1.3},
                {"word": "теперь", "start": 1.4, "end": 1.8},
                {"word": "продолжим", "start": 1.9, "end": 2.6},
                {"word": "обзор", "start": 2.7, "end": 3.2},
            ]
        }

        result = QwenTranslationStage._remove_ad_speech(
            segments,
            [{"start": 0.0, "end": 1.05}],
            words,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 1)
        self.assertEqual(result[0].ru, "а теперь продолжим обзор")
        self.assertEqual(result[0].start, 1.2)

    def test_accepts_own_channel_giveaway_as_removable_integration(self) -> None:
        segments = [
            Segment(
                id=1,
                start=0.0,
                end=4.0,
                ru="Подписчики моего телеграм канала участвуют в розыгрыше.",
            )
        ]
        response = json.dumps({"ad_ranges": [{
            "start_id": 1,
            "end_id": 1,
            "start_quote": "Подписчики моего телеграм канала",
            "end_quote": "участвуют в розыгрыше",
            "kind": "self_promo",
            "confidence": 0.97,
            "brand": "own Telegram channel",
            "reason": "channel promotion",
        }]})
        stage = QwenTranslationStage(
            server="llama-server", model=Path("model.gguf"),
            ad_confidence_threshold=0.85,
        )

        with patch.object(stage, "_chat_completion", return_value=response):
            ranges = stage._detect_ad_ranges(segments, 12345)

        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["kind"], "self_promo")
        self.assertEqual((ranges[0]["start"], ranges[0]["end"]), (0.0, 4.0))

    def test_finds_return_to_main_topic_inside_mixed_segment(self) -> None:
        words = [
            {"word": "значит", "start": 8.0, "end": 8.4},
            {"word": "Плеерок", "start": 8.5, "end": 9.0},
            {"word": "как", "start": 9.1, "end": 9.2},
            {"word": "вы", "start": 9.3, "end": 9.4},
            {"word": "поняли", "start": 9.5, "end": 9.8},
            {"word": "обновление", "start": 9.9, "end": 10.4},
        ]

        boundary = QwenTranslationStage._transition_boundary(words)

        self.assertEqual(boundary, 9.0)

    def test_splits_batch_after_repeated_malformed_json(self) -> None:
        segments = [
            Segment(id=index, start=index, end=index + 1, ru=f"Фраза {index}")
            for index in range(1, 5)
        ]
        stage = QwenTranslationStage(server="llama-server", model=Path("model.gguf"))
        responses = [
            "broken",
            "still broken",
            "broken again",
            '{"translations":[{"id":1,"en":"One"},{"id":2,"en":"Two"}]}',
            '{"translations":[{"id":3,"en":"Three"},{"id":4,"en":"Four"}]}',
        ]
        with patch.object(stage, "_chat_completion", side_effect=responses) as request:
            result = stage._request_translations(
                12345,
                segments,
                stage._translation_prompt(segments, None, None),
                split_prompt_builder=lambda subset: stage._translation_prompt(
                    subset, None, None
                ),
            )

        self.assertEqual(result, {1: "One", 2: "Two", 3: "Three", 4: "Four"})
        self.assertEqual(request.call_count, 5)


if __name__ == "__main__":
    unittest.main()
