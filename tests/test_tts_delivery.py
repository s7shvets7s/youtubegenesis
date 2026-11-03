from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_en_dubber.models import Job, Manifest, Segment
from ru_en_dubber.tts.cosyvoice_worker import _instruction
from ru_en_dubber.tts.delivery import prepare_delivery_plan
from ru_en_dubber.tts.stage import ChatterboxTTSStage


class DeliveryPlanTests(unittest.TestCase):
    def test_plan_snapshots_valid_direction_and_rejects_stale_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = Manifest(
                job=Job(id="voice-test", source_file="script-source.mp4"),
                segments=[Segment(1, 0, 8, "A discovery.", en="A discovery.")],
            )
            direction = root / "direction.json"
            payload = {"version": 1, "segments": [
                {"id": 1, "text": "A discovery.", "style": "curious", "intensity": 0.5}
            ]}
            direction.write_text(json.dumps(payload), encoding="utf-8")
            plan = prepare_delivery_plan(manifest, root, direction)
            self.assertEqual(plan[0]["style"], "curious")
            self.assertEqual(manifest.artifacts["tts_delivery_plan"], "tts-delivery.json")
            self.assertIn("curiosity", _instruction("curious", 0.5))
            manifest.segments[0].en = "A changed line."
            with self.assertRaisesRegex(ValueError, "differs"):
                prepare_delivery_plan(manifest, root, direction)

    def test_cosyvoice_adapter_passes_style_and_keeps_report_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "python.exe"
            reference = root / "voice.wav"
            model_dir = root / "model"
            model_dir.mkdir()
            (model_dir / "cosyvoice3.yaml").write_text("model", encoding="utf-8")
            executable.write_bytes(b"exe")
            reference.write_bytes(b"wav")
            manifest = Manifest(
                job=Job(id="voice-test", source_file="script-source.mp4"),
                segments=[Segment(1, 0, 8, "A discovery.", en="A discovery.")],
            )
            direction = root / "direction.json"
            direction.write_text(json.dumps({"version": 1, "segments": [
                {"id": 1, "text": "A discovery.", "style": "curious", "intensity": 0.5}
            ]}), encoding="utf-8")
            stage = ChatterboxTTSStage(
                python=str(executable), reference=reference, provider="cosyvoice3",
                model_dir=model_dir, direction_file=direction,
            )

            def fake_worker(command: list[str], _environment: dict, _count: int):
                self.assertEqual(command[command.index("-m") + 1],
                                 "ru_en_dubber.tts.cosyvoice_worker")
                payload = json.loads((root / "tts-input.json").read_text(encoding="utf-8"))
                self.assertEqual(payload["segments"][0]["style"], "curious")
                output = root / "tts" / "000001.wav"
                output.parent.mkdir(exist_ok=True)
                output.write_bytes(b"wav")
                (root / "tts.json").write_text(json.dumps({"segments": [
                    {"id": 1, "path": "tts/000001.wav", "duration_seconds": 3.0}
                ]}), encoding="utf-8")
                return 0, ""

            with patch.object(stage, "_run_worker", side_effect=fake_worker):
                stage.run(manifest, root)
            self.assertEqual(manifest.segments[0].tts_file, "tts/000001.wav")
            self.assertEqual(manifest.metadata["tts"]["provider"], "cosyvoice3")


if __name__ == "__main__":
    unittest.main()
