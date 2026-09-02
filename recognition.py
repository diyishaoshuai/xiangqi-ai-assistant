from __future__ import annotations

import base64
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

try:
    import cv2
    import numpy as np
    from cchess_onnx.full_classifier import FULL_CLASSIFIER_ONNX
    from cchess_onnx.helper_4_kpt import extract_chessboard
    from cchess_onnx.rtmpose import RTMPOSE_ONNX
except ImportError:  # The legacy recognizer remains available as a safe fallback.
    cv2 = np = FULL_CLASSIFIER_ONNX = extract_chessboard = RTMPOSE_ONNX = None

from core import FILES
from app_paths import resource_base as _runtime_base, user_data_dir


class TransientBoardFrame(RuntimeError):
    """A locked board is visible, but this animation/occlusion frame is unusable."""


# Ratios of the 9x10 intersection rectangle inside this game's 16:9 screenshot.
GRID_RATIOS = (0.0953, 0.0972, 0.4758, 0.8570)

# 16x16 glyph features learned from the game's own piece artwork. These cover
# the five pieces present in the supplied endgame. New artwork is learned from
# the review dialog and stored locally as numeric features, never screenshots.
BUILTIN_TEMPLATES = {
    "K": "AAClhQAAADiI/OcAAAAAAAAAwf9fAAAAP//qAAMvJQAAAGH/qQAAAF3/87vw9uqNAwBR/44AVACS//bqpMzowFkAUv+cfH4k2+3tvTB26qBzAFb/rsClwOrh5FcAG9t/MQBb/JS65+PY4dUTABnMZBIAbPCdx9KCedjAAAA/wEUAAH7eydt+AEPUuQAATrE1AACiyI3VcABBybcVAHXAJwAHw64lt6wAKr24moerpQAAdNKHAFCcAA+ur7KvskcAc8a8EwAAAAAhqZ9sfDoAALKrJwAAAAAAGKKgGAAAAAAAAAAAAAAAAAmZmgAAAAAAAAAAAAAAAAAAk5QAAAAAAA==",
    "A": "zPPSUAAAVf7//+EAAAAAAC////MAAJz//v/qCAAAAAAA3v/EAAAkRd7/zwAAAAAAl//+MQAAAAbk864AHCMAAP/hLAAAAAAn5+qWGanevm3hEQAAAAAAG9vhxsbSyca+1QAAAAAAADTY0s/Py8W6q9cAAAAAAA6az8nLtJmETw2iDG+BSGzEz8bLoQkAAAAAcjZpzNXWz7rByWYAAAAAAH8xAC6XnVoztMBTAAAAAACsWQAAAAAAM7GuVQAAAAAAyZMAAAAAJJmlo5yYkCwAAMdyAAAvb5+hn52ipKO1PQDEWAA4o6qflZaaoKCy028AtzIAAA5jnJN4RB4MPWEAAA==",
    "C": "//8uAAB7/w4Agsa0UQAAAMP/SgAj/+dtx////88AAAB6/yYAsv///89t3//NAAAAuP8AAP//6lcAAIv/sgAAAPn/AACdqw9vfABq8o0AAAD//2YAABV429tcbOF/AAAA////FDHJ49K+HXvNZAAAAP/289XhpXjVcwB/wEgAAAD/nzbqySBVwQMAjageAAAA/4cAP7qcvosAAJ+oAAAAAP+BACS0uocBF4q9kAAAAA//skycxnAAAAAvZAIAAAAX7eTMusQTAAAAAAAAAAAAF+fAz827IQAAAAAAAAAAGl7EEGPJwYchAwAAAAkxXHxgVgAAoLSxq5yWi4t/f3ZhTg==",
    "k": "AAD/+QAAAADi/1OP/OIAAAAA78IAAACfpgAA2ek9AAAAAPx8AABs0hYKzOlzAAAA////LQAjuWC2zMmsQwAAAP///wAAAAAAgI8wAAAAAAAAyf8AAAAAFnwAACApAAAAAJb8GgAAIJkAAAAASQAAAADfzEMzZmYNAAAAMxAAAADC1qkAAFBDAAApBhAaAAAA35ljAAZGSUAAAAAAOQAAAMYAUAAAPTYgAAAAAC0AAABMAJYAAAAAIAAAAAAmAAAAAACWAAAAACYWAAAAGgAAAABcjwAAAAAmEwAAAAMAAAAAPVkAAAAAAAAAAAAAAAAAAEMAAAAAAAAAAAAAAAAAAA==",
    "b": "AAAAAAAAAP//AAAAAAAAAAAAAAAAAPz///KAAAAAAAAAAAAAbP///////5MAAAAAAAAAFpNWAOn59UYAAAAAAAAAAAAAALn8wiAAAC0mAAAAAAAAKf//6R0AAAAAbINs/2AAAP+GnNIAAAAAAHBgHc//1v9mAD2vBgAQQ3wAAAAA6f/cABCccIaAUwAAAAAAAADsz9a2g1ZjMAAAAAAAAAAAALKvfFBGTAAAAAAABgAAAAB2mXBGQ0xMLQAAABAAAAC8qYlzXAAAM1AAAAAAAACTxnMAA2MAABYTKQAAAAAAE68tAABmAAATEBoAAAAAAEyWVgANbAAAHRATAAAAAA==",
}


