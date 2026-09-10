from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


FILES = "abcdefghi"

PIECE_NAMES = {
    "K": "帅",
    "A": "仕",
    "B": "相",
    "N": "马",
    "R": "车",
    "C": "炮",
    "P": "兵",
    "k": "将",
    "a": "士",
    "b": "象",
    "n": "马",
    "r": "车",
    "c": "炮",
    "p": "卒",
}

START_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"
PUZZLE_FEN = "4k4/9/9/9/4C1b2/9/9/3A1A3/4K4/3C5 w - - 0 1"


class FenError(ValueError):
    pass


def piece_side(piece: str) -> str:
    return "w" if piece.isupper() else "b"


def parse_fen(fen: str) -> tuple[dict[tuple[int, int], str], str]:
    fields = fen.strip().split()
    if not fields:
        raise FenError("FEN 为空")
    rows = fields[0].split("/")
    if len(rows) != 10:
        raise FenError("FEN 必须包含 10 行")
    board: dict[tuple[int, int], str] = {}
    allowed = set(PIECE_NAMES)
    for row_index, row in enumerate(rows):
        x = 0
        for char in row:
            if char.isdigit():
                x += int(char)
            elif char in allowed:
                if x >= 9:
                    raise FenError(f"第 {row_index + 1} 行超过 9 路")
                y = 9 - row_index
                board[(x, y)] = char
                x += 1
            else:
                raise FenError(f"未知棋子字符：{char}")
        if x != 9:
            raise FenError(f"第 {row_index + 1} 行不是 9 路")
    side = fields[1] if len(fields) > 1 else "w"
    if side not in {"w", "b"}:
        raise FenError("走子方必须是 w 或 b")
    return board, side


def make_fen(board: dict[tuple[int, int], str], side: str) -> str:
    rows: list[str] = []
    for y in range(9, -1, -1):
        row = ""
        empty = 0
        for x in range(9):
            piece = board.get((x, y))
            if piece:
                if empty:
                    row += str(empty)
                    empty = 0
                row += piece
            else:
                empty += 1
        if empty:
            row += str(empty)
        rows.append(row)
    return "/".join(rows) + f" {side} - - 0 1"


def square_name(square: tuple[int, int]) -> str:
    x, y = square
    return f"{FILES[x]}{y}"


def parse_move(move: str) -> tuple[tuple[int, int], tuple[int, int]]:
    move = move.strip().lower()
    if len(move) != 4 or move[0] not in FILES or move[2] not in FILES:
        raise ValueError(f"无效着法：{move}")
    if not move[1].isdigit() or not move[3].isdigit():
        raise ValueError(f"无效着法：{move}")
    start = FILES.index(move[0]), int(move[1])
    end = FILES.index(move[2]), int(move[3])
    if not (0 <= start[1] <= 9 and 0 <= end[1] <= 9):
        raise ValueError(f"无效着法：{move}")
    return start, end


def apply_move(board: dict[tuple[int, int], str], move: str) -> dict[tuple[int, int], str]:
    start, end = parse_move(move)
    if start not in board:
        raise ValueError(f"{square_name(start)} 没有棋子")
    updated = dict(board)
    updated[end] = updated.pop(start)
    return updated


def _pieces_between(
    board: dict[tuple[int, int], str],
    start: tuple[int, int],
    end: tuple[int, int],
) -> int | None:
    sx, sy = start
    tx, ty = end
    if sx != tx and sy != ty:
        return None
    step_x = 0 if sx == tx else (1 if tx > sx else -1)
    step_y = 0 if sy == ty else (1 if ty > sy else -1)
    x, y = sx + step_x, sy + step_y
    count = 0
    while (x, y) != (tx, ty):
        if (x, y) in board:
            count += 1
        x += step_x
        y += step_y
    return count


