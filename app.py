from __future__ import annotations

import os
import logging
import queue
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageEnhance, ImageGrab, ImageTk
except ImportError:  # Source mode can still run without screenshot support.
    Image = ImageEnhance = ImageGrab = ImageTk = None

if Image is not None:
    from recognition import Detection, PieceRecognizer
else:
    Detection = PieceRecognizer = None

from core import (
    FILES,
    PIECE_NAMES,
    PUZZLE_FEN,
    START_FEN,
    AnalysisLine,
    apply_move,
    describe_move,
    find_direct_king_capture,
    format_pv,
    is_king_capture_move,
    make_fen,
    move_gives_check,
    no_win_reason,
    parse_fen,
    parse_move,
    piece_side,
    score_text,
    square_name,
    validate_position,
)
from engine import EngineError, PikafishEngine
from app_paths import resource_base as app_base
from diagnostics import APP_VERSION, configure_logging, install_exception_logging
from automation import (
    AutomationState,
    ConfirmationKind,
    FatalAutomationError,
    HotkeyLatch,
    RecoverableAutomationError,
    StableBoardTracker,
    TransitionKind,
    UserInterferenceError,
    classify_board_transition,
    classify_click_confirmation,
    click_screen_move,
    enable_dpi_awareness,
    f1_pressed,
    foreground_window,
    position_is_safe,
    position_is_terminal,
    session_event_is_current,
    turn_for_new_game,
    user_input_is_idle,
    window_at_point,
)


APP_NAME = "本地象棋 AI 助手"
BG = "#151716"
PANEL = "#202421"
PANEL_2 = "#292e2a"
GRID = "#4c3927"
BOARD = "#d6ab6d"
RED = "#b92d2b"
BLACK = "#171717"
ACCENT = "#5dc98b"
TEXT = "#edf1ed"
MUTED = "#9ca69f"
NO_WIN_TEST_FEN = "5a3/4ak3/4b4/9/2b6/5C3/9/9/5K3/9 w - - 0 1"
DIRECT_CAPTURE_TEST_FEN = "9/4Rk3/9/9/9/P8/9/9/4A4/3AK4 w - - 0 1"
LOOP_TEST_FEN = "4k4/4a4/4b4/9/9/9/4n4/8P/R1NK5/2B6 w - - 0 1"


def _line_keeps_winning_chances(line: AnalysisLine) -> bool:
    if line.score_type == "mate":
        return line.score > 0
    if line.wdl is not None:
        wins, _draws, losses = line.wdl
        return wins > 0 and wins >= losses
    return line.score > 0


def _line_keeps_clear_win(line: AnalysisLine) -> bool:
    if line.score_type == "mate":
        return line.score > 0
    if line.wdl is not None:
        wins, _draws, losses = line.wdl
        return wins >= 500 and wins > losses
    return line.score >= 200


def _renumber_lines(lines: list[AnalysisLine]) -> list[AnalysisLine]:
    return [
        AnalysisLine(
            index,
            line.depth,
            line.score_type,
            line.score,
            list(line.pv),
            line.wdl,
        )
        for index, line in enumerate(lines, start=1)
    ]


def prefer_fresh_winning_line(
    lines: list[AnalysisLine], avoided_moves: set[str]
) -> tuple[list[AnalysisLine], str | None]:
    """Promote a still-winning root move when the old best move caused a loop."""
    if not lines or not avoided_moves or lines[0].best_move not in avoided_moves:
        return lines, None
    replacement = next(
        (
            line
            for line in lines[1:]
            if line.best_move not in avoided_moves and _line_keeps_winning_chances(line)
        ),
        None,
    )
    if replacement is None:
        return lines, None
    reordered = [replacement, *[line for line in lines if line is not replacement]]
    return _renumber_lines(reordered), replacement.best_move


def prefer_quiet_winning_line(
    lines: list[AnalysisLine],
    board: dict[tuple[int, int], str],
    side: str,
) -> tuple[list[AnalysisLine], str | None]:
    """After repeated checks, promote a non-checking move that keeps the win."""
    if not lines or not _checking_line_should_yield(lines[0], board, side):
        return lines, None
    replacement = next(
        (
            line
            for line in lines[1:]
            if _line_keeps_clear_win(line)
            and not is_king_capture_move(board, line.best_move)
            and not move_gives_check(board, side, line.best_move)
        ),
        None,
    )
    if replacement is None:
        return lines, None
    reordered = [replacement, *[line for line in lines if line is not replacement]]
    return _renumber_lines(reordered), replacement.best_move


def _checking_line_should_yield(
    line: AnalysisLine,
    board: dict[tuple[int, int], str],
    side: str,
) -> bool:
    try:
        checking = move_gives_check(board, side, line.best_move)
    except ValueError:
        return False
    if not checking or is_king_capture_move(board, line.best_move):
        return False
    # Do not interrupt a short, concrete mating sequence.
    return not (line.score_type == "mate" and 0 < line.score <= 5)