@dataclass(slots=True)
class Detection:
    square: tuple[int, int]
    side: str
    piece: str | None
    confidence: float
    margin: float
    patch: Image.Image
    feature: list[float]


@dataclass(frozen=True, slots=True)
class BoardGeometry:
    """Map canonical Xiangqi squares back to pixels in the source image."""

    inverse_matrix: tuple[float, ...]
    rotated: bool
    confidence: float
    image_size: tuple[int, int]
    piece_centers: tuple[tuple[int, int, float, float], ...] = ()

    def point_for_square(self, square: tuple[int, int]) -> tuple[float, float]:
        x, rank = square
        if not (0 <= x <= 8 and 0 <= rank <= 9):
            raise ValueError(f"棋盘坐标越界：{square}")
        # The classifier operates on the 350x400 intersection rectangle in
        # the 450x500 perspective-normalized image.  Convert the canonical
        # red-at-bottom coordinates back to the classifier orientation first.
        if self.rotated:
            column, row = 8 - x, rank
        else:
            column, row = x, 9 - rank
        target_x = 50.0 + column * (350.0 / 8.0)
        target_y = 50.0 + row * (400.0 / 9.0)
        matrix = self.inverse_matrix
        denominator = matrix[6] * target_x + matrix[7] * target_y + matrix[8]
        if abs(denominator) < 1e-9:
            raise ValueError("棋盘透视映射无效")
        source_x = (
            matrix[0] * target_x + matrix[1] * target_y + matrix[2]
        ) / denominator
        source_y = (
            matrix[3] * target_x + matrix[4] * target_y + matrix[5]
        ) / denominator
        return source_x, source_y

    def point_for_piece(self, square: tuple[int, int]) -> tuple[float, float]:
        """Return the detected disc centre, falling back to its intersection."""
        for x, rank, center_x, center_y in self.piece_centers:
            if (x, rank) == square:
                return center_x, center_y
        return self.point_for_square(square)


def _feature(patch: Image.Image, red: bool) -> list[float]:
    image = patch.crop((8, 8, 56, 56)).resize((16, 16)).convert("RGB")
    values: list[float] = []
    for r, g, b in image.getdata():
        if red:
            value = max(0.0, min(1.0, (r - max(g, b) * 1.1) / 100))
        else:
            value = max(0.0, min(1.0, (100 - (r + g + b) / 3) / 80))
        values.append(value)
    return values


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left)
    right_norm = sum(b * b for b in right)
    if not left_norm or not right_norm:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def _decode_feature(data: str) -> list[float]:
    return [value / 255 for value in base64.b64decode(data)]


def _encode_feature(feature: list[float]) -> str:
    raw = bytes(max(0, min(255, round(value * 255))) for value in feature)
    return base64.b64encode(raw).decode("ascii")


