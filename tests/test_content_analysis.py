from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_en_dubber.content.analysis import (
    ContentAnalysisStage,
    action_for,
    merge_ad_ranges,
    removed_before,
    subtract_ranges,
    validate_blocks,
    visual_integration_ranges,
    visual_for_interval,
)
from ru_en_dubber.content.visual import classify_face_observations, text_graphic_score
from ru_en_dubber.models import Job, Manifest, Segment


class ContentAnalysisContracts(unittest.TestCase):
    def test_ripple_mapping_splits_content_around_ads(self) -> None:
        ranges = [{"start": 10.0, "end": 20.0}]
        self.assertEqual(subtract_ranges(5.0, 25.0, ranges), [(5.0, 10.0), (20.0, 25.0)])
        self.assertEqual(removed_before(25.0, ranges), 10.0)

    def test_only_qwen_confirmed_author_is_replaced_in_selective_modes(self) -> None:
        for mode in ("source-clean", "hybrid", "rebuild"):
            self.assertEqual(action_for(
                mode, "author", True, "person", visual_confidence=0.95
            ), "replace_media")
        self.assertEqual(action_for("hybrid", "unknown", False, "map"), "keep")
        self.assertEqual(action_for("source-clean", "unknown", False, "map"), "keep")
        self.assertEqual(action_for("source-clean", "person_unknown", False, "person"), "keep")
        self.assertEqual(action_for(
            "source-clean", "movie_scene", False, "original", True,
            text_role="editorial_overlay", visual_confidence=0.95,
            detected_text="Следующий эпизод",
            translated_text="Next episode",
            text_cleanup="static_ready",
        ), "localize_text")

    def test_text_localization_requires_visible_static_cyrillic(self) -> None:
        common = {
            "text_role": "editorial_overlay",
            "visual_confidence": 0.98,
            "translated_text": "Next episode",
            "text_cleanup": "static_ready",
        }
        self.assertEqual(
            action_for(
                "hybrid", "movie_scene", False, "original",
                detected_text="Next episode", **common,
            ),
            "keep",
        )
        self.assertEqual(
            action_for(
                "hybrid", "movie_scene", False, "original",
                detected_text="Следующий эпизод",
                text_cleanup="text_cleanup_review",
                **{key: value for key, value in common.items() if key != "text_cleanup"},
            ),
            "keep",
        )

    def test_film_only_override_never_marks_an_actor_as_author(self) -> None:
        shot = {"index": 1, "start": 0.0, "end": 3.0, "frames": ["frame.jpg"]}
        result = ContentAnalysisStage._validated_visual_shot(shot, {
            "source_type": "author",
            "contains_author": True,
            "text_role": "editorial_overlay",
            "detected_text": "Переход к следующему эпизоду",
            "translated_text": "Moving to the next episode",
            "text_regions": [{
                "detected_text": "Переход к следующему эпизоду",
                "translated_text": "Moving to the next episode",
                "bbox": {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.1},
                "motion": "static",
                "background": "simple",
                "visible_in_all_frames": True,
                "confidence": 0.97,
                "style": {"alignment": "center"},
            }],
            "confidence": 0.97,
        }, "film_only")

        self.assertEqual(result["source_type"], "movie_scene")
        self.assertFalse(result["contains_author"])
        self.assertEqual(result["translated_text"], "Moving to the next episode")
        self.assertEqual(result["text_cleanup_status"], "static_ready")

    def test_transcript_like_text_without_visual_region_is_not_localized(self) -> None:
        shot = {"index": 1, "start": 0.0, "end": 3.0, "frames": ["frame.jpg"]}
        result = ContentAnalysisStage._validated_visual_shot(shot, {
            "source_type": "movie_scene",
            "contains_author": False,
            "text_role": "editorial_overlay",
            "detected_text": "Фраза только из транскрипции",
            "translated_text": "A transcript-only phrase",
            "confidence": 0.99,
        }, "film_only")

        self.assertEqual(result["detected_text"], "")
        self.assertEqual(result["translated_text"], "")
        self.assertEqual(result["text_cleanup_status"], "not_needed")

    def test_implausibly_long_visual_text_is_not_localized(self) -> None:
        shot = {"index": 1, "start": 0.0, "end": 3.0, "frames": ["frame.jpg"]}
        sentence = " ".join(["длинная"] * 20)
        result = ContentAnalysisStage._validated_visual_shot(shot, {
            "source_type": "movie_scene",
            "contains_author": False,
            "text_role": "editorial_overlay",
            "text_regions": [{
                "detected_text": sentence,
                "translated_text": "long transcript sentence",
                "bbox": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.1},
                "motion": "static",
                "background": "simple",
                "visible_in_all_frames": True,
                "confidence": 0.99,
            }],
            "confidence": 0.99,
        }, "film_only")

        self.assertEqual(result["text_cleanup_status"], "text_cleanup_review")
        self.assertEqual(result["translated_text"], "")
        self.assertEqual(
            result["text_regions"][0]["decision_reason"],
            "implausible_visual_text_length",
        )

    def test_visual_qr_handle_becomes_self_promo_range_even_if_role_is_wrong(self) -> None:
        ranges = visual_integration_ranges([{
            "index": 248,
            "start": 752.32,
            "end": 763.08,
            "source_type": "movie_scene",
            "text_role": "editorial_overlay",
            "confidence": 0.91,
            "reason": "QR code and channel handle over the movie scene",
            "text_regions": [{
                "detected_text": "@AHEAD_FOR_BREAD",
                "confidence": 0.95,
            }],
        }])

        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["kind"], "self_promo")
        self.assertEqual(ranges[0]["brand"], "@AHEAD_FOR_BREAD")
        self.assertEqual((ranges[0]["start"], ranges[0]["end"]), (752.32, 763.08))

    def test_merges_visual_lead_in_with_transcript_integration(self) -> None:
        result = merge_ad_ranges([
            {"start": 752.32, "end": 763.08, "confidence": 0.95,
             "kind": "self_promo", "brand": "@AHEAD_FOR_BREAD"},
            {"start": 758.0, "end": 763.32, "confidence": 0.97,
             "kind": "self_promo", "start_segment_id": 147,
             "end_segment_id": 147, "brand": "Telegram"},
        ])

        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["start"], result[0]["end"]), (752.32, 763.32))
        self.assertEqual(result[0]["start_segment_id"], 147)

    def test_moving_text_is_kept_for_manual_cleanup_review(self) -> None:
        shot = {"index": 1, "start": 0.0, "end": 3.0, "frames": ["frame.jpg"]}
        result = ContentAnalysisStage._validated_visual_shot(shot, {
            "source_type": "movie_scene",
            "contains_author": False,
            "text_role": "title_card",
            "text_regions": [{
                "detected_text": "НОВОСТИ",
                "translated_text": "NEWS",
                "bbox": {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.2},
                "motion": "moving",
                "background": "complex",
                "visible_in_all_frames": True,
                "confidence": 0.99,
            }],
            "confidence": 0.99,
        }, "film_only")

        self.assertEqual(result["text_cleanup_status"], "text_cleanup_review")
        self.assertEqual(result["translated_text"], "")
        self.assertTrue(result["requires_review"])

    def test_uncertain_video_type_cannot_confirm_author(self) -> None:
        shot = {"index": 1, "start": 0.0, "end": 3.0, "frames": ["frame.jpg"]}
        result = ContentAnalysisStage._validated_visual_shot(shot, {
            "source_type": "author", "contains_author": True,
            "text_role": "none", "confidence": 0.99,
        }, "uncertain")

        self.assertEqual(result["source_type"], "uncertain")
        self.assertFalse(result["contains_author"])

    def test_persistent_centered_face_is_conservative_author_signal(self) -> None:
        author = classify_face_observations([
            {"face": True, "area_ratio": 0.08, "center_score": 0.9},
            {"face": True, "area_ratio": 0.09, "center_score": 0.8},
            {"face": True, "area_ratio": 0.08, "center_score": 0.85},
        ], 5.0)
        ambiguous = classify_face_observations([
            {"face": True, "area_ratio": 0.08, "center_score": 0.9},
            {"face": False, "area_ratio": 0.0, "center_score": 0.0},
            {"face": False, "area_ratio": 0.0, "center_score": 0.0},
        ], 5.0)
        self.assertTrue(author["contains_author"])
        self.assertFalse(ambiguous["contains_author"])
        self.assertEqual(ambiguous["source_type"], "person_unknown")
        self.assertTrue(ambiguous["requires_review"])
        self.assertEqual(action_for("hybrid", ambiguous["source_type"], False, "person"), "keep")

    def test_text_graphic_detector_finds_title_layout_without_ocr(self) -> None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV is unavailable")
        title = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(title, "THREE SPEECH TECHNIQUES", (35, 125),
                    cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(title, "METHOD ONE", (35, 235),
                    cv2.FONT_HERSHEY_DUPLEX, 1.8, (20, 120, 245), 3, cv2.LINE_AA)
        plain = np.full_like(title, 90)
        for y in range(40, 360, 70):
            cv2.line(plain, (0, y), (640, y), (120, 120, 120), 5)
        self.assertGreaterEqual(text_graphic_score(cv2, title), 0.58)
        self.assertLess(text_graphic_score(cv2, plain), 0.58)

    def test_visual_overlap_uses_duration_not_shot_count(self) -> None:
        result = visual_for_interval(0, 10, [
            {"index": 1, "start": 0, "end": 8, "contains_author": True, "confidence": 0.9, "requires_review": False},
            {"index": 2, "start": 8, "end": 10, "contains_author": False, "confidence": 0.4, "requires_review": True},
        ])
        self.assertTrue(result["contains_author"])
        self.assertEqual(result["source_type"], "author")

    def test_visual_overlap_promotes_persistent_text_graphic(self) -> None:
        result = visual_for_interval(0, 4, [{
            "index": 1, "start": 0, "end": 4, "contains_author": False,
            "contains_text": True, "text_confidence": 0.9, "confidence": 0.4,
            "requires_review": False,
        }])
        self.assertTrue(result["contains_text"])
        self.assertEqual(result["source_type"], "text_graphic")

    def test_block_schema_rejects_unknown_fields_and_invalid_ids(self) -> None:
        segments = [Segment(1, 0, 2, "а", en="A"), Segment(2, 2, 4, "б", en="B")]
        valid = {"content_blocks": [{"start_id": 1, "end_id": 2, "topic": "Topic",
                                      "visual_intent": "event", "subject": "Subject"}]}
        self.assertEqual(validate_blocks(valid, segments)[0]["segment_ids"], [1, 2])
        with self.assertRaises(ValueError):
            validate_blocks({"content_blocks": [dict(valid["content_blocks"][0], command="calc")]}, segments)
        with self.assertRaises(ValueError):
            validate_blocks({"content_blocks": [dict(valid["content_blocks"][0], end_id=99)]}, segments)
        with self.assertRaises(ValueError):
            validate_blocks({"content_blocks": [dict(valid["content_blocks"][0], end_id=1)]}, segments)


class ContentAnalysisStageTests(unittest.TestCase):
    def test_combines_ad_structure_visuals_and_post_cut_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "source.mp4").write_bytes(b"video")
            (root / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
            server, model = root / "llama-server.exe", root / "model.gguf"
            projector = root / "mmproj.gguf"
            server.write_bytes(b"server")
            model.write_bytes(b"model")
            projector.write_bytes(b"projector")
            manifest = Manifest(Job("job", "source.mp4"), segments=[
                Segment(1, 0, 5, "Введение", en="Introduction"),
                Segment(2, 5, 10, "Реклама", en="Advertisement"),
                Segment(3, 10, 15, "Карта", en="The route on a map"),
            ], artifacts={"transcript": "transcript.json"})
            stage = ContentAnalysisStage(server=str(server), model=model, vision_projector=projector, python="python",
                                         source_mode="hybrid", ad_removal_mode="remove")
            structure = json.dumps({"video_summary": "Test", "content_blocks": [
                {"start_id": 1, "end_id": 1, "topic": "Intro", "visual_intent": "person", "subject": "narrator"},
                {"start_id": 2, "end_id": 2, "topic": "Sponsor", "visual_intent": "object", "subject": "sponsor"},
                {"start_id": 3, "end_id": 3, "topic": "Route", "visual_intent": "map", "subject": "route map"},
            ]})
            visual = [
                {"index": 1, "start": 0.0, "end": 5.0, "contains_author": True, "source_type": "author",
                 "confidence": 0.9, "requires_review": False, "preview": "shot-0001.jpg"},
                {"index": 2, "start": 5.0, "end": 15.0, "contains_author": False, "source_type": "unknown",
                 "confidence": 0.3, "requires_review": True, "preview": "shot-0002.jpg"},
            ]
            process, log = Mock(), io.BytesIO()
            with (
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._resolve_executable", return_value=str(server)),
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._reserve_port", return_value=1234),
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._start_server", return_value=(process, log)),
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._wait_until_ready"),
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._stop_server"),
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._load_word_timings", return_value={}),
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._detect_ad_ranges",
                      return_value=[{"start": 5.0, "end": 10.0, "confidence": 0.98}]),
                patch("ru_en_dubber.content.analysis.QwenTranslationStage._chat_completion", return_value=structure),
                patch.object(stage, "_detect_shots", return_value={"scenes": [
                    {"index": 1, "start_seconds": 0, "end_seconds": 5},
                    {"index": 2, "start_seconds": 5, "end_seconds": 15}]}),
                patch("ru_en_dubber.content.analysis.SourceVisualAnalyzer.analyze", return_value=visual),
                patch.object(stage, "_analyze_video_type", return_value="mixed"),
                patch.object(stage, "_analyze_visual_shots", return_value=visual),
            ):
                result = stage.run(manifest, root)
            plan = json.loads((root / "content-analysis/episode-plan.json").read_text(encoding="utf-8"))
            self.assertEqual([item["topic"] for item in plan["episodes"]], ["Intro", "Route"])
            self.assertEqual(plan["episodes"][0]["action"], "replace_media")
            self.assertEqual(plan["episodes"][1]["action"], "keep")
            self.assertEqual(plan["episodes"][1]["timeline_start"], 5.0)
            self.assertEqual([segment.id for segment in result.segments], [1, 3])
            self.assertEqual(result.metadata["content_analysis"]["removed_ad_seconds"], 5.0)
            self.assertEqual(result.artifacts["episode_plan"], "content-analysis/episode-plan.json")
            cached = stage.run(result, root)
            self.assertEqual([segment.id for segment in cached.segments], [1, 3])
            self.assertEqual(cached.metadata["ad_removal"]["ranges"][0]["start"], 5.0)


if __name__ == "__main__":
    unittest.main()
