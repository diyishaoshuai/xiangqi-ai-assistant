"""Presentation regressions. These tests never send real mouse/keyboard input."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import XiangqiApp
from core import AnalysisLine, START_FEN, apply_move, parse_fen
from ui import BoardLayout, ScrollPage


class BoardLayoutTests(unittest.TestCase):
    def test_board_and_piece_rims_fit_small_and_large_canvases(self):
        for width, height in ((484, 380), (780, 580), (1200, 900), (340, 280)):
            with self.subTest(size=(width, height)):
                layout = BoardLayout.fit(width, height)
                self.assertGreater(layout.cell, 0)
                self.assertGreater(layout.x0 - layout.cell * .67, 0)
                self.assertGreater(layout.y0 - layout.cell * .67, 0)
                self.assertLess(layout.x0 + layout.cell * 8.67, width)
                self.assertLess(layout.y0 + layout.cell * 9.67, height)

    def test_all_ninety_intersections_round_trip_after_resize_and_flip(self):
        app = object.__new__(XiangqiApp)
        for size in ((484, 380), (780, 580), (1200, 900)):
            layout = BoardLayout.fit(*size)
            app.CELL, app.X0, app.Y0 = layout.cell, layout.x0, layout.y0
            for app.assisted_side in ("w", "b"):
                for x in range(9):
                    for y in range(10):
                        px, py = app._canvas_point((x, y))
                        self.assertEqual(app._nearest_square(SimpleNamespace(x=px, y=py)), (x, y))

    def test_outside_board_and_between_intersections_do_not_pick_a_piece(self):
        app = object.__new__(XiangqiApp)
        layout = BoardLayout.fit(780, 580)
        app.CELL, app.X0, app.Y0, app.assisted_side = layout.cell, layout.x0, layout.y0, "w"
        self.assertIsNone(app._nearest_square(SimpleNamespace(x=0, y=0)))
        self.assertIsNone(app._nearest_square(SimpleNamespace(x=app.X0 + .5 * app.CELL, y=app.Y0)))

    def test_larger_canvas_increases_piece_size_and_tiny_startup_is_safe(self):
        self.assertGreater(BoardLayout.fit(1200, 900).cell, BoardLayout.fit(484, 380).cell)
        self.assertEqual(BoardLayout.fit(0, 0).cell, 1)

    def test_sidebar_scrollbar_is_removed_when_content_fits_again(self):
        canvas, content, scrollbar = Mock(), Mock(), Mock()
        canvas.winfo_width.return_value = 400
        canvas.winfo_height.return_value = 600
        content.winfo_reqheight.return_value = 700
        scrollbar.winfo_manager.return_value = ""
        page = SimpleNamespace(canvas=canvas, content=content, scrollbar=scrollbar, window=1)
        ScrollPage._fit(page)
        scrollbar.pack.assert_called_once()
        content.winfo_reqheight.return_value = 550
        scrollbar.winfo_manager.return_value = "pack"
        ScrollPage._fit(page)
        scrollbar.pack_forget.assert_called_once()
        canvas.yview_moveto.assert_called_with(0)
        canvas.itemconfigure.assert_called_with(1, width=400, height=600)


@unittest.skipUnless(os.name == "nt", "Native Windows/Tk interface")
class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self.data = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": self.data.name})
        self.env.start()
        self.key = patch("app.f1_pressed", return_value=False)
        self.key.start()
        self.engine = patch("app.PikafishEngine", return_value=Mock())
        self.engine.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = XiangqiApp(self.root)
        self.app.auto_analysis_var.set(False)
        self.app._invalidate_analysis()
        self.app.load_preset(START_FEN)
        self.app.ui.update_analysis()

    def tearDown(self):
        self.app._on_close()
        self.engine.stop()
        self.key.stop()
        self.env.stop()
        self.data.cleanup()

    def show_line(self):
        app = self.app
        app.analysis_board, app.analysis_side = dict(app.board), app.side
        line = AnalysisLine(1, 20, "cp", 25, ["b2e2", "b7e7"], (20, 980, 0))
        app._show_analysis([line], line.best_move)
        return line

    def test_workspace_exposes_three_pages_and_all_fourteen_pieces(self):
        self.assertEqual(len(self.app.ui.tabs.tabs()), 3)
        self.assertEqual(set(self.app.ui.piece_buttons), set("KABNRCPkabnrcp"))
        self.assertIn("F1", self.app.mouse_auto_button.cget("text"))
        self.assertTrue(self.app.follow_best_var.get())
        self.assertTrue(self.app.always_on_top_var.get())

    def test_time_display_converts_seconds_to_existing_engine_milliseconds(self):
        ui = self.app.ui
        ui.time_label.set("0.5 秒")
        ui.time_combo.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.app.time_var.get(), 500)
        self.app.time_var.set(30000)
        self.assertEqual(ui.time_label.get(), "30 秒")

    def test_resizing_preserves_fen_and_draws_every_piece_in_both_orientations(self):
        app = self.app
        before = dict(app.board)
        for side in ("w", "b"):
            app.player_side_var.set(side)
            app._player_side_changed()
            for width, height in ((484, 380), (780, 580)):
                app.ui._resize_board(SimpleNamespace(width=width, height=height))
                app.draw_board()
                self.assertEqual(app.board, before)
                self.assertEqual(len(app.canvas.find_withtag("piece")), 32)
                self.assertEqual(len(app.canvas.find_withtag("piece-label")), 32)

    def test_piece_palette_edit_and_undo_redo_keep_original_commands(self):
        app = self.app
        app.ui.piece_buttons["R"].invoke()
        self.assertEqual(app.tool.get(), "R")
        x, y = app._canvas_point((4, 4))
        app._board_click(SimpleNamespace(x=x, y=y))
        self.assertEqual(app.board[(4, 4)], "R")
        self.assertIn("添加红", app.ui.tool_hint.get())
        app.ui.undo_button.invoke()
        self.assertNotIn((4, 4), app.board)
        app.ui.redo_button.invoke()
        self.assertEqual(app.board[(4, 4)], "R")

    def test_recommendation_card_and_apply_button_use_the_selected_engine_move(self):
        line = self.show_line()
        before = dict(self.app.board)
        self.assertIn("炮", self.app.ui.move_var.get())
        self.assertFalse(self.app.ui.apply_button.instate(["disabled"]))
        self.app.ui.apply_button.invoke()
        self.assertEqual(self.app.board, apply_move(before, line.best_move))
        self.assertTrue(self.app.ui.apply_button.instate(["disabled"]))

    def test_stale_results_do_not_draw_old_arrows_or_enable_apply(self):
        self.show_line()
        self.app.clear_board()
        self.app._select_analysis_index(0)
        self.assertIsNone(self.app.best_arrow)
        self.assertTrue(self.app.ui.apply_button.instate(["disabled"]))
        self.assertEqual(self.app.ui.move_var.get(), "等待分析")

    def test_follow_accepts_a_one_move_pv_without_a_predicted_reply(self):
        app = self.app
        before = dict(app.board)
        app.analysis_board, app.analysis_side = dict(app.board), app.side
        line = AnalysisLine(1, 20, "cp", 25, ["b2e2"], (20, 980, 0))
        app._show_analysis([line], "b2e2")
        self.assertTrue(app._auto_follow_recommendation((1, 7)))
        app._move_selected_to((4, 7))
        self.assertEqual(app.board, apply_move(apply_move(before, "b2e2"), "b7e7"))
        self.assertEqual(app.engine_move_history, ["b2e2", "b7e7"])
        self.assertEqual(app.side, "w")
        self.assertFalse(app.follow_move_pending)

    def test_busy_and_idle_states_update_controls_and_remove_progress_bar(self):
        self.app.analysis_running = True
        self.app.ui.update_analysis()
        self.assertFalse(self.app.ui.stop_button.instate(["disabled"]))
        self.assertEqual(self.app.ui.state_var.get(), "引擎思考中")
        self.assertEqual(self.app.ui.progress.winfo_manager(), "pack")
        self.app.analysis_running = False
        self.app.ui.update_analysis()
        self.assertTrue(self.app.ui.stop_button.instate(["disabled"]))
        self.assertEqual(self.app.ui.progress.winfo_manager(), "")

    def test_fen_variable_remains_bidirectional_and_loads_original_format(self):
        self.app.ui.fen_entry.delete(0, "end")
        self.app.ui.fen_entry.insert(0, START_FEN.replace(" w ", " b "))
        self.app.load_fen()
        self.assertEqual(self.app.side, "b")
        self.assertIn("黑方", self.app.ui.turn_var.get())
        self.assertEqual(self.app.board, parse_fen(START_FEN)[0])

    def test_takeover_button_still_calls_original_toggle(self):
        with patch.object(self.app, "_start_mouse_autoplay") as start:
            self.app.mouse_auto_button.invoke()
        start.assert_called_once()

    def test_f1_keeps_global_edge_detection_after_ui_rebuild(self):
        app = self.app
        with patch("app.f1_pressed", side_effect=[True, True, True]), patch.object(app, "_toggle_mouse_autoplay") as toggle:
            for _ in range(3):
                self.root.after_cancel(app._hotkey_poll_after_id)
                app._poll_f1_hotkey()
        toggle.assert_called_once()

    def test_background_cache_refreshes_with_resize_flip_and_visibility(self):
        from PIL import Image

        app = self.app
        app.background_image = Image.new("RGB", (400, 450), "white")
        app.draw_board()
        old_image = app.background_tk
        app.draw_board()
        self.assertIs(app.background_tk, old_image)
        app.ui._resize_board(SimpleNamespace(width=484, height=380))
        app.draw_board()
        self.assertIsNot(app.background_tk, old_image)
        app.ui.background_var.set(False)
        app.draw_board()
        self.assertNotIn("image", [app.canvas.type(item) for item in app.canvas.find_all()])


if __name__ == "__main__":
    unittest.main()
