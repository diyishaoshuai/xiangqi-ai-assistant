"""Adaptive engine verification for automatic mouse play."""
from __future__ import annotations

from dataclasses import dataclass

from automation import legal_successors, move_is_legal
from core import AnalysisLine, apply_move, parse_move, piece_side
from engine import EngineSearchResult


PIECE_VALUES = {
    "K": 10_000,
    "R": 900,
    "C": 450,
    "N": 400,
    "B": 200,
    "A": 200,
    "P": 100,
}
MAJOR_PIECES = frozenset({"R", "C", "N"})
MEDIUM_VERIFY_MS = 3000
HIGH_VERIFY_MS = 5000


@dataclass(frozen=True, slots=True)
class MoveRiskReport:
    verification_ms: int
    severity: str
    reasons: tuple[str, ...]

    @property
    def needs_verification(self) -> bool:
        return self.verification_ms > 0

    @property
    def status_text(self) -> str:
        if not self.reasons:
            return "搜索结果稳定"
        return "；".join(self.reasons)


def _opponent(side: str) -> str:
    return "b" if side == "w" else "w"


def _dangerous_major_captures(
    board: dict[tuple[int, int], str],
    owner: str,
) -> dict[tuple[int, int], tuple[str, ...]]:
    captures: dict[tuple[int, int], list[str]] = {}
    for move, after_capture in legal_successors(board, _opponent(owner)):
        start, end = parse_move(move)
        victim = board.get(end)
        if victim is None or piece_side(victim) != owner or victim.upper() not in MAJOR_PIECES:
            continue
        attacker = board.get(start)
        recapturable = any(
            parse_move(reply)[1] == end
            and after_capture.get(end) is not None
            and piece_side(after_capture[end]) != owner
            for reply, _after_reply in legal_successors(after_capture, owner)
        )
        attacker_value = PIECE_VALUES.get(attacker.upper(), 0) if attacker else 0
        victim_value = PIECE_VALUES.get(victim.upper(), 0)
        # A defended capture by an equal/more valuable attacker is an exchange,
        # not a hanging piece.  Lower-value attackers still win material even
        # when recaptured and therefore remain a verification trigger.
        if recapturable and attacker_value >= victim_value:
            continue
        captures.setdefault(end, []).append(move)
    return {square: tuple(moves) for square, moves in captures.items()}


def _pv_reply_captures_major(
    board: dict[tuple[int, int], str],
    side: str,
    line: AnalysisLine | None,
) -> str | None:
    if line is None or len(line.pv) < 2:
        return None
    try:
        after = apply_move(board, line.pv[0])
        _start, end = parse_move(line.pv[1])
    except ValueError:
        return None
    victim = after.get(end)
    if victim is None or piece_side(victim) != side or victim.upper() not in MAJOR_PIECES:
        return None
    return victim.upper()


def score_value(line: AnalysisLine | None) -> int | None:
    if line is None:
        return None
    if line.score_type == "mate":
        return 1_000_000 if line.score > 0 else -1_000_000
    return line.score


def result_is_legal(
    board: dict[tuple[int, int], str],
    side: str,
    result: EngineSearchResult,
) -> bool:
    return bool(result.bestmove and result.bestmove != "(none)"
                and move_is_legal(board, result.bestmove, side))


def restricted_result_is_acceptable(
    unrestricted: EngineSearchResult,
    restricted: EngineSearchResult,
    *,
    tolerance_cp: int = 80,
) -> bool:
    """Only accept a rule-avoidance move when it does not throw the game away."""
    base = unrestricted.primary
    alternative = restricted.primary
    base_score = score_value(base)
    alternative_score = score_value(alternative)
    if base_score is None or alternative_score is None:
        return False
    if alternative is not None and alternative.score_type == "mate" and alternative.score > 0:
        return True
    base_is_clear_win = bool(
        base is not None
        and (
            (base.score_type == "mate" and base.score > 0)
            or (base.score_type == "cp" and base.score >= 200)
            or (base.wdl is not None and base.wdl[0] >= 500 and base.wdl[0] > base.wdl[2])
        )
    )
    if base_is_clear_win:
        return bool(
            alternative is not None
            and (
                (alternative.score_type == "mate" and alternative.score > 0)
                or (alternative.score_type == "cp" and alternative.score >= 100)
                or (
                    alternative.wdl is not None
                    and alternative.wdl[0] > 0
                    and alternative.wdl[0] >= alternative.wdl[2]
                )
            )
        )
    return alternative_score >= base_score - tolerance_cp


class MoveDecisionPolicy:
    """Classify a proposed engine move; static chess rules only request more search."""

    def assess(
        self,
        board: dict[tuple[int, int], str],
        side: str,
        result: EngineSearchResult,
    ) -> MoveRiskReport:
        medium: list[str] = []
        high: list[str] = []
        line = result.primary
        move = result.bestmove

        if not result.trusted:
            medium.append("引擎末轮结果不完整")
        if result.completed_depth < 16:
            medium.append(f"搜索深度仅 {result.completed_depth}")
        if result.root_stability < 3:
            medium.append("最近根着尚未稳定")

        if not move or move == "(none)" or not move_is_legal(board, move, side):
            high.append("首选着与当前合法局面不一致")
        else:
            start, end = parse_move(move)
            moving_piece = board.get(start)
            captured_piece = board.get(end)
            after = apply_move(board, move)
            capturable_after = _dangerous_major_captures(after, side)
            if capturable_after:
                labels = sorted({after[square].upper() for square in capturable_after})
                high.append("走后车炮马可被立即吃掉：" + "/".join(labels))

            capturable_before = _dangerous_major_captures(board, side)
            unresolved = set(capturable_before) & set(capturable_after)
            if unresolved and start not in unresolved:
                high.append("没有处理已经受攻击的大子")

            if moving_piece is not None and captured_piece is not None:
                mover_value = PIECE_VALUES.get(moving_piece.upper(), 0)
                captured_value = PIECE_VALUES.get(captured_piece.upper(), 0)
                if mover_value > captured_value and end in capturable_after:
                    high.append("高价值棋子吃低价值棋子后可能被回吃")

        pv_victim = _pv_reply_captures_major(board, side, line)
        if pv_victim is not None:
            medium.append(f"主变化显示对手下一步吃{pv_victim}")

        if line is not None:
            if line.score_type == "mate" and line.score < 0:
                high.append("引擎显示己方存在被杀序列")
            elif line.score_type == "cp" and line.score < -250:
                high.append(f"局面评估为 {line.score / 100:.2f}")
            if line.wdl is not None and line.wdl[2] == 1000:
                high.append("胜和负权重为全负")

        reasons = tuple(dict.fromkeys([*high, *medium]))
        if high:
            return MoveRiskReport(HIGH_VERIFY_MS, "high", reasons)
        if medium:
            return MoveRiskReport(MEDIUM_VERIFY_MS, "medium", reasons)
        return MoveRiskReport(0, "safe", ())
