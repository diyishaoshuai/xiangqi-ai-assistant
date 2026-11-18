"""Display-only WDL history. It never participates in move selection."""
from __future__ import annotations

import tkinter as tk


def red_probabilities(line, side):
    """Return red win/draw/black win percentages, or no measured WDL."""
    if side not in ("w", "b") or line.wdl is None:
        return None
    values = tuple(line.wdl)
    if len(values) != 3 or any(v < 0 for v in values) or sum(values) <= 0:
        return None
    win, draw, loss = (100.0 * v / sum(values) for v in values)
    return (win, draw, loss) if side == "w" else (loss, draw, win)


class WinrateHistory:
    def __init__(self):
        self.root = None
        self.moves = ()
        self.points = {}

    def sync(self, root, moves):
        moves = tuple(moves)
        if root != self.root:
            self.points.clear()
        else:
            common = 0
            for a, b in zip(self.moves, moves):
                if a != b:
                    break
                common += 1
            self.points = {ply: value for ply, value in self.points.items() if ply <= common}
        self.root, self.moves = root, moves

    def record(self, root, moves, line, side):
        self.sync(root, moves)
        value = red_probabilities(line, side)
        if value is not None:
            self.points[len(self.moves)] = value


class WinrateChart(tk.Canvas):
    def __init__(self, parent, *, scale=1.0):
        super().__init__(parent, height=round(128 * scale), width=1,
                         bg="#F5F6EF", highlightthickness=0)
        self.history = WinrateHistory()
        self.scale = scale
        self.bind("<Configure>", lambda _event: self.redraw())

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        left, right, top, bottom = 34, max(35, w - 14), 23, max(24, h - 25)
        font = ("Microsoft YaHei UI", -max(9, round(10 * self.scale)))
        for percentage in (0, 50, 100):
            y = bottom - (bottom - top) * percentage / 100
            self.create_line(left, y, right, y, fill="#DDE3DA", dash=(3, 3))
            self.create_text(left - 4, y, text=str(percentage), anchor="e", fill="#7C877F", font=font)
        points = sorted(self.history.points.items())
        if not points:
            self.create_text((left + right) / 2, (top + bottom) / 2,
                             text="等待已完成分析的胜率数据", fill="#7C877F", font=font)
            return
        last_ply, values = points[-1]
        self.create_text(left, 10, anchor="w", font=font, fill="#263E35",
                         text=f"红胜 {values[0]:.1f}% · 和 {values[1]:.1f}% · 黑胜 {values[2]:.1f}%")
        end = max(1, len(self.history.moves))
        coords = [(left + (right - left) * ply / end,
                   bottom - (bottom - top) * value[0] / 100) for ply, value in points]
        if len(coords) > 1:
            self.create_line(*[v for pair in coords for v in pair], fill="#B34E42", width=2)
        for x, y in coords:
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#B34E42", outline="")
        self.create_text(left, h - 10, anchor="w", text="0", font=font, fill="#7C877F")
        self.create_text(right, h - 10, anchor="e", text=f"第 {last_ply} 半回合", font=font, fill="#7C877F")