def find_engine() -> Path:
    base = app_base()
    configured = os.environ.get("PIKAFISH_DIR")
    candidates = [
        base / "engine" / "pikafish.exe",
        base / "engine" / "pikafish-sse41-popcnt.exe",
        *(
            [Path(configured) / "Windows" / "pikafish-sse41-popcnt.exe"]
            if configured
            else []
        ),
        base.parent / "Pikafish" / "Windows" / "pikafish-sse41-popcnt.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


class XiangqiApp:
    CELL = 64
    X0 = 62
    Y0 = 52
    CANVAS_W = 640
    CANVAS_H = 690

    def __init__(self, root: tk.Tk):
        self.log_path = configure_logging()
        install_exception_logging()
        self.logger = logging.getLogger("xiangqi_ai.app")
        self.logger.info("application session started version=%s resource_base=%s", APP_VERSION, app_base())
        self.root = root
        original_report = root.report_callback_exception

        def report_callback_exception(exc_type, exc_value, traceback):
            self.logger.error("Tk callback exception", exc_info=(exc_type, exc_value, traceback))
            original_report(exc_type, exc_value, traceback)

        root.report_callback_exception = report_callback_exception
        self.root.title(APP_NAME)
        self.root.geometry("1280x820")
        self.root.minsize(1120, 740)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        menu = tk.Menu(self.root)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="使用说明", command=lambda: self.show_help_document(False))
        help_menu.add_command(label="第三方说明与许可证", command=lambda: self.show_help_document(True))
        menu.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menu)

        self.board, self.side = parse_fen(PUZZLE_FEN)
        self.assisted_side = "w"
        self.selected_square: tuple[int, int] | None = None
        self.tool = tk.StringVar(value="move")
        self.side_var = tk.StringVar(value="w")
        self.player_side_var = tk.StringVar(value="w")
        self.orientation_var = tk.StringVar(value="Pikafish · 完全离线 · 红方在下")
        self.time_var = tk.IntVar(value=3000)
        self.multipv_var = tk.IntVar(value=1)
        self.auto_analysis_var = tk.BooleanVar(value=True)
        self.follow_best_var = tk.BooleanVar(value=True)
        self.always_on_top_var = tk.BooleanVar(value=True)
        self.mouse_auto_running = False
        self.mouse_auto_stop_event = threading.Event()
        self.mouse_auto_state = AutomationState.IDLE
        self.mouse_auto_session_id = 0
        self.mouse_auto_thread: threading.Thread | None = None
        self.mouse_auto_pending_start = False
        self.mouse_auto_consent_confirmed = False
        self.mouse_auto_last_status: tuple[int, AutomationState, str] | None = None
        self.mouse_auto_recovery_count = 0
        self.mouse_hotkey_latch = HotkeyLatch(was_pressed=f1_pressed())
        self.mouse_auto_button: ttk.Button | None = None
        self.status_var = tk.StringVar(value="就绪：局面变化后将自动分析")
        self.fen_var = tk.StringVar(value=make_fen(self.board, self.side))
        self.best_arrow: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.analysis_lines: list[AnalysisLine] = []
        self.analysis_side = self.side
        self.analysis_board = dict(self.board)
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.background_image = None
        self.background_tk = None
        self.undo_stack: list[tuple[dict[tuple[int, int], str], str]] = []
        self.redo_stack: list[tuple[dict[tuple[int, int], str], str]] = []
        self.auto_analysis_after_id: str | None = None
        self.analysis_generation = 0
        self.analysis_running = False
        self.follow_move_pending = False
        self.follow_move_description: str | None = None
        self.follow_blocked_positions: dict[str, tuple[str, str]] = {}
        initial_fen = make_fen(self.board, self.side)
        self.completed_position_history: deque[str] = deque([initial_fen], maxlen=24)
        self.engine_history_fen = initial_fen
        self.engine_move_history: list[str] = []
        self.used_root_moves: dict[str, set[str]] = {}
        self.position_results: dict[str, AnalysisLine] = {}
        self.consecutive_assisted_checks = 0
        self.outcome_notice_keys: set[str] = set()
        self.closing = False

        cpu_count = os.cpu_count() or 4
        self.engine = PikafishEngine(
            find_engine(), threads=max(1, min(8, cpu_count - 1)), hash_mb=256
        )
        self.recognizer = PieceRecognizer() if PieceRecognizer is not None else None
        self.review_image_tk = None

        self._configure_style()
        self._build_ui()
        self.root.attributes("-topmost", True)
        self.draw_board()
        self.root.after(100, self._poll_results)
        self.root.after(50, self._poll_f1_hotkey)
        self._schedule_auto_analysis(650)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("TButton", padding=(10, 7), background=PANEL_2, foreground=TEXT)
        style.map("TButton", background=[("active", "#384039")])
        style.configure("Accent.TButton", padding=(12, 8), background=ACCENT, foreground="#0d1a11")
        style.map("Accent.TButton", background=[("active", "#78d9a0")])
        style.configure("TRadiobutton", background=PANEL, foreground=TEXT)
        style.map("TRadiobutton", background=[("active", PANEL)])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("Treeview", background="#161917", fieldbackground="#161917", foreground=TEXT, rowheight=28)
        style.configure("Treeview.Heading", background=PANEL_2, foreground=TEXT)
        style.map("Treeview", background=[("selected", "#335742")])

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=18, pady=(14, 8))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            textvariable=self.orientation_var,
            foreground=MUTED,
        ).pack(side="left", padx=18, pady=(6, 0))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        left = ttk.Frame(body, style="Panel.TFrame")
        left.pack(side="left", fill="y")
        self.canvas = tk.Canvas(
            left,
            width=self.CANVAS_W,
            height=self.CANVAS_H,
            bg=BOARD,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(padx=10, pady=10)
        self.canvas.bind("<Button-1>", self._board_click)
        self.canvas.bind("<Button-3>", self._board_erase)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self._build_controls(right)

    def _build_controls(self, parent: ttk.Frame) -> None:
        setup = ttk.Frame(parent, style="Panel.TFrame")
        setup.pack(fill="x", pady=(0, 10))
        ttk.Label(setup, text="摆局工具", background=PANEL, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 4))

        tools = ttk.Frame(setup, style="Panel.TFrame")
        tools.pack(fill="x", padx=10, pady=4)
        ttk.Radiobutton(tools, text="移动棋子", variable=self.tool, value="move").pack(side="left", padx=3)
        ttk.Radiobutton(tools, text="橡皮", variable=self.tool, value="erase").pack(side="left", padx=3)
        ttk.Button(tools, text="撤销", command=self.undo).pack(side="right", padx=3)
        ttk.Button(tools, text="重做", command=self.redo).pack(side="right", padx=3)

        palette = ttk.Frame(setup, style="Panel.TFrame")
        palette.pack(fill="x", padx=10, pady=4)
        for piece in "KABNRCPkabnrcp":
            color = RED if piece.isupper() else BLACK
            rb = tk.Radiobutton(
                palette,
                text=PIECE_NAMES[piece],
                variable=self.tool,
                value=piece,
                indicatoron=False,
                width=3,
                bg="#efe2c7",
                fg=color,
                activebackground="#fff0cf",
                selectcolor="#f5cc7d",
                relief="flat",
                padx=2,
                pady=5,
            )
            rb.pack(side="left", padx=2, pady=2)

        quick = ttk.Frame(setup, style="Panel.TFrame")
        quick.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(quick, text="标准开局", command=lambda: self.load_preset(START_FEN)).pack(side="left", padx=3)
        ttk.Button(quick, text="双炮残局", command=lambda: self.load_preset(PUZZLE_FEN)).pack(side="left", padx=3)
        ttk.Button(quick, text="清空", command=self.clear_board).pack(side="left", padx=3)
        ttk.Button(quick, text="导入并识别", command=self.import_screenshot).pack(side="right", padx=3)
        ttk.Button(quick, text="粘贴并识别", command=self.paste_screenshot).pack(side="right", padx=3)

        fen_panel = ttk.Frame(parent, style="Panel.TFrame")
        fen_panel.pack(fill="x", pady=(0, 10))
        fen_top = ttk.Frame(fen_panel, style="Panel.TFrame")
        fen_top.pack(fill="x", padx=12, pady=(9, 4))
        ttk.Label(fen_top, text="FEN", background=PANEL, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        ttk.Radiobutton(fen_top, text="红走", variable=self.side_var, value="w", command=self._side_changed).pack(side="right", padx=4)
        ttk.Radiobutton(fen_top, text="黑走", variable=self.side_var, value="b", command=self._side_changed).pack(side="right", padx=4)
        player_row = ttk.Frame(fen_panel, style="Panel.TFrame")
        player_row.pack(fill="x", padx=12, pady=(0, 3))
        ttk.Label(player_row, text="我的棋", style="Muted.TLabel").pack(side="left")
        ttk.Radiobutton(
            player_row,
            text="执红（红在下）",
            variable=self.player_side_var,
            value="w",
            command=self._player_side_changed,
        ).pack(side="left", padx=(10, 4))
        ttk.Radiobutton(
            player_row,
            text="执黑（黑在下）",
            variable=self.player_side_var,
            value="b",
            command=self._player_side_changed,
        ).pack(side="left", padx=4)
        fen_entry = tk.Entry(fen_panel, textvariable=self.fen_var, bg="#111411", fg=TEXT, insertbackground=TEXT, relief="flat")
        fen_entry.pack(fill="x", padx=12, pady=4, ipady=6)
        fen_actions = ttk.Frame(fen_panel, style="Panel.TFrame")
        fen_actions.pack(fill="x", padx=10, pady=(2, 9))
        ttk.Button(fen_actions, text="载入 FEN", command=self.load_fen).pack(side="left", padx=3)
        ttk.Button(fen_actions, text="复制 FEN", command=self.copy_fen).pack(side="left", padx=3)

        analysis = ttk.Frame(parent, style="Panel.TFrame")
        analysis.pack(fill="both", expand=True)
        analysis_top = ttk.Frame(analysis, style="Panel.TFrame")
        analysis_top.pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(analysis_top, text="引擎分析", background=PANEL, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        ttk.Label(analysis_top, text="思考", style="Muted.TLabel").pack(side="left", padx=(18, 4))
        ttk.Combobox(
            analysis_top,
            textvariable=self.time_var,
            values=(500, 1000, 3000, 5000, 10000, 30000),
            width=7,
            state="readonly",
        ).pack(side="left")
        ttk.Label(analysis_top, text="毫秒", style="Muted.TLabel").pack(side="left", padx=(3, 10))
        ttk.Label(analysis_top, text="候选", style="Muted.TLabel").pack(side="left", padx=(2, 4))
        ttk.Spinbox(analysis_top, from_=1, to=5, textvariable=self.multipv_var, width=4).pack(side="left")
        ttk.Button(analysis_top, text="停止", command=self.stop_analysis).pack(side="right", padx=3)
        ttk.Button(analysis_top, text="开始分析", style="Accent.TButton", command=self.start_analysis).pack(side="right", padx=3)

        analysis_options = ttk.Frame(analysis, style="Panel.TFrame")
        analysis_options.pack(fill="x", padx=12, pady=(0, 5))
        ttk.Checkbutton(
            analysis_options,
            text="自动分析",
            variable=self.auto_analysis_var,
            command=self._auto_analysis_toggled,
        ).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(
            analysis_options,
            text="跟随首选着（只录对方走子）",
            variable=self.follow_best_var,
            command=self._follow_best_toggled,
        ).pack(side="left")
        ttk.Button(
            analysis_options,
            text="打开日志",
            command=self.open_log,
        ).pack(side="right")
        ttk.Checkbutton(
            analysis_options,
            text="窗口置顶",
            variable=self.always_on_top_var,
            command=self._topmost_toggled,
        ).pack(side="right", padx=(0, 12))

        mouse_row = ttk.Frame(analysis, style="Panel.TFrame")
        mouse_row.pack(fill="x", padx=12, pady=(0, 5))
        self.mouse_auto_button = ttk.Button(
            mouse_row,
            text="自动接管鼠标",
            command=self._toggle_mouse_autoplay,
        )
        self.mouse_auto_button.pack(side="left")
        ttk.Label(
            mouse_row,
            text="自动识别对手走子并代下首选着；F1 全局启停",
            style="Muted.TLabel",
        ).pack(side="left", padx=(10, 0))

        columns = ("rank", "score", "move", "depth")
        self.tree = ttk.Treeview(analysis, columns=columns, show="headings", height=6)
        self.tree.heading("rank", text="#")
        self.tree.heading("score", text="判断")
        self.tree.heading("move", text="首选着")
        self.tree.heading("depth", text="深度")
        self.tree.column("rank", width=34, anchor="center", stretch=False)
        self.tree.column("score", width=90, anchor="center", stretch=False)
        self.tree.column("move", width=210, anchor="w")
        self.tree.column("depth", width=52, anchor="center", stretch=False)
        self.tree.pack(fill="x", padx=12, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._analysis_selected)
        self.tree.bind("<Double-1>", self.apply_selected_move)

        ttk.Label(analysis, text="主要变化（双击候选着可落子）", style="Muted.TLabel").pack(anchor="w", padx=12, pady=(7, 3))
        self.pv_text = tk.Text(
            analysis,
            height=6,
            wrap="word",
            bg="#111411",
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=9,
            pady=7,
        )
        self.pv_text.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.pv_text.configure(state="disabled")
        ttk.Label(analysis, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w", padx=12, pady=(0, 10))

    def _record_undo(self) -> None:
        self.undo_stack.append((dict(self.board), self.side))
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> None:
        if not self.undo_stack:
            return
        self.redo_stack.append((dict(self.board), self.side))
        self.board, self.side = self.undo_stack.pop()
        self._position_changed(reset_repetition=True)

    def redo(self) -> None:
        if not self.redo_stack:
            return
        self.undo_stack.append((dict(self.board), self.side))
        self.board, self.side = self.redo_stack.pop()
        self._position_changed(reset_repetition=True)

    def _side_changed(self) -> None:
        self.side = self.side_var.get()
        self._position_changed(record=False, reset_repetition=True)

    def _player_side_changed(self) -> None:
        self.assisted_side = self.player_side_var.get()
        self.orientation_var.set(
            "Pikafish · 完全离线 · 黑方在下"
            if self.assisted_side == "b"
            else "Pikafish · 完全离线 · 红方在下"
        )
        self._position_changed(record=False, reset_history=True)
        side_name = "黑" if self.assisted_side == "b" else "红"
        self.status_var.set(f"已切换为执{side_name}视角；正在重新分析当前局面")

    def _position_changed(
        self,
        record: bool = False,
        schedule_analysis: bool = True,
        reset_repetition: bool = False,
        reset_history: bool = False,
    ) -> None:
        self.follow_move_pending = False
        self.follow_move_description = None
        self.side_var.set(self.side)
        self.fen_var.set(make_fen(self.board, self.side))
        self.selected_square = None
        self.best_arrow = None
        self.draw_board()
        if reset_repetition or reset_history:
            signature = make_fen(self.board, self.side)
            self.completed_position_history.clear()
            self.completed_position_history.append(signature)
            self.engine_history_fen = signature
            self.engine_move_history.clear()
            self.used_root_moves.clear()
            self.position_results.clear()
            self.follow_blocked_positions.clear()
            self.outcome_notice_keys.clear()
            self.consecutive_assisted_checks = 0
        if schedule_analysis:
            self._schedule_auto_analysis()
        else:
            self._invalidate_analysis()

    def load_preset(self, fen: str) -> None:
        self._record_undo()
        self.board, self.side = parse_fen(fen)
        self.background_image = None
        self.background_tk = None
        self._position_changed(reset_repetition=True)

    def clear_board(self) -> None:
        self._record_undo()
        self.board = {}
        self._position_changed(reset_repetition=True)

    def load_fen(self) -> None:
        try:
            board, side = parse_fen(self.fen_var.get())
        except ValueError as exc:
            messagebox.showerror("FEN 错误", str(exc))
            return
        self._record_undo()
        self.board, self.side = board, side
        self._position_changed(reset_repetition=True)

    def copy_fen(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(make_fen(self.board, self.side))
        self.status_var.set("FEN 已复制")

    def open_log(self) -> None:
        for handler in logging.getLogger("xiangqi_ai").handlers:
            handler.flush()
        try:
            os.startfile(self.log_path)
            self.status_var.set(f"已打开日志：{self.log_path}")
        except OSError as exc:
            messagebox.showerror("无法打开日志", f"{self.log_path}\n\n{exc}")

    def show_help_document(self, licenses: bool = False):
        base = app_base()
        paths = [base / ("THIRD_PARTY_NOTICES.md" if licenses else "README.md")]
        if licenses:
            paths.extend(sorted(path for path in (base / "licenses").rglob("*") if path.is_file()))
            if not (base / "licenses").exists():
                paths.extend(sorted((base / "vision_licenses").glob("*.txt")))
                paths.extend(find_engine().parent.parent / name for name in ("Copying.txt", "NNUE-License.md", "AUTHORS"))
        try:
            content = "\n\n".join(f"--- {path.name} ---\n\n{path.read_text(encoding='utf-8', errors='replace')}"
                                      for path in paths if path.is_file())
        except OSError as exc:
            self.logger.exception("unable to read bundled documentation")
            messagebox.showerror("无法打开说明", str(exc))
            return None
        window = tk.Toplevel(self.root)
        window.title("第三方说明与许可证" if licenses else "使用说明")
        window.geometry("880x640")
        scrollbar = ttk.Scrollbar(window)
        scrollbar.pack(side="right", fill="y")
        document = tk.Text(window, wrap="word", padx=16, pady=12, yscrollcommand=scrollbar.set)
        document.pack(fill="both", expand=True)
        scrollbar.config(command=document.yview)
        document.insert("1.0", content)
        document.config(state="disabled")
        return window

    def _canvas_point(self, square: tuple[int, int]) -> tuple[float, float]:
        x, y = square
        if self.assisted_side == "b":
            display_x, display_y = 8 - x, y
        else:
            display_x, display_y = x, 9 - y
        return self.X0 + display_x * self.CELL, self.Y0 + display_y * self.CELL

    def _nearest_square(self, event: tk.Event) -> tuple[int, int] | None:
        display_x = round((event.x - self.X0) / self.CELL)
        display_y = round((event.y - self.Y0) / self.CELL)
        if not (0 <= display_x <= 8 and 0 <= display_y <= 9):
            return None
        cx = self.X0 + display_x * self.CELL
        cy = self.Y0 + display_y * self.CELL
        if abs(event.x - cx) > self.CELL * 0.46 or abs(event.y - cy) > self.CELL * 0.46:
            return None
        if self.assisted_side == "b":
            return 8 - display_x, display_y
        return display_x, 9 - display_y

    def _board_click(self, event: tk.Event) -> None:
        square = self._nearest_square(event)
        if square is None:
            return
        tool = self.tool.get()
        if tool == "erase":
            if square in self.board:
                self._record_undo()
                self.board.pop(square, None)
                self._position_changed(reset_repetition=True)
            return
        if tool in PIECE_NAMES:
            self._record_undo()
            self.board[square] = tool
            self._position_changed(reset_repetition=True)
            return
        if self.selected_square is None:
            if square in self.board:
                piece = self.board[square]
                if (
                    piece_side(piece) != self.side
                    and self._auto_follow_recommendation(square)
                ):
                    return
                self.selected_square = square
                self.draw_board()
            return
        if square == self.selected_square:
            self.selected_square = None
            self.draw_board()
            return
        self._move_selected_to(square)

    def _move_selected_to(self, square: tuple[int, int]) -> None:
        if self.selected_square is None or self.selected_square not in self.board:
            return
        followed_recommendation = self.follow_move_pending
        followed_description = self.follow_move_description
        move = square_name(self.selected_square) + square_name(square)
        self._record_undo()
        piece = self.board.pop(self.selected_square)
        self.board[square] = piece
        self.side = "b" if piece_side(piece) == "w" else "w"
        if followed_recommendation:
            self.engine_move_history.append(move)
            self._position_changed()
        else:
            # Free-form manual edits are accepted by the board UI. Treat the
            # resulting position as a fresh history root so an accidental
            # illegal edit can never terminate the UCI engine.
            self._position_changed(reset_history=True)
        if followed_recommendation:
            if self._record_completed_follow_position():
                return
            self.status_var.set(
                f"已补录上一手：{followed_description or '首选着'}；"
                "已录入对方走子，正在分析你的下一步"
            )

    def _board_erase(self, event: tk.Event) -> None:
        square = self._nearest_square(event)
        if square in self.board:
            self._record_undo()
            self.board.pop(square, None)
            self._position_changed(reset_repetition=True)

    def _record_completed_follow_position(self) -> bool:
        signature = make_fen(self.board, self.side)
        self.completed_position_history.append(signature)
        repetitions = self.completed_position_history.count(signature)
        if repetitions < 3:
            return False
        self.follow_move_pending = False
        self.follow_move_description = None
        prior = self.position_results.get(signature)
        still_winning = prior is not None and _line_keeps_winning_chances(prior)
        block_kind = "repetition_win" if still_winning else "repetition"
        block_reason = "当前必胜局面仍发生 3 次重复，已暂停并等待重新同步" if still_winning else "当前局面已经出现 3 次，已暂停自动跟随"
        self.follow_blocked_positions[signature] = (block_kind, block_reason)
        self._invalidate_analysis()
        if still_winning:
            score = score_text(prior, self.side)
            message = (
                f"已检测到同一局面出现 3 次，但引擎对这个局面仍给出“{score}”，"
                "所以不能把它误报成无胜或直接建议认输。\n\n"
                "这更可能是实际走子与助手内部历史没有完全同步，或对手通过另一条路线"
                "回到了同一局面。自动跟随已暂停，请重新截图识别后继续。"
            )
            self.status_var.set("必胜局面发生三次重复：已停止循环，请重新截图同步")
            self.logger.warning(
                "winning repetition stopped fen=%s score=%s:%s wdl=%s moves=%s",
                signature,
                prior.score_type,
                prior.score,
                prior.wdl,
                " ".join(self.engine_move_history),
            )
            messagebox.showwarning("循环已停止", message)
        else:
            message = (
                "已检测到同一局面出现 3 次，当前局面的自动跟随已暂停。\n\n"
                "引擎没有找到可保持胜势的避循环方案。"
                "如果当前关卡必须获胜，建议直接认输并重开。\n\n"
                "“跟随首选着”的勾选偏好没有改变；载入新的可胜局面后会自动恢复。"
            )
            self.status_var.set("检测到三次重复局面：已停止循环，建议认输重开")
            self.logger.warning("threefold loop stopped fen=%s", signature)
            messagebox.showwarning("建议认输", message)
        return True

    def _record_assisted_check(
        self,
        board: dict[tuple[int, int], str],
        moving_side: str,
        move: str,
    ) -> None:
        if moving_side != self.assisted_side:
            return
        try:
            checking = move_gives_check(board, moving_side, move)
        except ValueError:
            checking = False
        if checking and not is_king_capture_move(board, move):
            self.consecutive_assisted_checks += 1
        else:
            self.consecutive_assisted_checks = 0
        self.logger.info(
            "assisted move check-state move=%s checking=%s streak=%s",
            move,
            checking,
            self.consecutive_assisted_checks,
        )

    def draw_board(self) -> None:
        c = self.canvas
        c.delete("all")
        c.create_rectangle(0, 0, self.CANVAS_W, self.CANVAS_H, fill=BOARD, outline="")
        if self.background_image is not None and ImageTk is not None:
            grid_w, grid_h = self.CELL * 8, self.CELL * 9
            rendered = self.background_image.resize((grid_w, grid_h))
            if self.assisted_side == "b":
                rendered = rendered.rotate(180)
            rendered = ImageEnhance.Brightness(rendered).enhance(0.56)
            self.background_tk = ImageTk.PhotoImage(rendered)
            c.create_image(self.X0, self.Y0, image=self.background_tk, anchor="nw")

        for x in range(9):
            px = self.X0 + x * self.CELL
            c.create_line(px, self.Y0, px, self.Y0 + 4 * self.CELL, fill=GRID, width=2)
            c.create_line(px, self.Y0 + 5 * self.CELL, px, self.Y0 + 9 * self.CELL, fill=GRID, width=2)
            if x in {0, 8}:
                c.create_line(px, self.Y0 + 4 * self.CELL, px, self.Y0 + 5 * self.CELL, fill=GRID, width=2)
        for row in range(10):
            py = self.Y0 + row * self.CELL
            c.create_line(self.X0, py, self.X0 + 8 * self.CELL, py, fill=GRID, width=2)

        for top in (0, 7):
            y1 = self.Y0 + top * self.CELL
            y3 = y1 + 2 * self.CELL
            x1 = self.X0 + 3 * self.CELL
            x3 = self.X0 + 5 * self.CELL
            c.create_line(x1, y1, x3, y3, fill=GRID, width=2)
            c.create_line(x3, y1, x1, y3, fill=GRID, width=2)

        river_y = self.Y0 + 4.5 * self.CELL
        left_river, right_river = (
            ("汉 界", "楚 河") if self.assisted_side == "b" else ("楚 河", "汉 界")
        )
        c.create_text(self.X0 + 2 * self.CELL, river_y, text=left_river, fill=GRID, font=("SimSun", 22, "bold"))
        c.create_text(self.X0 + 6 * self.CELL, river_y, text=right_river, fill=GRID, font=("SimSun", 22, "bold"))

        displayed_files = reversed(FILES) if self.assisted_side == "b" else FILES
        for display_x, file_name in enumerate(displayed_files):
            px = self.X0 + display_x * self.CELL
            c.create_text(px, self.Y0 - 20, text=file_name, fill="#725d43", font=("Consolas", 10))
            c.create_text(px, self.Y0 + 9 * self.CELL + 20, text=file_name, fill="#725d43", font=("Consolas", 10))
        for y in range(10):
            _, py = self._canvas_point((0, y))
            c.create_text(self.X0 - 25, py, text=str(y), fill="#725d43", font=("Consolas", 10))

        if self.best_arrow:
            start, end = self.best_arrow
            sx, sy = self._canvas_point(start)
            ex, ey = self._canvas_point(end)
            c.create_line(sx, sy, ex, ey, fill=ACCENT, width=9, arrow=tk.LAST, arrowshape=(18, 22, 8), stipple="gray50")

        if self.selected_square:
            sx, sy = self._canvas_point(self.selected_square)
            c.create_oval(sx - 29, sy - 29, sx + 29, sy + 29, outline=ACCENT, width=4)

        for square, piece in self.board.items():
            x, y = self._canvas_point(square)
            fill = "#f4e4c5" if piece.isupper() else "#ddd8cb"
            outline = RED if piece.isupper() else BLACK
            c.create_oval(x - 25, y - 25, x + 25, y + 25, fill=fill, outline=outline, width=3)
            c.create_oval(x - 20, y - 20, x + 20, y + 20, outline=outline, width=1)
            c.create_text(x, y, text=PIECE_NAMES[piece], fill=outline, font=("SimSun", 25, "bold"))

    def _prepare_screenshot(self, image) -> None:
        if Image is None:
            messagebox.showerror("缺少组件", "此版本未包含 Pillow，无法导入截图。")
            return
        image = image.convert("RGB")
        width, height = image.size
        if width / max(height, 1) > 1.35:
            box = (
                int(width * 0.0953),
                int(height * 0.0972),
                int(width * 0.4758),
                int(height * 0.8570),
            )
            image = image.crop(box)
        self.background_image = image
        self.draw_board()
        self.status_var.set("截图已作为棋盘底图")

    def _auto_recognize_image(self, image) -> None:
        if self.recognizer is None:
            messagebox.showerror("缺少组件", "此版本未包含截图识别组件。")
            return
        try:
            grid, detections = self.recognizer.recognize(image)
        except Exception as exc:
            messagebox.showerror("识别失败", str(exc))
            return
        backend = getattr(self.recognizer, "last_backend", "")
        backend_text = "ONNX 深度模型" if backend == "onnx" else "兼容模板"
        if backend == "template-fallback":
            reason = getattr(self.recognizer, "last_error", "未知原因")
            messagebox.showwarning(
                "深度模型未能使用",
                f"本次已进入旧版兼容识别，结果可能不准确。\n\n原因：{reason}",
            )
        if not detections:
            self._prepare_screenshot(image)
            messagebox.showwarning(
                "没有识别到棋子",
                f"{backend_text}未找到棋子。图片已作为底图载入，可继续手动摆局。",
            )
            return

        self._record_undo()
        self.background_image = grid
        self.board = {
            item.square: item.piece
            for item in detections
            if item.piece is not None
        }
        self.selected_square = None
        self.best_arrow = None
        unknown = [item for item in detections if item.piece is None]
        self._position_changed(
            schedule_analysis=not unknown,
            reset_repetition=True,
        )
        if unknown:
            self.status_var.set(
                f"{backend_text}识别到 {len(detections)} 枚；{len(unknown)} 个低置信度格子需要确认"
            )
            self._review_unknown_detections(unknown, len(detections))
        else:
            self.status_var.set(
                f"{backend_text}识别完成：{len(detections)} 枚棋子，正在准备自动分析"
            )

    def _review_unknown_detections(
        self, pending: list["Detection"], total_count: int
    ) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("确认新棋子")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        title_var = tk.StringVar()
        ttk.Label(
            dialog,
            textvariable=title_var,
            background=PANEL,
            foreground=TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(padx=20, pady=(16, 8))
        image_label = tk.Label(dialog, bg="#111411", bd=8, relief="flat")
        image_label.pack(padx=20, pady=6)
        ttk.Label(
            dialog,
            text="深度模型对这些格子不够确定，请快速确认后再交给引擎。",
            style="Muted.TLabel",
        ).pack(padx=20, pady=(4, 8))
        button_frame = ttk.Frame(dialog, style="Panel.TFrame")
        button_frame.pack(padx=16, pady=(2, 8))
        footer = ttk.Frame(dialog, style="Panel.TFrame")
        footer.pack(fill="x", padx=16, pady=(0, 14))

        state = {"index": 0}

        def finish() -> None:
            self._position_changed(reset_repetition=True)
            confirmed = sum(1 for item in pending if item.square in self.board)
            self.status_var.set(
                f"识别完成：检测 {total_count} 格，已确认 {confirmed} 枚棋子；正在准备自动分析"
            )
            dialog.grab_release()
            dialog.destroy()

        def choose(piece: str | None) -> None:
            item = pending[state["index"]]
            if piece:
                self.board[item.square] = piece
                self.recognizer.learn(piece, item.feature)
            state["index"] += 1
            if state["index"] >= len(pending):
                finish()
            else:
                show_current()

        def show_current() -> None:
            item = pending[state["index"]]
            square = f"{FILES[item.square[0]]}{item.square[1]}"
            side_text = "红方" if item.side == "w" else "黑方"
            title_var.set(
                f"{state['index'] + 1}/{len(pending)}　{square}　{side_text}棋子"
            )
            preview = item.patch.resize((160, 160))
            self.review_image_tk = ImageTk.PhotoImage(preview)
            image_label.configure(image=self.review_image_tk)
            for child in button_frame.winfo_children():
                child.destroy()
            pieces = "KABNRCP" if item.side == "w" else "kabnrcp"
            for piece in pieces:
                tk.Button(
                    button_frame,
                    text=PIECE_NAMES[piece],
                    command=lambda value=piece: choose(value),
                    width=4,
                    bg="#f3e2c1",
                    fg=RED if piece.isupper() else BLACK,
                    relief="flat",
                    font=("SimSun", 16, "bold"),
                    padx=4,
                    pady=7,
                ).pack(side="left", padx=3)

        ttk.Button(footer, text="此处无子/跳过", command=lambda: choose(None)).pack(side="right")
        show_current()

    def import_screenshot(self) -> None:
        if Image is None:
            messagebox.showerror("缺少组件", "此版本未包含截图组件。")
            return
        path = filedialog.askopenfilename(
            title="选择棋盘截图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            self._auto_recognize_image(Image.open(path))
        except Exception as exc:
            messagebox.showerror("无法打开图片", str(exc))

    def paste_screenshot(self) -> None:
        if ImageGrab is None:
            messagebox.showerror("缺少组件", "此版本未包含截图组件。")
            return
        try:
            image = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror("读取剪贴板失败", str(exc))
            return
        if image is None or not hasattr(image, "size"):
            messagebox.showinfo("剪贴板", "剪贴板里没有图片。先在游戏中截图或复制图片。")
            return
        self._auto_recognize_image(image)

    def _cancel_auto_analysis_timer(self) -> None:
        if self.auto_analysis_after_id is None:
            return
        try:
            self.root.after_cancel(self.auto_analysis_after_id)
        except tk.TclError:
            pass
        self.auto_analysis_after_id = None

    def _invalidate_analysis(self) -> int:
        self._cancel_auto_analysis_timer()
        self.analysis_generation += 1
        if self.analysis_running:
            self.engine.stop()
            self.analysis_running = False
        return self.analysis_generation

    def _schedule_auto_analysis(self, delay: int = 500) -> None:
        generation = self._invalidate_analysis()
        if self.closing or not self.auto_analysis_var.get():
            return
        self.auto_analysis_after_id = self.root.after(
            delay,
            lambda: self._launch_analysis(generation, manual=False),
        )

    def _auto_analysis_toggled(self) -> None:
        if self.auto_analysis_var.get():
            self.status_var.set("自动分析已开启，正在准备分析当前局面")
            self._schedule_auto_analysis(100)
        else:
            self._invalidate_analysis()
            self.status_var.set("自动分析已关闭")

    def _follow_best_toggled(self) -> None:
        if self.follow_best_var.get():
            blocked = self.follow_blocked_positions.get(make_fen(self.board, self.side))
            if blocked is not None:
                self.status_var.set(
                    f"跟随偏好已开启，但{blocked[1]}；新局面会自动恢复"
                )
            else:
                self.status_var.set("跟随首选着已开启：下次可直接点击对方棋子")
        else:
            self.status_var.set("跟随首选着已关闭：请手动录入双方走子")

    def _topmost_toggled(self) -> None:
        enabled = bool(self.always_on_top_var.get())
        try:
            self.root.attributes("-topmost", enabled)
            if enabled:
                self.root.lift()
            self.status_var.set("窗口置顶已开启" if enabled else "窗口置顶已关闭")
        except tk.TclError as exc:
            self.always_on_top_var.set(False)
            messagebox.showerror("无法切换窗口置顶", str(exc))

    def start_analysis(self) -> None:
        generation = self._invalidate_analysis()
        self._launch_analysis(generation, manual=True)

    def _launch_analysis(self, generation: int, manual: bool) -> None:
        self.auto_analysis_after_id = None
        if self.closing or generation != self.analysis_generation:
            return
        if not manual and not self.auto_analysis_var.get():
            return
        issues = validate_position(self.board)
        if issues:
            self.status_var.set("等待完整局面：需保留双方将帅")
            if manual:
                messagebox.showwarning("局面不完整", "\n".join(issues))
            return
        board_snapshot = dict(self.board)
        side_snapshot = self.side
        fen = make_fen(self.board, self.side)
        direct_capture = find_direct_king_capture(self.board, self.side)
        if direct_capture is not None:
            self.analysis_running = False
            self.analysis_board = board_snapshot
            self.analysis_side = side_snapshot
            self.logger.warning(
                "direct king capture overrides engine fen=%s move=%s",
                fen,
                direct_capture,
            )
            self._show_analysis(
                [
                    AnalysisLine(
                        1,
                        0,
                        "mate",
                        1,
                        [direct_capture],
                        (1000, 0, 0),
                    )
                ],
                direct_capture,
            )
            return
        movetime = int(self.time_var.get())
        multipv = int(self.multipv_var.get())
        signature = fen
        visit_count = self.completed_position_history.count(signature)
        avoided_moves: set[str] = set()
        avoid_checks = (
            self.side == self.assisted_side
            and self.consecutive_assisted_checks >= 2
        )
        if self.side == self.assisted_side and visit_count >= 2:
            avoided_moves = set(self.used_root_moves.get(signature, set()))
            if avoided_moves:
                # A single-PV search will deterministically select the same
                # locally best move whenever a FEN comes back. On the second
                # visit, compare several full-strength winning alternatives
                # before the third occurrence can be reached.
                multipv = max(multipv, 5)
                movetime = max(movetime, 5000)
        if avoid_checks:
            multipv = max(multipv, 12)
            movetime = max(movetime, 5000)
        mode = "自动分析" if not manual else "分析"
        if avoided_moves and avoid_checks:
            self.status_var.set(
                f"局面重复且已连续将军：深算 {multipv} 条候选，寻找保胜安静着"
            )
        elif avoid_checks:
            self.status_var.set(
                f"已连续将军 {self.consecutive_assisted_checks} 手：深算非将军胜法"
            )
        elif avoided_moves:
            self.status_var.set(
                f"检测到局面再次出现：深算 {multipv} 条候选，主动避开重复着"
            )
        else:
            self.status_var.set(f"{mode}中… {movetime / 1000:g} 秒，{multipv} 条候选")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._set_pv_text("")
        self.analysis_running = True
        thread = threading.Thread(
            target=self._analysis_worker,
            args=(
                fen,
                movetime,
                multipv,
                generation,
                board_snapshot,
                side_snapshot,
                manual,
                self.engine_history_fen,
                list(self.engine_move_history),
                avoided_moves,
                avoid_checks,
            ),
            daemon=True,
        )
        thread.start()

    def _analysis_worker(
        self,
        fen: str,
        movetime: int,
        multipv: int,
        generation: int,
        board_snapshot: dict[tuple[int, int], str],
        side_snapshot: str,
        manual: bool,
        history_fen: str,
        move_history: list[str],
        avoided_moves: set[str],
        avoid_checks: bool,
    ) -> None:
        try:
            lines, bestmove = self.engine.analyse(
                fen,
                movetime,
                multipv,
                history_fen=history_fen,
                moves=move_history,
            )
            lines, replacement = prefer_fresh_winning_line(lines, avoided_moves)
            if replacement is not None:
                old_best = bestmove
                bestmove = replacement
                self.logger.warning(
                    "anti-loop promoted move fen=%s old=%s new=%s avoided=%s history=%s",
                    fen,
                    old_best,
                    replacement,
                    sorted(avoided_moves),
                    " ".join(move_history),
                )
            quiet_replacement = None
            quiet_unavailable = False
            if avoid_checks and lines:
                quiet_needed = _checking_line_should_yield(
                    lines[0], board_snapshot, side_snapshot
                )
                lines, quiet_replacement = prefer_quiet_winning_line(
                    lines, board_snapshot, side_snapshot
                )
                quiet_unavailable = quiet_needed and quiet_replacement is None
                if quiet_replacement is not None:
                    old_best = bestmove
                    bestmove = quiet_replacement
                    self.logger.warning(
                        "anti-check promoted move fen=%s old=%s new=%s streak=%s history=%s",
                        fen,
                        old_best,
                        quiet_replacement,
                        self.consecutive_assisted_checks,
                        " ".join(move_history),
                    )
            self.result_queue.put(
                (
                    "analysis",
                    (
                        generation,
                        lines,
                        bestmove,
                        board_snapshot,
                        side_snapshot,
                        bool(replacement),
                        bool(quiet_replacement),
                        quiet_unavailable,
                    ),
                )
            )
        except Exception as exc:
            self.result_queue.put(("error", (generation, exc, manual)))

    def stop_analysis(self) -> None:
        self._invalidate_analysis()
        self.status_var.set("分析已停止；下次局面变化仍会自动分析")

    def _poll_results(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "analysis":
                    (
                        generation,
                        lines,
                        bestmove,
                        board_snapshot,
                        side_snapshot,
                        avoided_loop,
                        avoided_checks,
                        quiet_unavailable,
                    ) = payload
                    if generation != self.analysis_generation:
                        continue
                    self.analysis_running = False
                    self.analysis_board = board_snapshot
                    self.analysis_side = side_snapshot
                    self._show_analysis(
                        lines,
                        bestmove,
                        avoided_loop=avoided_loop,
                        avoided_checks=avoided_checks,
                        quiet_unavailable=quiet_unavailable,
                    )
                elif kind == "error":
                    generation, error, manual = payload
                    if generation != self.analysis_generation:
                        continue
                    self.analysis_running = False
                    self.status_var.set("分析失败")
                    if manual or not self.auto_analysis_var.get():
                        messagebox.showerror("引擎错误", str(error))
                    else:
                        messagebox.showerror("自动分析失败", str(error))
                elif kind == "mouse_board":
                    session_id, board, side, grid, history_fen, moves, status = payload
                    if not session_event_is_current(
                        session_id,
                        self.mouse_auto_session_id,
                        running=self.mouse_auto_running,
                        stopping=self.mouse_auto_state == AutomationState.STOPPING,
                    ):
                        continue
                    self.board = dict(board)
                    self.side = side
                    self.side_var.set(side)
                    self.fen_var.set(make_fen(self.board, self.side))
                    self.background_image = grid
                    self.selected_square = None
                    self.best_arrow = None
                    self.engine_history_fen = history_fen
                    self.engine_move_history = list(moves)
                    self.draw_board()
                    self.status_var.set(status)
                elif kind == "mouse_status":
                    session_id, state, status = payload
                    if not session_event_is_current(
                        session_id,
                        self.mouse_auto_session_id,
                        running=self.mouse_auto_running,
                        stopping=self.mouse_auto_state == AutomationState.STOPPING,
                    ):
                        continue
                    self.mouse_auto_state = state
                    self.status_var.set(str(status))
                elif kind == "mouse_done":
                    session_id, level, title, detail = payload
                    if session_id != self.mouse_auto_session_id:
                        continue
                    self._finish_mouse_autoplay(session_id, level, title, detail)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_results)

    def _show_analysis(
        self,
        lines: list[AnalysisLine],
        bestmove: str,
        *,
        avoided_loop: bool = False,
        avoided_checks: bool = False,
        quiet_unavailable: bool = False,
    ) -> None:
        self.analysis_lines = lines
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, line in enumerate(lines):
            direct_capture = is_king_capture_move(self.analysis_board, line.best_move)
            try:
                move_text = describe_move(self.analysis_board, line.best_move)
            except ValueError:
                move_text = line.best_move
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    line.multipv,
                    score_text(line, self.analysis_side),
                    move_text,
                    "立即" if direct_capture else line.depth,
                ),
            )
        if lines:
            signature = make_fen(self.analysis_board, self.analysis_side)
            self.position_results[signature] = lines[0]
            self.tree.selection_set("0")
            self.tree.focus("0")
            self._select_analysis_index(0)
            top = score_text(lines[0], self.analysis_side)
            if is_king_capture_move(self.analysis_board, lines[0].best_move):
                move_text = describe_move(self.analysis_board, lines[0].best_move)
                self.status_var.set(f"绝杀：{move_text}；立即吃将")
                return
            if self._handle_no_win(lines[0], top):
                return
            if self.follow_best_var.get():
                if avoided_checks:
                    self.status_var.set(
                        f"已结束连续将军：改走 {describe_move(self.analysis_board, lines[0].best_move)}；{top}"
                    )
                elif avoided_loop:
                    self.status_var.set(
                        f"已避开旧循环：改走 {describe_move(self.analysis_board, lines[0].best_move)}；{top}"
                    )
                elif quiet_unavailable:
                    self.status_var.set(
                        f"已复核连续将军：暂未找到保持胜势的非将军着；{top}"
                    )
                else:
                    self.status_var.set(
                        f"完成：{top}；照首选着走后，直接点击对方走动的棋子"
                    )
            else:
                self.status_var.set(f"完成：{top}；双击候选着可直接落子")
        else:
            self.status_var.set(f"分析结束：{bestmove or '无合法着法'}")

    def _handle_no_win(self, line: AnalysisLine, score: str) -> bool:
        # During manual two-sided analysis the side to move can temporarily be
        # the opponent. Only tell the player to resign when evaluating the side
        # selected as their assisted side.
        if self.analysis_side != self.assisted_side:
            return False
        reason = no_win_reason(line)
        notice_key = make_fen(self.analysis_board, self.analysis_side)
        if reason is None:
            blocked = self.follow_blocked_positions.get(notice_key)
            if blocked is not None and blocked[0] == "no_win":
                self.follow_blocked_positions.pop(notice_key, None)
            return False
        self.follow_move_pending = False
        self.follow_move_description = None
        self.follow_blocked_positions[notice_key] = ("no_win", reason)
        self.status_var.set(f"{score}：当前局面无法取胜，已暂停跟随；建议认输重开")
        self.logger.warning(
            "no-win outcome fen=%s depth=%s score=%s:%s wdl=%s reason=%s",
            notice_key,
            line.depth,
            line.score_type,
            line.score,
            line.wdl,
            reason,
        )
        if notice_key not in self.outcome_notice_keys:
            self.outcome_notice_keys.add(notice_key)
            wdl_text = ""
            if line.wdl is not None:
                wins, draws, losses = line.wdl
                wdl_text = (
                    f"\n\nWDL：胜 {wins / 10:.1f}% / "
                    f"和 {draws / 10:.1f}% / 负 {losses / 10:.1f}%"
                )
            messagebox.showwarning(
                "建议认输",
                f"{reason}。{wdl_text}\n\n"
                "当前局面的自动跟随已暂停，不会继续循环；"
                "你的勾选偏好没有改变，进入新的可胜局面后会自动恢复。\n\n"
                "如果当前关卡必须获胜，请直接认输并重开。\n\n"
                f"分析日志：{self.log_path}",
            )
        return True

    def _analysis_selected(self, _event=None) -> None:
        selection = self.tree.selection()
        if selection:
            self._select_analysis_index(int(selection[0]))

    def _select_analysis_index(self, index: int) -> None:
        if not (0 <= index < len(self.analysis_lines)):
            return
        line = self.analysis_lines[index]
        try:
            self.best_arrow = parse_move(line.best_move)
        except ValueError:
            self.best_arrow = None
        pv = format_pv(self.analysis_board, self.analysis_side, line.pv)
        self._set_pv_text(pv)
        self.draw_board()

    def _set_pv_text(self, text: str) -> None:
        self.pv_text.configure(state="normal")
        self.pv_text.delete("1.0", "end")
        self.pv_text.insert("1.0", text)
        self.pv_text.configure(state="disabled")

    def _current_analysis_line(self) -> AnalysisLine | None:
        if (
            not self.analysis_lines
            or self.analysis_running
            or self.board != self.analysis_board
            or self.side != self.analysis_side
        ):
            return None
        selection = self.tree.selection()
        index = int(selection[0]) if selection else 0
        if not (0 <= index < len(self.analysis_lines)):
            return None
        line = self.analysis_lines[index]
        return line if line.best_move else None

    def _auto_follow_recommendation(self, opponent_square: tuple[int, int]) -> bool:
        if not self.follow_best_var.get():
            return False
        signature = make_fen(self.board, self.side)
        blocked = self.follow_blocked_positions.get(signature)
        if blocked is not None:
            next_step = "请认输重开或载入新局面" if blocked[0] == "no_win" else "请重新截图同步或载入新局面"
            self.status_var.set(f"{blocked[1]}；{next_step}")
            return True
        line = self._current_analysis_line()
        if line is None:
            return False
        try:
            updated = apply_move(self.board, line.best_move)
        except ValueError:
            return False
        next_side = "b" if self.side == "w" else "w"
        remaining_piece = updated.get(opponent_square)
        if remaining_piece is None or piece_side(remaining_piece) != next_side:
            self.status_var.set(
                "首选着会吃掉你点击的棋子，未自动代走；请核对实际走法"
            )
            return False

        before = dict(self.board)
        moving_side = self.side
        self._record_undo()
        self.board = updated
        self.side = next_side
        self.engine_move_history.append(line.best_move)
        self.used_root_moves.setdefault(signature, set()).add(line.best_move)
        self._record_assisted_check(before, moving_side, line.best_move)
        self._position_changed(schedule_analysis=False)
        self.follow_move_pending = True
        self.follow_move_description = describe_move(before, line.best_move)
        self.selected_square = opponent_square
        self.draw_board()
        self.status_var.set(
            f"已自动代走：{self.follow_move_description}；请选择对方落点"
        )
        return True

    def apply_selected_move(self, _event=None) -> None:
        line = self._current_analysis_line()
        if line is None:
            self.status_var.set("当前推荐已过期，请等待本局面分析完成")
            return
        try:
            updated = apply_move(self.board, line.best_move)
        except ValueError as exc:
            messagebox.showerror("无法落子", str(exc))
            return
        before = dict(self.board)
        moving_side = self.side
        self._record_undo()
        self.board = updated
        self.side = "b" if self.side == "w" else "w"
        self.engine_move_history.append(line.best_move)
        self._record_assisted_check(before, moving_side, line.best_move)
        self._position_changed()
        self.status_var.set(f"已走：{describe_move(before, line.best_move)}")

    def _poll_f1_hotkey(self) -> None:
        if self.closing:
            return
        if self.mouse_hotkey_latch.update(f1_pressed()):
            self.logger.info("global F1 takeover toggle")
            self._toggle_mouse_autoplay()
        try:
            self.root.after(50, self._poll_f1_hotkey)
        except tk.TclError:
            pass

    def _toggle_mouse_autoplay(self) -> None:
        if self.mouse_auto_running:
            if self.mouse_auto_state == AutomationState.STOPPING:
                self.mouse_auto_pending_start = True
                self.status_var.set("当前会话停止后将重新启动自动接管…")
            else:
                self._request_mouse_autoplay_stop("F1 急停")
            return
        self._start_mouse_autoplay()

    def _start_mouse_autoplay(self) -> None:
        if self.mouse_auto_running or self.closing:
            return
        if ImageGrab is None or self.recognizer is None:
            messagebox.showerror("无法接管", "当前版本缺少屏幕截图或 ONNX 识别组件。")
            return
        if os.name != "nt":
            messagebox.showerror("无法接管", "鼠标自动接管目前仅支持 Windows。")
            return
        side_name = "红方" if self.assisted_side == "w" else "黑方"
        turn_name = "红方" if self.side == "w" else "黑方"
        if not self.mouse_auto_consent_confirmed:
            confirmed = messagebox.askokcancel(
                "启动自动接管",
                "仅限单机残局、复盘或规则明确允许使用辅助的场景。\n"
                "请勿用于真人匹配、排位或其他禁止辅助的对局。\n\n"
                f"当前设置：我执{side_name[0]}，现在{turn_name}走。\n"
                "确认游戏棋盘完整显示在主屏幕上；启动后助手窗口会隐藏。\n\n"
                "用户操作鼠标或切换窗口时程序会暂停并自动恢复。\n"
                "按 F1 可在任何时候急停并恢复助手窗口。",
            )
            if not confirmed:
                return
            self.mouse_auto_consent_confirmed = True

        self._invalidate_analysis()
        self.mouse_auto_session_id += 1
        session_id = self.mouse_auto_session_id
        stop_event = threading.Event()
        self.mouse_auto_stop_event = stop_event
        self.mouse_auto_running = True
        self.mouse_auto_pending_start = False
        self.mouse_auto_state = AutomationState.ACQUIRING
        self.mouse_auto_last_status = None
        self.mouse_auto_recovery_count = 0
        if self.mouse_auto_button is not None:
            self.mouse_auto_button.configure(
                text="停止自动接管（F1）",
                state="normal",
            )
        self.status_var.set("自动接管启动中：正在锁定游戏棋盘；F1 急停")
        assisted_side = self.assisted_side
        current_side = self.side
        movetime = int(self.time_var.get())
        multipv = int(self.multipv_var.get())
        self.root.update_idletasks()
        self.root.withdraw()
        worker = threading.Thread(
            target=self._mouse_autoplay_worker,
            args=(
                session_id,
                stop_event,
                assisted_side,
                current_side,
                movetime,
                multipv,
            ),
            daemon=True,
            name=f"mouse-autoplay-{session_id}",
        )
        self.mouse_auto_thread = worker
        worker.start()

    def _request_mouse_autoplay_stop(self, reason: str = "用户停止") -> None:
        if not self.mouse_auto_running:
            return
        self.mouse_auto_state = AutomationState.STOPPING
        self.mouse_auto_stop_event.set()
        self.engine.stop()
        self.status_var.set(f"{reason}：正在停止自动接管…")
        if self.mouse_auto_button is not None:
            self.mouse_auto_button.configure(text="正在停止…", state="disabled")

    def _mouse_autoplay_cancelled(
        self,
        session_id: int,
        stop_event: threading.Event,
    ) -> bool:
        return (
            self.closing
            or stop_event.is_set()
            or session_id != self.mouse_auto_session_id
        )

    def _mouse_sleep(
        self,
        session_id: int,
        stop_event: threading.Event,
        seconds: float,
    ) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._mouse_autoplay_cancelled(session_id, stop_event):
                raise InterruptedError("用户已停止自动接管")
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _queue_mouse_status(
        self,
        session_id: int,
        state: AutomationState,
        status: str,
    ) -> None:
        key = (session_id, state, status)
        if key == self.mouse_auto_last_status:
            return
        self.mouse_auto_last_status = key
        self.logger.info(
            "mouse autoplay state session=%s state=%s status=%s",
            session_id,
            state.value,
            status,
        )
        self.result_queue.put(("mouse_status", (session_id, state, status)))

    def _log_mouse_recovery(self, session_id: int, reason: str) -> None:
        if session_id != self.mouse_auto_session_id:
            return
        self.mouse_auto_recovery_count += 1
        self.logger.info(
            "mouse autoplay recovered session=%s count=%s reason=%s",
            session_id,
            self.mouse_auto_recovery_count,
            reason,
        )

    def _capture_mouse_board(
        self,
        session_id: int,
        stop_event: threading.Event,
        *,
        allow_terminal: bool = False,
    ):
        if self._mouse_autoplay_cancelled(session_id, stop_event):
            raise InterruptedError("用户已停止自动接管")
        image = ImageGrab.grab()
        grid, detections = self.recognizer.recognize(image)
        backend = getattr(self.recognizer, "last_backend", "")
        geometry = getattr(self.recognizer, "last_geometry", None)
        unknown = [item for item in detections if item.piece is None]
        board = {
            item.square: item.piece
            for item in detections
            if item.piece is not None
        }
        if backend != "onnx" or geometry is None:
            reason = getattr(self.recognizer, "last_error", "无法定位棋盘")
            if "找不到深度识别模型" in reason or "缺少 ONNX" in reason:
                raise FatalAutomationError(reason)
            raise RuntimeError(f"本帧无法可靠定位棋盘：{reason}")
        if unknown:
            raise RuntimeError(f"本帧有 {len(unknown)} 个低置信度格子")
        if not position_is_safe(board) and not (
            allow_terminal and position_is_terminal(board)
        ):
            raise RuntimeError("本帧没有可靠识别到合法将帅数量")
        if geometry.confidence < 0.10:
            raise RuntimeError(
                f"棋盘定位置信度过低（{geometry.confidence:.2f}）"
            )
        width, height = geometry.image_size
        corners = [
            geometry.point_for_square(square)
            for square in ((0, 0), (8, 0), (0, 9), (8, 9))
        ]
        if any(
            not (2 <= x < width - 2 and 2 <= y < height - 2)
            for x, y in corners
        ):
            raise RuntimeError("识别到的棋盘落点超出主屏幕")
        return board, grid, geometry

    def _capture_stable_mouse_board(
        self,
        session_id: int,
        stop_event: threading.Event,
        *,
        stable_frames: int = 2,
        allow_terminal: bool = False,
        accept=None,
        state: AutomationState = AutomationState.WAITING_BOARD,
        status: str = "棋盘暂不可用，正在等待自动恢复",
    ):
        tracker = StableBoardTracker(stable_frames)
        latest = None
        last_detail = ""
        recovery_detail = ""
        while True:
            if self._mouse_autoplay_cancelled(session_id, stop_event):
                raise InterruptedError("用户已停止自动接管")
            try:
                current = self._capture_mouse_board(
                    session_id,
                    stop_event,
                    allow_terminal=allow_terminal,
                )
                candidate = current[0]
                if accept is not None and not accept(candidate):
                    tracker.reset()
                    detail = "盘面变化尚未通过安全校验"
                else:
                    latest = current
                    detail = ""
                    if tracker.observe(candidate):
                        if recovery_detail:
                            self._log_mouse_recovery(
                                session_id,
                                recovery_detail,
                            )
                        return latest
            except FatalAutomationError:
                raise
            except Exception as exc:
                detail = str(exc)
                tracker.reset()
            if detail and detail != last_detail:
                last_detail = detail
                recovery_detail = detail
                self._queue_mouse_status(
                    session_id,
                    state,
                    f"{status}：{detail}；F1 急停",
                )
            self._mouse_sleep(session_id, stop_event, 0.30)

    def _queue_mouse_board(
        self,
        session_id: int,
        board,
        side: str,
        grid,
        history_fen: str,
        moves: list[str],
        status: str,
    ) -> None:
        self.result_queue.put(
            (
                "mouse_board",
                (
                    session_id,
                    dict(board),
                    side,
                    grid,
                    history_fen,
                    list(moves),
                    status,
                ),
            )
        )

    @staticmethod
    def _known_new_game_board(
        candidate: dict[tuple[int, int], str],
        reference: dict[tuple[int, int], str],
        session_start_board: dict[tuple[int, int], str],
        standard_board: dict[tuple[int, int], str],
    ) -> bool:
        return (
            position_is_safe(candidate)
            and candidate != reference
            and (candidate == session_start_board or candidate == standard_board)
        )

    @staticmethod
    def _window_for_geometry(geometry) -> int:
        return window_at_point(geometry.point_for_square((4, 4)))

    def _wait_for_click_ready(
        self,
        session_id: int,
        stop_event: threading.Event,
        target_window: int,
        board,
        session_start_board,
        standard_board,
    ):
        pause_reason = ""
        while True:
            if foreground_window() != target_window:
                pause_reason = "游戏窗口切换"
                self._queue_mouse_status(
                    session_id,
                    AutomationState.WAITING_BOARD,
                    "游戏窗口已切换；返回棋盘后将自动继续，F1 急停",
                )
                self._mouse_sleep(session_id, stop_event, 0.20)
                continue
            if not user_input_is_idle(1.2):
                pause_reason = "用户输入"
                self._queue_mouse_status(
                    session_id,
                    AutomationState.WAITING_USER_IDLE,
                    "检测到用户正在操作；空闲 1.2 秒后自动继续，F1 急停",
                )
                self._mouse_sleep(session_id, stop_event, 0.10)
                continue
            current = self._capture_stable_mouse_board(
                session_id,
                stop_event,
                stable_frames=3,
                state=AutomationState.WAITING_BOARD,
                status="点击前正在重新确认棋盘",
            )
            candidate = current[0]
            if candidate == board:
                if (
                    foreground_window() == target_window
                    and user_input_is_idle(1.2)
                ):
                    if pause_reason:
                        self._log_mouse_recovery(session_id, pause_reason)
                    return "ready", current
                continue
            if self._known_new_game_board(
                candidate,
                board,
                session_start_board,
                standard_board,
            ):
                return "new_game", current
            self._queue_mouse_status(
                session_id,
                AutomationState.WAITING_BOARD,
                "盘面与思考前不一致，已暂停点击并等待安全重锁；F1 急停",
            )

    def _mouse_autoplay_worker(
        self,
        session_id: int,
        stop_event: threading.Event,
        assisted_side: str,
        current_side: str,
        movetime: int,
        requested_multipv: int,
    ) -> None:
        history_fen = ""
        move_history: list[str] = []
        visits: deque[str] = deque(maxlen=24)
        used_moves: dict[str, set[str]] = {}
        consecutive_checks = 0
        game_over = False
        session_start_turn = current_side
        standard_board, _ = parse_fen(START_FEN)
        session_start_board: dict[tuple[int, int], str] = {}
        target_window = 0

        try:
            self._mouse_sleep(session_id, stop_event, 0.75)
            self._queue_mouse_status(
                session_id,
                AutomationState.ACQUIRING,
                "正在连续识别三帧并锁定游戏窗口；F1 急停",
            )
            while True:
                board, grid, geometry = self._capture_stable_mouse_board(
                    session_id,
                    stop_event,
                    stable_frames=3,
                    state=AutomationState.ACQUIRING,
                    status="正在锁定稳定棋盘",
                )
                target_window = self._window_for_geometry(geometry)
                if target_window and foreground_window() == target_window:
                    break
                self._queue_mouse_status(
                    session_id,
                    AutomationState.WAITING_BOARD,
                    "已识别棋盘，但游戏窗口不在前台；切回后自动继续，F1 急停",
                )
                self._mouse_sleep(session_id, stop_event, 0.30)

            session_start_board = dict(board)
            history_fen = make_fen(board, current_side)
            visits.append(history_fen)
            self._queue_mouse_board(
                session_id,
                board,
                current_side,
                grid,
                history_fen,
                move_history,
                "棋盘和游戏窗口已锁定；自动对局运行中，F1 急停",
            )

            def adopt_new_game(candidate, new_grid, new_geometry) -> bool:
                nonlocal board, grid, geometry, target_window
                nonlocal current_side, history_fen, move_history, visits
                nonlocal used_moves, consecutive_checks, game_over
                nonlocal session_start_board
                new_target = self._window_for_geometry(new_geometry)
                if not new_target or foreground_window() != new_target:
                    self._queue_mouse_status(
                        session_id,
                        AutomationState.WAITING_BOARD,
                        "检测到新棋盘；等待游戏窗口回到前台后继续，F1 急停",
                    )
                    return False
                board = dict(candidate)
                grid = new_grid
                geometry = new_geometry
                target_window = new_target
                current_side = turn_for_new_game(
                    board,
                    standard_board,
                    session_start_turn,
                )
                session_start_board = dict(board)
                history_fen = make_fen(board, current_side)
                move_history = []
                visits = deque([history_fen], maxlen=24)
                used_moves = {}
                consecutive_checks = 0
                game_over = False
                self.logger.info(
                    "mouse autoplay new game reset session=%s side=%s fen=%s",
                    session_id,
                    current_side,
                    history_fen,
                )
                self._queue_mouse_board(
                    session_id,
                    board,
                    current_side,
                    grid,
                    history_fen,
                    move_history,
                    "已识别新对局并重置历史；自动接管继续运行，F1 急停",
                )
                return True

            while not self._mouse_autoplay_cancelled(session_id, stop_event):
                if game_over:
                    self._queue_mouse_status(
                        session_id,
                        AutomationState.WAITING_NEXT_GAME,
                        "本局已结束；自动接管保持开启并等待下一局，F1 急停",
                    )
                    candidate, next_grid, next_geometry = self._capture_stable_mouse_board(
                        session_id,
                        stop_event,
                        stable_frames=3,
                        accept=lambda item: item != board,
                        state=AutomationState.WAITING_NEXT_GAME,
                        status="正在等待新的稳定棋盘",
                    )
                    if adopt_new_game(candidate, next_grid, next_geometry):
                        continue
                    self._mouse_sleep(session_id, stop_event, 0.30)
                    continue

                if current_side != assisted_side:
                    self._queue_mouse_status(
                        session_id,
                        AutomationState.WAITING_OPPONENT,
                        "等待对手走子并进行双帧确认；F1 急停",
                    )

                    def acceptable_opponent_board(candidate) -> bool:
                        if candidate == board:
                            return False
                        transition = classify_board_transition(
                            board,
                            candidate,
                            current_side,
                        )
                        return (
                            transition.kind != TransitionKind.AMBIGUOUS
                            or self._known_new_game_board(
                                candidate,
                                board,
                                session_start_board,
                                standard_board,
                            )
                        )

                    next_board, grid, geometry = self._capture_stable_mouse_board(
                        session_id,
                        stop_event,
                        stable_frames=2,
                        allow_terminal=True,
                        accept=acceptable_opponent_board,
                        state=AutomationState.WAITING_BOARD,
                        status="画面暂时无法解释为对手的一步棋，正在安全重锁",
                    )
                    transition = classify_board_transition(
                        board,
                        next_board,
                        current_side,
                    )
                    if transition.kind == TransitionKind.AMBIGUOUS:
                        if adopt_new_game(next_board, grid, geometry):
                            continue
                        continue
                    opponent_move = transition.move
                    if opponent_move is None:
                        continue
                    self.logger.info(
                        "mouse autoplay observed opponent move=%s",
                        opponent_move,
                    )
                    move_history.append(opponent_move)
                    board = next_board
                    current_side = assisted_side
                    signature = make_fen(board, current_side)
                    visits.append(signature)
                    self._queue_mouse_board(
                        session_id,
                        board,
                        current_side,
                        grid,
                        history_fen,
                        move_history,
                        f"已识别对手走子 {opponent_move}；正在思考",
                    )
                    if transition.kind == TransitionKind.TERMINAL_MOVE:
                        game_over = True
                    continue

                signature = make_fen(board, current_side)
                visit_count = visits.count(signature)
                avoided = used_moves.get(signature, set()) if visit_count >= 2 else set()
                avoid_checks = consecutive_checks >= 2
                multipv = requested_multipv
                think_time = movetime
                if avoided:
                    multipv = max(multipv, 5)
                    think_time = max(think_time, 5000)
                if avoid_checks:
                    multipv = max(multipv, 12)
                    think_time = max(think_time, 5000)
                self._queue_mouse_status(
                    session_id,
                    AutomationState.THINKING,
                    f"自动思考中… {think_time / 1000:g} 秒，{multipv} 条候选；F1 急停",
                )
                direct_capture = find_direct_king_capture(board, current_side)
                if direct_capture is not None:
                    lines = [
                        AnalysisLine(
                            1,
                            0,
                            "mate",
                            1,
                            [direct_capture],
                            (1000, 0, 0),
                        )
                    ]
                    bestmove = direct_capture
                else:
                    lines, bestmove = self.engine.analyse(
                        signature,
                        think_time,
                        multipv,
                        history_fen=history_fen,
                        moves=move_history,
                    )
                    lines, replacement = prefer_fresh_winning_line(lines, set(avoided))
                    if replacement is not None:
                        bestmove = replacement
                    if avoid_checks:
                        lines, replacement = prefer_quiet_winning_line(
                            lines,
                            board,
                            current_side,
                        )
                        if replacement is not None:
                            bestmove = replacement
                if self._mouse_autoplay_cancelled(session_id, stop_event):
                    raise InterruptedError("用户已停止自动接管")
                if not lines or not bestmove or bestmove == "(none)":
                    game_over = True
                    self._queue_mouse_status(
                        session_id,
                        AutomationState.WAITING_NEXT_GAME,
                        "引擎确认当前没有合法着法；等待下一局，F1 急停",
                    )
                    continue
                reason = no_win_reason(lines[0])
                if reason is not None:
                    game_over = True
                    self._queue_mouse_status(
                        session_id,
                        AutomationState.WAITING_NEXT_GAME,
                        f"{reason}；已停止本局点击并等待下一局，F1 急停",
                    )
                    continue

                start, end = parse_move(bestmove)
                moving_piece = board.get(start)
                if moving_piece is None or piece_side(moving_piece) != assisted_side:
                    raise FatalAutomationError(
                        f"引擎首选着与确认盘面不一致：{bestmove}"
                    )
                before = dict(board)
                expected = apply_move(before, bestmove)
                move_text = describe_move(before, bestmove)
                click_attempts = 0
                move_finished = False
                new_game_adopted = False

                while not move_finished and not self._mouse_autoplay_cancelled(
                    session_id,
                    stop_event,
                ):
                    ready_kind, fresh = self._wait_for_click_ready(
                        session_id,
                        stop_event,
                        target_window,
                        before,
                        session_start_board,
                        standard_board,
                    )
                    fresh_board, grid, geometry = fresh
                    if ready_kind == "new_game":
                        new_game_adopted = adopt_new_game(
                            fresh_board,
                            grid,
                            geometry,
                        )
                        break
                    start_point = geometry.point_for_square(start)
                    end_point = geometry.point_for_square(end)
                    width, height = geometry.image_size
                    if any(
                        not (2 <= x < width - 2 and 2 <= y < height - 2)
                        for x, y in (start_point, end_point)
                    ):
                        self._queue_mouse_status(
                            session_id,
                            AutomationState.WAITING_BOARD,
                            "最新棋盘坐标越界，正在重新定位；F1 急停",
                        )
                        continue

                    self._queue_mouse_status(
                        session_id,
                        AutomationState.CLICKING,
                        f"正在代走：{move_text}；F1 急停",
                    )
                    first_click_sent = False
                    transaction_completed = False
                    try:
                        click_result = click_screen_move(
                            start_point,
                            end_point,
                            lambda: self._mouse_autoplay_cancelled(
                                session_id,
                                stop_event,
                            ),
                            guard=lambda: foreground_window() == target_window,
                        )
                        first_click_sent = click_result.first_click_sent
                        transaction_completed = click_result.completed
                        click_attempts += 1
                        self.logger.info(
                            "mouse autoplay click complete session=%s attempt=%s cursor_attempts=%s",
                            session_id,
                            click_attempts,
                            click_result.cursor_attempts,
                        )
                    except UserInterferenceError as exc:
                        first_click_sent = exc.first_click_sent
                        if first_click_sent:
                            click_attempts += 1
                        self.logger.info(
                            "mouse autoplay paused for user input session=%s first_click=%s reason=%s",
                            session_id,
                            first_click_sent,
                            exc,
                        )
                        self._queue_mouse_status(
                            session_id,
                            AutomationState.WAITING_USER_IDLE,
                            f"{exc}；空闲后自动恢复，F1 急停",
                        )
                        if not first_click_sent:
                            continue
                    except RecoverableAutomationError as exc:
                        first_click_sent = exc.first_click_sent
                        click_attempts += 1
                        self.logger.warning(
                            "mouse autoplay click retry session=%s attempt=%s first_click=%s reason=%s",
                            session_id,
                            click_attempts,
                            first_click_sent,
                            exc,
                        )
                        self._queue_mouse_status(
                            session_id,
                            AutomationState.WAITING_BOARD,
                            f"{exc}；正在重新锁定，F1 急停",
                        )
                        if not first_click_sent and click_attempts < 3:
                            self._mouse_sleep(session_id, stop_event, 0.40)
                            continue

                    self._queue_mouse_status(
                        session_id,
                        AutomationState.CONFIRMING,
                        "已发送落子，正在确认游戏画面；F1 急停",
                    )
                    if transaction_completed or first_click_sent:
                        self._mouse_sleep(session_id, stop_event, 0.45)

                    opponent_side = "b" if current_side == "w" else "w"

                    def plausible_confirmation(candidate) -> bool:
                        confirmation = classify_click_confirmation(
                            before,
                            expected,
                            candidate,
                            opponent_side,
                        )
                        return (
                            confirmation.kind != ConfirmationKind.AMBIGUOUS
                            or self._known_new_game_board(
                                candidate,
                                before,
                                session_start_board,
                                standard_board,
                            )
                        )

                    allow_unchanged = click_attempts < 3
                    confirmed_board, grid, geometry = self._capture_stable_mouse_board(
                        session_id,
                        stop_event,
                        stable_frames=2 if allow_unchanged else 3,
                        allow_terminal=True,
                        accept=(
                            plausible_confirmation
                            if allow_unchanged
                            else lambda item: item != before and plausible_confirmation(item)
                        ),
                        state=AutomationState.WAITING_BOARD,
                        status=(
                            "正在确认落子结果"
                            if allow_unchanged
                            else "连续三次未能完成点击，已停止重试并等待盘面变化"
                        ),
                    )
                    if confirmed_board == before:
                        self.logger.warning(
                            "mouse autoplay move not applied session=%s attempt=%s move=%s",
                            session_id,
                            click_attempts,
                            bestmove,
                        )
                        self._queue_mouse_status(
                            session_id,
                            AutomationState.WAITING_BOARD,
                            f"落子未生效（第 {click_attempts}/3 次），重新锁盘后再试；F1 急停",
                        )
                        self._mouse_sleep(session_id, stop_event, 0.55)
                        continue
                    if self._known_new_game_board(
                        confirmed_board,
                        before,
                        session_start_board,
                        standard_board,
                    ):
                        new_game_adopted = adopt_new_game(
                            confirmed_board,
                            grid,
                            geometry,
                        )
                        break

                    used_moves.setdefault(signature, set()).add(bestmove)
                    move_history.append(bestmove)
                    try:
                        checking = move_gives_check(before, assisted_side, bestmove)
                    except ValueError:
                        checking = False
                    consecutive_checks = (
                        consecutive_checks + 1
                        if checking and not is_king_capture_move(before, bestmove)
                        else 0
                    )

                    if confirmed_board == expected:
                        board = confirmed_board
                        current_side = opponent_side
                        visits.append(make_fen(board, current_side))
                        if position_is_terminal(board):
                            game_over = True
                        self._queue_mouse_board(
                            session_id,
                            board,
                            current_side,
                            grid,
                            history_fen,
                            move_history,
                            (
                                f"已确认绝杀：{move_text}；保持接管并等待下一局"
                                if game_over
                                else f"已确认代走：{move_text}；等待对手"
                            ),
                        )
                        move_finished = True
                        continue

                    confirmation = classify_click_confirmation(
                        before,
                        expected,
                        confirmed_board,
                        opponent_side,
                    )
                    if confirmation.kind == ConfirmationKind.AMBIGUOUS:
                        self._queue_mouse_status(
                            session_id,
                            AutomationState.WAITING_BOARD,
                            "落子后盘面暂时无法解释，保持待机并继续重锁；F1 急停",
                        )
                        continue
                    fast_reply = confirmation.move
                    if fast_reply is None:
                        continue
                    self.logger.info(
                        "mouse autoplay observed fast opponent reply=%s",
                        fast_reply,
                    )
                    move_history.append(fast_reply)
                    board = confirmed_board
                    current_side = assisted_side
                    visits.append(make_fen(board, current_side))
                    self._queue_mouse_board(
                        session_id,
                        board,
                        current_side,
                        grid,
                        history_fen,
                        move_history,
                        f"已确认代走并识别对手秒回 {fast_reply}；正在思考",
                    )
                    if confirmation.kind == ConfirmationKind.TERMINAL_REPLY:
                        game_over = True
                    move_finished = True

                if new_game_adopted:
                    continue

        except InterruptedError:
            self.result_queue.put(
                (
                    "mouse_done",
                    (
                        session_id,
                        "silent",
                        "自动接管已停止",
                        "已停止点击并保留最后确认的局面。",
                    ),
                )
            )
        except (FatalAutomationError, EngineError) as exc:
            self.logger.exception("mouse autoplay fatal error")
            self.result_queue.put(
                (
                    "mouse_done",
                    (
                        session_id,
                        "error",
                        "自动接管无法继续",
                        f"{exc}\n\n这是不可自动恢复的组件或引擎错误。",
                    ),
                )
            )
        except Exception as exc:
            self.logger.exception("mouse autoplay unexpected fatal error")
            self.result_queue.put(
                (
                    "mouse_done",
                    (
                        session_id,
                        "error",
                        "自动接管发生意外错误",
                        f"{exc}\n\n已停止点击并保留最后确认的局面。",
                    ),
                )
            )

    def _finish_mouse_autoplay(
        self,
        session_id: int,
        level: str,
        title: str,
        detail: str,
    ) -> None:
        if session_id != self.mouse_auto_session_id:
            return
        was_stopping = self.mouse_auto_state == AutomationState.STOPPING
        self.mouse_auto_stop_event.set()
        self.mouse_auto_running = False
        self.mouse_auto_state = AutomationState.IDLE
        self.mouse_auto_thread = None
        restart = self.mouse_auto_pending_start and not self.closing
        self.mouse_auto_pending_start = False
        if self.mouse_auto_button is not None:
            self.mouse_auto_button.configure(text="自动接管鼠标", state="normal")
        if not restart:
            try:
                self.root.deiconify()
                if self.always_on_top_var.get():
                    self.root.attributes("-topmost", True)
                    self.root.lift()
            except tk.TclError:
                return
        self.status_var.set(detail)
        if level == "error" and not was_stopping:
            messagebox.showerror(title, detail)
        if restart:
            self.root.after(120, self._start_mouse_autoplay)
        elif not self.closing and self.auto_analysis_var.get():
            self._schedule_auto_analysis(350)

    def _on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.mouse_auto_pending_start = False
        self.mouse_auto_stop_event.set()
        self._invalidate_analysis()
        try:
            self.engine.close()
        finally:
            logger = logging.getLogger("xiangqi_ai")
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            self.root.destroy()


def uninstall_smoke_session() -> None:
    """Hidden, non-clicking integration fixture for installer verification."""
    root = tk.Tk()
    root.withdraw()
    app = XiangqiApp(root)
    app.auto_analysis_var.set(False)
    app._invalidate_analysis()
    root.withdraw()
    app.engine.start()
    if "--seed-uninstall-data" in sys.argv:
        from recognition import TemplatePieceRecognizer

        learner = TemplatePieceRecognizer()
        learner.learn("R", [0.0] * 256)
        for handler in logging.getLogger("xiangqi_ai").handlers:
            if isinstance(handler, RotatingFileHandler):
                for _ in range(3):
                    app.logger.info("uninstall test rollover")
                    handler.doRollover()
    app.logger.info("UNINSTALL_SMOKE_READY")
    if "--runtime-smoke-session" in sys.argv:
        from build_checks import save_report

        save_report("runtime-smoke.json", {
            "pid": os.getpid(), "engine_pid": app.engine.process.pid,
            "resource_base": str(app_base()), "log_path": str(app.log_path),
        })
    if "--uninstall-smoke-thinking" in sys.argv:
        def think_until_closed() -> None:
            try:
                app.engine.analyse(PUZZLE_FEN, 60000, 1)
            except EngineError:
                if not app.closing:
                    app.logger.exception("unexpected uninstall fixture engine failure")

        threading.Thread(target=think_until_closed, daemon=True).start()
    root.mainloop()


def main() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    XiangqiApp(root)
    root.mainloop()


def engine_self_test() -> int:
    engine = PikafishEngine(find_engine(), threads=2, hash_mb=64)
    try:
        lines, bestmove = engine.analyse(PUZZLE_FEN, 800, 1)
        return 0 if lines and bestmove and lines[0].wdl is not None else 2
    except Exception:
        return 3
    finally:
        engine.close()


def no_win_engine_self_test() -> int:
    engine = PikafishEngine(find_engine(), threads=2, hash_mb=64)
    try:
        lines, bestmove = engine.analyse(NO_WIN_TEST_FEN, 350, 1)
        if not lines or not bestmove:
            return 17
        line = lines[0]
        return (
            0
            if line.wdl == (0, 1000, 0)
            and no_win_reason(line) is not None
            and score_text(line, "w") == "和棋 0.00"
            else 18
        )
    except Exception:
        return 19
    finally:
        engine.close()


def anti_loop_engine_self_test() -> int:
    """Regression for the reported king/knight four-ply loop."""
    engine = PikafishEngine(find_engine(), threads=8, hash_mb=256)
    history = ["d1d0", "e3f1", "d0d1", "f1e3"]
    try:
        lines, bestmove = engine.analyse(
            LOOP_TEST_FEN,
            5000,
            5,
            history_fen=LOOP_TEST_FEN,
            moves=history,
        )
        promoted, replacement = prefer_fresh_winning_line(lines, {"d1d0"})
        if not lines or not bestmove:
            return 42
        if not promoted or promoted[0].best_move == "d1d0":
            return 43
        if not _line_keeps_winning_chances(promoted[0]):
            return 44
        return 0
    except Exception:
        return 45
    finally:
        engine.close()


def anti_check_policy_self_test() -> int:
    try:
        board, side = parse_fen("4k4/9/9/9/9/9/9/9/R8/3K5 w - - 0 1")
        long_mate = [
            AnalysisLine(1, 32, "mate", 10, ["a1a9"], (1000, 0, 0)),
            AnalysisLine(2, 32, "cp", 650, ["a1a2"], (1000, 0, 0)),
        ]
        promoted, move = prefer_quiet_winning_line(long_mate, board, side)
        if move != "a1a2" or promoted[0].best_move != "a1a2":
            return 56
        short_mate = [
            AnalysisLine(1, 32, "mate", 3, ["a1a9"], (1000, 0, 0)),
            AnalysisLine(2, 32, "cp", 650, ["a1a2"], (1000, 0, 0)),
        ]
        retained, move = prefer_quiet_winning_line(short_mate, board, side)
        if move is not None or retained[0].best_move != "a1a9":
            return 57
        return 0
    except Exception:
        return 58


def outcome_guard_self_test() -> int:
    root: tk.Tk | None = None
    app: XiangqiApp | None = None
    original_warning = messagebox.showwarning
    warnings: list[tuple[str, str]] = []
    try:
        messagebox.showwarning = lambda title, body: warnings.append((title, body))
        root = tk.Tk()
        root.withdraw()
        app = XiangqiApp(root)
        app.auto_analysis_var.set(False)
        app._invalidate_analysis()
        app.board, app.side = parse_fen(NO_WIN_TEST_FEN)
        app.assisted_side = app.side
        app.analysis_board = dict(app.board)
        app.analysis_side = app.side
        line = AnalysisLine(1, 245, "cp", 0, ["f4f2"], (0, 1000, 0))
        app._show_analysis([line], "f4f2")
        signature = make_fen(app.board, app.side)
        blocked = app.follow_blocked_positions.get(signature)
        if (
            not app.follow_best_var.get()
            or blocked is None
            or blocked[0] != "no_win"
            or not warnings
            or "建议认输" not in warnings[-1][0]
        ):
            return 20

        app.completed_position_history.clear()
        app.completed_position_history.extend([signature, signature])
        if not app._record_completed_follow_position() or not app.follow_best_var.get():
            return 21
        blocked = app.follow_blocked_positions.get(signature)
        if (
            blocked is None
            or blocked[0] != "repetition"
            or len(warnings) < 2
            or "3 次" not in warnings[-1][1]
        ):
            return 22

        app.board, app.side = parse_fen(LOOP_TEST_FEN)
        app._position_changed(schedule_analysis=False, reset_repetition=True)
        app.analysis_board = dict(app.board)
        app.analysis_side = app.side
        winning_line = AnalysisLine(1, 39, "mate", 15, ["d1d0"], (1000, 0, 0))
        app._show_analysis([winning_line], "d1d0")
        winning_signature = make_fen(app.board, app.side)
        app.completed_position_history.extend([winning_signature, winning_signature])
        if not app._record_completed_follow_position():
            return 46
        blocked = app.follow_blocked_positions.get(winning_signature)
        if (
            blocked is None
            or blocked[0] != "repetition_win"
            or warnings[-1][0] != "循环已停止"
            or "不能把它误报成无胜" not in warnings[-1][1]
        ):
            return 47
        return 0
    except Exception:
        return 23
    finally:
        messagebox.showwarning = original_warning
        if app is not None:
            app._on_close()
        elif root is not None:
            root.destroy()


def recognition_self_test(image_path: str) -> int:
    if Image is None or PieceRecognizer is None:
        return 4
    try:
        recognizer = PieceRecognizer()
        _, detections = recognizer.recognize(Image.open(image_path))
        geometry = recognizer.last_geometry
        width, height = geometry.image_size if geometry is not None else (0, 0)
        corners = (
            [
                geometry.point_for_square(square)
                for square in ((0, 0), (8, 0), (0, 9), (8, 9))
            ]
            if geometry is not None
            else []
        )
        return (
            0
            if recognizer.last_backend == "onnx"
            and detections
            and all(item.piece for item in detections)
            and geometry is not None
            and all(0 <= x < width and 0 <= y < height for x, y in corners)
            else 5
        )
    except Exception:
        return 6


def auto_analysis_self_test() -> int:
    root: tk.Tk | None = None
    app: XiangqiApp | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = XiangqiApp(root)
        app.time_var.set(350)
        app._schedule_auto_analysis(10)

        def wait_for(predicate, timeout: float = 8.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                root.update()
                if predicate():
                    return True
                time.sleep(0.02)
            return False

        first_board = dict(app.board)
        if not wait_for(
            lambda: bool(app.analysis_lines)
            and app.analysis_board == first_board
            and not app.analysis_running
        ):
            return 7
        app.tree.selection_set("0")
        app.apply_selected_move()
        second_board = dict(app.board)
        if second_board == first_board:
            return 8
        if not wait_for(
            lambda: bool(app.analysis_lines)
            and app.analysis_board == second_board
            and not app.analysis_running
        ):
            return 9
        return 0
    except Exception:
        return 10
    finally:
        if app is not None:
            app._on_close()
        elif root is not None:
            root.destroy()


def follow_best_self_test() -> int:
    root: tk.Tk | None = None
    app: XiangqiApp | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = XiangqiApp(root)
        app.time_var.set(350)
        app._schedule_auto_analysis(10)

        def wait_for(predicate, timeout: float = 8.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                root.update()
                if predicate():
                    return True
                time.sleep(0.02)
            return False

        initial_board = dict(app.board)
        initial_side = app.side
        if not wait_for(
            lambda: app._current_analysis_line() is not None
            and not app.analysis_running
        ):
            return 11
        line = app._current_analysis_line()
        if len(line.pv) < 2:
            return 12
        expected_after_best = apply_move(initial_board, line.best_move)
        opponent_side = "b" if initial_side == "w" else "w"
        opponent_square, target = parse_move(line.pv[1])
        if piece_side(expected_after_best.get(opponent_square, "P")) != opponent_side:
            return 12
        if not app._auto_follow_recommendation(opponent_square):
            return 12
        if (
            app.board != expected_after_best
            or app.side != opponent_side
            or app.selected_square != opponent_square
            or not app.follow_move_pending
        ):
            return 13
        app._move_selected_to(target)
        completed_board = dict(app.board)
        if (
            app.side != initial_side
            or app.follow_move_pending
            or app.engine_move_history != [line.best_move, line.pv[1]]
        ):
            return 14
        if not wait_for(
            lambda: app.analysis_board == completed_board
            and bool(app.analysis_lines)
            and not app.analysis_running
        ):
            return 15
        return 0
    except Exception:
        return 16
    finally:
        if app is not None:
            app._on_close()
        elif root is not None:
            root.destroy()


def follow_recovery_self_test() -> int:
    """Regression: a prior no-win warning must not disable next puzzle sync."""
    root: tk.Tk | None = None
    app: XiangqiApp | None = None
    original_warning = messagebox.showwarning
    try:
        messagebox.showwarning = lambda *_args, **_kwargs: None
        root = tk.Tk()
        root.withdraw()
        app = XiangqiApp(root)
        app.auto_analysis_var.set(False)
        app._invalidate_analysis()

        app.board, app.side = parse_fen(NO_WIN_TEST_FEN)
        app._position_changed(schedule_analysis=False, reset_repetition=True)
        app.analysis_board = dict(app.board)
        app.analysis_side = app.side
        draw_line = AnalysisLine(1, 245, "cp", 0, ["f4f2"], (0, 1000, 0))
        app._show_analysis([draw_line], "f4f2")
        if not app.follow_best_var.get():
            return 24

        before_fen = "9/5k3/9/9/2b3b2/9/8P/5A3/4AK3/5R3 w - - 0 1"
        app.board, app.side = parse_fen(before_fen)
        app._position_changed(schedule_analysis=False, reset_repetition=True)
        app.analysis_board = dict(app.board)
        app.analysis_side = app.side
        win_line = AnalysisLine(1, 80, "mate", 12, ["f0g0"], (1000, 0, 0))
        app._show_analysis([win_line], "f0g0")
        if not app.follow_best_var.get():
            return 25
        if make_fen(app.board, app.side) in app.follow_blocked_positions:
            return 26

        if not app._auto_follow_recommendation((2, 5)):
            return 27
        if app.board.get((6, 0)) != "R" or (5, 0) in app.board:
            return 28
        app._move_selected_to((4, 7))
        if (
            app.board.get((6, 0)) != "R"
            or app.board.get((4, 7)) != "b"
            or (2, 5) in app.board
            or app.side != "w"
        ):
            return 29
        if "已补录上一手" not in app.status_var.get():
            return 30
        return 0
    except Exception:
        return 31
    finally:
        messagebox.showwarning = original_warning
        if app is not None:
            app._on_close()
        elif root is not None:
            root.destroy()


def topmost_self_test() -> int:
    root: tk.Tk | None = None
    app: XiangqiApp | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = XiangqiApp(root)
        app.auto_analysis_var.set(False)
        app._invalidate_analysis()
        if not bool(root.attributes("-topmost")):
            return 32
        app.always_on_top_var.set(False)
        app._topmost_toggled()
        if bool(root.attributes("-topmost")):
            return 33
        app.always_on_top_var.set(True)
        app._topmost_toggled()
        if not bool(root.attributes("-topmost")):
            return 34
        return 0
    except Exception:
        return 35
    finally:
        if app is not None:
            app._on_close()
        elif root is not None:
            root.destroy()


def orientation_self_test() -> int:
    root: tk.Tk | None = None
    app: XiangqiApp | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = XiangqiApp(root)
        app.auto_analysis_var.set(False)
        app._invalidate_analysis()
        if app._canvas_point((4, 9))[1] != app.Y0:
            return 50
        app.player_side_var.set("b")
        app._player_side_changed()
        if app.assisted_side != "b":
            return 51
        if app._canvas_point((4, 9))[1] != app.Y0 + 9 * app.CELL:
            return 52
        event = type(
            "BoardEvent",
            (),
            {"x": app.X0 + 8 * app.CELL, "y": app.Y0},
        )()
        if app._nearest_square(event) != (0, 0):
            return 53
        if "黑方在下" not in app.orientation_var.get():
            return 54
        return 0
    except Exception:
        return 55
    finally:
        if app is not None:
            app._on_close()
        elif root is not None:
            root.destroy()


def direct_king_capture_self_test() -> int:
    root: tk.Tk | None = None
    app: XiangqiApp | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = XiangqiApp(root)
        app.auto_analysis_var.set(False)
        app._invalidate_analysis()
        app.board, app.side = parse_fen(DIRECT_CAPTURE_TEST_FEN)
        app._position_changed(schedule_analysis=False, reset_repetition=True)
        generation = app.analysis_generation
        app._launch_analysis(generation, manual=True)
        if not app.analysis_lines or app.analysis_lines[0].best_move != "e8f8":
            return 36
        if app.analysis_lines[0].score_type != "mate" or app.analysis_lines[0].score != 1:
            return 37
        values = app.tree.item("0", "values")
        if "吃将" not in str(values) or "立即" not in str(values):
            return 38
        if "立即吃将" not in app.status_var.get():
            return 39
        app.apply_selected_move()
        if app.board.get((5, 8)) != "R" or "k" in app.board.values():
            return 40
        return 0
    except Exception:
        return 41
    finally:
        if app is not None:
            app._on_close()
        elif root is not None:
            root.destroy()


if __name__ == "__main__":
    if "--runtime-probe" in sys.argv:
        from build_checks import runtime_probe

        raise SystemExit(runtime_probe(sys.modules[__name__]))
    if "--automation-self-test" in sys.argv:
        from build_checks import automation_probe

        raise SystemExit(automation_probe())
    if "--help-self-test" in sys.argv:
        from build_checks import help_probe

        raise SystemExit(help_probe(sys.modules[__name__]))
    if "--log-stress-self-test" in sys.argv:
        from build_checks import log_stress_probe

        raise SystemExit(log_stress_probe())
    if "--uninstall-smoke-session" in sys.argv or "--runtime-smoke-session" in sys.argv:
        uninstall_smoke_session()
        raise SystemExit(0)
    if "--engine-self-test" in sys.argv:
        raise SystemExit(engine_self_test())
    if "--no-win-engine-self-test" in sys.argv:
        raise SystemExit(no_win_engine_self_test())
    if "--anti-loop-engine-self-test" in sys.argv:
        raise SystemExit(anti_loop_engine_self_test())
    if "--anti-check-policy-self-test" in sys.argv:
        raise SystemExit(anti_check_policy_self_test())
    if "--outcome-guard-self-test" in sys.argv:
        raise SystemExit(outcome_guard_self_test())
    if "--recognition-self-test" in sys.argv:
        index = sys.argv.index("--recognition-self-test")
        image_path = sys.argv[index + 1] if index + 1 < len(sys.argv) else ""
        raise SystemExit(recognition_self_test(image_path))
    if "--auto-analysis-self-test" in sys.argv:
        raise SystemExit(auto_analysis_self_test())
    if "--follow-best-self-test" in sys.argv:
        raise SystemExit(follow_best_self_test())
    if "--follow-recovery-self-test" in sys.argv:
        raise SystemExit(follow_recovery_self_test())
    if "--topmost-self-test" in sys.argv:
        raise SystemExit(topmost_self_test())
    if "--orientation-self-test" in sys.argv:
        raise SystemExit(orientation_self_test())
    if "--direct-king-capture-self-test" in sys.argv:
        raise SystemExit(direct_king_capture_self_test())
    main()