def crop_game_grid(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    width, height = image.size
    if width / max(height, 1) > 1.35:
        left, top, right, bottom = GRID_RATIOS
        return image.crop(
            (
                round(width * left),
                round(height * top),
                round(width * right),
                round(height * bottom),
            )
        )
    return image


def _patch(grid: Image.Image, x: int, rank: int, radius_factor: float) -> Image.Image:
    step_x = grid.width / 8
    step_y = grid.height / 9
    center_x = x * step_x
    center_y = (9 - rank) * step_y
    radius = min(step_x, step_y) * radius_factor
    return grid.crop(
        (center_x - radius, center_y - radius, center_x + radius, center_y + radius)
    ).resize((64, 64))


def _source_patch(
    image: Image.Image,
    center_x: float,
    center_y: float,
    step_x: float,
    step_y: float,
    radius_factor: float,
) -> Image.Image:
    radius = min(step_x, step_y) * radius_factor
    return image.crop(
        (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        )
    ).resize((64, 64))


def _occupied(patch: Image.Image) -> bool:
    gray = patch.convert("L")
    histogram = gray.histogram()
    total = sum(histogram)
    bright_ratio = sum(histogram[135:]) / max(total, 1)
    return bright_ratio >= 0.35


def _is_red(patch: Image.Image) -> bool:
    pixels = list(patch.convert("RGB").getdata())
    red_pixels = sum(
        1
        for r, g, b in pixels
        if r > 75 and r > g * 1.25 and r > b * 1.18
    )
    return red_pixels / max(len(pixels), 1) >= 0.04


class TemplatePieceRecognizer:
    def __init__(self):
        self.templates: dict[str, list[list[float]]] = {
            piece: [_decode_feature(data)] for piece, data in BUILTIN_TEMPLATES.items()
        }
        self.data_dir = user_data_dir()
        self.learned_path = self.data_dir / "recognition_templates.json"
        self._load_learned()

    def _load_learned(self) -> None:
        try:
            path = self.learned_path
            if not path.exists():
                # Older versions used this fallback when LOCALAPPDATA was absent.
                path = Path.home() / ".xiangqi_ai" / "recognition_templates.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            for piece, items in payload.items():
                if piece not in "KABNRCPkabnrcp" or not isinstance(items, list):
                    continue
                self.templates.setdefault(piece, []).extend(
                    _decode_feature(item) for item in items if isinstance(item, str)
                )
        except (OSError, ValueError, TypeError):
            return

    def learn(self, piece: str, feature: list[float]) -> None:
        if piece not in "KABNRCPkabnrcp":
            return
        self.templates.setdefault(piece, []).append(feature)
        learned: dict[str, list[str]] = {}
        for label, features in self.templates.items():
            builtin_count = 1 if label in BUILTIN_TEMPLATES else 0
            extra = features[builtin_count:]
            if extra:
                learned[label] = [_encode_feature(item) for item in extra[-12:]]
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.learned_path.write_text(
                json.dumps(learned, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def classify(self, feature: list[float], side: str) -> tuple[str | None, float, float]:
        candidates: list[tuple[float, str]] = []
        for piece, samples in self.templates.items():
            if (piece.isupper()) != (side == "w"):
                continue
            candidates.append((max(_cosine(feature, sample) for sample in samples), piece))
        candidates.sort(reverse=True)
        if not candidates:
            return None, 0.0, 0.0
        best_score, best_piece = candidates[0]
        second_score = candidates[1][0] if len(candidates) > 1 else 0.0
        margin = best_score - second_score
        if best_score < 0.70 or margin < 0.10:
            return None, best_score, margin
        return best_piece, best_score, margin

    def recognize(self, image: Image.Image) -> tuple[Image.Image, list[Detection]]:
        source = image.convert("RGB")
        grid = crop_game_grid(source)
        if source.width / max(source.height, 1) > 1.35:
            left = source.width * GRID_RATIOS[0]
            top = source.height * GRID_RATIOS[1]
            step_x = source.width * (GRID_RATIOS[2] - GRID_RATIOS[0]) / 8
            step_y = source.height * (GRID_RATIOS[3] - GRID_RATIOS[1]) / 9
        else:
            left = 0.0
            top = 0.0
            step_x = source.width / 8
            step_y = source.height / 9
        detections: list[Detection] = []
        for rank in range(10):
            for x in range(9):
                center_x = left + x * step_x
                center_y = top + (9 - rank) * step_y
                occupancy_patch = _source_patch(
                    source, center_x, center_y, step_x, step_y, 0.32
                )
                if not _occupied(occupancy_patch):
                    continue
                glyph_patch = _source_patch(
                    source, center_x, center_y, step_x, step_y, 0.25
                )
                side = "w" if _is_red(occupancy_patch) else "b"
                feature = _feature(glyph_patch, red=side == "w")
                piece, confidence, margin = self.classify(feature, side)
                detections.append(
                    Detection(
                        square=(x, rank),
                        side=side,
                        piece=piece,
                        confidence=confidence,
                        margin=margin,
                        patch=glyph_patch,
                        feature=feature,
                    )
                )
        return grid, detections


class NeuralBoardRecognizer:
    """Adapter for the pretrained Chinese Chess Recognition ONNX models."""

    def __init__(self, model_dir: Path):
        if any(
            item is None
            for item in (cv2, np, FULL_CLASSIFIER_ONNX, extract_chessboard, RTMPOSE_ONNX)
        ):
            raise RuntimeError("缺少 ONNX Runtime、OpenCV 或 NumPy")
        pose_path = model_dir / "board_pose.onnx"
        classifier_path = model_dir / "board_classifier.onnx"
        if not pose_path.exists() or not classifier_path.exists():
            raise RuntimeError(f"找不到深度识别模型：{model_dir}")
        self.pose = RTMPOSE_ONNX(str(pose_path))
        self.classifier = FULL_CLASSIFIER_ONNX(str(classifier_path))
        self.last_layout = ""
        self.last_keypoint_scores: list[float] = []
        self.last_geometry: BoardGeometry | None = None
        self.last_search_bbox = None
        self.last_image_size = None
        self.tracking_failures = 0
        self.last_timings = {}

    @staticmethod
    def _validate_keypoints(image_rgb, keypoints, search_bbox=None) -> None:
        height, width = image_rgb.shape[:2]
        if keypoints.shape != (4, 2) or not np.isfinite(keypoints).all():
            raise RuntimeError("棋盘关键点无效")
        polygon = np.float32(
            [keypoints[0], keypoints[1], keypoints[3], keypoints[2]]
        )
        area = abs(float(cv2.contourArea(polygon)))
        top_width = float(np.linalg.norm(keypoints[1] - keypoints[0]))
        bottom_width = float(np.linalg.norm(keypoints[3] - keypoints[2]))
        left_height = float(np.linalg.norm(keypoints[2] - keypoints[0]))
        right_height = float(np.linalg.norm(keypoints[3] - keypoints[1]))
        # A phone/emulator board may occupy only a small part of a 4K desktop.
        # Judge its size against the region sent to the pose model, not always
        # against the whole screenshot.  Absolute edge limits still prevent a
        # tiny false positive from becoming clickable geometry.
        reference_area = width * height
        if search_bbox is not None:
            left, top, right, bottom = search_bbox
            reference_area = max(1, min(width, right) - max(0, left)) * max(
                1, min(height, bottom) - max(0, top)
            )
        if area < reference_area * 0.025:
            raise RuntimeError("没有可靠定位到完整棋盘")
        if min(top_width, bottom_width, left_height, right_height) < 80:
            raise RuntimeError("识别到的棋盘尺寸过小")

    @staticmethod
    def _needs_rotation(rows: list[list[str]]) -> bool:
        red_king_rows = [r for r, row in enumerate(rows) if "K" in row]
        black_king_rows = [r for r, row in enumerate(rows) if "k" in row]
        if red_king_rows and black_king_rows:
            return red_king_rows[0] < black_king_rows[0]
        if red_king_rows:
            return red_king_rows[0] <= 2
        if black_king_rows:
            return black_king_rows[0] >= 7
        return False

    @staticmethod
    def _cell_patch(board_image: Image.Image, column: int, row: int) -> Image.Image:
        center_x = 50 + column * (350 / 8)
        center_y = 50 + row * (400 / 9)
        radius = 23
        return board_image.crop(
            (
                center_x - radius,
                center_y - radius,
                center_x + radius,
                center_y + radius,
            )
        ).resize((64, 64))

    @staticmethod
    def _candidate_bboxes(height: int, width: int) -> list[list[int]]:
        candidates = [[0, 0, width, height]]
        if width > height * 1.15:
            # The released game-screenshot examples commonly place the board
            # on the left. Preserve the upstream extent and also try explicit
            # left/right square regions so other client layouts can compete.
            candidates.extend(
                [
                    [0, 0, height, width],
                    [0, 0, height, height],
                    [width - height, 0, width, height],
                ]
            )
        unique: list[list[int]] = []
        for item in candidates:
            if item not in unique:
                unique.append(item)
        return unique

    @staticmethod
    def _tiled_candidate_bboxes(height: int, width: int) -> list[list[int]]:
        """Cover small game windows anywhere on a large desktop.

        These regions are only reached when the cheap whole/left/right search
        did not produce a trustworthy board, so an already locked platform
        keeps its previous latency.  Two scales cover a typical desktop game
        window and a narrow phone/emulator window without assuming its x/y.
        """
        short = min(height, width)
        candidates: list[list[int]] = []

        def origins(length: int, extent: int) -> list[int]:
            if extent >= length:
                return [0]
            step = max(1, round(extent * 0.60))
            values = list(range(0, length - extent + 1, step))
            end = length - extent
            if not values or values[-1] != end:
                values.append(end)
            return values

        for fraction in (0.65, 0.42):
            extent = max(240, min(short, round(short * fraction)))
            for top in origins(height, extent):
                for left in origins(width, extent):
                    candidates.append([left, top, left + extent, top + extent])
        return candidates

    @staticmethod
    def _candidate_is_plausible(rows: list[list[str]]) -> bool:
        labels = [label for row in rows for label in row]
        limits = {
            "K": 1, "A": 2, "B": 2, "N": 2, "R": 2, "C": 2, "P": 5,
            "k": 1, "a": 2, "b": 2, "n": 2, "r": 2, "c": 2, "p": 5,
        }
        if any(labels.count(piece) > limit for piece, limit in limits.items()):
            return False
        if labels.count("K") != 1 or labels.count("k") != 1:
            return False
        red = next((row for row, values in enumerate(rows) if "K" in values), -1)
        black = next((row for row, values in enumerate(rows) if "k" in values), -1)
        return (red <= 2 and black >= 7) or (black <= 2 and red >= 7)

    @staticmethod
    def _calibrate_standard_start(rows, confidences):
        """Recover a new visual theme from its unambiguous standard setup.

        Even a domain-shifted classifier generally distinguishes a piece from
        an empty intersection.  If and only if all 32 occupied intersections
        exactly match the standard initial layout, their identities are known
        from coordinates.  Red/black orientation must still have independent
        visual/classifier evidence; otherwise no correction is made.
        """
        occupied = {(row, column) for row in range(10) for column in range(9)
                    if rows[row][column] != "."}
        expected = ({(0, column) for column in range(9)}
                    | {(2, 1), (2, 7)} | {(3, column) for column in range(0, 9, 2)}
                    | {(6, column) for column in range(0, 9, 2)} | {(7, 1), (7, 7)}
                    | {(9, column) for column in range(9)})
        if occupied != expected:
            return rows, confidences, False

        upper_top = lower_top = upper_bottom = lower_bottom = 0.0
        for row in (*range(4), *range(6, 10)):
            for column in range(9):
                label = rows[row][column]
                confidence = float(confidences[row][column])
                if label not in "KABNRCPkabnrcp" or confidence < .55:
                    continue
                bottom = row >= 6
                if label.isupper():
                    upper_bottom += confidence if bottom else 0.0
                    upper_top += confidence if not bottom else 0.0
                else:
                    lower_bottom += confidence if bottom else 0.0
                    lower_top += confidence if not bottom else 0.0
        normal_votes = upper_bottom + lower_top
        rotated_votes = upper_top + lower_bottom
        if abs(normal_votes - rotated_votes) < 2.0:
            return rows, confidences, False
        red_bottom = normal_votes > rotated_votes
        black_back = list("rnbakabnr")
        red_back = list("RNBAKABNR")
        standard = [["."] * 9 for _ in range(10)]
        top_back, bottom_back = ((black_back, red_back) if red_bottom else (red_back, black_back))
        standard[0], standard[9] = top_back, bottom_back
        top_cannon = "c" if red_bottom else "C"
        top_pawn = "p" if red_bottom else "P"
        bottom_cannon = "C" if red_bottom else "c"
        bottom_pawn = "P" if red_bottom else "p"
        standard[2][1] = standard[2][7] = top_cannon
        standard[7][1] = standard[7][7] = bottom_cannon
        for column in range(0, 9, 2):
            standard[3][column] = top_pawn
            standard[6][column] = bottom_pawn
        calibrated = [[float(value) for value in row] for row in confidences]
        for row, column in expected:
            calibrated[row][column] = max(.78, min(.94, calibrated[row][column]))
        return standard, calibrated, True

    @staticmethod
    def _candidate_score(
        rows: list[list[str]], confidences: list[list[float]], keypoint_scores
    ) -> float:
        labels = [label for row in rows for label in row]
        all_scores = [float(value) for row in confidences for value in row]
        piece_scores = [
            float(confidences[row][column])
            for row in range(10)
            for column in range(9)
            if rows[row][column] in "KABNRCPkabnrcp"
        ]
        if not piece_scores:
            return -10.0
        score = sum(piece_scores) / len(piece_scores)
        score += 0.15 * (sum(all_scores) / len(all_scores))
        score += 0.05 * float(np.mean(keypoint_scores))
        if labels.count("K") == 1 and labels.count("k") == 1:
            score += 0.50
        else:
            score -= 0.75
        score -= labels.count("x") * 0.04
        limits = {
            "K": 1, "A": 2, "B": 2, "N": 2, "R": 2, "C": 2, "P": 5,
            "k": 1, "a": 2, "b": 2, "n": 2, "r": 2, "c": 2, "p": 5,
        }
        score -= sum(max(0, labels.count(piece) - limit) for piece, limit in limits.items()) * 0.10
        return score

    @staticmethod
    def _hint_bbox(geometry: BoardGeometry | None, image_size) -> list[int] | None:
        if geometry is None or geometry.image_size != image_size:
            return None
        corners = [geometry.point_for_square(square) for square in ((0, 0), (8, 0), (0, 9), (8, 9))]
        left, right = min(p[0] for p in corners), max(p[0] for p in corners)
        top, bottom = min(p[1] for p in corners), max(p[1] for p in corners)
        pad_x, pad_y = (right - left) * .14, (bottom - top) * .14
        width, height = image_size
        return [max(0, int(left - pad_x)), max(0, int(top - pad_y)),
                min(width, int(right + pad_x)), min(height, int(bottom + pad_y))]

    @staticmethod
    def _normalized_square(square: tuple[int, int], rotated: bool) -> tuple[float, float]:
        x, rank = square
        column, row = ((8 - x, rank) if rotated else (x, 9 - rank))
        return 50.0 + column * (350.0 / 8.0), 50.0 + row * (400.0 / 9.0)

    @classmethod
    def _geometry_from_keypoints(
        cls,
        keypoints,
        keypoint_scores,
        *,
        rotated: bool,
        image_size: tuple[int, int],
    ) -> BoardGeometry:
        normalized_corners = np.float32(
            [[50, 50], [400, 50], [50, 450], [400, 450]]
        )
        inverse = cv2.getPerspectiveTransform(
            normalized_corners,
            np.float32(keypoints),
        )
        return BoardGeometry(
            inverse_matrix=tuple(float(value) for value in inverse.reshape(-1)),
            rotated=rotated,
            confidence=min((float(value) for value in keypoint_scores), default=0.0),
            image_size=image_size,
        )

    @staticmethod
    def _grid_step(geometry: BoardGeometry) -> float:
        samples = []
        for x in (0, 4, 7):
            for rank in (0, 4, 9):
                if x < 8:
                    samples.append(math.dist(
                        geometry.point_for_square((x, rank)),
                        geometry.point_for_square((x + 1, rank)),
                    ))
                if rank < 9:
                    samples.append(math.dist(
                        geometry.point_for_square((x, rank)),
                        geometry.point_for_square((x, rank + 1)),
                    ))
        return float(np.median(samples)) if samples else 0.0

    @classmethod
    def _refine_geometry_from_pieces(
        cls,
        image: Image.Image,
        geometry: BoardGeometry,
        board: dict[tuple[int, int], str],
    ) -> BoardGeometry:
        """Fit the grid through detected piece discs and retain exact disc centres.

        The pose network supplies a safe first projection.  Hough circles are
        only accepted close to known occupied intersections, and RANSAC must
        agree before the grid transform itself is adjusted.  Sparse or
        non-circular themes simply keep the pose projection.
        """
        if not board or cv2 is None or np is None:
            return geometry
        step = cls._grid_step(geometry)
        if not math.isfinite(step) or step < 16:
            return geometry
        all_points = [
            geometry.point_for_square((x, rank))
            for x in range(9)
            for rank in range(10)
        ]
        margin = step * 0.65
        width, height = image.size
        left = max(0, math.floor(min(point[0] for point in all_points) - margin))
        top = max(0, math.floor(min(point[1] for point in all_points) - margin))
        right = min(width, math.ceil(max(point[0] for point in all_points) + margin))
        bottom = min(height, math.ceil(max(point[1] for point in all_points) + margin))
        if left >= right or top >= bottom:
            return geometry
        gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        crop = gray[top:bottom, left:right]
        scale = min(1.0, 900.0 / max(crop.shape[:2]))
        sample = (
            cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if scale < 1.0
            else crop
        )
        sample_step = step * scale
        circles = cv2.HoughCircles(
            cv2.GaussianBlur(sample, (5, 5), 1.2),
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(8.0, sample_step * 0.55),
            param1=90,
            param2=max(12.0, sample_step * 0.15),
            minRadius=max(5, round(sample_step * 0.24)),
            maxRadius=max(7, round(sample_step * 0.52)),
        )
        if circles is None:
            return geometry
        candidates = [
            (left + float(x) / scale, top + float(y) / scale)
            for x, y, _radius in circles[0]
        ]
        matches: dict[tuple[int, int], tuple[float, float]] = {}
        for square in board:
            projected = geometry.point_for_square(square)
            nearby = [
                center for center in candidates
                if math.dist(projected, center) <= step * 0.30
            ]
            if nearby:
                matches[square] = min(nearby, key=lambda center: math.dist(projected, center))
        piece_centers = tuple(
            (square[0], square[1], center[0], center[1])
            for square, center in sorted(matches.items())
        )
        # Even without enough evidence to alter the complete grid, an
        # individual source piece can still be clicked at its observed centre.
        observed = BoardGeometry(
            geometry.inverse_matrix,
            geometry.rotated,
            geometry.confidence,
            geometry.image_size,
            piece_centers,
        )
        if len(matches) < 6:
            return observed
        if len({square[0] for square in matches}) < 3 or len({square[1] for square in matches}) < 3:
            return observed
        source = np.float32([
            cls._normalized_square(square, geometry.rotated)
            for square in matches
        ])
        destination = np.float32([matches[square] for square in matches])
        matrix, mask = cv2.findHomography(
            source,
            destination,
            cv2.RANSAC,
            max(2.0, step * 0.08),
        )
        if matrix is None or mask is None or int(mask.sum()) < max(5, round(len(matches) * 0.65)):
            return observed
        refined = BoardGeometry(
            tuple(float(value) for value in matrix.reshape(-1)),
            geometry.rotated,
            geometry.confidence,
            geometry.image_size,
            piece_centers,
        )
        corners = ((0, 0), (8, 0), (0, 9), (8, 9))
        if any(
            math.dist(geometry.point_for_square(square), refined.point_for_square(square)) > step * 0.35
            for square in corners
        ):
            return observed
        return refined

    def refresh_geometry(
        self,
        image: Image.Image,
        geometry_hint: BoardGeometry,
        board: dict[tuple[int, int], str],
        *,
        minimum_geometry_confidence: float = 0.10,
        cancelled=None,
    ) -> BoardGeometry:
        """Refresh clickable intersections using pose only, without 90-cell classification."""
        if geometry_hint is None or geometry_hint.image_size != image.size:
            raise RuntimeError("棋盘尺寸变化，需要重新完整识别")

        def check_cancelled():
            if cancelled is not None and cancelled():
                raise InterruptedError("用户已停止自动接管")

        check_cancelled()
        started = time.perf_counter()
        image_rgb = np.asarray(image.convert("RGB"))
        image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        height, width = image_bgr.shape[:2]
        bboxes = []
        if self.last_search_bbox is not None and self.last_image_size == image.size:
            bboxes.append(list(self.last_search_bbox))
        hinted = self._hint_bbox(geometry_hint, image.size)
        if hinted is not None and hinted not in bboxes:
            bboxes.append(hinted)
        last_error = "没有可用的跟踪区域"
        for bbox in bboxes:
            try:
                check_cancelled()
                keypoints, scores = self.pose.pred(image=image_bgr, bbox=bbox)
                check_cancelled()
                self._validate_keypoints(image_rgb, keypoints, bbox)
                confidence = min((float(value) for value in scores), default=0.0)
                if confidence < minimum_geometry_confidence:
                    raise RuntimeError(f"棋盘定位置信度过低（{confidence:.2f}）")
                fresh = self._geometry_from_keypoints(
                    keypoints,
                    scores,
                    rotated=geometry_hint.rotated,
                    image_size=image.size,
                )
                old_step = self._grid_step(geometry_hint)
                corners = ((0, 0), (8, 0), (0, 9), (8, 9))
                if any(
                    math.dist(geometry_hint.point_for_square(square), fresh.point_for_square(square))
                    > max(12.0, old_step * 0.55)
                    for square in corners
                ):
                    raise RuntimeError("棋盘位置变化过大，需要重新完整确认")
                circle_started = time.perf_counter()
                fresh = self._refine_geometry_from_pieces(image, fresh, board)
                self.last_timings = {
                    "fast_pose_ms": round((circle_started - started) * 1000, 1),
                    "circle_refine_ms": round((time.perf_counter() - circle_started) * 1000, 1),
                    "regions": 1,
                }
                self.last_geometry = fresh
                return fresh
            except InterruptedError:
                raise
            except Exception as exc:
                last_error = str(exc)
        raise RuntimeError(f"快速棋盘定位失败：{last_error}")

    def recognize(self, image: Image.Image, *, geometry_hint=None,
                  minimum_geometry_confidence: float = 0.0,
                  cancelled=None) -> tuple[Image.Image, list[Detection]]:
        def check_cancelled():
            if cancelled is not None and cancelled():
                raise InterruptedError("用户已停止自动接管")

        check_cancelled()
        self.last_timings = {"pose_ms": 0.0, "classifier_ms": 0.0, "regions": 0}

        def predict(model, stage, *args, **kwargs):
            started = time.perf_counter()
            try:
                return model.pred(*args, **kwargs)
            finally:
                self.last_timings[stage + "_ms"] += round((time.perf_counter() - started) * 1000, 1)

        image_rgb = np.asarray(image.convert("RGB"))
        image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        frame_height, frame_width = image_bgr.shape[:2]
        best = None
        errors: list[str] = []
        primary_bboxes = self._candidate_bboxes(frame_height, frame_width)
        bboxes = list(primary_bboxes)
        hint_bbox = self._hint_bbox(geometry_hint, image.size)
        if hint_bbox is not None:
            bboxes.insert(0, hint_bbox)
            # Repeat the region that actually worked, not only a newly guessed
            # padded crop. Coordinates still come from a fresh pose inference.
            previous_bbox = getattr(self, "last_search_bbox", None)
            if previous_bbox is not None and getattr(self, "last_image_size", None) == image.size:
                bboxes.insert(0, list(previous_bbox))
        bboxes.extend(self._tiled_candidate_bboxes(frame_height, frame_width))
        bboxes = [list(item) for item in dict.fromkeys(tuple(bbox) for bbox in bboxes)]
        for bbox in bboxes:
            try:
                check_cancelled()
                self.last_timings["regions"] += 1
                keypoints, keypoint_scores = predict(self.pose, "pose",
                    image=image_bgr,
                    bbox=bbox,
                )
                check_cancelled()
                self._validate_keypoints(image_rgb, keypoints, bbox)
                # Do not select a high-classification candidate whose coordinates
                # automation will reject, when another region can locate it safely.
                if min(keypoint_scores, default=0.0) < minimum_geometry_confidence:
                    raise RuntimeError(f"棋盘定位置信度过低（{min(keypoint_scores):.2f}）")
                transformed, _, _ = extract_chessboard(image_rgb, keypoints)
                _, rows, confidences, layout = predict(self.classifier, "classifier",
                    transformed, is_rgb=True
                )
                rows, confidences, calibrated = self._calibrate_standard_start(rows, confidences)
                if calibrated:
                    layout = "\n".join("".join(row) for row in rows)
                check_cancelled()
                if minimum_geometry_confidence > 0.0:
                    unknown = sum(label == "x" or (label == "." and float(confidences[r][c]) < .45)
                                  for r, row in enumerate(rows) for c, label in enumerate(row))
                    if unknown:
                        # A temporary animation at the SAME freshly located
                        # board cannot be fixed by classifying this stale image
                        # four more ways. Try a NEW frame first. Periodic full
                        # recovery remains available for a persistently bad ROI.
                        if hint_bbox is not None and self._pose_matches_hint(keypoints, geometry_hint):
                            self.tracking_failures = getattr(self, "tracking_failures", 0) + 1
                            if self.tracking_failures < 3:
                                raise TransientBoardFrame(f"棋盘动画/遮挡，本帧有 {unknown} 个低置信度格子；等待新帧")
                        raise RuntimeError(f"本帧有 {unknown} 个低置信度格子")
                score = self._candidate_score(rows, confidences, keypoint_scores)
                if best is None or score > best[0]:
                    best = (
                        score,
                        keypoints,
                        keypoint_scores,
                        transformed,
                        rows,
                        confidences,
                        layout,
                        tuple(bbox),
                    )
                # A legal board with both kings and uniformly high class
                # confidence does not need the slower fallback regions.
                if score >= 1.45 and self._candidate_is_plausible(rows):
                    break
            except (InterruptedError, TransientBoardFrame):
                raise
            except Exception as exc:
                errors.append(str(exc))
        if best is None:
            self.tracking_failures = 0
            detail = errors[0] if errors else "没有候选区域"
            raise RuntimeError(f"深度模型没有可靠定位到棋盘：{detail}")
        _, keypoints, keypoint_scores, transformed, rows, confidences, layout, chosen_bbox = best
        self.last_search_bbox, self.last_image_size = chosen_bbox, image.size
        self.tracking_failures = 0
        self.last_layout = layout
        self.last_keypoint_scores = [float(value) for value in keypoint_scores]

        transformed_pil = Image.fromarray(transformed.astype("uint8"))
        rotate = self._needs_rotation(rows)
        self.last_geometry = self._geometry_from_keypoints(
            keypoints,
            keypoint_scores,
            rotated=rotate,
            image_size=image.size,
        )
        grid = transformed_pil.crop((50, 50, 400, 450))
        if rotate:
            grid = grid.transpose(Image.Transpose.ROTATE_180)

        detections: list[Detection] = []
        for row in range(10):
            for column in range(9):
                label = rows[row][column]
                confidence = float(confidences[row][column])
                uncertain_empty = label == "." and confidence < 0.45
                if label == "." and not uncertain_empty:
                    continue

                patch = self._cell_patch(transformed_pil, column, row)
                piece = label if label in "KABNRCPkabnrcp" else None
                if piece is not None:
                    side = "w" if piece.isupper() else "b"
                else:
                    side = "w" if _is_red(patch) else "b"
                square = (
                    (8 - column, row)
                    if rotate
                    else (column, 9 - row)
                )
                detections.append(
                    Detection(
                        square=square,
                        side=side,
                        piece=piece,
                        confidence=confidence,
                        margin=confidence,
                        patch=patch,
                        feature=_feature(patch, red=side == "w"),
                    )
                )
        board = {
            detection.square: detection.piece
            for detection in detections
            if detection.piece is not None
        }
        circle_started = time.perf_counter()
        self.last_geometry = self._refine_geometry_from_pieces(
            image,
            self.last_geometry,
            board,
        )
        self.last_timings["circle_refine_ms"] = round(
            (time.perf_counter() - circle_started) * 1000,
            1,
        )
        return grid, detections

    @staticmethod
    def _pose_matches_hint(keypoints, geometry):
        squares = ((8, 0), (0, 0), (8, 9), (0, 9)) if geometry.rotated else ((0, 9), (8, 9), (0, 0), (8, 0))
        previous = np.asarray([geometry.point_for_square(square) for square in squares])
        tolerance = max(2.0, float(np.linalg.norm(previous[1] - previous[0])) / 8 * .20)
        return bool(np.max(np.linalg.norm(keypoints - previous, axis=1)) <= tolerance)


class PieceRecognizer:
    """Pretrained ONNX recognition with the old template method as fallback."""

    def __init__(self):
        self.template = TemplatePieceRecognizer()
        self.neural: NeuralBoardRecognizer | None = None
        self.neural_load_attempted = False
        self.last_backend = ""
        self.last_error = ""
        self.last_geometry: BoardGeometry | None = None

    def _ensure_neural(self) -> NeuralBoardRecognizer:
        if self.neural is not None:
            return self.neural
        self.neural_load_attempted = True
        self.neural = NeuralBoardRecognizer(_runtime_base() / "vision_models")
        return self.neural

    def learn(self, piece: str, feature: list[float]) -> None:
        self.template.learn(piece, feature)

    def refresh_geometry(
        self,
        image: Image.Image,
        geometry_hint: BoardGeometry,
        board: dict[tuple[int, int], str],
        *,
        minimum_geometry_confidence: float = 0.10,
        cancelled=None,
    ) -> BoardGeometry:
        neural = self._ensure_neural()
        geometry = neural.refresh_geometry(
            image,
            geometry_hint,
            board,
            minimum_geometry_confidence=minimum_geometry_confidence,
            cancelled=cancelled,
        )
        self.last_backend = "onnx-fast-pose"
        self.last_error = ""
        self.last_geometry = geometry
        return geometry

    def recognize(self, image: Image.Image, *, geometry_hint=None,
                  minimum_geometry_confidence: float = 0.0,
                  cancelled=None) -> tuple[Image.Image, list[Detection]]:
        try:
            result = self._ensure_neural().recognize(
                image, geometry_hint=geometry_hint,
                minimum_geometry_confidence=minimum_geometry_confidence,
                cancelled=cancelled,
            )
            self.last_backend = "onnx"
            self.last_error = ""
            self.last_geometry = self.neural.last_geometry
            return result
        except InterruptedError:
            raise
        except Exception as exc:
            self.last_backend = "template-fallback"
            self.last_error = str(exc)
            self.last_geometry = None
            if cancelled is not None:
                # Automation requires ONNX. Slow template fallback can never pass
                # its safety gate and must not delay the emergency stop.
                raise RuntimeError(self.last_error) from exc
            return self.template.recognize(image)
