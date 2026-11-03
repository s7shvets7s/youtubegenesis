"""Conservative local source-picture classification.

The detector calls a scene `author` only when a centered face persists through the
shot. Ambiguous face shots remain `unknown` and require review; this avoids silently
classifying a film close-up as the narrator.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Callable


def text_graphic_score(cv2: Any, frame: Any) -> float:
    """Estimate whether a frame contains designed, readable text lines.

    This deliberately detects layout rather than language: clustered glyph edges in
    wide horizontal rows. It catches title cards, slides and lower thirds without an
    OCR runtime or a language-specific model.
    """
    height, width = frame.shape[:2]
    if height < 32 or width < 32:
        return 0.0
    scale = min(1.0, 960.0 / width)
    scan = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
    gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
    scan_height, scan_width = gray.shape[:2]
    gradient = cv2.morphologyEx(
        gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )
    _, binary = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    joined = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, scan_width // 65), 3)),
        iterations=1,
    )
    contours = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    rows: list[tuple[int, int, int, int, float]] = []
    frame_area = float(scan_width * scan_height)
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area = box_width * box_height
        if not (
            box_width >= scan_width * 0.12
            and scan_height * 0.014 <= box_height <= scan_height * 0.22
            and box_width / max(1, box_height) >= 2.0
            and area <= frame_area * 0.20
        ):
            continue
        density = cv2.countNonZero(binary[y:y + box_height, x:x + box_width]) / max(1, area)
        roi = binary[y:y + box_height, x:x + box_width]
        component_count, _, component_stats, _ = cv2.connectedComponentsWithStats(roi, 8)
        glyphs = 0
        for component in component_stats[1:component_count]:
            component_width = int(component[cv2.CC_STAT_WIDTH])
            component_height = int(component[cv2.CC_STAT_HEIGHT])
            component_area = int(component[cv2.CC_STAT_AREA])
            if (component_height >= box_height * 0.24
                    and 0.08 <= component_width / max(1, component_height) <= 2.2
                    and component_area >= 6):
                glyphs += 1
        if 0.025 <= density <= 0.48 and glyphs >= 3:
            rows.append((x, y, box_width, box_height, density))
    if not rows:
        return 0.0
    width_score = sum(min(0.55, row[2] / scan_width) for row in rows[:4])
    row_score = min(0.45, len(rows) * 0.16)
    return round(min(1.0, width_score + row_score), 3)


def classify_face_observations(
    observations: list[dict[str, float | bool]], duration: float
) -> dict[str, Any]:
    if not observations:
        return {"source_type": "unknown", "contains_author": False, "contains_text": False,
                "text_confidence": 0.0, "confidence": 0.0,
                "requires_review": True, "reason": "no_visual_samples"}
    present = [item for item in observations if bool(item.get("face"))]
    persistence = len(present) / len(observations)
    areas = [float(item.get("area_ratio") or 0.0) for item in present]
    centers = [float(item.get("center_score") or 0.0) for item in present]
    area = statistics.median(areas) if areas else 0.0
    centered = statistics.median(centers) if centers else 0.0
    talking_head = duration >= 1.5 and persistence >= 0.66 and 0.015 <= area <= 0.35 and centered >= 0.45
    text_scores = [float(item.get("text_score") or 0.0) for item in observations]
    text_persistence = sum(score >= 0.58 for score in text_scores) / len(observations)
    contains_text = text_persistence >= 0.60
    visible_person = bool(present)
    confidence = min(0.94, 0.48 + 0.30 * persistence + 0.16 * centered) if talking_head else min(0.70, 0.25 + 0.35 * persistence)
    return {
        "source_type": ("author" if talking_head else
                        ("person_unknown" if visible_person else
                         ("text_graphic" if contains_text else "unknown"))),
        "contains_author": talking_head,
        "contains_text": contains_text,
        "text_confidence": round(statistics.median(text_scores), 3) if text_scores else 0.0,
        "confidence": round(confidence, 3),
        "requires_review": not talking_head or confidence < 0.80,
        "reason": "persistent_centered_face" if talking_head else "author_not_confirmed",
    }


class SourceVisualAnalyzer:
    def __init__(self, samples_per_shot: int = 3) -> None:
        if not 1 <= samples_per_shot <= 9:
            raise ValueError("samples per shot must be between 1 and 9")
        self.samples_per_shot = samples_per_shot

    def analyze(self, source: Path, shots: list[dict[str, Any]], output_dir: Path,
                *, progress: Callable[[int, int], None] | None = None) -> list[dict[str, Any]]:
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("OpenCV is required for source-picture analysis") from error
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise RuntimeError(f"could not open source video: {source}")
        if not hasattr(cv2, "CascadeClassifier"):
            capture.release()
            raise RuntimeError(
                "OpenCV 4.x is required for face analysis; reinstall requirements/editing.txt"
            )
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(str(cascade_path))
        if detector.empty():
            capture.release()
            raise RuntimeError("OpenCV face detector is unavailable")
        output_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, Any]] = []
        try:
            for fallback_index, shot in enumerate(shots, 1):
                start = float(shot["start_seconds"])
                end = float(shot["end_seconds"])
                duration = end - start
                observations: list[dict[str, float | bool]] = []
                preview = None
                frame_names: list[str] = []
                for sample_index in range(self.samples_per_shot):
                    fraction = (sample_index + 1) / (self.samples_per_shot + 1)
                    capture.set(cv2.CAP_PROP_POS_MSEC, (start + duration * fraction) * 1000.0)
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        continue
                    height, width = frame.shape[:2]
                    scale = min(1.0, 960.0 / max(width, 1))
                    scan = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
                    if preview is None:
                        preview = scan
                    frame_name = (
                        f"shot-{int(shot.get('index', fallback_index)):04d}-"
                        f"{sample_index + 1:02d}.jpg"
                    )
                    cv2.imwrite(
                        str(output_dir / frame_name),
                        scan,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 82],
                    )
                    frame_names.append(frame_name)
                    gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
                    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                                       minSize=(40, 40))
                    if len(faces) == 0:
                        observations.append({"face": False, "area_ratio": 0.0, "center_score": 0.0,
                                             "text_score": text_graphic_score(cv2, scan)})
                        continue
                    x, y, face_width, face_height = max(faces, key=lambda value: value[2] * value[3])
                    frame_area = max(1, scan.shape[0] * scan.shape[1])
                    face_x = (x + face_width / 2) / scan.shape[1]
                    face_y = (y + face_height / 2) / scan.shape[0]
                    distance = ((face_x - 0.5) ** 2 + (face_y - 0.45) ** 2) ** 0.5
                    observations.append({
                        "face": True,
                        "area_ratio": face_width * face_height / frame_area,
                        "center_score": max(0.0, 1.0 - distance / 0.65),
                        "text_score": text_graphic_score(cv2, scan),
                    })
                classification = classify_face_observations(observations, duration)
                preview_name = None
                if preview is not None:
                    preview_name = frame_names[0]
                results.append({
                    "index": int(shot.get("index", fallback_index)),
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "preview": preview_name,
                    "frames": frame_names,
                    "observations": observations,
                    **classification,
                })
                if progress is not None:
                    progress(fallback_index, len(shots))
        finally:
            capture.release()
        return results
