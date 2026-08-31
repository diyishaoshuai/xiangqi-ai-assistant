"""Reuse a decoded board only when a NEW screenshot has identical RGB pixels.

No approximate hashes or downsampling. One changed pixel inside the board and
outer piece rims invalidates the cache. The screenshot bytes stay in memory.
"""
from __future__ import annotations

import math


class UnchangedBoardCache:
    def __init__(self):
        self.clear()

    def clear(self):
        self.image_size = self.bounds = self.pixels = None
        self.board = self.grid = self.geometry = None

    def remember(self, image, board, grid, geometry):
        corners = [geometry.point_for_square(square) for square in ((0, 0), (8, 0), (0, 9), (8, 9))]
        step = max(math.dist(corners[0], corners[1]) / 8,
                   math.dist(corners[2], corners[3]) / 8,
                   math.dist(corners[0], corners[2]) / 9,
                   math.dist(corners[1], corners[3]) / 9)
        margin = math.ceil(step * .75) + 2
        width, height = image.size
        bounds = (max(0, math.floor(min(p[0] for p in corners)) - margin),
                  max(0, math.floor(min(p[1] for p in corners)) - margin),
                  min(width, math.ceil(max(p[0] for p in corners)) + margin),
                  min(height, math.ceil(max(p[1] for p in corners)) + margin))
        if geometry.image_size != image.size or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            self.clear()
            return
        self.image_size = image.size
        self.bounds = bounds
        self.pixels = image.crop(bounds).convert("RGB").tobytes()
        self.board, self.grid, self.geometry = dict(board), grid, geometry

    def match(self, image, *, expected_board=None):
        if self.pixels is None or image.size != self.image_size:
            return None
        if expected_board is not None and self.board != expected_board:
            return None
        if image.crop(self.bounds).convert("RGB").tobytes() != self.pixels:
            return None
        return dict(self.board), self.grid, self.geometry
