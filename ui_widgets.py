"""Small native presentation primitives; no engine or mouse-control dependencies."""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None


class RoundedPanel(tk.Frame):
    """A normal Frame with antialiased corners; its geometry managers stay native."""
    def __init__(self, parent, *, fill, outside, radius=14, **kwargs):
        super().__init__(parent, bg=fill, bd=0, **kwargs)
        self._corners = []
        if Image is None:
            return
        r, factor = max(2, int(radius)), 3
        image = Image.new("RGB", (2*r*factor, 2*r*factor), outside)
        ImageDraw.Draw(image).rounded_rectangle((0, 0, 2*r*factor-1, 2*r*factor-1),
                                                r*factor, fill=fill)
        image = image.resize((2*r, 2*r), Image.Resampling.LANCZOS)
        for box, anchor, relx, rely in (((0, 0, r, r), "nw", 0, 0),
                ((r, 0, 2*r, r), "ne", 1, 0), ((0, r, r, 2*r), "sw", 0, 1),
                ((r, r, 2*r, 2*r), "se", 1, 1)):
            photo = ImageTk.PhotoImage(image.crop(box), master=self)
            corner = tk.Label(self, image=photo, bd=0, highlightthickness=0)
            corner.place(relx=relx, rely=rely, anchor=anchor)
            self._corners.append((corner, photo))


def install_round_styles(root, scale, palette):
    """Nine-slice native ttk images retain focus, invoke, disabled, and keyboard APIs."""
    if Image is None:
        return
    p = lambda n: max(1, round(n*scale))
    style = ttk.Style(root)
    photos = []

    def image(fill, border=None, *, switch=False, selected=False):
        factor = 3
        w, h = (p(32), p(19)) if switch else (p(32), p(32))
        canvas = Image.new("RGBA", (w*factor, h*factor))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((0, 0, w*factor-1, h*factor-1),
                              p(9)*factor, fill=fill, outline=border, width=factor)
        if switch:
            radius = p(6.5)*factor
            cx = (w-p(9.5) if selected else p(9.5))*factor
            cy = h*factor/2
            draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=palette["panel"])
        photo = ImageTk.PhotoImage(canvas.resize((w, h), Image.Resampling.LANCZOS), master=root)
        photos.append(photo)
        return photo

    variants = (("TButton", palette["panel"], palette["line"], "#f1f4ec", "#e5eddf"),
                ("Small.TButton", palette["panel"], palette["line"], "#f1f4ec", "#e5eddf"),
                ("Accent.TButton", palette["green"], None, "#346950", "#1e493b"),
                ("Ghost.TButton", palette["bg"], None, "#e9ede5", "#e1e8da"))
    for index, (name, fill, border, hover, pressed) in enumerate(variants):
        element = f"Quiet{index}.button"
        normal = image(fill, border)
        style.element_create(element, "image", normal,
            ("disabled", image("#edf0e7", "#e2e7d9")),
            ("pressed", image(pressed, border)), ("active", image(hover, border)),
            ("focus", image(fill, "#8ca67c")), border=p(10), sticky="nsew")
        style.layout(name, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"})]})]})])
        outside = palette["bg"] if name == "Ghost.TButton" else palette["panel"]
        style.configure(name, background=outside, padding=(p(6), 0))
        # Colour lives in the rounded image, not the rectangular ttk background.
        style.map(name, background=[("disabled", outside), ("pressed", outside),
                                    ("active", outside), ("!disabled", outside)])
    indicator = "Quiet.switch"
    style.element_create(indicator, "image", image("#d7dfcf", switch=True),
        ("selected", image(palette["green"], switch=True, selected=True)), sticky="")
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky":"nswe", "children":[
        (indicator, {"side":"right", "sticky":"e"}),
        ("Checkbutton.label", {"side":"left", "sticky":"w"})]})])
    style.configure("TCheckbutton", padding=(0, p(5)), font=("Microsoft YaHei UI", -p(12)))
    style.configure("Ghost.TButton", background=palette["bg"], foreground=palette["muted"],
                    padding=(p(9), p(6)), font=("Microsoft YaHei UI", -p(12)))
    style.layout("Ghost.TButton", [("Button.padding", {"sticky": "nsew", "children": [
        ("Button.label", {"sticky": "nsew"})]})])
    style.map("Ghost.TButton", foreground=[("active", palette["green"])])
    style.configure("Footer.TCheckbutton", padding=(0, p(2)))
    root._quiet_theme_images = photos


