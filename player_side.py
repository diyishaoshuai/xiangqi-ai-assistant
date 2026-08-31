"""Infer the local player's colour from source-image geometry, never UI orientation."""
from __future__ import annotations

import math


def bottom_player_side(board, geometry, *, king_confidences=None):
    """Return w/b only when both canonical kings map to opposite source halves.

    Recognition normalizes its board to red-at-bottom. Looking at that board's
    ranks, or at the assistant's own canvas, would therefore always choose red.
    Project the kings back into the *original screenshot* instead. Missing or
    uncertain evidence deliberately returns None rather than guessing a side.
    This says nothing about whose turn it is.
    """
    if geometry is None or not math.isfinite(geometry.confidence) or geometry.confidence < .10:
        return None
    kings = {piece: [square for square, value in board.items() if value == piece]
             for piece in ("K", "k")}
    if any(len(squares) != 1 for squares in kings.values()):
        return None
    for piece, ranks in (("K", range(3)), ("k", range(7, 10))):
        x, y = kings[piece][0]
        if x not in range(3, 6) or y not in ranks:
            return None
        if king_confidences is not None:
            confidence = king_confidences.get(piece, 0)
            if not math.isfinite(confidence) or confidence < .75:
                return None
    try:
        corners = [geometry.point_for_square(sq) for sq in ((0, 0), (8, 0), (0, 9), (8, 9))]
        red = geometry.point_for_square(kings["K"][0])
        black = geometry.point_for_square(kings["k"][0])
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    width, height = geometry.image_size
    if any(not (math.isfinite(x) and math.isfinite(y) and 0 <= x < width and 0 <= y < height)
           for x, y in [*corners, red, black]):
        return None
    top, bottom = min(y for _, y in corners), max(y for _, y in corners)
    if bottom - top < 40:
        return None
    middle, gap = (top + bottom) / 2, (bottom - top) * .18
    if red[1] > middle + gap and black[1] < middle - gap:
        return "w"
    if black[1] > middle + gap and red[1] < middle - gap:
        return "b"
    return None