def piece_attacks_square(
    board: dict[tuple[int, int], str],
    start: tuple[int, int],
    target: tuple[int, int],
) -> bool:
    piece = board.get(start)
    if not piece or start == target:
        return False
    sx, sy = start
    tx, ty = target
    dx, dy = tx - sx, ty - sy
    kind = piece.lower()
    side = piece_side(piece)

    if kind == "r":
        return _pieces_between(board, start, target) == 0
    if kind == "c":
        return _pieces_between(board, start, target) == 1
    if kind == "n":
        if (abs(dx), abs(dy)) == (2, 1):
            leg = sx + (1 if dx > 0 else -1), sy
        elif (abs(dx), abs(dy)) == (1, 2):
            leg = sx, sy + (1 if dy > 0 else -1)
        else:
            return False
        return leg not in board
    if kind == "b":
        if abs(dx) != 2 or abs(dy) != 2:
            return False
        if side == "w" and ty > 4:
            return False
        if side == "b" and ty < 5:
            return False
        return (sx + dx // 2, sy + dy // 2) not in board
    if kind == "a":
        palace_y = range(0, 3) if side == "w" else range(7, 10)
        return abs(dx) == 1 and abs(dy) == 1 and 3 <= tx <= 5 and ty in palace_y
    if kind == "k":
        if sx == tx and _pieces_between(board, start, target) == 0:
            return True  # Flying-general capture.
        palace_y = range(0, 3) if side == "w" else range(7, 10)
        return abs(dx) + abs(dy) == 1 and 3 <= tx <= 5 and ty in palace_y
    if kind == "p":
        forward = 1 if side == "w" else -1
        if dx == 0 and dy == forward:
            return True
        crossed_river = sy >= 5 if side == "w" else sy <= 4
        return crossed_river and abs(dx) == 1 and dy == 0
    return False


def move_gives_check(
    board: dict[tuple[int, int], str], side: str, move: str
) -> bool:
    """Return whether ``move`` leaves the enemy king under attack."""
    updated = apply_move(board, move)
    enemy_king = "k" if side == "w" else "K"
    target = next(
        (square for square, piece in updated.items() if piece == enemy_king),
        None,
    )
    if target is None:
        return True  # The move captured the king directly.
    return any(
        piece_side(piece) == side
        and piece_attacks_square(updated, square, target)
        for square, piece in updated.items()
    )


def find_direct_king_capture(
    board: dict[tuple[int, int], str], side: str
) -> str | None:
    enemy_king = "k" if side == "w" else "K"
    target = next((square for square, piece in board.items() if piece == enemy_king), None)
    if target is None:
        return None
    priority = {"r": 0, "c": 1, "n": 2, "p": 3, "k": 4, "a": 5, "b": 6}
    attackers = sorted(
        (
            square
            for square, piece in board.items()
            if piece_side(piece) == side and piece_attacks_square(board, square, target)
        ),
        key=lambda square: (priority.get(board[square].lower(), 99), square),
    )
    if not attackers:
        return None
    return square_name(attackers[0]) + square_name(target)


def is_king_capture_move(board: dict[tuple[int, int], str], move: str) -> bool:
    try:
        _, end = parse_move(move)
    except ValueError:
        return False
    return board.get(end, "").lower() == "k"


def _file_number(side: str, x: int) -> int:
    return 9 - x if side == "w" else x + 1


def _number_text(side: str, number: int) -> str:
    if side == "w":
        return "一二三四五六七八九"[number - 1]
    return str(number)


def _prefix_for_same_file(
    board: dict[tuple[int, int], str], start: tuple[int, int], piece: str
) -> str | None:
    x, y = start
    matches = sorted(
        [sq for sq, p in board.items() if p == piece and sq[0] == x],
        key=lambda sq: sq[1],
        reverse=piece.isupper(),
    )
    if len(matches) < 2:
        return None
    labels = ["前", "后"] if len(matches) == 2 else ["前", "中", "后"]
    try:
        return labels[matches.index((x, y))]
    except (IndexError, ValueError):
        return None


def move_notation(board: dict[tuple[int, int], str], move: str) -> str:
    start, end = parse_move(move)
    piece = board.get(start)
    if not piece:
        return move
    side = piece_side(piece)
    fx, fy = start
    tx, ty = end
    name = PIECE_NAMES[piece]
    prefix = _prefix_for_same_file(board, start, piece)
    head = (
        f"{prefix}{name}"
        if prefix
        else f"{name}{_number_text(side, _file_number(side, fx))}"
    )

    if fy == ty:
        return f"{head}平{_number_text(side, _file_number(side, tx))}"

    is_forward = ty > fy if side == "w" else ty < fy
    action = "进" if is_forward else "退"
    if piece.lower() in {"a", "b", "n"}:
        amount = _file_number(side, tx)
    else:
        amount = abs(ty - fy)
    return f"{head}{action}{_number_text(side, amount)}"


def describe_move(board: dict[tuple[int, int], str], move: str) -> str:
    start, end = parse_move(move)
    suffix = "，吃将" if board.get(end, "").lower() == "k" else ""
    return (
        f"{move_notation(board, move)}  "
        f"({square_name(start)}→{square_name(end)}){suffix}"
    )


def format_pv(
    board: dict[tuple[int, int], str],
    side: str,
    moves: Iterable[str],
    max_plies: int = 18,
) -> str:
    working = dict(board)
    current = side
    chunks: list[str] = []
    pair: list[str] = []
    move_no = 1
    for index, move in enumerate(list(moves)[:max_plies]):
        try:
            notation = move_notation(working, move)
            working = apply_move(working, move)
        except ValueError:
            notation = move
        if current == "w":
            if pair:
                chunks.append(f"{move_no}. " + "  ".join(pair))
                move_no += 1
            pair = [notation]
        else:
            if not pair:
                pair = ["…"]
            pair.append(notation)
            chunks.append(f"{move_no}. " + "  ".join(pair))
            pair = []
            move_no += 1
        current = "b" if current == "w" else "w"
    if pair:
        chunks.append(f"{move_no}. " + "  ".join(pair))
    return "　".join(chunks)


def validate_position(board: dict[tuple[int, int], str]) -> list[str]:
    issues: list[str] = []
    if sum(piece == "K" for piece in board.values()) != 1:
        issues.append("红帅必须恰好 1 枚")
    if sum(piece == "k" for piece in board.values()) != 1:
        issues.append("黑将必须恰好 1 枚")
    for square, piece in board.items():
        x, y = square
        if piece == "K" and not (3 <= x <= 5 and 0 <= y <= 2):
            issues.append("红帅不在九宫内")
        if piece == "k" and not (3 <= x <= 5 and 7 <= y <= 9):
            issues.append("黑将不在九宫内")
    return issues


@dataclass(slots=True)
class AnalysisLine:
    multipv: int
    depth: int
    score_type: str
    score: int
    pv: list[str]
    wdl: tuple[int, int, int] | None = None

    @property
    def best_move(self) -> str:
        return self.pv[0] if self.pv else ""


def score_text(line: AnalysisLine, root_side: str) -> str:
    if line.score_type == "mate":
        winning = root_side if line.score > 0 else ("b" if root_side == "w" else "w")
        label = "红" if winning == "w" else "黑"
        return f"{label}方杀 {abs(line.score)}"
    if line.score == 0:
        return "和棋 0.00"
    winning = root_side if line.score >= 0 else ("b" if root_side == "w" else "w")
    label = "红" if winning == "w" else "黑"
    return f"{label}优 {abs(line.score) / 100:.2f}"


def no_win_reason(line: AnalysisLine, minimum_depth: int = 24) -> str | None:
    """Return a forecast, never a proven outcome or instruction to stop play."""
    if line.score_type == "mate" and line.score < 0:
        return f"当前搜索发现被将死风险（杀分 {abs(line.score)}），仍继续寻找抵抗"
    if line.depth < minimum_depth or line.wdl is None:
        return None
    wins, draws, losses = line.wdl
    if wins != 0:
        return None
    if draws == 1000 and losses == 0:
        return "引擎估计局面趋于和棋，不代表已证明理论和棋"
    if losses == 1000 and draws == 0:
        return "引擎估计明显劣势，负权重达到显示上限；不代表实际必输"
    return (
        "引擎获胜权重低至显示为 0，不等于没有获胜可能"
        f"（和 {draws / 10:.1f}% / 负 {losses / 10:.1f}%）"
    )
