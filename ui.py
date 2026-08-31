"""Native desktop presentation. Chess and automation decisions stay in app/core."""
from __future__ import annotations

from dataclasses import dataclass
import math
import tkinter as tk
from tkinter import font as tkfont, ttk

from core import FILES, PIECE_NAMES, PUZZLE_FEN, START_FEN, describe_move, score_text
from ui_widgets import RoundedPanel, ToggleSwitch, animate_board, install_round_styles

BG = "#F6F5F1"
PANEL = "#FFFEFA"
PANEL_2 = "#F5F6EF"
TEXT = "#263E35"
MUTED = "#7C877F"
BORDER = "#E7E9E0"
ACCENT = "#285C49"
ACCENT_SOFT = "#EDF3EB"
RED = "#B34E42"
BLACK = "#384B41"
BOARD = "#F0E6D1"
GRID = "#B7A383"
FONT = "Microsoft YaHei UI"


@dataclass(frozen=True)
class BoardLayout:
    width: int
    height: int
    cell: int
    x0: float
    y0: float

    @classmethod
    def fit(cls, width: int, height: int):
        width, height = max(1, width), max(1, height)
        cell = max(1, int(min(max(1, width - 24) / 9.4, max(1, height - 20) / 10.4)))
        return cls(width, height, cell, (width - 8 * cell) / 2, (height - 9 * cell) / 2)