class ToggleSwitch(tk.Canvas):
    """A keyboard-accessible switch with a short, nonblocking knob transition."""
    def __init__(self, parent, variable, command=None, *, scale=1, bg="#fffefa", accent="#285c49"):
        self.variable, self.command = variable, command
        self.scale, self.accent = scale, accent
        super().__init__(parent, width=round(33*scale), height=round(22*scale), bg=bg,
                         highlightthickness=0, bd=0, takefocus=True, cursor="hand2")
        self._progress = float(variable.get())
        self._job = None
        self._trace = variable.trace_add("write", self._changed)
        self.bind("<Button-1>", lambda _e: self.invoke())
        self.bind("<space>", self._key)
        self.bind("<Return>", self._key)
        self.bind("<FocusIn>", lambda _e: self._paint())
        self.bind("<FocusOut>", lambda _e: self._paint())
        self.bind("<Destroy>", self._destroy)
        self._paint()

    def _key(self, _event):
        self.invoke()
        return "break"

    def invoke(self):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def _changed(self, *_args):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        target, start = float(self.variable.get()), self._progress
        begun = time.monotonic()
        motion = getattr(self.winfo_toplevel(), "_quiet_motion", None)
        if not self.winfo_ismapped() or (motion is not None and not motion.get()):
            self._progress = target
            self._paint()
            return

        def tick():
            self._job = None
            fraction = min(1, (time.monotonic()-begun)/.14)
            self._progress = start+(target-start)*(1-(1-fraction)**3)
            self._paint()
            if fraction < 1:
                self._job = self.after(16, tick)
        tick()

    def _paint(self):
        self.delete("all")
        s = self.scale
        fill = self.accent if self.variable.get() else "#d7dfcf"
        self.create_line(10*s, 11*s, 23*s, 11*s, width=18*s, fill=fill, capstyle=tk.ROUND)
        x = (10+13*self._progress)*s
        self.create_oval(x-6*s, 5*s, x+6*s, 17*s, fill="#fffefa", outline="")
        if self.focus_get() is self:
            self.create_rectangle(1, 1, 32*s, 21*s, outline="#8ca67c", dash=(2, 2))

    def _destroy(self, event):
        if event.widget is not self:
            return
        if self._job is not None:
            self.after_cancel(self._job)
        self.variable.trace_remove("write", self._trace)


def animate_board(app, previous, previous_points):
    """Move canvas tags only; model state is final immediately, engine never waits."""
    ui = getattr(app, "ui", None)
    if ui is None or not ui.motion_var.get() or not app.root.winfo_ismapped():
        return
    transitions = []
    removed = {sq: pc for sq, pc in previous.items() if app.board.get(sq) != pc}
    added = {sq: pc for sq, pc in app.board.items() if previous.get(sq) != pc}
    for square, piece in app.board.items():
        old = previous_points.get((square, piece)) if previous == app.board else None
        if old is None and square in added:
            origins = [sq for sq, pc in removed.items() if pc == piece]
            destinations = [sq for sq, pc in added.items() if pc == piece]
            if len(origins) == len(destinations) == 1:
                old = previous_points.get((origins[0], piece))
        if old is None:
            continue
        x, y = app._canvas_point(square)
        dx, dy = old[0]-x, old[1]-y
        if abs(dx)+abs(dy) > .5:
            tag = f"piece-at-{square[0]}-{square[1]}"
            app.canvas.move(tag, dx, dy)
            transitions.append([tag, dx, dy])
    if not transitions:
        return
    started, last = time.monotonic(), [1.0]

    def tick():
        ui._animation_job = None
        if app.closing:
            return
        fraction = min(1, (time.monotonic()-started)/.18)
        remaining = (1-fraction)**3
        for tag, dx, dy in transitions:
            app.canvas.move(tag, dx*(remaining-last[0]), dy*(remaining-last[0]))
        last[0] = remaining
        if fraction < 1:
            ui._animation_job = app.root.after(16, tick)
    ui._animation_job = app.root.after(16, tick)