def configure_styles(root):
    scale = max(1.0, min(2.0, root.winfo_fpixels("1i") / 96))
    px = lambda value: round(value * scale)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        tkfont.nametofont(name, root=root).configure(family=FONT, size=-px(14))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FONT, -px(14)), foreground=TEXT, background=BG)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Soft.TFrame", background=PANEL_2)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Panel.TLabel", background=PANEL)
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=(FONT, -px(12)))
    style.configure("Section.TLabel", background=PANEL, font=(FONT, -px(16), "bold"))
    style.configure("TButton", background=PANEL, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=PANEL, darkcolor=PANEL, borderwidth=1,
                    padding=(px(14), px(9)), relief="flat")
    style.map("TButton", background=[("disabled", PANEL_2), ("pressed", "#E7EDE8"), ("active", "#F0F5F1")],
              foreground=[("disabled", "#A0ABA4")], bordercolor=[("focus", ACCENT), ("active", "#B6C8BC")])
    style.configure("Accent.TButton", background=ACCENT, foreground="white", bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, font=(FONT, -px(14), "bold"))
    style.map("Accent.TButton", background=[("disabled", "#C8D8CD"), ("pressed", "#19523F"), ("active", "#2D8868")],
              foreground=[("disabled", "#F5F8F6"), ("!disabled", "white")],
              bordercolor=[("focus", "#133F30"), ("!focus", ACCENT)])
    style.configure("Small.TButton", padding=(px(11), px(6)), font=(FONT, -px(12)))
    style.configure("Danger.TButton", foreground=RED)
    style.configure("TCheckbutton", background=PANEL, foreground=TEXT, padding=(0, px(3)))
    style.map("TCheckbutton", background=[("active", PANEL)], indicatorbackground=[("selected", ACCENT)],
              indicatorforeground=[("selected", "white")])
    style.configure("Footer.TCheckbutton", background=BG, font=(FONT, -px(12)))
    style.map("Footer.TCheckbutton", background=[("active", BG)])
    for name, fg, selected in (("Segment", TEXT, ACCENT_SOFT), ("RedPiece", RED, "#F9E9E3"),
                                ("BlackPiece", BLACK, ACCENT_SOFT)):
        style.layout(name + ".TRadiobutton", style.layout("Toolbutton"))
        style.configure(name + ".TRadiobutton", background=PANEL_2, foreground=fg,
                        padding=(px(11), px(7)), relief="flat", borderwidth=1, bordercolor=BORDER,
                        lightcolor=PANEL_2, darkcolor=PANEL_2,
                        font=(FONT, -px(13)) if name == "Segment" else ("SimSun", -px(23), "bold"))
        style.map(name + ".TRadiobutton", background=[("selected", selected), ("active", "#F0F4EF")],
                  bordercolor=[("selected", ACCENT if name == "Segment" else fg), ("focus", ACCENT)],
                  relief=[("selected", "flat")])
    style.configure("TEntry", fieldbackground=PANEL_2, bordercolor=BORDER, padding=px(8))
    style.configure("TCombobox", fieldbackground=PANEL_2, background=PANEL_2, arrowcolor=MUTED,
                    bordercolor=BORDER, lightcolor=PANEL_2, darkcolor=PANEL_2, padding=px(6))
    style.map("TCombobox", fieldbackground=[("readonly", PANEL_2)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", PANEL_2)], selectforeground=[("readonly", TEXT)])
    style.configure("TSpinbox", fieldbackground=PANEL_2, background=PANEL_2, arrowcolor=MUTED,
                    bordercolor=BORDER, padding=px(6))
    style.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=(0, 0, 0, px(12)),
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
    style.configure("TNotebook.Tab", background=PANEL_2, foreground=MUTED, borderwidth=0,
                    padding=(px(21), px(10)), font=(FONT, -px(14), "bold"),
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
    style.map("TNotebook.Tab", background=[("selected", ACCENT_SOFT), ("active", "#F0F5F1")],
              foreground=[("selected", ACCENT)], expand=[("selected", (0, 0, 0, 0))],
              padding=[("selected", (px(21), px(10))), ("!selected", (px(21), px(10)))])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    borderwidth=0, rowheight=px(35), font=(FONT, -px(13)))
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview.Heading", background=PANEL_2, foreground=MUTED, relief="flat",
                    borderwidth=0, padding=(px(6), px(9)), font=(FONT, -px(12)))
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", ACCENT)])
    style.map("Treeview.Heading", background=[("active", "#EAF0EB")])
    style.configure("Vertical.TScrollbar", background="#D5DFD7", troughcolor=PANEL,
                    borderwidth=0, arrowsize=px(8), arrowcolor=MUTED, relief="flat",
                    bordercolor=PANEL, lightcolor="#D5DFD7", darkcolor="#D5DFD7")
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns",
        "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=ACCENT_SOFT,
                    borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(3))
    style.layout("Horizontal.TProgressbar", [("Horizontal.Progressbar.trough", {"sticky": "nswe",
        "children": [("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
    style.configure("TNotebook.Tab", background=PANEL, padding=(px(15), px(10)),
                    font=(FONT, -px(13)), foreground=MUTED)
    style.map("TNotebook.Tab", background=[("selected", ACCENT_SOFT), ("active", PANEL_2)],
              foreground=[("selected", ACCENT)],
              padding=[("selected", (px(15), px(10))), ("!selected", (px(15), px(10)))])
    style.configure("Segment.TRadiobutton", borderwidth=0, padding=(px(9), px(6)))
    style.configure("Treeview", rowheight=px(30), font=(FONT, -px(12)))
    install_round_styles(root, scale, {"bg": BG, "panel": PANEL, "line": BORDER,
                                     "green": ACCENT, "muted": MUTED})
    return scale


class ScrollPage(ttk.Frame):
    """Scrollable sidebar content; native text/tree scrolling keeps priority."""

    def __init__(self, parent, root):
        super().__init__(parent, style="Panel.TFrame")
        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, borderwidth=0, width=1, height=1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(self.canvas, style="Panel.TFrame", padding=(0, 0, 8, 0))
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.content.bind("<Configure>", self._fit)
        self.canvas.bind("<Configure>", self._fit)
        root.bind("<MouseWheel>", self._wheel, add="+")
        root.bind("<FocusIn>", self._focus, add="+")

    def _fit(self, _event=None):
        width = self.canvas.winfo_width()
        height = max(self.canvas.winfo_height(), self.content.winfo_reqheight())
        if height > self.canvas.winfo_height() + 1:
            if not self.scrollbar.winfo_manager():
                self.scrollbar.pack(side="right", fill="y", before=self.canvas)
        elif self.scrollbar.winfo_manager():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)
        self.canvas.itemconfigure(self.window, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _owns(self, widget):
        return (widget in (self.content, self.canvas) or str(widget).startswith(str(self.content) + ".")) and self.winfo_ismapped()

    def _wheel(self, event):
        if self._owns(event.widget) and not isinstance(event.widget, (tk.Text, ttk.Treeview, ttk.Combobox, ttk.Spinbox)):
            if self.content.winfo_height() > self.canvas.winfo_height():
                if event.delta:
                    steps = max(1, abs(event.delta) // 120) * (-1 if event.delta > 0 else 1)
                    self.canvas.yview_scroll(steps, "units")
                return "break"

    def _focus(self, event):
        if not self._owns(event.widget):
            return
        top = event.widget.winfo_rooty() - self.content.winfo_rooty()
        visible_top = self.canvas.canvasy(0)
        bottom = top + event.widget.winfo_height()
        height = self.canvas.winfo_height()
        if top < visible_top:
            self.canvas.yview_moveto(top / max(1, self.content.winfo_height()))
        elif bottom > visible_top + height:
            self.canvas.yview_moveto((bottom - height + 8) / max(1, self.content.winfo_height()))


class WorkspaceView:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.scale = app.ui_scale
        self.p = lambda n: round(n * self.scale)
        self.move_var = tk.StringVar(value="等待分析")
        self.eval_var = tk.StringVar(value="导入棋盘或开始摆局，查看推荐走法")
        self.state_var = tk.StringVar(value="准备就绪")
        self.turn_var = tk.StringVar()
        self.tool_hint = tk.StringVar()
        self.time_label = tk.StringVar(value="3 秒")
        self.background_var = tk.BooleanVar(value=True)
        self.motion_var = tk.BooleanVar(value=True)
        self.coordinates_var = tk.BooleanVar(value=True)
        self.arrow_var = tk.BooleanVar(value=True)
        self.root._quiet_motion = self.motion_var
        self.coordinate_var = tk.StringVar(value="导入棋盘，查看推荐走法")
        self.depth_var = tk.StringVar(value="本地引擎 · 完全离线")
        self.top_player_var = tk.StringVar(value="对方 · 黑方")
        self.bottom_player_var = tk.StringVar(value="我方 · 红方")
        self._animation_job = None
        self._last_board = {}
        self._last_points = {}
        self._last_layout = None
        self.settings_window = None
        self._resizing = None
        self._layout_refresh = None
        self._busy = False
        self._build_header()
        self._build_footer()
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=self.p(24), pady=(0, self.p(4)))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=self.p(400))
        self.board_card = self._card(body)
        self.board_card.grid(row=0, column=0, sticky="nsew", padx=(0, self.p(20)))
        self._build_board()
        self.sidebar = self._card(body)
        self.sidebar.grid(row=0, column=1, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.rowconfigure(0, weight=1)
        self.sidebar.columnconfigure(0, weight=1)
        self.tabs = ttk.Notebook(self.sidebar)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=self.p(16), pady=(self.p(12), self.p(4)))
        engine_note = ttk.Frame(self.sidebar, style="Panel.TFrame")
        engine_note.grid(row=1, column=0, sticky="ew", padx=self.p(20), pady=self.p(12))
        self._label(engine_note, "PIKAFISH / ONNX", "Muted.TLabel", font=(FONT, -self.p(10))).pack(side="left")
        self._label(engine_note, "本地运行 · 完全离线", "Muted.TLabel", font=(FONT, -self.p(10))).pack(side="right")
        self.pages = [ScrollPage(self.tabs, self.root) for _ in range(3)]
        for page, title in zip(self.pages, ("引擎分析", "摆局工具", "FEN")):
            self.tabs.add(page, text=title)
        self.tabs.enable_traversal()
        self._build_analysis(self.pages[0].content)
        self._build_editor(self.pages[1].content)
        self._build_fen(self.pages[2].content)
        self.tabs.bind("<<NotebookTabChanged>>", self._schedule_layout_refresh)
        app.tool.trace_add("write", self._tool_changed)
        app.side_var.trace_add("write", self._turn_changed)
        app.time_var.trace_add("write", self._time_changed)
        app.player_side_var.trace_add("write", self._players_changed)
        self._tool_changed()
        self._turn_changed()
        self._install_icon()

    def _card(self, parent):
        return RoundedPanel(parent, fill=PANEL, outside=BG, radius=self.p(16))

    def _label(self, parent, text="", style="Panel.TLabel", **kwargs):
        return ttk.Label(parent, text=text, style=style, **kwargs)

    def _section(self, parent, title, subtitle=None):
        self._label(parent, title, "Section.TLabel").pack(anchor="w", pady=(self.p(6), self.p(5)))
        if subtitle:
            self._label(parent, subtitle, "Muted.TLabel", wraplength=self.p(365), justify="left").pack(
                anchor="w", pady=(0, self.p(14)))

    def _build_header(self):
        app, p = self.app, self.p
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=p(24), pady=(p(14), p(12)))
        brand_row = ttk.Frame(header)
        brand_row.pack(fill="x")
        mark = tk.Canvas(brand_row, width=p(44), height=p(44), bg=BG, highlightthickness=0)
        mark.pack(side="left", padx=(0, p(12)))
        mark.create_rectangle(p(5), p(4), p(39), p(40), fill=ACCENT, outline="")
        mark.create_oval(p(8), p(7), p(36), p(37), outline="#8BAA90", width=1)
        mark.create_text(p(22), p(21), text="象", fill="#FBF8E9", font=("KaiTi", -p(28)))
        brand = ttk.Frame(brand_row)
        brand.pack(side="left")
        ttk.Label(brand, text="象棋 AI", font=(FONT, -p(20), "bold")).pack(anchor="w")
        ttk.Label(brand, text="YOUR QUIET ADVANTAGE", foreground=MUTED, font=("Segoe UI", -p(9))).pack(anchor="w", pady=(p(3), 0))
        ttk.Button(brand_row, text="设置", style="Ghost.TButton", command=self.show_settings).pack(side="right")
        ttk.Label(brand_row, text="本地引擎  /  完全离线", foreground=MUTED, font=(FONT, -p(11))).pack(side="right", padx=p(16))
        ttk.Separator(header).pack(fill="x", pady=(p(12), p(13)))
        action_row = ttk.Frame(header)
        action_row.pack(fill="x")
        ttk.Label(action_row, text="对弈工作台", font=(FONT, -p(21), "bold")).pack(side="left")
        ttk.Label(action_row, text="从容落子，心中有局。", foreground=MUTED, font=(FONT, -p(11))).pack(side="left", padx=p(14))
        app.mouse_auto_button = ttk.Button(action_row, text="自动接管   F1", style="Accent.TButton", command=app._toggle_mouse_autoplay)
        app.mouse_auto_button.pack(side="right", padx=(p(10), 0))
        ttk.Button(action_row, text="导入棋局", command=app.import_screenshot).pack(side="right", padx=(p(8), 0))
        ttk.Button(action_row, text="粘贴识别", command=app.paste_screenshot).pack(side="right")

    def _build_footer(self):
        p, app = self.p, self.app
        footer = ttk.Frame(self.root)
        footer.pack(side="bottom", fill="x", padx=p(24), pady=(p(10), p(12)))
        tools = ttk.Frame(footer)
        tools.pack(side="right", padx=(p(16), 0))
        self.author_label = ttk.Label(tools, text="作者：第一少帅", foreground=ACCENT, font=(FONT, -p(11)))
        self.author_label.pack(side="left", padx=(0, p(12)))
        ttk.Button(tools, text="运行日志", command=app.open_log, style="Ghost.TButton").pack(side="left", padx=(0, p(4)))
        ttk.Button(tools, text="使用说明", command=app.show_help_document, style="Ghost.TButton").pack(side="left")
        self.status_label = ttk.Label(footer, textvariable=app.status_var, foreground=MUTED,
                                     font=(FONT, -p(12)), justify="left", wraplength=p(650))
        self.status_label.pack(side="left", fill="x", expand=True)
        footer.bind("<Configure>", lambda event: self.status_label.configure(
            wraplength=max(p(100), event.width - tools.winfo_reqwidth() - p(24))))

    def _build_board(self):
        app, p = self.app, self.p
        heading = ttk.Frame(self.board_card, style="Panel.TFrame")
        heading.pack(fill="x", padx=p(22), pady=(p(18), p(12)))
        self._label(heading, textvariable=self.top_player_var, font=(FONT, -p(13), "bold")).pack(side="left")
        self.redo_button = ttk.Button(heading, text="重做", command=app.redo, style="Small.TButton")
        self.redo_button.pack(side="right", padx=(p(6), 0))
        self.undo_button = ttk.Button(heading, text="撤销", command=app.undo, style="Small.TButton")
        self.undo_button.pack(side="right")
        bottom = ttk.Frame(self.board_card, style="Panel.TFrame")
        bottom.pack(side="bottom", fill="x", padx=p(22), pady=(p(10), p(16)))
        for index, (label, variable, choices, callback) in enumerate((
            ("我的执棋", app.player_side_var, (("执红", "w"), ("执黑", "b")), app._player_side_changed),
            ("当前轮次", app.side_var, (("红走", "w"), ("黑走", "b")), app._side_changed),
        )):
            group = ttk.Frame(bottom, style="Panel.TFrame")
            group.pack(side="left" if index == 0 else "right")
            self._label(group, label, "Muted.TLabel").pack(side="left", padx=(0, p(8)))
            if index == 0:
                auto = ToggleSwitch(group, app.auto_player_side_var, app._auto_player_side_toggled, scale=self.scale)
                auto.pack(side="left", padx=(0, p(5)))
                auto_label = self._label(group, "自动", "Muted.TLabel")
                auto_label.pack(side="left", padx=(0, p(9)))
                auto_label.bind("<Button-1>", lambda _e: auto.invoke())
            row = ttk.Frame(group, style="Panel.TFrame")
            row.pack(side="left")
            for text, value in choices:
                ttk.Radiobutton(row, text=text, variable=variable, value=value, command=callback,
                                style="Segment.TRadiobutton").pack(side="left", padx=(0, p(4)))
        hint = ttk.Frame(self.board_card, style="Panel.TFrame")
        hint.pack(side="bottom", fill="x", padx=p(22), pady=(p(6), 0))
        self._label(hint, textvariable=app.player_side_hint_var, style="Muted.TLabel").pack(side="left")
        self._label(hint, textvariable=self.tool_hint, style="Muted.TLabel").pack(side="right")
        players = ttk.Frame(self.board_card, style="Panel.TFrame")
        players.pack(side="bottom", fill="x", padx=p(22), pady=(p(8), p(6)))
        self._label(players, textvariable=self.bottom_player_var, font=(FONT, -p(13), "bold")).pack(side="left")
        self.turn_label = self._label(players, textvariable=self.turn_var, foreground=RED, font=(FONT, -p(12)))
        self.turn_label.pack(side="right")
        app.canvas = tk.Canvas(self.board_card, width=1, height=1, bg=PANEL, highlightthickness=0, cursor="hand2")
        app.canvas.pack(fill="both", expand=True, padx=p(12))
        app.canvas.bind("<Button-1>", app._board_click)
        app.canvas.bind("<Button-3>", app._board_erase)
        app.canvas.bind("<Configure>", self._resize_board)

    def _build_analysis(self, panel):
        p, app = self.p, self.app
        recommendation = RoundedPanel(panel, fill=ACCENT_SOFT, outside=PANEL, radius=p(12))
        recommendation.pack(fill="x", pady=(p(3), p(6)))
        top = tk.Frame(recommendation, bg=ACCENT_SOFT)
        top.pack(fill="x", padx=p(16), pady=(p(12), 0))
        ttk.Label(top, text="✧ 推荐走法", background=ACCENT_SOFT,
                  foreground=MUTED, font=(FONT, -p(11))).pack(side="left")
        ttk.Label(top, textvariable=self.eval_var, background=ACCENT_SOFT,
                  foreground=ACCENT, font=(FONT, -p(12), "bold")).pack(side="right")
        self.move_label = ttk.Label(recommendation, textvariable=self.move_var, background=ACCENT_SOFT,
                                   foreground=ACCENT, font=(FONT, -p(29), "bold"), wraplength=p(325))
        self.move_label.pack(anchor="w", padx=p(16), pady=(p(9), p(3)))
        ttk.Label(recommendation, textvariable=self.coordinate_var, background=ACCENT_SOFT,
                  foreground=MUTED, font=("Consolas", -p(12))).pack(anchor="w", padx=p(16))
        detail = tk.Frame(recommendation, bg=ACCENT_SOFT)
        detail.pack(fill="x", padx=p(16), pady=(p(9), p(10)))
        ttk.Label(detail, textvariable=self.state_var, background=ACCENT_SOFT,
                  foreground=ACCENT, font=(FONT, -p(11))).pack(side="left")
        ttk.Label(detail, textvariable=self.depth_var, background=ACCENT_SOFT,
                  foreground=MUTED, font=(FONT, -p(10))).pack(side="right")
        self.apply_button = ttk.Button(panel, text="▷  落下选中着", command=app.apply_selected_move, style="Accent.TButton")
        self.apply_button.pack(fill="x", pady=(0, p(10)))
        settings = ttk.Frame(panel, style="Panel.TFrame")
        settings.pack(fill="x", pady=(0, p(8)))
        self._label(settings, "思考", "Muted.TLabel").pack(side="left", padx=(0, p(7)))
        self.time_combo = ttk.Combobox(settings, textvariable=self.time_label, width=7, state="readonly",
                                      values=("0.5 秒", "1 秒", "3 秒", "5 秒", "10 秒", "30 秒"))
        self.time_combo.pack(side="left")
        self.time_combo.bind("<<ComboboxSelected>>", lambda _event: app.time_var.set(round(float(self.time_label.get().split()[0]) * 1000)))
        self._label(settings, "候选", "Muted.TLabel").pack(side="left", padx=(p(12), p(7)))
        ttk.Spinbox(settings, from_=1, to=5, textvariable=app.multipv_var, width=3, state="readonly").pack(side="left")
        self.stop_button = ttk.Button(settings, text="停止", style="Small.TButton", command=app.stop_analysis, width=3)
        self.stop_button.pack(side="right")
        self.start_button = ttk.Button(settings, text="分析", width=3, style="Small.TButton", command=app.start_analysis)
        self.start_button.pack(side="right", padx=(p(5), p(5)))
        self._switch_row(panel, "自动分析", app.auto_analysis_var, app._auto_analysis_toggled)
        self._switch_row(panel, "跟随首选着", app.follow_best_var, app._follow_best_toggled,
                         "只录对方走子，自动补上我方走法")
        ttk.Separator(panel).pack(fill="x", pady=p(7))
        self.progress_frame = ttk.Frame(panel, style="Panel.TFrame", height=p(3))
        self.progress_frame.pack(fill="x", pady=(0, p(3)))
        self.progress_frame.pack_propagate(False)
        self.progress = ttk.Progressbar(self.progress_frame, mode="indeterminate")
        table_heading = ttk.Frame(panel, style="Panel.TFrame")
        self.table_heading = table_heading
        table_heading.pack(fill="x", pady=(0, p(7)))
        self._label(table_heading, "候选走法", font=(FONT, -p(13), "bold")).pack(side="left")
        self._label(table_heading, "选择后预览", "Muted.TLabel", font=(FONT, -p(10))).pack(side="right")
        table = ttk.Frame(panel, style="Panel.TFrame")
        table.pack(fill="x")
        app.tree = ttk.Treeview(table, columns=("rank", "move", "score", "depth"), show="", height=3, selectmode="browse")
        # Preserve the public/internal tuple order used by existing self-tests.
        app.tree.configure(columns=("rank", "score", "move", "depth"), displaycolumns=("rank", "move", "score", "depth"))
        for name, title, width, anchor in (("rank", "#", 24, "center"), ("move", "着法", 145, "w"),
                                           ("score", "局势", 90, "center"), ("depth", "深度", 36, "center")):
            app.tree.heading(name, text=title)
            app.tree.column(name, width=p(width), minwidth=p(24), anchor=anchor, stretch=name == "move")
        scroll = ttk.Scrollbar(table, orient="vertical", command=app.tree.yview)
        app.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        app.tree.pack(fill="x", expand=True)
        self.empty_label = self._label(table, "导入棋盘后，候选走法会显示在这里。", "Muted.TLabel")
        app.tree.tag_configure("alternate", background="#F8F9F3")
        app.tree.bind("<<TreeviewSelect>>", app._analysis_selected)
        app.tree.bind("<Double-1>", app.apply_selected_move)
        app.tree.bind("<Return>", app.apply_selected_move)
        self._label(panel, "主要变化", "Muted.TLabel").pack(anchor="w", pady=(p(7), p(4)))
        pv_frame = ttk.Frame(panel, style="Panel.TFrame", height=p(54))
        pv_frame.pack_propagate(False)
        pv_frame.pack(fill="both", expand=True)
        app.pv_text = tk.Text(pv_frame, height=2, width=1, wrap="word", bg=PANEL_2, fg=TEXT,
                             relief="flat", padx=p(12), pady=p(6), font=(FONT, -p(12)),
                             spacing1=0, spacing3=0, highlightthickness=0, highlightbackground=BORDER,
                             selectbackground="#D5E9DC", insertbackground=TEXT)
        pv_scroll = ttk.Scrollbar(pv_frame, orient="vertical", command=app.pv_text.yview)
        app.pv_text.configure(yscrollcommand=pv_scroll.set)
        pv_scroll.pack(side="right", fill="y")
        app.pv_text.pack(side="left", fill="both", expand=True)
        app.pv_text.configure(state="disabled")

    def _switch_row(self, panel, text, variable, command=None, hint=None):
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", pady=self.p(5))
        switch = ToggleSwitch(row, variable, command, scale=self.scale)
        switch.pack(side="right", padx=(self.p(10), 0))
        label = self._label(row, text, font=(FONT, -self.p(12)))
        label.pack(anchor="w")
        label.bind("<Button-1>", lambda _e: switch.invoke())
        if hint:
            self._label(row, hint, "Muted.TLabel", font=(FONT, -self.p(10)),
                        wraplength=self.p(280)).pack(anchor="w", pady=(self.p(3), 0))
        return switch

    def show_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        p, app = self.p, self.app
        window = self.settings_window = tk.Toplevel(self.root)
        window.title("工作台设置")
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.resizable(False, False)
        panel = ttk.Frame(window, style="Panel.TFrame", padding=p(24))
        panel.pack(fill="both", expand=True)
        self._section(panel, "工作台设置", "界面设置不改变识别模型和引擎预算。")
        self._switch_row(panel, "自动判断我的执棋", app.auto_player_side_var,
                         app._auto_player_side_toggled, "以原始截图下方为我方；手动执红/执黑可覆盖。")
        self._switch_row(panel, "流畅动效", self.motion_var, app.draw_board)
        self._switch_row(panel, "显示棋盘坐标", self.coordinates_var, app.draw_board)
        self._switch_row(panel, "显示推荐箭头", self.arrow_var, app.draw_board)
        self._switch_row(panel, "显示识别截图底图", self.background_var, app.draw_board)
        self._switch_row(panel, "窗口置顶", app.always_on_top_var, app._topmost_toggled)
        ttk.Button(panel, text="完成", style="Accent.TButton", command=window.destroy).pack(fill="x", pady=(p(20), 0))
        window.bind("<Escape>", lambda _e: window.destroy())
        window.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width()-window.winfo_reqwidth())//2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height()-window.winfo_reqheight())//2)
        window.geometry(f"+{x}+{y}")
        window.focus_set()

    def _build_editor(self, panel):
        p, app = self.p, self.app
        self._section(panel, "构建你的局面", "选择工具后，在左侧棋盘上操作。所有修改都支持撤销。")
        modes = ttk.Frame(panel, style="Panel.TFrame")
        modes.pack(fill="x", pady=(0, p(20)))
        for text, value in (("移动棋子", "move"), ("橡皮擦", "erase")):
            ttk.Radiobutton(modes, text=text, variable=app.tool, value=value,
                            style="Segment.TRadiobutton").pack(side="left", padx=(0, p(8)))
        self.piece_buttons = {}
        for label, pieces, style in (("红方棋子", "KABNRCP", "RedPiece.TRadiobutton"),
                                     ("黑方棋子", "kabnrcp", "BlackPiece.TRadiobutton")):
            self._label(panel, label, "Muted.TLabel").pack(anchor="w", pady=(p(6), p(9)))
            row = ttk.Frame(panel, style="Panel.TFrame")
            row.pack(fill="x", pady=(0, p(18)))
            for index, piece in enumerate(pieces):
                row.columnconfigure(index, weight=1, uniform="pieces")
                button = ttk.Radiobutton(row, text=PIECE_NAMES[piece], variable=app.tool, value=piece, style=style, width=2)
                button.grid(row=0, column=index, sticky="ew", padx=(0, p(3)))
                self.piece_buttons[piece] = button
        self._section(panel, "快捷局面")
        presets = ttk.Frame(panel, style="Panel.TFrame")
        presets.pack(fill="x", pady=(p(5), p(14)))
        presets.columnconfigure((0, 1), weight=1)
        ttk.Button(presets, text="标准开局", command=lambda: app.load_preset(START_FEN)).grid(row=0, column=0, sticky="ew", padx=(0, p(6)))
        ttk.Button(presets, text="双炮残局", command=lambda: app.load_preset(PUZZLE_FEN)).grid(row=0, column=1, sticky="ew", padx=(p(6), 0))
        ttk.Button(panel, text="清空棋盘", command=app.clear_board, style="Danger.TButton").pack(fill="x", pady=(0, p(18)))
        ttk.Checkbutton(panel, text="显示识别截图底图", variable=self.background_var, command=app.draw_board).pack(anchor="w")
        self._label(panel, "底图仅用于核对识别结果，不影响引擎分析。\n棋盘方向由下方“我的执棋”切换。", "Muted.TLabel",
                    wraplength=p(360), justify="left").pack(anchor="w", pady=(p(10), 0))

    def _build_fen(self, panel):
        p, app = self.p, self.app
        self._section(panel, "导入与分享局面", "用 FEN 保存棋子位置和走子方，\n方便复盘、交流与复现问题。")
        self._label(panel, "当前局面的 FEN", "Muted.TLabel").pack(anchor="w", pady=(p(8), p(8)))
        self.fen_entry = ttk.Entry(panel, textvariable=app.fen_var, font=("Consolas", -p(13)), width=1)
        self.fen_entry.pack(fill="x", pady=(0, p(12)))
        self.fen_entry.bind("<Return>", lambda _event: app.load_fen())
        row = ttk.Frame(panel, style="Panel.TFrame")
        row.pack(fill="x", pady=(0, p(24)))
        ttk.Button(row, text="载入 FEN", command=app.load_fen, style="Accent.TButton").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="复制 FEN", command=app.copy_fen).pack(side="left", padx=(p(8), 0))
        self._section(panel, "使用提示")
        for number, message in (("01", "粘贴 FEN 后，点击“载入 FEN”或按 Enter。"),
                                ("02", "核对棋盘下方的走子方和你的执棋方。"),
                                ("03", "回到“引擎分析”，查看新局面的推荐走法。")):
            box = ttk.Frame(panel, style="Panel.TFrame")
            box.pack(fill="x", pady=p(10))
            self._label(box, number, foreground=ACCENT, font=("Consolas", -p(15), "bold")).pack(side="left", anchor="n", padx=(0, p(10)))
            self._label(box, message, "Muted.TLabel", wraplength=p(315), justify="left").pack(side="left", anchor="n")
        self._label(panel, "复制 FEN 只分享棋局，不包含日志、截图或本机路径。", "Muted.TLabel",
                    wraplength=p(360)).pack(anchor="w", pady=(p(24), 0))

    def _resize_board(self, event):
        layout = BoardLayout.fit(event.width, event.height)
        app = self.app
        app.CANVAS_W, app.CANVAS_H = layout.width, layout.height
        app.CELL, app.X0, app.Y0 = layout.cell, layout.x0, layout.y0
        if self._resizing is not None:
            self.root.after_cancel(self._resizing)
        self._resizing = self.root.after_idle(self._redraw)

    def _redraw(self):
        self._resizing = None
        if not self.app.closing:
            self.app.draw_board()

    def _time_changed(self, *_args):
        self.time_label.set(f"{self.app.time_var.get() / 1000:g} 秒")
        self._presentation_key = None
        self.update_analysis()

    def _schedule_layout_refresh(self, _event=None):
        if self._layout_refresh is None and not self.app.closing:
            self._layout_refresh = self.root.after_idle(self._refresh_pages)

    def _refresh_pages(self):
        self._layout_refresh = None
        if not self.app.closing:
            for page in self.pages:
                page._fit()

    def _turn_changed(self, *_args):
        self.turn_var.set("● 红方走子" if self.app.side_var.get() == "w" else "● 黑方走子")
        self.turn_label.configure(foreground=RED if self.app.side_var.get() == "w" else BLACK)

    def _players_changed(self, *_args):
        black = self.app.player_side_var.get() == "b"
        self.top_player_var.set("对方 · 红方" if black else "对方 · 黑方")
        self.bottom_player_var.set("我方 · 黑方" if black else "我方 · 红方")

    def _tool_changed(self, *_args):
        tool = self.app.tool.get()
        if tool in PIECE_NAMES:
            self.tool_hint.set(f"添加{'红' if tool.isupper() else '黑'}{PIECE_NAMES[tool]} · 点击交叉点放置")
        else:
            self.tool_hint.set("橡皮擦 · 点击棋子移除" if tool == "erase" else "移动模式 · 先点棋子，再点落点")

    def update_analysis(self):
        app = self.app
        busy = app.analysis_running
        line = app._current_analysis_line()
        children = app.tree.get_children()
        presentation = (busy, id(line), children, bool(app.undo_stack), bool(app.redo_stack))
        if presentation == getattr(self, "_presentation_key", None):
            return
        self._presentation_key = presentation
        if busy != self._busy:
            self._busy = busy
            if busy:
                self.progress.pack(fill="both", expand=True)
                self.progress.start(14)
            else:
                self.progress.stop()
                self.progress.pack_forget()
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.apply_button.configure(state="normal" if line else "disabled")
        if children:
            self.empty_label.place_forget()
        else:
            self.empty_label.configure(text="正在计算候选走法…" if busy else "导入棋盘后，候选走法会显示在这里。")
            self.empty_label.place(relx=.5, rely=.65, anchor="center")
        if busy:
            self.state_var.set("引擎思考中")
            self.move_var.set("正在寻找最佳着法…")
            self.eval_var.set("计算中")
            self.coordinate_var.set("局面变化时自动更新")
            self.depth_var.set(f"预算 {app.time_var.get()/1000:g} 秒")
        elif line:
            self.state_var.set("首选推荐" if line is app.analysis_lines[0] else f"候选 {line.multipv}")
            try:
                move = describe_move(app.analysis_board, line.best_move)
            except ValueError:
                move = line.best_move
            self.move_var.set(move.split("（")[0].split(" (")[0].strip())
            self.eval_var.set(score_text(line, app.analysis_side))
            self.coordinate_var.set(f"{line.best_move[:2]}  →  {line.best_move[2:]}")
            self.depth_var.set(f"深度 {line.depth} · {app.time_var.get()/1000:g} 秒档")
        else:
            self.state_var.set("准备就绪")
            self.move_var.set("等待分析")
            self.eval_var.set("—")
            self.coordinate_var.set("导入棋盘，查看推荐走法")
            self.depth_var.set("本地引擎 · 完全离线")
        self.undo_button.configure(state="normal" if app.undo_stack else "disabled")
        self.redo_button.configure(state="normal" if app.redo_stack else "disabled")
        self._schedule_layout_refresh()

    def close(self):
        self.progress.stop()
        if self._animation_job is not None:
            self.root.after_cancel(self._animation_job)
            self._animation_job = None
        if self._resizing is not None:
            self.root.after_cancel(self._resizing)
            self._resizing = None
        if self._layout_refresh is not None:
            self.root.after_cancel(self._layout_refresh)
            self._layout_refresh = None

    def _install_icon(self):
        # Small, code-native chessboard mark; kept in memory, no sidecar file.
        icon = tk.PhotoImage(master=self.root, width=32, height=32)
        icon.put(ACCENT, to=(0, 0, 32, 32))
        for point in (8, 16, 24):
            icon.put("#90C3A9", to=(point, 5, point + 1, 27))
            icon.put("#90C3A9", to=(5, point, 27, point + 1))
        for y in range(10, 23):
            for x in range(10, 23):
                if (x - 16) ** 2 + (y - 16) ** 2 <= 36:
                    icon.put("#FFF6E5", (x, y))
        self._icon = icon
        self.root.iconphoto(True, icon)


def draw_chessboard(app):
    c, cell, x0, y0 = app.canvas, app.CELL, app.X0, app.Y0
    ui = getattr(app, "ui", None)
    if ui is not None and ui._animation_job is not None:
        app.root.after_cancel(ui._animation_job)
        ui._animation_job = None
    previous = ui._last_board if ui is not None else {}
    previous_points = ui._last_points if ui is not None else {}
    layout = (app.CANVAS_W, app.CANVAS_H, cell, x0, y0)
    if ui is not None and ui._last_layout != layout:
        previous_points = {}
    c.delete("all")
    c.create_rectangle(0, 0, app.CANVAS_W, app.CANVAS_H, fill=PANEL, outline="")
    edge = cell * 0.67
    bounds = (x0 - edge, y0 - edge, x0 + 8 * cell + edge, y0 + 9 * cell + edge)
    c.create_rectangle(*(value + (2 if index % 2 == 0 else 3) for index, value in enumerate(bounds)),
                       fill="#E7E5DD", outline="")
    left, top, right, bottom = bounds
    r = max(3, cell*.18)
    c.create_polygon(left+r, top, right-r, top, right, top, right, top+r,
                     right, bottom-r, right, bottom, right-r, bottom, left+r, bottom,
                     left, bottom, left, bottom-r, left, top+r, left, top,
                     smooth=True, splinesteps=16, fill=BOARD, outline="#DECFB3", width=1)
    c.create_rectangle(x0 - cell * .13, y0 - cell * .13, x0 + 8.13 * cell, y0 + 9.13 * cell,
                       outline="#BDA27B", width=1)
    show_background = not hasattr(app, "ui") or app.ui.background_var.get()
    if app.background_image is not None and show_background:
        from PIL import ImageEnhance, ImageTk

        key = (app.background_image, cell, app.assisted_side)
        if getattr(app, "_background_cache_key", None) != key:
            rendered = app.background_image.resize((8 * cell, 9 * cell))
            if app.assisted_side == "b":
                rendered = rendered.rotate(180)
            rendered = ImageEnhance.Brightness(rendered).enhance(0.82)
            app.background_tk = ImageTk.PhotoImage(rendered)
            app._background_cache_key = key
        c.create_image(x0, y0, image=app.background_tk, anchor="nw")
    for x in range(9):
        px = x0 + x * cell
        c.create_line(px, y0, px, y0 + 4 * cell, fill=GRID)
        c.create_line(px, y0 + 5 * cell, px, y0 + 9 * cell, fill=GRID)
        if x in (0, 8):
            c.create_line(px, y0 + 4 * cell, px, y0 + 5 * cell, fill=GRID)
    for row in range(10):
        c.create_line(x0, y0 + row * cell, x0 + 8 * cell, y0 + row * cell, fill=GRID)
    for row in (0, 7):
        c.create_line(x0 + 3 * cell, y0 + row * cell, x0 + 5 * cell, y0 + (row + 2) * cell, fill=GRID)
        c.create_line(x0 + 5 * cell, y0 + row * cell, x0 + 3 * cell, y0 + (row + 2) * cell, fill=GRID)
    for row, columns in ((2, (1, 7)), (7, (1, 7)), (3, (0, 2, 4, 6, 8)), (6, (0, 2, 4, 6, 8))):
        for col in columns:
            for dx in (-1, 1):
                if (col == 0 and dx < 0) or (col == 8 and dx > 0):
                    continue
                for dy in (-1, 1):
                    x, y = x0 + col * cell + dx * .09 * cell, y0 + row * cell + dy * .09 * cell
                    c.create_line(x, y + dy * .13 * cell, x, y, x + dx * .13 * cell, y, fill=GRID)
    words = ("汉 界", "楚 河") if app.assisted_side == "b" else ("楚 河", "汉 界")
    for col, word in zip((2, 6), words):
        c.create_text(x0 + col * cell, y0 + 4.5 * cell, text=word, fill="#967952",
                      font=("KaiTi", -max(10, int(cell * .36))))
    files = reversed(FILES) if app.assisted_side == "b" else FILES
    coordinate_font = ("Consolas", -max(8, int(cell * .20)))
    for col, name in enumerate(files if ui is None or ui.coordinates_var.get() else []):
        for row in (-.55, 9.55):
            c.create_text(x0 + col * cell, y0 + row * cell, text=name, fill="#958568", font=coordinate_font, tags="coordinate")
    for row in (range(10) if ui is None or ui.coordinates_var.get() else []):
        _, py = app._canvas_point((0, row))
        c.create_text(x0 - .55 * cell, py, text=str(row), fill="#958568", font=coordinate_font, tags="coordinate")
    if app.best_arrow and (ui is None or ui.arrow_var.get()):
        start, end = app.best_arrow
        sx, sy = app._canvas_point(start)
        ex, ey = app._canvas_point(end)
        length = math.hypot(ex - sx, ey - sy)
        if length:
            dx, dy = (ex - sx) / length, (ey - sy) / length
            c.create_line(sx + dx * cell * .34, sy + dy * cell * .34,
                          ex - dx * cell * .22, ey - dy * cell * .22,
                          fill=ACCENT, width=max(3, cell * .08), arrow=tk.LAST,
                          arrowshape=(cell * .22, cell * .27, cell * .10), tags="recommendation")
            c.create_oval(ex - cell * .31, ey - cell * .31, ex + cell * .31, ey + cell * .31,
                          outline=ACCENT, width=2, tags="recommendation")
    radius = cell * .40
    for square, piece in app.board.items():
        x, y = app._canvas_point(square)
        color = RED if piece.isupper() else BLACK
        tag = f"piece-at-{square[0]}-{square[1]}"
        c.create_oval(x - radius + 1, y - radius + 3, x + radius + 1, y + radius + 3, fill="#CCBD9E", outline="", tags=tag)
        c.create_oval(x - radius, y - radius, x + radius, y + radius, fill="#FFF8E9", outline="#CDBB9A",
                      width=max(1, cell * .02), tags=("piece", tag))
        inner = radius - max(3, cell * .075)
        c.create_oval(x - inner, y - inner, x + inner, y + inner, outline=color, width=1, tags=tag)
        c.create_text(x, y - 1, text=PIECE_NAMES[piece], fill=color,
                      font=("KaiTi", -max(10, int(cell * .58))), tags=("piece-label", tag))
    if app.selected_square is not None:
        x, y = app._canvas_point(app.selected_square)
        radius = cell * .48
        c.create_oval(x - radius, y - radius, x + radius, y + radius, outline=ACCENT, width=3, tags="selection")
    if ui is not None:
        animate_board(app, previous, previous_points)
        ui._last_board = dict(app.board)
        ui._last_points = {(sq, pc): app._canvas_point(sq) for sq, pc in app.board.items()}
        ui._last_layout = layout
