#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude-monitor — interfaccia grafica (Tkinter, solo stdlib).

Cruscotto desktop sopra `claude_monitor.py`: riusa la stessa logica di scansione,
deduplica e calcolo del costo, quindi i numeri coincidono col CLI alla cifra.

    python  claude_monitor_gui.py          con console (per i traceback)
    pythonw claude_monitor_gui.py          senza console

Nessuna dipendenza esterna. Vedi README.md.

Note sull'aspetto: niente widget ttk a rilievo. Superfici piatte, gerarchia
affidata a spaziatura e peso del testo, tabella disegnata su Canvas per poter
mettere le barre di quota in riga. Tema chiaro/scuro seguendo Windows.
"""

from __future__ import annotations

import os
import sys

# --------------------------------------------------------------------------- #
# Prologo: DEVE stare prima di importare claude_monitor.
# Con pythonw.exe stdout/stderr sono None; claude_monitor scrive su stderr in
# warn()/info() e legge sys.stdout.encoding al momento dell'import.
# --------------------------------------------------------------------------- #
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import datetime as dt
import glob
import json
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from tkinter import filedialog, messagebox

import claude_monitor as cm  # NON chiamare mai cm.init_color(): inietterebbe ANSI

APP_TITLE = "claude-monitor"
LIVE_INTERVAL = 2.0
LIVE_REDISCOVER = 5
PUMP_MS = 80


# --------------------------------------------------------------------------- #
# Tema — valori dalla palette di riferimento, validati per contrasto
# --------------------------------------------------------------------------- #

PALETTES = {
    "light": {
        "page":      "#f9f9f7",
        "surface":   "#fcfcfb",
        "raised":    "#ffffff",
        "ink":       "#0b0b0b",
        "ink2":      "#52514e",
        "muted":     "#898781",
        "line":      "#e1e0d9",
        "border":    "#dcdbd3",
        "accent":    "#2a78d6",
        "accent_bg": "#eaf1fc",
        "track":     "#ecebe5",
        "good":      "#0ca30c",
        "hover":     "#f1f0ec",
        "sel":       "#e6eefb",
    },
    "dark": {
        "page":      "#0d0d0d",
        "surface":   "#1a1a19",
        "raised":    "#212120",
        "ink":       "#ffffff",
        "ink2":      "#c3c2b7",
        "muted":     "#898781",
        "line":      "#2c2c2a",
        "border":    "#333331",
        "accent":    "#3987e5",
        "accent_bg": "#16233a",
        "track":     "#2c2c2a",
        "good":      "#0ca30c",
        "hover":     "#242423",
        "sel":       "#1d2b41",
    },
}


def windows_prefers_dark() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        with key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


class Theme:
    def __init__(self, mode: str):
        self.mode = mode
        self.c = PALETTES[mode]
        family = "Segoe UI"
        for candidate in ("Segoe UI Variable Text", "Segoe UI"):
            if candidate in tkfont.families():
                family = candidate
                break
        display = "Segoe UI Variable Display" if \
            "Segoe UI Variable Display" in tkfont.families() else family
        self.f_body = tkfont.Font(family=family, size=9)
        self.f_body_bold = tkfont.Font(family=family, size=9, weight="bold")
        self.f_small = tkfont.Font(family=family, size=8)
        self.f_head = tkfont.Font(family=family, size=8, weight="bold")
        self.f_title = tkfont.Font(family=display, size=13, weight="bold")
        self.f_stat = tkfont.Font(family=display, size=21, weight="bold")
        self.f_mono = tkfont.Font(family="Consolas", size=9)

    def __getitem__(self, key):
        return self.c[key]


def set_titlebar(root: tk.Tk, dark: bool) -> None:
    """Barra del titolo scura: senza questo il tema scuro stona col frame di Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, build precedenti
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def app_icon(root: tk.Tk, theme: Theme) -> None:
    """Icona generata a runtime: un quadrato con l'accento, meglio della piuma di Tk."""
    try:
        size = 32
        img = tk.PhotoImage(width=size, height=size)
        acc = theme["accent"]
        bg = theme["surface"]
        img.put(bg, to=(0, 0, size, size))
        for y in range(size):
            for x in range(size):
                dx = min(x, size - 1 - x)
                dy = min(y, size - 1 - y)
                if dx < 3 or dy < 3:
                    continue
                if dx < 6 and dy < 6 and (6 - dx) ** 2 + (6 - dy) ** 2 > 16:
                    continue
                img.put(acc, to=(x, y, x + 1, y + 1))
        root.iconphoto(True, img)
        root._icon_ref = img  # evita la garbage collection
    except Exception:
        pass


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


# --------------------------------------------------------------------------- #
# Formattazione
# --------------------------------------------------------------------------- #

_IT_TRANS = str.maketrans(",.", ".,")


class Fmt:
    italian = False

    @classmethod
    def cost(cls, value: float) -> str:
        s = cm.h_cost(value)
        return s.translate(_IT_TRANS) if cls.italian else s

    @staticmethod
    def tokens(n: int) -> str:
        return cm.h_tokens(n)

    @staticmethod
    def dur(seconds) -> str:
        return cm.h_dur(seconds)

    @staticmethod
    def time(epoch) -> str:
        return cm.h_time(epoch)

    @staticmethod
    def ago(epoch) -> str:
        return cm.h_ago(epoch)


# Valuta dell'abbonamento: impostata all'avvio da pricing.json.
SUB_CURRENCY = ""


def MONEY(value: float) -> str:
    """Costo reale, nella valuta con cui paghi l'abbonamento."""
    if not SUB_CURRENCY:
        return "—"
    s = cm.money(value, SUB_CURRENCY)
    return s.translate(_IT_TRANS) if Fmt.italian else s


# --------------------------------------------------------------------------- #
# Componenti
# --------------------------------------------------------------------------- #


class FlatButton(tk.Canvas):
    """Pulsante piatto con angoli arrotondati e stato hover."""

    def __init__(self, master, theme: Theme, text, command=None,
                 primary=False, width=None, toggle=False):
        self.t = theme
        self.text = text
        self.command = command
        self.primary = primary
        self.toggle = toggle
        self.active = False
        self.hover = False
        pad = 14
        w = width or theme.f_body.measure(text) + pad * 2
        h = 30
        super().__init__(master, width=w, height=h, highlightthickness=0,
                         bd=0, bg=theme["page"])
        # NB: non usare self._w / self._h — sono il path Tcl interno del widget
        self.bw, self.bh = w, h
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def set_bg(self, color):
        self.configure(bg=color)
        self._draw()

    def set_active(self, value: bool):
        self.active = value
        self._draw()

    def set_text(self, text: str):
        self.text = text
        self._draw()

    def _colors(self):
        t = self.t
        if self.primary:
            return t["accent"], "#ffffff", t["accent"]
        if self.active:
            return t["accent_bg"], t["accent"], t["accent_bg"]
        if self.hover:
            return t["hover"], t["ink"], t["border"]
        return t["surface"], t["ink2"], t["border"]

    def _draw(self):
        self.delete("all")
        fill, fg, outline = self._colors()
        round_rect(self, 1, 1, self.bw - 1, self.bh - 1, 7,
                   fill=fill, outline=outline)
        self.create_text(self.bw / 2, self.bh / 2, text=self.text,
                         fill=fg, font=self.t.f_body)

    def _on_enter(self, _e):
        self.hover = True
        self._draw()

    def _on_leave(self, _e):
        self.hover = False
        self._draw()

    def _on_click(self, _e):
        if self.toggle:
            self.active = not self.active
            self._draw()
        if self.command:
            self.command()


class StatTile(tk.Frame):
    """Tessera con un numero in evidenza. Per un valore singolo il numero È il grafico."""

    def __init__(self, master, theme: Theme, label: str, width=190):
        super().__init__(master, bg=theme["surface"], highlightthickness=1,
                         highlightbackground=theme["border"],
                         highlightcolor=theme["border"])
        self.t = theme
        self.configure(width=width, height=106)
        self.pack_propagate(False)
        box = tk.Frame(self, bg=theme["surface"])
        box.pack(fill="both", expand=True, padx=14, pady=12)
        self.l_label = tk.Label(box, text=label.upper(), bg=theme["surface"],
                                fg=theme["muted"], font=theme.f_head, anchor="w")
        self.l_label.pack(fill="x")
        self.l_value = tk.Label(box, text="—", bg=theme["surface"], fg=theme["ink"],
                                font=theme.f_stat, anchor="w")
        self.l_value.pack(fill="x", pady=(2, 0))
        self.l_sub = tk.Label(box, text="", bg=theme["surface"], fg=theme["ink2"],
                              font=theme.f_small, anchor="w", justify="left")
        self.l_sub.pack(fill="x", pady=(2, 0))

    def set(self, value: str, sub: str = "", value_color: str | None = None):
        self.l_value.config(text=value, fg=value_color or self.t["ink"])
        self.l_sub.config(text=sub)


class LimitsTile(tk.Frame):
    """Consumo rispetto ai limiti del piano: finestra di 5 ore e di 7 giorni.

    Il dato viene da ~/.claude.json (cachedUsageUtilization), l'unico posto su
    disco dove sopravvive fra una sessione e l'altra. Claude Code lo aggiorna solo
    quando parla con l'API, quindi può essere vecchio: qui si dice sempre di
    quando è, e una finestra già scaduta non viene mostrata come se fosse viva.
    """

    def __init__(self, master, theme: Theme, width=270):
        super().__init__(master, bg=theme["surface"], highlightthickness=1,
                         highlightbackground=theme["border"])
        self.t = theme
        self.warn_pct = 75
        self.crit_pct = 90
        self.configure(width=width, height=106)
        self.pack_propagate(False)
        box = tk.Frame(self, bg=theme["surface"])
        box.pack(fill="both", expand=True, padx=14, pady=12)
        tk.Label(box, text="LIMITI DEL PIANO", bg=theme["surface"], fg=theme["muted"],
                 font=theme.f_head, anchor="w").pack(fill="x")
        self.canvas = tk.Canvas(box, height=42, highlightthickness=0, bd=0,
                                bg=theme["surface"])
        self.canvas.pack(fill="x", pady=(6, 0))
        self.l_sub = tk.Label(box, text="", bg=theme["surface"], fg=theme["muted"],
                              font=theme.f_small, anchor="w")
        self.l_sub.pack(fill="x")
        self.data = None
        self.canvas.bind("<Configure>", lambda e: self._draw())

    def set_thresholds(self, warn, crit):
        self.warn_pct, self.crit_pct = warn, crit

    def set_data(self, data):
        self.data = data
        self._draw()
        if not data:
            self.l_sub.config(text="nessun dato disponibile")
            return
        age = data["age_min"]
        quando = (f"{int(age)} min fa" if age < 90 else
                  f"{age / 60:.0f} ore fa" if age < 48 * 60 else
                  f"{age / 1440:.0f} giorni fa")
        note = [f"letto {quando}"]
        extra = data.get("extra")
        if extra:
            note.append(f"crediti {extra['used']:.2f}/{extra['limit']:.2f} {extra['currency']}")
        self.l_sub.config(text="  ·  ".join(note))

    def _draw(self):
        c = self.canvas
        t = self.t
        c.delete("all")
        width = max(c.winfo_width(), 120)
        if not self.data:
            c.create_text(0, 20, text="—", anchor="w", fill=t["muted"], font=t.f_body)
            return
        label_w, pct_w = 58, 46
        bar_w = max(30, width - label_w - pct_w)
        for i, key in enumerate(("five_hour", "seven_day")):
            w = self.data.get(key)
            y = 8 + i * 22
            nome = "5 ore" if key == "five_hour" else "7 giorni"
            c.create_text(0, y, text=nome, anchor="w", fill=t["ink2"], font=t.f_small)
            round_rect(c, label_w, y - 4, label_w + bar_w, y + 4, 4,
                       fill=t["track"], outline="")
            if not w or w.get("expired"):
                c.create_text(width, y, text="—", anchor="e", fill=t["muted"],
                              font=t.f_small)
                continue
            pct = w["pct"]
            colore = (t["good"] if pct < self.warn_pct else
                      "#eda100" if pct < self.crit_pct else "#e34948")
            if pct > 0:
                round_rect(c, label_w, y - 4, label_w + max(8, bar_w * min(pct, 100) / 100),
                           y + 4, 4, fill=colore, outline="")
            c.create_text(width, y, text=f"{pct:.0f}%", anchor="e",
                          fill=t["ink"], font=t.f_small)


def read_plan_limits(warn_stale_min=60):
    """Legge il consumo dei limiti da ~/.claude.json. None se non c'è nulla di utile."""
    path = os.path.join(os.path.expanduser("~"), ".claude.json")
    try:
        st = os.stat(path)
        if st.st_size > 32 * 1024 * 1024:      # config gigantesca: lascio perdere
            return None
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        return None
    cached = (cfg or {}).get("cachedUsageUtilization") or {}
    util = cached.get("utilization") or {}
    fetched = cached.get("fetchedAtMs") or 0
    if not util:
        return None
    now = time.time()
    out = {"age_min": max(0.0, (now * 1000 - fetched) / 60000) if fetched else 1e9}
    for key in ("five_hour", "seven_day"):
        w = util.get(key)
        if not isinstance(w, dict) or w.get("utilization") is None:
            out[key] = None
            continue
        expired = False
        resets = w.get("resets_at")
        if isinstance(resets, str):
            ts = cm.parse_ts(resets)
            # finestra già girata: la percentuale registrata non vale più niente
            expired = bool(ts and ts < now)
        out[key] = {"pct": float(w["utilization"]), "resets_at": resets,
                    "expired": expired}
    extra = util.get("extra_usage") or {}
    if extra.get("is_enabled") and extra.get("monthly_limit"):
        out["extra"] = {
            "used": (extra.get("used_credits") or 0) / 100.0,
            "limit": extra["monthly_limit"] / 100.0,
            "currency": extra.get("currency") or "",
        }
    return out


class Segmented(tk.Frame):
    """Selettore a segmenti: sostituisce il Notebook, che è il widget più datato."""

    def __init__(self, master, theme: Theme, options, command):
        super().__init__(master, bg=theme["page"])
        self.t = theme
        self.command = command
        self.buttons = []
        for i, name in enumerate(options):
            b = FlatButton(self, theme, name, command=lambda i=i: self.select(i))
            b.set_bg(theme["page"])
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)
        self.index = 0
        self.select(0, fire=False)

    def select(self, index: int, fire=True):
        self.index = index
        for i, b in enumerate(self.buttons):
            b.set_active(i == index)
        if fire:
            self.command(index)


class Dropdown(FlatButton):
    """Combobox sostituita da un menu nativo, colorato col tema."""

    def __init__(self, master, theme: Theme, options, command, width=None):
        self.options = options
        self.on_pick = command
        self.value = options[0][0]
        super().__init__(master, theme, options[0][0] + "  ▾",
                         command=self._popup, width=width)
        self.menu = tk.Menu(self, tearoff=0, bg=theme["surface"], fg=theme["ink"],
                            activebackground=theme["accent"], activeforeground="#ffffff",
                            bd=0, font=theme.f_body, relief="flat")
        for label, spec in options:
            self.menu.add_command(label=label,
                                  command=lambda l=label, s=spec: self._pick(l, s))

    def _popup(self):
        try:
            self.menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.bh + 2)
        finally:
            self.menu.grab_release()

    def _pick(self, label, spec):
        self.value = label
        self.set_text(label + "  ▾")
        self.on_pick(spec)


class SearchBox(tk.Frame):
    def __init__(self, master, theme: Theme, placeholder, on_change, width=150):
        super().__init__(master, bg=theme["surface"], highlightthickness=1,
                         highlightbackground=theme["border"],
                         highlightcolor=theme["accent"])
        self.t = theme
        self.var = tk.StringVar()
        # il segnaposto iniziale scriverebbe nella variabile e farebbe partire il
        # callback prima che il chiamante abbia finito di costruirsi
        self._ready = False
        self.var.trace_add("write",
                           lambda *a: self._ready and on_change(self.var.get()))
        tk.Label(self, text="⌕", bg=theme["surface"], fg=theme["muted"],
                 font=theme.f_body).pack(side="left", padx=(8, 0))
        self.entry = tk.Entry(self, textvariable=self.var, bd=0, relief="flat",
                              bg=theme["surface"], fg=theme["ink"],
                              insertbackground=theme["ink"], font=theme.f_body,
                              width=int(width / 7))
        self.entry.pack(side="left", fill="x", expand=True, padx=6, pady=5)
        self.placeholder = placeholder
        self._show_placeholder()
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self._ready = True

    def _show_placeholder(self):
        if not self.var.get():
            self.entry.config(fg=self.t["muted"])
            self._ph = True
            self.entry.insert(0, self.placeholder)

    def _focus_in(self, _e):
        if getattr(self, "_ph", False):
            self.entry.delete(0, "end")
            self.entry.config(fg=self.t["ink"])
            self._ph = False

    def _focus_out(self, _e):
        if not self.var.get():
            self._show_placeholder()

    def get(self) -> str:
        return "" if getattr(self, "_ph", False) else self.var.get()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        self._ph = False
        self.entry.config(fg=self.t["ink"])
        self.entry.insert(0, value)


class Tooltip:
    """Spiegazione al passaggio del mouse. Una sola finestra, riusata."""

    def __init__(self, master, theme: Theme):
        self.master = master
        self.t = theme
        self.win = None
        self.label = None
        self.key = None

    def show(self, key, text, x, y):
        if key == self.key and self.win is not None:
            return
        self.key = key
        if self.win is None:
            self.win = tk.Toplevel(self.master)
            self.win.overrideredirect(True)      # niente barra del titolo
            self.win.attributes("-topmost", True)
            frame = tk.Frame(self.win, bg=self.t["border"])
            frame.pack()
            self.label = tk.Label(frame, bg=self.t["raised"], fg=self.t["ink"],
                                  font=self.t.f_small, justify="left", anchor="w",
                                  padx=10, pady=7, wraplength=380)
            self.label.pack(padx=1, pady=1)
        self.label.config(text=text)
        self.win.update_idletasks()
        self.win.geometry(f"+{int(x)}+{int(y)}")
        self.win.deiconify()

    def hide(self):
        self.key = None
        if self.win is not None:
            self.win.withdraw()


class DataTable(tk.Frame):
    """Tabella disegnata su Canvas.

    Il Treeview di ttk non permette barre in riga né spaziature decenti, ed è
    quello che fa sembrare l'app vecchia. Qui: righe alte, nessun bordo, hover,
    intestazioni ordinabili e una colonna con la barra di quota.
    """

    def __init__(self, master, theme: Theme, columns, on_activate=None, share_key=None):
        super().__init__(master, bg=theme["surface"], highlightthickness=1,
                         highlightbackground=theme["border"],
                         highlightcolor=theme["border"])
        self.t = theme
        self.columns = columns
        self.on_activate = on_activate
        self.share_key = share_key        # funzione riga -> valore per la barra
        self.rows: list[dict] = []
        self.total_row = None
        self.sort_col = None
        self.sort_desc = True
        self.hover_index = -1
        self.sel_index = -1
        self.placeholder = None
        self.help: dict[str, str] = {}   # cid -> spiegazione al passaggio del mouse

        self.row_h = max(30, theme.f_body.metrics("linespace") + 16)
        self.head_h = 34
        self.pad = 16

        self.head = tk.Canvas(self, height=self.head_h, highlightthickness=0, bd=0,
                              bg=theme["surface"])
        self.head.pack(fill="x")
        body = tk.Frame(self, bg=theme["surface"])
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, highlightthickness=0, bd=0, bg=theme["surface"])
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview,
                                   width=10, bd=0, relief="flat",
                                   troughcolor=theme["surface"],
                                   bg=theme["border"], activebackground=theme["muted"],
                                   highlightthickness=0)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.tip = Tooltip(self, theme)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.head.bind("<Button-1>", self._on_head_click)
        self.head.bind("<Motion>", self._on_head_motion)
        self.head.bind("<Leave>", lambda e: self.tip.hide())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(-1))
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-1>", self._on_double)
        for widget in (self.canvas, self.head):
            widget.bind("<MouseWheel>", self._on_wheel)

    # -- dati -------------------------------------------------------------- #

    def set_rows(self, rows, total_row=None):
        self.rows = rows
        self.total_row = total_row
        self.placeholder = None
        self.sel_index = -1
        self._redraw()

    def set_placeholder(self, text):
        self.rows = []
        self.total_row = None
        self.placeholder = text
        self._redraw()

    def set_heading(self, cid, label):
        """Rinomina una colonna a runtime: le etichette dei costi cambiano col regime."""
        for i, c in enumerate(self.columns):
            if c[0] == cid:
                self.columns[i] = (c[0], label) + tuple(c[2:])
        self._redraw()

    def sort_by(self, cid):
        if self.sort_col == cid:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = cid
            self.sort_desc = True
        self._redraw()

    def _sorted(self):
        rows = list(self.rows)
        if self.sort_col:
            key = next((c[4] for c in self.columns if c[0] == self.sort_col), None)
            if key:
                try:
                    rows.sort(key=key, reverse=self.sort_desc)
                except TypeError:
                    rows.sort(key=lambda r: str(key(r)), reverse=self.sort_desc)
        return rows

    # -- geometria ---------------------------------------------------------- #

    def _layout(self):
        width = max(self.canvas.winfo_width(), 400)
        fixed = sum(c[2] for c in self.columns if c[3] != "w")
        flex = [c for c in self.columns if c[3] == "w"]
        avail = width - self.pad * 2 - fixed - 12
        flex_w = {}
        if flex:
            base = sum(c[2] for c in flex)
            for c in flex:
                flex_w[c[0]] = max(90, int(avail * c[2] / base)) if base else 120
        xs, x = {}, self.pad
        for c in self.columns:
            w = flex_w.get(c[0], c[2])
            xs[c[0]] = (x, w)
            x += w
        return xs

    # -- disegno ------------------------------------------------------------ #

    def _redraw(self):
        t = self.t
        xs = self._layout()
        width = max(self.canvas.winfo_width(), 400)

        self.head.delete("all")
        self.head.configure(width=width)
        for cid, label, _w, align, _key, _fmt in self.columns:
            x, w = xs[cid]
            text = label.upper()
            if cid == self.sort_col:
                text += "  ▾" if self.sort_desc else "  ▴"
            # l'intestazione della colonna con le barre va a sinistra, dove parte la barra
            left_aligned = align == "w" or cid == "share"
            anchor = "w" if left_aligned else "e"
            tx = x if left_aligned else x + w - 10
            self.head.create_text(tx, self.head_h / 2, text=text, anchor=anchor,
                                  fill=t["accent"] if cid == self.sort_col else t["muted"],
                                  font=t.f_head)
        self.head.create_line(0, self.head_h - 1, width, self.head_h - 1, fill=t["line"])

        c = self.canvas
        c.delete("all")

        if self.placeholder is not None:
            c.create_text(width / 2, 60, text=self.placeholder, fill=t["muted"],
                          font=t.f_body)
            c.configure(scrollregion=(0, 0, width, 120))
            self.scroll.pack_forget()
            return

        rows = self._sorted()
        share_max = 0.0
        self.share_total = 0.0
        if self.share_key and rows:
            try:
                values = [abs(self.share_key(r)) for r in rows]
                share_max = max(values) or 0.0
                self.share_total = sum(values)
            except Exception:
                share_max = 0.0

        y = 0
        for i, row in enumerate(rows):
            if i == self.sel_index:
                c.create_rectangle(4, y, width - 4, y + self.row_h,
                                   fill=t["sel"], outline="")
            elif i == self.hover_index:
                c.create_rectangle(4, y, width - 4, y + self.row_h,
                                   fill=t["hover"], outline="")
            if i:
                c.create_line(self.pad, y, width - self.pad, y, fill=t["line"])
            self._draw_row(c, row, xs, y, share_max, width)
            y += self.row_h

        if self.total_row:
            c.create_line(self.pad, y + 3, width - self.pad, y + 3, fill=t["border"])
            y += 4
            for cid, value in self.total_row.items():
                if cid not in xs or value in (None, ""):
                    continue
                x, w = xs[cid]
                align = next(col[3] for col in self.columns if col[0] == cid)
                if align == "w":
                    c.create_text(x, y + self.row_h / 2, text=value, anchor="w",
                                  fill=t["ink"], font=t.f_body_bold)
                else:
                    c.create_text(x + w - 10, y + self.row_h / 2, text=value, anchor="e",
                                  fill=t["ink"], font=t.f_body_bold)
            y += self.row_h

        c.configure(scrollregion=(0, 0, width, y))
        need = y > c.winfo_height()
        if need and not self.scroll.winfo_ismapped():
            self.scroll.pack(side="right", fill="y")
        elif not need and self.scroll.winfo_ismapped():
            self.scroll.pack_forget()

    def _draw_row(self, c, row, xs, y, share_max, width):
        t = self.t
        mid = y + self.row_h / 2
        for cid, _label, _w, align, _key, fmt in self.columns:
            x, w = xs[cid]
            if cid == "share":
                self._draw_bar(c, x, mid, w - 14, row, share_max)
                continue
            if cid == "sharepct":
                continue
            value = fmt(row)
            if value is None or value == "":
                continue
            strong = cid in ("project", "cost")
            color = t["ink"] if strong else t["ink2"]
            font = t.f_body_bold if cid == "cost" else t.f_body
            if align == "w":
                text = self._clip(str(value), w - 12, font)
                c.create_text(x, mid, text=text, anchor="w", fill=color, font=font)
            else:
                c.create_text(x + w - 10, mid, text=str(value), anchor="e",
                              fill=color, font=font)

    def _draw_bar(self, c, x, mid, w, row, share_max):
        """Barra + percentuale sul totale: magnitudine, una serie sola.

        La barra è relativa al massimo (si vede il ranking), la percentuale è sul
        totale (si legge il peso reale). Due informazioni diverse, entrambe utili.
        """
        t = self.t
        label_w = 46
        bar_w = max(20, w - label_w)
        h = 7
        y1, y2 = mid - h / 2, mid + h / 2
        round_rect(c, x, y1, x + bar_w, y2, h / 2, fill=t["track"], outline="")
        try:
            value = abs(self.share_key(row))
        except Exception:
            return
        if share_max and value > 0:
            round_rect(c, x, y1, x + max(h, bar_w * value / share_max), y2, h / 2,
                       fill=t["accent"], outline="")
        total = getattr(self, "share_total", 0) or 0
        if total > 0:
            p = 100 * value / total
            txt = f"{p:.0f}%" if p >= 1 else ("<1%" if p > 0 else "—")
            c.create_text(x + w, mid, text=txt, anchor="e", fill=t["ink2"],
                          font=t.f_body)

    def _clip(self, text, width, font):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    # -- interazione -------------------------------------------------------- #

    def _row_at(self, event):
        y = self.canvas.canvasy(event.y)
        index = int(y // self.row_h)
        rows = self._sorted()
        return index if 0 <= index < len(rows) else -1

    def _set_hover(self, index):
        if index != self.hover_index:
            self.hover_index = index
            self._redraw()

    def _on_motion(self, event):
        self._set_hover(self._row_at(event))
        self.canvas.configure(cursor="hand2" if self.hover_index >= 0 and self.on_activate
                              else "")

    def _on_click(self, event):
        self.sel_index = self._row_at(event)
        self._redraw()

    def _on_double(self, event):
        index = self._row_at(event)
        if index >= 0 and self.on_activate:
            self.on_activate(self._sorted()[index])

    def _column_at(self, x_px):
        for cid, (x, w) in self._layout().items():
            if x - 8 <= x_px <= x + w:
                return cid
        return None

    def _on_head_click(self, event):
        cid = self._column_at(event.x)
        if cid:
            self.sort_by(cid)

    def _on_head_motion(self, event):
        cid = self._column_at(event.x)
        text = self.help.get(cid) if cid else None
        if not text:
            self.tip.hide()
            return
        self.tip.show(cid, text,
                      self.head.winfo_rootx() + event.x + 12,
                      self.head.winfo_rooty() + self.head_h + 6)

    def _on_wheel(self, event):
        if self.scroll.winfo_ismapped():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
            self._set_hover(-1)

    def selected_tsv(self) -> str:
        rows = self._sorted()
        if not (0 <= self.sel_index < len(rows)):
            return ""
        head = "\t".join(c[1] for c in self.columns if c[0] != "share")
        row = rows[self.sel_index]
        vals = "\t".join(str(c[5](row)) for c in self.columns if c[0] != "share")
        return head + "\n" + vals


# --------------------------------------------------------------------------- #
# Individuazione della sessione attiva
# --------------------------------------------------------------------------- #


def main_transcript_for(base: str, session_id: str) -> str | None:
    hits = glob.glob(os.path.join(base, "*", session_id + ".jsonl"))
    return hits[0] if hits else None


def active_from_session_records(base: str) -> tuple[str | None, str | None]:
    """Sessione attiva secondo ~/.claude/sessions/<pid>.json (processi vivi)."""
    home = os.path.dirname(os.path.abspath(base))
    best = None
    for path in glob.glob(os.path.join(home, "sessions", "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue
        if not isinstance(rec, dict) or not rec.get("sessionId"):
            continue
        stamp = rec.get("updatedAt") or rec.get("startedAt") or 0
        if isinstance(stamp, str):
            stamp = cm.parse_ts(stamp) or 0
        if best is None or stamp > best[0]:
            best = (stamp, rec)
    if best is None:
        return None, None
    return main_transcript_for(base, best[1]["sessionId"]), best[1].get("status")


def active_from_mtime(base: str) -> str | None:
    newest, target = -1.0, None
    for path in glob.glob(os.path.join(base, "**", "*.jsonl"), recursive=True):
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if mt > newest:
            newest, target = mt, path
    if target is None:
        return None
    return main_transcript_for(base, cm.session_id_from_path(target)) or target


def find_active_transcript(base: str) -> tuple[str | None, str | None]:
    path, status = active_from_session_records(base)
    if path:
        return path, status
    return active_from_mtime(base), None


# --------------------------------------------------------------------------- #
# Aggregazione per progetto
# --------------------------------------------------------------------------- #


def aggregate_by_project(sessions: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for s in sessions:
        key = s["project"] or "?"
        p = agg.get(key)
        if p is None:
            p = agg[key] = {
                "project": key, "sessions": 0, "user_prompts": 0, "assistant_msgs": 0,
                "tool_calls": 0, "duration": 0.0, "active": 0.0, "cost": 0.0,
                "real": 0.0, "tokens": cm.new_tok(), "end": 0.0,
            }
        p["sessions"] += 1
        p["user_prompts"] += s["user_prompts"]
        p["assistant_msgs"] += s["assistant_msgs"]
        p["tool_calls"] += s["tool_calls"]
        p["duration"] += s["duration"]
        p["active"] += s["active"]
        p["cost"] += s["cost"]
        p["real"] += s.get("real_cost", 0.0)
        cm.add_tok(p["tokens"], s["tokens"])
        p["end"] = max(p["end"], s["end"] or 0)
    for p in agg.values():
        p["real_txt"] = MONEY(p["real"])
    return list(agg.values())


def aggregate_by_month(sessions: list[dict], pricing: dict) -> list[dict]:
    """Consumo, costo ipotetico e costo reale per mese di fatturazione.

    I bucket vengono dai singoli messaggi, quindi una sessione a cavallo di due
    mesi contribuisce a entrambi nella giusta proporzione.
    """
    # il rapporto incrocia due valute solo se l'abbonamento non è in dollari
    rate = 1.0 if (cm.display_currency(pricing) or "USD").upper() == "USD" \
        else cm.fx_usd_per_unit(pricing)
    api_mode = (cm.billing_of(pricing).get("mode") or "subscription").lower() == "api"
    agg: dict[str, dict] = {}
    for s in sessions:
        for month, models in (s.get("per_month") or {}).items():
            slot = agg.get(month)
            if slot is None:
                slot = agg[month] = {"month": month, "hyp": 0.0, "real": 0.0,
                                     "sessions": 0, "tokens": 0, "output": 0}
            for data in models.values():
                slot["hyp"] += data["cost"]
                tok = data["tokens"]
                slot["tokens"] += sum(tok[k] for k in
                                      ("input", "output", "cache_read",
                                       "cache_w5m", "cache_w1h"))
                slot["output"] += tok["output"]
            slot["sessions"] += 1
            slot["real"] += s.get("per_month_real", {}).get(month, 0.0)

    now = dt.datetime.now().strftime("%Y-%m")
    out = []
    for month, slot in agg.items():
        # strftime("%B") seguirebbe il locale C: nomi dei mesi espliciti
        try:
            year, num = month.split("-")
            label = f"{MONTH_NAMES[int(num) - 1]} {year}"
        except (ValueError, IndexError):
            label = month
        slot["label"] = label + ("  ·  in corso" if month == now else "")
        slot["real_txt"] = MONEY(slot["real"])
        # a consumo il rapporto sarebbe sempre 1: non dice nulla
        slot["ratio"] = 0 if api_mode else (
            (slot["hyp"] / (slot["real"] * rate)) if (rate and slot["real"]) else 0)
        slot["ratio_txt"] = f"{slot['ratio']:.1f}×" if slot["ratio"] else "—"
        out.append(slot)
    return out


# --------------------------------------------------------------------------- #
# Colonne  (id, intestazione, larghezza, allineamento, chiave, formattatore)
# --------------------------------------------------------------------------- #

PROJECT_COLUMNS = [
    ("project",  "Progetto", 190, "w", lambda r: (r["project"] or "").lower(), lambda r: r["project"]),
    ("cost",     "Se fosse API", 110, "e", lambda r: r["cost"], lambda r: Fmt.cost(r["cost"])),
    ("share",    "Quota del consumo", 165, "e", lambda r: r["cost"], lambda r: ""),
    ("sessions", "Sess",      55, "e", lambda r: r["sessions"], lambda r: r["sessions"]),
    ("active",   "Attivo",    85, "e", lambda r: r["active"],   lambda r: Fmt.dur(r["active"])),
    ("msgs",     "Messaggi",  95, "e", lambda r: r["assistant_msgs"],
     lambda r: f"{r['user_prompts']}/{r['assistant_msgs']}"),
    ("tout",     "Output",    80, "e", lambda r: r["tokens"]["output"],
     lambda r: Fmt.tokens(r["tokens"]["output"])),
    ("cr",       "Cache R",   90, "e", lambda r: r["tokens"]["cache_read"],
     lambda r: Fmt.tokens(r["tokens"]["cache_read"])),
    ("last",     "Ultima",    80, "e", lambda r: r["end"],      lambda r: Fmt.ago(r["end"])),
]

SESSION_COLUMNS = [
    ("project",  "Progetto", 140, "w", lambda r: (r["project"] or "").lower(), lambda r: r["project"]),
    ("title",    "Titolo",   260, "w", lambda r: (r["title"] or r["first_prompt"] or "").lower(),
     lambda r: r["title"] or r["first_prompt"] or "—"),
    ("cost",     "Se fosse API", 105, "e", lambda r: r["cost"], lambda r: Fmt.cost(r["cost"])),
    ("share",    "Quota del consumo", 150, "e", lambda r: r["cost"], lambda r: ""),
    ("start",    "Inizio",    95, "e", lambda r: r["start"] or 0, lambda r: Fmt.time(r["start"])),
    ("active",   "Attivo",    80, "e", lambda r: r["active"],   lambda r: Fmt.dur(r["active"])),
    ("msgs",     "Messaggi",  90, "e", lambda r: r["assistant_msgs"],
     lambda r: f"{r['user_prompts']}/{r['assistant_msgs']}"),
    ("last",     "Ultima",    80, "e", lambda r: r["end"] or 0, lambda r: Fmt.ago(r["end"])),
]

MONTH_COLUMNS = [
    ("month",    "Mese",      130, "w", lambda r: r["month"], lambda r: r["label"]),
    ("real",     "Hai pagato", 110, "e", lambda r: r["real"], lambda r: r["real_txt"]),
    ("hyp",      "Se fosse API", 115, "e", lambda r: r["hyp"], lambda r: Fmt.cost(r["hyp"])),
    ("share",    "Peso",      110, "e", lambda r: r["hyp"],   lambda r: ""),
    ("ratio",    "Resa",       70, "e", lambda r: r["ratio"] or 0, lambda r: r["ratio_txt"]),
    ("sessions", "Sess",       55, "e", lambda r: r["sessions"], lambda r: r["sessions"]),
    ("tokens",   "Token",      90, "e", lambda r: r["tokens"], lambda r: Fmt.tokens(r["tokens"])),
    ("output",   "Output",     80, "e", lambda r: r["output"], lambda r: Fmt.tokens(r["output"])),
]

# Scheda Persone: una riga per postazione del team. La fonte non sono i
# transcript locali (che contengono solo il proprio lavoro) ma l'archivio del
# raccoglitore, alimentato dalla telemetria nativa di tutte le macchine.
TEAM_COLUMNS = [
    ("person",   "Postazione", 160, "w", lambda r: str(r["person"]).lower(),
     lambda r: r["person"]),
    ("paid",     "Hai pagato", 100, "e", lambda r: r.get("paid", 0),
     lambda r: r.get("paid_txt", "—")),
    ("cost",     "Se fosse API", 105, "e", lambda r: r["cost"],
     lambda r: Fmt.cost(r["cost"])),
    ("ratio",    "Resa",        65, "e", lambda r: r.get("ratio", 0),
     lambda r: r.get("ratio_txt", "—")),
    ("share",    "Quota del consumo", 150, "e", lambda r: r["cost"], lambda r: ""),
    ("sessions", "Sess",        55, "e", lambda r: r["sessions"],
     lambda r: r["sessions"]),
    ("projects", "Prog",        50, "e", lambda r: r.get("projects", 0),
     lambda r: r.get("projects") or "—"),
    ("active",   "Attivo",      80, "e", lambda r: r["active"],
     lambda r: Fmt.dur(r["active"])),
    ("tok",      "Token",       80, "e", lambda r: r["total_tokens"],
     lambda r: Fmt.tokens(r["total_tokens"])),
    ("cr",       "Cache R",     80, "e", lambda r: r["tokens"]["cache_read"],
     lambda r: Fmt.tokens(r["tokens"]["cache_read"])),
    ("models",   "Modelli",    130, "w", lambda r: len(r["models"]),
     lambda r: ", ".join(m.replace("claude-", "") for m in r["models"]) or "—"),
    ("last",     "Ultima",      75, "e", lambda r: r["last"],
     lambda r: Fmt.ago(r["last"])),
]


def team_db_candidates(config: dict) -> list[str]:
    """Dove cercare l'archivio del raccoglitore, in ordine di precedenza."""
    fuori = os.environ.get("CM_TEAM_DB")
    scelto = ((config.get("team") or {}).get("db")) if config else None
    return [p for p in (fuori, scelto,
                        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "cm-team.db"),
                        "cm-team.db") if p]


def fmt_ratio(value: float) -> str:
    """Quante volte una postazione ha reso quello che costa.

    Sotto un decimo si scrive '<0,1×' invece di arrotondare a zero: una
    postazione che rende poco e una che non rende nulla sono cose diverse,
    e la seconda e' la sola su cui abbia senso intervenire.
    """
    if not value:
        return "—"
    if value < 0.1:
        return "<0,1×"
    return f"{value:.1f}×".replace(".", ",")


def team_money(value: float, currency: str) -> str:
    """Importo nella valuta dell'abbonamento di team."""
    simboli = {"EUR": "€", "USD": "$", "GBP": "£"}
    s = simboli.get((currency or "").upper(), "")
    txt = f"{value:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{s}{txt}" if s else f"{txt} {currency}"


def load_team_rows(config: dict, since: float | None = None):
    """Righe per postazione dall'archivio del raccoglitore.

    Restituisce (righe, livello_riservatezza, nota, riepilogo). La nota spiega
    perche' non c'e' nulla, quando non c'e' nulla: senza, una scheda vuota
    sembra un guasto.
    """
    try:
        import cm_collector
    except ImportError:
        return [], None, "cm_collector.py non trovato accanto al pannello", {}

    for path in team_db_candidates(config):
        if not os.path.isfile(path):
            continue
        try:
            store = cm_collector.Store(path)          # sola lettura
            righe = cm_collector.team_rows(store, since)
            mesi = cm_collector.observed_months(store, since)[0]
            store.con.close()
        except Exception as exc:                      # archivio illeggibile
            return [], None, f"archivio non leggibile: {exc}", {}
        if not righe:
            return [], store.level, ("archivio presente ma ancora vuoto — "
                                     "il raccoglitore non ha ricevuto dati"), {}

        team = config.get("team") or {}
        righe, riepilogo = cm_collector.team_costs(
            righe, team, mesi, cm.fx_usd_per_unit(config))
        valuta = riepilogo["currency"]
        for r in righe:
            r["paid_txt"] = team_money(r["paid"], valuta) if r["paid"] else "—"
            r["ratio_txt"] = fmt_ratio(r["ratio"])
        return righe, store.level, "", riepilogo

    return [], None, ("nessun archivio di team: avvia il raccoglitore con "
                      "python cm_collector.py"), {}


# Spiegazioni al passaggio del mouse sull'intestazione.
def build_help(pricing: dict, totals: dict) -> dict:
    api = cm.cost_columns(pricing)[1] is None
    sub = cm.subscription_of(pricing)
    quota = MONEY(float(sub["monthly_cost"])) if sub else "la quota"
    pagato = MONEY(totals.get("real", 0.0))
    if api:
        costo = ("Quanto hai speso davvero.\n\n"
                 "Sei a consumo: il prezzo dei token È l'addebito.")
    else:
        costo = ("Quanto ti sarebbe costato a listino API.\n\n"
                 f"NON l'hai pagato: hai l'abbonamento, paghi {quota} al mese "
                 "qualunque cosa tu faccia. Serve a misurare il consumo in una "
                 "unità comprensibile, e a sapere cosa succederebbe se passassi "
                 f"all'API. In tutto hai pagato {pagato}.")
    return {
        "project": "Cartella del progetto, dal percorso di lavoro della sessione.",
        "title": "Titolo che Claude Code assegna da solo alla conversazione.",
        "cost": costo,
        "hyp": costo,
        "share": ("Quanto pesa questa riga sul consumo totale mostrato.\n\n"
                  "La barra confronta col massimo (si vede la classifica), la "
                  "percentuale è sul totale (si legge il peso vero).\n\n"
                  + ("" if api else
                     "NON è una fetta di euro: l'abbonamento si paga uguale, "
                     "e in un mese usato poco quasi tutta la quota resta "
                     "semplicemente inutilizzata. Per quello guarda la scheda Mesi.")),
        "real": (f"Soldi davvero usciti dal conto: {quota} per ogni mese in cui "
                 "hai usato Claude Code.\n\nSi paga uguale anche in un mese "
                 "usato poco." if not api else
                 "Quanto hai speso davvero a consumo."),
        "ratio": ("Quanto valore hai tirato fuori dall'abbonamento.\n\n"
                  "5× vuol dire che hai consumato l'equivalente di cinque volte "
                  "quello che hai pagato. Sotto 1× l'abbonamento è in perdita."),
        "sessions": "Numero di conversazioni distinte.",
        "active": "Tempo di lavoro vero: le pause oltre 5 minuti non contano.",
        "msgs": "I tuoi turni / i messaggi di Claude.\n\n"
                "Il secondo è molto più alto perché ogni lettura di file, comando "
                "o modifica è un messaggio a sé.",
        "tokens": "Tutti i token trattati, cache compresa.",
        "output": "Token generati da Claude. Sono i più cari, ma la fetta minore.",
        "tout": "Token generati da Claude. Sono i più cari, ma la fetta minore.",
        "cr": ("Token riletti dalla cache: il contesto della conversazione, "
               "ricaricato a ogni messaggio.\n\nCostano un decimo dell'input, ma "
               "sono così tanti da fare la parte più grossa del conto."),
        "start": "Primo messaggio della sessione.",
        "last": "Ultima attività.",
        "month": "Mese di fatturazione. I messaggi sono attribuiti al mese in cui "
                 "sono avvenuti, quindi una sessione lunga può contare su due mesi.",
    }

PERIODS = [("Sempre", None), ("Oggi", "oggi"), ("7 giorni", "7d"),
           ("30 giorni", "30d"), ("90 giorni", "90d")]

# Switch abbonamento / consumo: cambia il significato del costo REALE.
BILLING_MODES = [("Abbonamento", "subscription"), ("API a consumo", "api")]

MONTH_NAMES = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
               "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


# --------------------------------------------------------------------------- #
# Finestra di dettaglio
# --------------------------------------------------------------------------- #


class DetailWindow(tk.Toplevel):
    def __init__(self, app, session: dict):
        super().__init__(app.root, bg=app.t["page"])
        self.app = app
        self.t = app.t
        self.session = session
        self.title(f"{APP_TITLE} — {session['project']} · {session['session_id'][:8]}")
        self.geometry("1120x660")
        self.q: queue.Queue = queue.Queue()
        set_titlebar(self, self.t.mode == "dark")

        head = tk.Frame(self, bg=self.t["page"])
        head.pack(fill="x", padx=18, pady=(16, 10))
        self.l_title = tk.Label(head, text=session["title"] or "caricamento…",
                                bg=self.t["page"], fg=self.t["ink"],
                                font=self.t.f_title, anchor="w", justify="left")
        self.l_title.pack(fill="x")
        self.l_meta = tk.Label(head, text="", bg=self.t["page"], fg=self.t["ink2"],
                               font=self.t.f_small, anchor="w", justify="left")
        self.l_meta.pack(fill="x", pady=(3, 0))

        tiles = tk.Frame(self, bg=self.t["page"])
        tiles.pack(fill="x", padx=18)
        self.tiles = {}
        for key, label in (("cost", "Costo"), ("active", "Tempo attivo"),
                           ("msgs", "Messaggi"), ("tools", "Chiamate a tool")):
            tile = StatTile(tiles, self.t, label, width=175)
            tile.pack(side="left", padx=(0, 10))
            self.tiles[key] = tile

        def num(get, fmt):
            return lambda r: "" if r["prompt"] else fmt(get(r))

        cols = [
            ("time",  "Ora",       75, "w", lambda r: r["ts"] or 0,
             lambda r: cm.h_time(r["ts"], "%H:%M:%S")),
            ("model", "Modello",  150, "w", lambda r: r["model"],
             lambda r: r["model"] + ("  ·sub" if r["subagent"] else "")),
            ("what",  "Tool / testo", 300, "w", lambda r: r["what"], lambda r: r["what"]),
            ("tout",  "Output",    75, "e", lambda r: r["tok"]["output"],
             num(lambda r: r["tok"]["output"], Fmt.tokens)),
            ("cw",    "Cache W",   80, "e", lambda r: r["tok"]["cache_w5m"] + r["tok"]["cache_w1h"],
             num(lambda r: r["tok"]["cache_w5m"] + r["tok"]["cache_w1h"], Fmt.tokens)),
            ("cr",    "Cache R",   80, "e", lambda r: r["tok"]["cache_read"],
             num(lambda r: r["tok"]["cache_read"], Fmt.tokens)),
            ("cost",  "Costo",     85, "e", lambda r: r["cost"],
             num(lambda r: r["cost"], Fmt.cost)),
            ("share", "Peso",     90, "e", lambda r: r["cost"], lambda r: ""),
            ("cum",   "Cumulato",  90, "e", lambda r: r["cum"], lambda r: Fmt.cost(r["cum"])),
        ]
        bar = tk.Frame(self, bg=self.t["page"])
        bar.pack(fill="x", padx=18, pady=(14, 8))
        self.seg = Segmented(bar, self.t, ["Conversazione", "Costi"], self._on_view)
        self.seg.pack(side="left")
        b_md = FlatButton(bar, self.t, "Esporta .md", command=self.export_markdown,
                          width=100)
        b_md.set_bg(self.t["page"])
        b_md.pack(side="right")

        self.holder = tk.Frame(self, bg=self.t["page"])
        self.holder.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        self.table = DataTable(self.holder, self.t, cols, share_key=lambda r: r["cost"])
        self.table.set_placeholder("caricamento…")
        self.chat = ChatView(self.holder, self.t)
        self.chat.set_placeholder("caricamento…")
        self.chat.pack(fill="both", expand=True)
        self.current = self.chat

        self.session_full = None
        self.messages = []
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(PUMP_MS, self._pump)

    def _on_view(self, index):
        self.current.pack_forget()
        self.current = self.chat if index == 0 else self.table
        self.current.pack(fill="both", expand=True)

    def export_markdown(self):
        if not self.session_full:
            return
        title = (self.session_full.get("title") or "conversazione")
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "-" for ch in title)[:60]
        when = cm.h_time(self.session_full.get("start"), "%Y-%m-%d")
        path = filedialog.asksaveasfilename(
            parent=self, title="Esporta la conversazione", defaultextension=".md",
            initialfile=f"{when} {safe}.md".strip(),
            filetypes=[("Markdown", "*.md"), ("Tutti i file", "*.*")])
        if not path:
            return
        try:
            text = cm.conversation_markdown(self.session_full, self.messages,
                                            self.app.pricing)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.l_meta.config(text=f"esportata in {path}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Esportazione fallita:\n{exc}")

    def _worker(self):
        try:
            pricing, base = self.app.pricing, self.app.base
            main = main_transcript_for(base, self.session["session_id"])
            files = cm.session_files_from_transcript(main) if main else self.session["files"]
            sess = cm.new_session(self.session["session_id"])
            messages = []
            for path in files:
                rec = cm.scan_file(path, pricing, keep_messages=True)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0
                cm.merge_record(sess, rec, mtime)
                for m in rec.get("messages", []):
                    m["subagent"] = rec["is_subagent"]
                    messages.append(m)
            cm.finalize(sess, pricing, self.app.idle_gap)
            messages.sort(key=lambda m: (m["ts"] is None, m["ts"] or 0))
            self.q.put(("chat", sess, messages))

            rows, running = [], 0.0
            for m in messages:
                if m["kind"] == "prompt":
                    rows.append({"ts": m["ts"], "model": "tu", "subagent": False,
                                 "prompt": True, "tok": cm.new_tok(), "cost": 0.0,
                                 "cum": running, "what": m["text"][:300]})
                    continue
                cost, _ = cm.cost_of(m["model"], m["tok"], pricing)
                running += cost
                rows.append({"ts": m["ts"], "model": m["model"], "subagent": m["subagent"],
                             "prompt": False, "tok": m["tok"], "cost": cost,
                             "cum": running, "what": ", ".join(dict.fromkeys(m["tools"]))})
            self.q.put(("ok", sess, rows))
        except BaseException:
            self.q.put(("error", traceback.format_exc(), None))

    def _pump(self):
        try:
            kind, a, b = self.q.get_nowait()
        except queue.Empty:
            self.after(PUMP_MS, self._pump)
            return
        if kind == "chat":
            # arriva prima del resto: la conversazione è quello che si guarda subito
            self.session_full, self.messages = a, b
            self.chat.render(b)
            self.after(PUMP_MS, self._pump)
            return
        if kind == "error":
            self.l_title.config(text="errore nel calcolo del dettaglio")
            self.table.set_placeholder((a or "").strip().splitlines()[-1])
            self.chat.set_placeholder("non sono riuscito a leggere la conversazione")
            return
        sess, rows = a, b
        agents = ", ".join(f"{k}×{v}" for k, v in sorted(sess["agents"].items()))
        self.l_title.config(text=sess["title"] or sess["first_prompt"] or "(senza titolo)")
        self.l_meta.config(text=(
            f"{sess['cwd'] or ''}   ·   branch {sess['git_branch'] or '—'}   ·   "
            f"Claude Code v{sess['version'] or '?'}   ·   "
            f"{cm.h_time(sess['start'], '%d/%m/%Y %H:%M')} → "
            f"{cm.h_time(sess['end'], '%d/%m/%Y %H:%M')}"
            + (f"   ·   subagent: {agents}" if agents else "")))
        notional = (self.app.pricing.get("plan") or "").lower() == "subscription"
        self.tiles["cost"].set(Fmt.cost(sess["cost"]),
                               "stima nozionale" if notional else "costo API reale")
        self.tiles["active"].set(Fmt.dur(sess["active"]), f"su {Fmt.dur(sess['duration'])} totali")
        self.tiles["msgs"].set(str(sess["assistant_msgs"]),
                               f"{sess['user_prompts']} tuoi · {sess['subagent_prompts']} subagent")
        self.tiles["tools"].set(str(sess["tool_calls"]),
                                f"{sess['api_errors']} errori API")
        self.table.set_rows(rows)


# --------------------------------------------------------------------------- #
# Pannello di configurazione
# --------------------------------------------------------------------------- #


class Field(tk.Frame):
    """Etichetta + casella di testo, con una riga di spiegazione sotto."""

    def __init__(self, master, theme: Theme, label, value, hint="", width=24):
        super().__init__(master, bg=theme["surface"])
        self.t = theme
        row = tk.Frame(self, bg=theme["surface"])
        row.pack(fill="x")
        tk.Label(row, text=label, bg=theme["surface"], fg=theme["ink"],
                 font=theme.f_body, anchor="w", width=20).pack(side="left")
        box = tk.Frame(row, bg=theme["surface"], highlightthickness=1,
                       highlightbackground=theme["border"],
                       highlightcolor=theme["accent"])
        box.pack(side="left")
        self.var = tk.StringVar(value="" if value is None else str(value))
        self.entry = tk.Entry(box, textvariable=self.var, bd=0, relief="flat",
                              bg=theme["surface"], fg=theme["ink"],
                              insertbackground=theme["ink"], font=theme.f_body,
                              width=width)
        self.entry.pack(padx=8, pady=5)
        self.l_err = tk.Label(row, text="", bg=theme["surface"], fg="#e34948",
                              font=theme.f_small)
        self.l_err.pack(side="left", padx=(8, 0))
        if hint:
            tk.Label(self, text=hint, bg=theme["surface"], fg=theme["muted"],
                     font=theme.f_small, anchor="w", justify="left",
                     wraplength=520).pack(fill="x", padx=(140, 0), pady=(1, 0))
        self.pack(fill="x", pady=6)

    def get(self):
        return self.var.get().strip()

    def error(self, msg=""):
        self.l_err.config(text=msg)


class ChoiceRow(tk.Frame):
    """Etichetta + scelta fra poche opzioni, come segmenti cliccabili."""

    def __init__(self, master, theme: Theme, label, options, value, hint=""):
        super().__init__(master, bg=theme["surface"])
        self.t = theme
        self.options = options
        self.value = value
        row = tk.Frame(self, bg=theme["surface"])
        row.pack(fill="x")
        tk.Label(row, text=label, bg=theme["surface"], fg=theme["ink"],
                 font=theme.f_body, anchor="w", width=20).pack(side="left")
        self.buttons = []
        for text, spec in options:
            b = FlatButton(row, theme, text, command=lambda s=spec: self.select(s))
            b.set_bg(theme["surface"])
            b.pack(side="left", padx=(0, 6))
            self.buttons.append((b, spec))
        self.select(value, fire=False)
        if hint:
            tk.Label(self, text=hint, bg=theme["surface"], fg=theme["muted"],
                     font=theme.f_small, anchor="w", justify="left",
                     wraplength=520).pack(fill="x", padx=(140, 0), pady=(1, 0))
        self.pack(fill="x", pady=6)

    def select(self, spec, fire=True):
        self.value = spec
        for b, s in self.buttons:
            b.set_active(s == spec)

    def get(self):
        return self.value


class ToggleRow(tk.Frame):
    def __init__(self, master, theme: Theme, label, value):
        super().__init__(master, bg=theme["surface"])
        self.t = theme
        self.value = bool(value)
        tk.Label(self, text=label, bg=theme["surface"], fg=theme["ink"],
                 font=theme.f_body, anchor="w", width=26).pack(side="left")
        self.c = tk.Canvas(self, width=38, height=20, highlightthickness=0, bd=0,
                           bg=theme["surface"])
        self.c.pack(side="left")
        self.c.bind("<Button-1>", lambda e: self.toggle())
        self._draw()
        self.pack(fill="x", pady=5)

    def _draw(self):
        t = self.t
        self.c.delete("all")
        on = self.value
        round_rect(self.c, 1, 2, 37, 18, 8,
                   fill=t["accent"] if on else t["track"], outline="")
        x = 28 if on else 10
        self.c.create_oval(x - 7, 3, x + 7, 17, fill="#ffffff", outline="")

    def toggle(self):
        self.value = not self.value
        self._draw()

    def get(self):
        return self.value


class ChatView(tk.Frame):
    """La conversazione, leggibile: cosa hai chiesto e cosa è stato fatto."""

    MAX_MESSAGES = 400   # oltre, la Text di Tk diventa lenta senza aggiungere nulla

    def __init__(self, master, theme: Theme):
        super().__init__(master, bg=theme["surface"], highlightthickness=1,
                         highlightbackground=theme["border"])
        self.t = theme
        self.text = tk.Text(self, bg=theme["surface"], fg=theme["ink"], bd=0,
                            relief="flat", wrap="word", font=theme.f_body,
                            padx=22, pady=16, spacing1=2, spacing3=4,
                            insertbackground=theme["ink"], highlightthickness=0,
                            cursor="arrow")
        self.text.pack(side="left", fill="both", expand=True)
        bar = tk.Scrollbar(self, orient="vertical", command=self.text.yview, width=10,
                           bd=0, relief="flat", troughcolor=theme["surface"],
                           bg=theme["border"], highlightthickness=0)
        bar.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=bar.set)

        self.text.tag_configure("you", foreground=theme["accent"],
                                font=(theme.f_body_bold.cget("family"), 10, "bold"),
                                spacing1=14, spacing3=4)
        self.text.tag_configure("who", foreground=theme["ink2"],
                                font=theme.f_body_bold, spacing1=10, spacing3=2)
        self.text.tag_configure("body", foreground=theme["ink"], lmargin1=0, lmargin2=0)
        self.text.tag_configure("yourtext", foreground=theme["ink"],
                                lmargin1=14, lmargin2=14)
        self.text.tag_configure("tools", foreground=theme["muted"],
                                font=theme.f_small, spacing3=6)
        self.text.tag_configure("note", foreground=theme["muted"], font=theme.f_small)
        self.text.tag_configure("strong", foreground=theme["ink"],
                                font=theme.f_body_bold)
        self.text.configure(state="disabled")

    _BOLD = __import__("re").compile(r"\*\*(.+?)\*\*", __import__("re").S)

    def _insert_rich(self, text, tag):
        """Inserisce il testo rendendo il grassetto Markdown, invece di mostrare gli asterischi."""
        pos = 0
        for m in self._BOLD.finditer(text):
            if m.start() > pos:
                self.text.insert("end", text[pos:m.start()], tag)
            self.text.insert("end", m.group(1), (tag, "strong"))
            pos = m.end()
        self.text.insert("end", text[pos:] + "\n", tag)

    def set_placeholder(self, msg):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", msg + "\n", "note")
        self.text.configure(state="disabled")

    def render(self, messages):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")

        visible = [m for m in messages if not m.get("subagent")]
        skipped = 0
        if len(visible) > self.MAX_MESSAGES:
            skipped = len(visible) - self.MAX_MESSAGES
            visible = visible[-self.MAX_MESSAGES:]
            t.insert("end", f"… {skipped} messaggi precedenti non mostrati "
                            f"(ci sono tutti nell'esportazione .md)\n\n", "note")

        pending = []

        def flush():
            if pending:
                uniq = list(dict.fromkeys(pending))
                t.insert("end", "⚙ " + ", ".join(uniq) + "\n", "tools")
                pending.clear()

        for m in visible:
            when = cm.h_time(m.get("ts"), "%d/%m %H:%M")
            if m["kind"] == "prompt":
                flush()
                t.insert("end", f"Tu · {when}\n", "you")
                self._insert_rich((m.get("text") or "").strip(), "yourtext")
                continue
            said = (m.get("text") or "").strip()
            if said:
                flush()
                t.insert("end", f"Claude · {when}\n", "who")
                self._insert_rich(said, "body")
            else:
                pending.extend(m.get("tools") or [])
        flush()
        if not visible:
            t.insert("end", "Nessun messaggio in questa sessione.\n", "note")
        t.configure(state="disabled")
        t.yview_moveto(0.0)   # si legge dall'inizio: è il percorso fatto


PERSON_PROJECT_COLUMNS = [
    ("project",  "Progetto",   210, "w", lambda r: (r["project"] or "").lower(),
     lambda r: r["project"]),
    ("cost",     "Se fosse API", 110, "e", lambda r: r["cost"],
     lambda r: Fmt.cost(r["cost"])),
    ("share",    "Quota del consumo", 170, "e", lambda r: r["cost"], lambda r: ""),
    ("sessions", "Sess",        55, "e", lambda r: r["sessions"],
     lambda r: r["sessions"]),
    ("active",   "Attivo",      85, "e", lambda r: r["active"],
     lambda r: Fmt.dur(r["active"])),
    ("msgs",     "Messaggi",    95, "e", lambda r: r["assistant_msgs"],
     lambda r: f"{r['user_prompts']}/{r['assistant_msgs']}"),
    ("last",     "Ultima",      80, "e", lambda r: r["last"],
     lambda r: Fmt.ago(r["last"])),
]


class PersonWindow(tk.Toplevel):
    """Su cosa ha lavorato una postazione: la vista per il ribaltamento.

    Legge dall'archivio del raccoglitore, non dai transcript locali: qui i
    progetti possono essere di un'altra macchina.
    """

    def __init__(self, app, riga: dict, config: dict):
        super().__init__(app.root, bg=app.t["page"])
        self.app = app
        self.t = app.t
        self.title(f"{APP_TITLE} — {riga['person']}")
        self.geometry("980x560")
        set_titlebar(self, self.t.mode == "dark")

        head = tk.Frame(self, bg=self.t["page"])
        head.pack(fill="x", padx=18, pady=(16, 10))
        tk.Label(head, text=riga["person"], bg=self.t["page"], fg=self.t["ink"],
                 font=self.t.f_title, anchor="w").pack(fill="x")
        fonte = ("dai transcript, storico completo" if riga.get("source") == "transcript"
                 else "solo telemetria: manca tutto quello che precede l'accensione")
        tk.Label(head, text=fonte, bg=self.t["page"], fg=self.t["ink2"],
                 font=self.t.f_small, anchor="w").pack(fill="x", pady=(3, 0))

        tiles = tk.Frame(self, bg=self.t["page"])
        tiles.pack(fill="x", padx=18)
        for etichetta, valore in (
            ("Se fosse API", Fmt.cost(riga["cost"])),
            ("Hai pagato", riga.get("paid_txt", "—")),
            ("Resa", riga.get("ratio_txt", "—")),
            ("Tempo attivo", Fmt.dur(riga["active"])),
            ("Sessioni", str(riga["sessions"])),
        ):
            t_ = StatTile(tiles, self.t, etichetta, width=150)
            t_.pack(side="left", padx=(0, 10))
            t_.set(valore)

        holder = tk.Frame(self, bg=self.t["page"])
        holder.pack(fill="both", expand=True, padx=18, pady=(14, 0))
        self.tbl = DataTable(holder, self.t, PERSON_PROJECT_COLUMNS,
                             share_key=lambda r: r["cost"])
        self.tbl.pack(fill="both", expand=True)

        stato = tk.Label(self, text="", bg=self.t["page"], fg=self.t["muted"],
                         font=self.t.f_small, anchor="w")
        stato.pack(fill="x", padx=18, pady=(6, 12))

        righe = []
        try:
            import cm_collector
            for path in team_db_candidates(config):
                if os.path.isfile(path):
                    store = cm_collector.Store(path)
                    righe = cm_collector.projects_of(store, riga["person"])
                    store.con.close()
                    break
        except Exception as exc:
            stato.config(text=f"archivio non leggibile: {exc}")

        if not righe:
            self.tbl.set_rows([])
            self.tbl.set_placeholder(
                "nessun progetto: la telemetria non sa su cosa si lavora, "
                "serve cm_agent.py su quella macchina")
            stato.config(text="i progetti vengono solo dai transcript")
            return

        self.tbl.set_rows(righe, {
            "project":  f"{len(righe)} progetti",
            "cost":     Fmt.cost(sum(r["cost"] for r in righe)),
            "sessions": str(sum(r["sessions"] for r in righe)),
            "active":   Fmt.dur(sum(r["active"] for r in righe)),
            "msgs":     f"{sum(r['user_prompts'] for r in righe)}/"
                        f"{sum(r['assistant_msgs'] for r in righe)}",
        })
        stato.config(text="i progetti vengono dai transcript spediti da cm_agent")


class SettingsWindow(tk.Toplevel):
    """Configurazione con dei campi veri, non un file JSON da interpretare."""

    def __init__(self, app):
        super().__init__(app.root, bg=app.t["page"])
        self.app = app
        self.t = app.t
        self.title(f"{APP_TITLE} — configurazione")
        self.geometry("790x780")
        self.minsize(740, 640)
        self.transient(app.root)
        set_titlebar(self, self.t.mode == "dark")

        self.path = app.pricing.get("_path") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.raw = json.load(fh)
        except Exception:
            self.raw = {}

        sub = self.raw.get("subscription") or {}
        bil = self.raw.get("billing") or {}
        fx = self.raw.get("fx") or {}
        dft = self.raw.get("defaults") or {}
        sl = self.raw.get("statusline") or {}
        self.theme_before = dft.get("theme", "auto")

        head = tk.Frame(self, bg=self.t["page"])
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text="Configurazione", bg=self.t["page"], fg=self.t["ink"],
                 font=self.t.f_title).pack(anchor="w")
        tk.Label(head, text=self.path, bg=self.t["page"], fg=self.t["muted"],
                 font=self.t.f_small).pack(anchor="w")

        nav = tk.Frame(self, bg=self.t["page"])
        nav.pack(fill="x", padx=18)
        self.pages = {}
        self.body = tk.Frame(self, bg=self.t["page"])
        self.body.pack(fill="both", expand=True, padx=18, pady=(10, 0))
        self.seg = Segmented(nav, self.t,
                             ["Abbonamento", "Team", "Aspetto", "Statusline", "Listino"],
                             self._show_page)

        # ---- Abbonamento
        p = self._page("Abbonamento")
        self.f_mode = ChoiceRow(p, self.t, "Come paghi",
                                [("Abbonamento", "subscription"), ("API a consumo", "api")],
                                bil.get("mode", "subscription"),
                                "In abbonamento il costo per token è solo un riferimento: "
                                "quello che paghi è la quota. A consumo è l'addebito vero.")
        self.f_plan = Field(p, self.t, "Piano", sub.get("plan", ""))
        self.f_cost = Field(p, self.t, "Costo mensile", sub.get("monthly_cost", ""), width=12,
                            hint="Quanto ti addebitano davvero ogni mese, IVA compresa se "
                                 "la paghi. Non è scritto da nessuna parte su disco.")
        self.f_cur = ChoiceRow(p, self.t, "Valuta",
                               [("EUR", "EUR"), ("USD", "USD"), ("GBP", "GBP")],
                               (sub.get("currency") or "EUR").upper())
        self.f_note = Field(p, self.t, "Nota", sub.get("note", ""))
        self.f_since = Field(p, self.t, "Attivo dal", sub.get("since", ""), width=12,
                             hint="AAAA-MM-GG. Prima di questa data non vengono conteggiate quote.")
        self.f_fx = Field(p, self.t, "Cambio USD per unità", fx.get("usd_per_unit", ""),
                          width=12,
                          hint="Serve solo a confrontare il listino API (in USD) con la quota. "
                               "Vuoto = non mostrare la resa.")
        self.f_apiproj = Field(p, self.t, "Progetti a consumo",
                               ", ".join(bil.get("api_projects") or []), width=34,
                               hint="Separati da virgola: progetti lanciati con "
                                    "ANTHROPIC_API_KEY, fatturati a chiamata anche se hai l'abbonamento.")

        # ---- Team
        tm = self.raw.get("team") or {}
        p = self._page("Team")
        tk.Label(p, text="Consumo di più macchine, dalla telemetria di Claude Code "
                         "raccolta da cm_collector.py.",
                 bg=self.t["surface"], fg=self.t["muted"], font=self.t.f_small,
                 anchor="w", justify="left").pack(fill="x", pady=(0, 10))
        self.f_seats = Field(p, self.t, "Postazioni pagate", tm.get("seats", 0), width=8,
                             hint="Quante ne paghi in tutto, non quante ne vedi usare. "
                                  "Chi non usa lo strumento non manda telemetria e non "
                                  "compare: le postazioni ferme si scoprono solo così.")
        self.f_fee = Field(p, self.t, "Quota mensile per postazione",
                           tm.get("fee_per_seat", 0.0), width=12,
                           hint="0 spegne le colonne di spesa e lascia solo il consumo.")
        self.f_tcur = ChoiceRow(p, self.t, "Valuta della quota",
                                [("EUR", "EUR"), ("USD", "USD"), ("GBP", "GBP")],
                                (tm.get("currency") or "EUR").upper())
        self.f_tdb = Field(p, self.t, "Archivio del raccoglitore", tm.get("db") or "",
                           width=34,
                           hint="Vuoto = cercalo accanto al pannello (cm-team.db).")

        # Il livello di riservatezza non si imposta qui: lo decide il raccoglitore
        # quando scrive, perché la telemetria manda l'indirizzo comunque. Qui si
        # può solo mostrare quello in vigore, letto dall'archivio stesso.
        livello = "nessun archivio trovato"
        try:
            import cm_collector as _cc
            for _p in team_db_candidates(self.raw):
                if os.path.isfile(_p):
                    _s = _cc.Store(_p)
                    livello = _s.level
                    _s.con.close()
                    break
        except Exception:
            livello = "non leggibile"
        tk.Label(p, text=f"Riservatezza in vigore: {livello}",
                 bg=self.t["surface"], fg=self.t["ink2"], font=self.t.f_body,
                 anchor="w").pack(fill="x", pady=(14, 2))
        tk.Label(p, text="Si imposta sul raccoglitore, non qui: la telemetria manda "
                         "l'indirizzo di posta comunque, quindi il livello va imposto "
                         "dove il dato viene scritto.\n"
                         "    python cm_collector.py --privacy aggregato|pseudonimo|nominativo",
                 bg=self.t["surface"], fg=self.t["muted"], font=self.t.f_small,
                 anchor="w", justify="left").pack(fill="x")

        # ---- Aspetto
        p = self._page("Aspetto")
        self.f_theme = ChoiceRow(p, self.t, "Tema",
                                 [("Scuro", "dark"), ("Chiaro", "light"),
                                  ("Come Windows", "auto")],
                                 dft.get("theme", "auto"),
                                 "Cambiandolo l'applicazione si riavvia da sola.")
        self.f_locale = ChoiceRow(p, self.t, "Numeri",
                                  [("1,234.56", "us"), ("1.234,56", "it")],
                                  dft.get("locale", "us"))
        self.f_auto = Field(p, self.t, "Aggiorna ogni (min)",
                            dft.get("auto_refresh_minutes", 5), width=8,
                            hint="0 per disattivare la riscansione automatica.")
        self.f_idle = Field(p, self.t, "Pausa non attiva (s)", dft.get("idle_gap", 300),
                            width=8,
                            hint="Oltre questa pausa fra due eventi il tempo non conta come lavoro.")
        self.f_live = Field(p, self.t, "Live ogni (s)", dft.get("live_interval", 2.0), width=8)
        self.f_top = Field(p, self.t, "Sessioni mostrate", dft.get("top", 20), width=8,
                           hint="Vale per il CLI. 0 = tutte.")

        # ---- Statusline
        p = self._page("Statusline")
        self.t_sl = ToggleRow(p, self.t, "Segmento attivo", sl.get("enabled", True))
        self.t_cost = ToggleRow(p, self.t, "Mostra il costo", sl.get("show_cost", True))
        self.t_time = ToggleRow(p, self.t, "Mostra il tempo attivo", sl.get("show_active_time", True))
        self.t_msg = ToggleRow(p, self.t, "Mostra i messaggi", sl.get("show_messages", True))
        self.t_lim = ToggleRow(p, self.t, "Mostra i limiti del piano", sl.get("show_limits", True))
        self.f_warn = Field(p, self.t, "Arancione oltre (%)", sl.get("limit_warn_pct", 75), width=8)
        self.f_crit = Field(p, self.t, "Rosso oltre (%)", sl.get("limit_critical_pct", 90), width=8,
                            hint="Percentuale della finestra di 5 ore consumata.")

        # ---- Listino
        p = self._page("Listino")
        tk.Label(p, text="Prezzi in USD per 1.000.000 di token.",
                 bg=self.t["surface"], fg=self.t["muted"], font=self.t.f_small,
                 anchor="w").pack(fill="x", pady=(0, 8))
        head_row = tk.Frame(p, bg=self.t["surface"])
        head_row.pack(fill="x")
        for text, w in (("Modello", 26), ("Input", 10), ("Output", 10)):
            tk.Label(head_row, text=text, bg=self.t["surface"], fg=self.t["muted"],
                     font=self.t.f_head, anchor="w", width=w).pack(side="left")
        wrap = tk.Frame(p, bg=self.t["surface"])
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=self.t["surface"], highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        bar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview, width=10,
                           bd=0, relief="flat", troughcolor=self.t["surface"],
                           bg=self.t["border"], highlightthickness=0)
        bar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=bar.set)
        inner = tk.Frame(canvas, bg=self.t["surface"])
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.model_rows = []
        for name, price in (self.raw.get("models") or {}).items():
            row = tk.Frame(inner, bg=self.t["surface"])
            row.pack(fill="x", pady=1)
            e_name = self._mini(row, name, 24)
            e_in = self._mini(row, price.get("in", ""), 8)
            e_out = self._mini(row, price.get("out", ""), 8)
            self.model_rows.append((e_name, e_in, e_out))

        # ---- azioni
        foot = tk.Frame(self, bg=self.t["page"])
        foot.pack(fill="x", padx=18, pady=14)
        self.l_msg = tk.Label(foot, text="", bg=self.t["page"], fg=self.t["muted"],
                              font=self.t.f_small)
        self.l_msg.pack(side="left")
        b_save = FlatButton(foot, self.t, "Salva", command=self.save, primary=True, width=90)
        b_save.set_bg(self.t["page"])
        b_save.pack(side="right")
        b_cancel = FlatButton(foot, self.t, "Annulla", command=self.destroy, width=84)
        b_cancel.set_bg(self.t["page"])
        b_cancel.pack(side="right", padx=(0, 8))

        self.seg.pack(side="left")
        self._show_page(0)

    # -- costruzione ------------------------------------------------------- #

    def _mini(self, master, value, width):
        box = tk.Frame(master, bg=self.t["surface"], highlightthickness=1,
                       highlightbackground=self.t["border"])
        box.pack(side="left", padx=(0, 8))
        var = tk.StringVar(value="" if value is None else str(value))
        tk.Entry(box, textvariable=var, bd=0, relief="flat", bg=self.t["surface"],
                 fg=self.t["ink"], insertbackground=self.t["ink"],
                 font=self.t.f_body, width=width).pack(padx=6, pady=3)
        return var

    def _page(self, name):
        frame = tk.Frame(self.body, bg=self.t["surface"], highlightthickness=1,
                         highlightbackground=self.t["border"])
        inner = tk.Frame(frame, bg=self.t["surface"])
        inner.pack(fill="both", expand=True, padx=18, pady=16)
        self.pages[name] = frame
        self._current_page = inner
        return inner

    def _show_page(self, index):
        name = ["Abbonamento", "Aspetto", "Statusline", "Listino"][index]
        for key, frame in self.pages.items():
            frame.pack_forget()
        self.pages[name].pack(fill="both", expand=True)

    # -- salvataggio -------------------------------------------------------- #

    def _num(self, field, cast, required=True, minimum=None):
        raw = field.get()
        if not raw:
            if required:
                field.error("obbligatorio")
                return None, False
            field.error("")
            return None, True
        try:
            value = cast(raw.replace(",", "."))
        except ValueError:
            field.error("non è un numero")
            return None, False
        if minimum is not None and value < minimum:
            field.error(f"min {minimum}")
            return None, False
        field.error("")
        return value, True

    def save(self):
        ok = True
        cost, good = self._num(self.f_cost, float, minimum=0)
        ok &= good
        fx, good = self._num(self.f_fx, float, required=False, minimum=0)
        ok &= good
        auto, good = self._num(self.f_auto, float, minimum=0)
        ok &= good
        idle, good = self._num(self.f_idle, float, minimum=1)
        ok &= good
        live, good = self._num(self.f_live, float, minimum=0.5)
        ok &= good
        top, good = self._num(self.f_top, int, minimum=0)
        ok &= good
        warn, good = self._num(self.f_warn, float, minimum=0)
        ok &= good
        crit, good = self._num(self.f_crit, float, minimum=0)
        ok &= good
        seats, good = self._num(self.f_seats, int, minimum=0)
        ok &= good
        fee, good = self._num(self.f_fee, float, minimum=0)
        ok &= good
        since = self.f_since.get()
        if since and not re_date(since):
            self.f_since.error("AAAA-MM-GG")
            ok = False
        else:
            self.f_since.error("")
        if not ok:
            self.l_msg.config(text="correggi i campi in rosso", fg="#e34948")
            return

        raw = self.raw
        raw.setdefault("billing", {})["mode"] = self.f_mode.get()
        raw["billing"]["api_projects"] = [s.strip() for s in
                                          self.f_apiproj.get().split(",") if s.strip()]
        raw.setdefault("subscription", {}).update({
            "plan": self.f_plan.get(), "monthly_cost": cost,
            "currency": self.f_cur.get(), "note": self.f_note.get(), "since": since,
        })
        raw.setdefault("fx", {})["usd_per_unit"] = fx
        raw.setdefault("team", {}).update({
            "seats": seats, "fee_per_seat": fee,
            "currency": self.f_tcur.get(), "db": self.f_tdb.get().strip() or None,
        })
        raw.setdefault("defaults", {}).update({
            "theme": self.f_theme.get(), "locale": self.f_locale.get(),
            "auto_refresh_minutes": auto, "idle_gap": idle,
            "live_interval": live, "top": top,
        })
        raw.setdefault("statusline", {}).update({
            "enabled": self.t_sl.get(), "show_cost": self.t_cost.get(),
            "show_active_time": self.t_time.get(), "show_messages": self.t_msg.get(),
            "show_limits": self.t_lim.get(),
            "limit_warn_pct": warn, "limit_critical_pct": crit,
        })
        models = {}
        for e_name, e_in, e_out in self.model_rows:
            name = e_name.get().strip()
            if not name:
                continue
            try:
                models[name] = {"in": float(e_in.get()), "out": float(e_out.get())}
            except ValueError:
                self.l_msg.config(text=f"prezzo non valido per {name}", fg="#e34948")
                return
        if models:
            raw["models"] = models
        raw["updated"] = dt.date.today().isoformat()

        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Non riesco a salvare:\n{exc}")
            return

        if self.f_theme.get() != self.theme_before:
            self.l_msg.config(text="riavvio…", fg=self.t["muted"])
            self.after(250, self.app.restart)
            return
        self.destroy()
        self.app.apply_config_change()


def re_date(value: str) -> bool:
    try:
        dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Applicazione
# --------------------------------------------------------------------------- #


class App:
    def __init__(self, root: tk.Tk, args, config: dict | None = None):
        self.root = root
        self.base = args.base
        self.idle_gap = args.idle_gap
        self.pricing = config if config is not None else cm.load_config(args.pricing)
        self.q: queue.Queue = queue.Queue()
        self.gen = 0
        self.scanning = False
        self.sessions: list[dict] = []
        self.filtered: list[dict] = []
        # Scheda Persone: fonte separata dai transcript, popolata dal raccoglitore
        self.team_rows: list[dict] = []
        self.team_hint = ""
        self.team_summary: dict = {}
        self.live_stop: threading.Event | None = None
        self.live_gen = 0
        self.period_spec = None
        self.project_filter = ""
        self.pending_detail = getattr(args, "detail", None)
        self.auto_refresh_min = float(getattr(args, "auto_refresh", None) or 0)
        self._auto_job = None
        self._next_auto = 0.0
        self.auto_triggered = False
        # già caricata all'avvio: la prima scansione non deve rileggerla e
        # sovrascrivere le opzioni passate da riga di comando
        try:
            self._config_mtime = os.path.getmtime(self.pricing.get("_path") or "")
        except (OSError, TypeError):
            self._config_mtime = None
        self.state_path = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "claude-monitor", "gui.json")

        mode = args.theme
        if mode == "auto":
            mode = "dark" if windows_prefers_dark() else "light"
        self.t = Theme(mode)

        cm.LOG_HOOK = self._log_hook

        root.title(APP_TITLE)
        root.configure(bg=self.t["page"])
        root.minsize(980, 560)
        self._build_ui()
        set_titlebar(root, mode == "dark")
        app_icon(root, self.t)
        self._restore_state()
        self._relabel_costs()
        mode = (cm.billing_of(self.pricing).get("mode") or "subscription").lower()
        for i, (label, spec) in enumerate(BILLING_MODES):
            if spec == mode:
                self.dd_billing.value = label
                self.dd_billing.set_text(label + "  ▾")
        tab = getattr(args, "tab", None)
        if tab in ("sessioni", "mesi"):
            self.segmented.select(1 if tab == "sessioni" else 2)

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<F5>", lambda e: self.refresh())
        root.bind("<Control-c>", self._copy_selection)

        self.projects.set_placeholder("analisi dei transcript…")
        self.sessions_tbl.set_placeholder("analisi dei transcript…")
        self._pump_job = root.after(PUMP_MS, self._pump)
        self._boot_job = root.after(50, self.refresh)
        self._limits_job = root.after(120, self._tick_limits)
        self._schedule_auto_refresh()
        if getattr(args, "live", False) or getattr(self, "_restored_live", False):
            self.btn_live.set_active(True)
            root.after(200, self._toggle_live)

    # -- aggiornamento automatico ------------------------------------------- #

    def _schedule_auto_refresh(self) -> None:
        """Riscansione periodica dei transcript (default 5 minuti, 0 = disattivata).

        È indipendente dal Live: quello segue solo la sessione attiva, questo
        rinfresca tutte le sessioni e i totali.
        """
        if self._auto_job is not None:
            try:
                self.root.after_cancel(self._auto_job)
            except Exception:
                pass
            self._auto_job = None
        if self.auto_refresh_min <= 0:
            return
        self._next_auto = time.time() + self.auto_refresh_min * 60
        self._auto_job = self.root.after(int(self.auto_refresh_min * 60_000),
                                         self._auto_refresh)

    def _auto_refresh(self) -> None:
        self._auto_job = None
        if not self.scanning:
            self.auto_triggered = True
            self.refresh()
        self._schedule_auto_refresh()

    def _tick_limits(self) -> None:
        """Consumo dei limiti del piano: file piccolo, rilettura ogni 30 s.

        Indipendente dal Live, perché i limiti valgono anche quando non stai
        lavorando — anzi, è quando riprendi che vuoi sapere quanto ne resta.
        """
        try:
            sl = self.pricing.get("statusline") or {}
            self.limits_tile.set_thresholds(float(sl.get("limit_warn_pct", 75)),
                                            float(sl.get("limit_critical_pct", 90)))
            self.limits_tile.set_data(read_plan_limits())
        except Exception:
            pass
        self._limits_job = self.root.after(30_000, self._tick_limits)

    # -- UI ----------------------------------------------------------------- #

    def _build_ui(self):
        t = self.t
        root = self.root

        # ---- barra superiore
        top = tk.Frame(root, bg=t["page"])
        top.pack(fill="x", padx=20, pady=(16, 0))
        left = tk.Frame(top, bg=t["page"])
        left.pack(side="left")
        tk.Label(left, text=APP_TITLE, bg=t["page"], fg=t["ink"],
                 font=t.f_title).pack(side="left")
        self.l_scope = tk.Label(left, text="", bg=t["page"], fg=t["muted"],
                                font=t.f_small)
        self.l_scope.pack(side="left", padx=(10, 0), pady=(4, 0))

        right = tk.Frame(top, bg=t["page"])
        right.pack(side="right")
        self.btn_live = FlatButton(right, t, "● Live", command=self._toggle_live,
                                   toggle=True, width=76)
        self.btn_live.set_bg(t["page"])
        self.btn_live.pack(side="right", padx=(6, 0))
        self.btn_refresh = FlatButton(right, t, "Aggiorna", command=self.refresh, width=84)
        self.btn_refresh.set_bg(t["page"])
        self.btn_refresh.pack(side="right", padx=(6, 0))
        b_config = FlatButton(right, t, "⚙ Configura", command=self.open_config, width=104)
        b_config.set_bg(t["page"])
        b_config.pack(side="right", padx=(6, 0))
        b_export = FlatButton(right, t, "Esporta  ▾", command=self._export_menu, width=88)
        b_export.set_bg(t["page"])
        b_export.pack(side="right", padx=(6, 0))
        self.b_export = b_export
        self.menu_export = tk.Menu(b_export, tearoff=0, bg=t["surface"], fg=t["ink"],
                                   activebackground=t["accent"], activeforeground="#ffffff",
                                   bd=0, font=t.f_body, relief="flat")
        self.menu_export.add_command(label="Conversazioni in Markdown…",
                                     command=self.export_conversations)
        self.menu_export.add_command(label="Dati in JSON…", command=self.export_json)
        self.menu_export.add_command(label="Riepilogo del team in Markdown…",
                                     command=self.export_relazione)
        self.search = SearchBox(right, t, "filtra progetto", self._on_search, width=140)
        self.search.pack(side="right", padx=(6, 0))
        self.dd_period = Dropdown(right, t, PERIODS, self._on_period, width=104)
        self.dd_period.set_bg(t["page"])
        self.dd_period.pack(side="right", padx=(6, 0))
        self.dd_billing = Dropdown(right, t, BILLING_MODES, self._on_billing, width=136)
        self.dd_billing.set_bg(t["page"])
        self.dd_billing.pack(side="right")

        # ---- tessere
        tiles = tk.Frame(root, bg=t["page"])
        tiles.pack(fill="x", padx=20, pady=(14, 0))
        self.tiles = {}
        # la prima tessera è l'unica cifra davvero uscita dal conto
        for key, label in (("paid", "Hai pagato"), ("cost", "Se fosse API"),
                           ("active", "Tempo attivo"), ("msgs", "Messaggi"),
                           ("live", "Sessione attiva")):
            tile = StatTile(tiles, t, label, width=205 if key == "live" else 158)
            tile.pack(side="left", padx=(0, 10))
            self.tiles[key] = tile
        self.limits_tile = LimitsTile(tiles, t, width=250)
        self.limits_tile.pack(side="left")

        # ---- barra di avanzamento (sottile, sotto le tessere)
        self.progress = tk.Canvas(root, height=3, highlightthickness=0, bd=0,
                                  bg=t["page"])
        self.progress.pack(fill="x", padx=20, pady=(12, 0))
        self._progress_value = 0.0

        # ---- selettore + tabelle
        bar = tk.Frame(root, bg=t["page"])
        bar.pack(fill="x", padx=20, pady=(10, 8))
        self.segmented = Segmented(bar, t, ["Progetti", "Sessioni", "Mesi", "Persone"],
                                   self._on_tab)
        self.segmented.pack(side="left")
        self.l_hint = tk.Label(bar, text="doppio click su una sessione per il dettaglio",
                               bg=t["page"], fg=t["muted"], font=t.f_small)
        self.l_hint.pack(side="right", pady=(6, 0))

        holder = tk.Frame(root, bg=t["page"])
        holder.pack(fill="both", expand=True, padx=20)
        self.projects = DataTable(holder, t, PROJECT_COLUMNS,
                                  on_activate=self.open_project,
                                  share_key=lambda r: r["cost"])
        self.sessions_tbl = DataTable(holder, t, SESSION_COLUMNS,
                                      on_activate=self.open_detail,
                                      share_key=lambda r: r["cost"])
        self.months_tbl = DataTable(holder, t, MONTH_COLUMNS,
                                    share_key=lambda r: r["hyp"])
        self.team_tbl = DataTable(holder, t, TEAM_COLUMNS,
                                  on_activate=self.open_person,
                                  share_key=lambda r: r["cost"])
        self.holder = holder
        self.projects.pack(fill="both", expand=True)
        self.current_table = self.projects
        self._tables = (self.projects, self.sessions_tbl,
                        self.months_tbl, self.team_tbl)

        # ---- barra di stato
        status = tk.Frame(root, bg=t["page"])
        status.pack(fill="x", padx=20, pady=(8, 12))
        self.l_status = tk.Label(status, text="pronto", bg=t["page"], fg=t["ink2"],
                                 font=t.f_small, anchor="w")
        self.l_status.pack(side="left")
        tk.Label(status, text=cm.plan_note(self.pricing), bg=t["page"],
                 fg=t["muted"], font=t.f_small, anchor="e").pack(side="right")

    def _on_tab(self, index):
        for table in self._tables:
            table.pack_forget()
        self.current_table = self._tables[index]
        self.current_table.pack(fill="both", expand=True)
        if index == 1:
            hint = "doppio click su una sessione per il dettaglio"
        elif index == 3:
            hint = self.team_hint or "doppio click su una postazione per i suoi progetti"
        else:
            hint = getattr(self, "legend", "")
        self.l_hint.config(text=hint)

    def _set_progress(self, frac):
        """Barra sottile visibile solo durante la scansione: a riposo sparisce."""
        c = self.progress
        c.delete("all")
        if frac <= 0:
            return
        width = max(c.winfo_width(), 1)
        round_rect(c, 0, 0, width, 3, 1.5, fill=self.t["track"], outline="")
        round_rect(c, 0, 0, max(3, width * frac), 3, 1.5,
                   fill=self.t["accent"], outline="")

    # -- scansione ----------------------------------------------------------- #

    def refresh(self):
        if self.scanning:
            return
        self._reload_config()
        self.scanning = True
        self.gen += 1
        gen = self.gen
        self.btn_refresh.set_text("…")
        self.l_status.config(text="analisi dei transcript…")
        threading.Thread(target=self._scan_worker, args=(gen,), daemon=True).start()

    def _scan_worker(self, gen):
        try:
            def cb(done, total, path, cached):
                self.q.put(("progress", gen, (done, total)))
            sessions = cm.collect(self.base, self.pricing, True, self.idle_gap,
                                  None, True, cb)
            self.q.put(("sessions", gen, sessions))
        except BaseException:
            self.q.put(("error", gen, traceback.format_exc()))
        # L'archivio del team e' una fonte separata dai transcript: se manca o e'
        # illeggibile la scheda lo dice, ma la scansione locale non ne risente.
        try:
            self.q.put(("team", gen, load_team_rows(self.pricing)))
        except BaseException:
            self.q.put(("team", gen, ([], None, "lettura fallita")))

    def _reload_config(self) -> None:
        """Rilegge config.json a ogni Aggiorna: prezzi, abbonamento e default
        cambiano senza riavviare. Il tema no — quello si applica al riavvio."""
        path = self.pricing.get("_path")
        if not path or not os.path.isfile(path):
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if mtime == getattr(self, "_config_mtime", None):
            return
        self._config_mtime = mtime
        try:
            fresh = cm.load_config(path)
        except Exception:
            return
        # lo switch scelto a mano nella GUI vince sul file finché la finestra è aperta
        mode = (cm.billing_of(self.pricing).get("mode") or "subscription")
        self.pricing = fresh
        self.pricing.setdefault("billing", {})["mode"] = mode
        d = cm.defaults_of(fresh)
        self.idle_gap = float(d.get("idle_gap", self.idle_gap))
        Fmt.italian = d.get("locale", "us") == "it"
        global SUB_CURRENCY
        SUB_CURRENCY = cm.display_currency(self.pricing)
        self._relabel_costs()

    def _log_hook(self, level, msg):
        self.q.put(("log", self.gen, (level, msg)))
        return True

    # -- live ---------------------------------------------------------------- #

    def _toggle_live(self):
        if self.btn_live.active:
            self.live_gen += 1
            self.live_stop = threading.Event()
            threading.Thread(target=self._live_worker,
                             args=(self.live_gen, self.live_stop), daemon=True).start()
            self.tiles["live"].set("…", "individuo la sessione")
        else:
            if self.live_stop:
                self.live_stop.set()
            self.live_stop = None
            self.tiles["live"].set("—", "live disattivato")

    def _live_worker(self, gen, stop):
        live: dict[str, cm.LiveFile] = {}
        target = None
        status = None
        tick = 0
        while True:
            try:
                if tick % LIVE_REDISCOVER == 0 or not target:
                    found, status = find_active_transcript(self.base)
                    if found != target:
                        target = found
                        live.clear()
                if not target:
                    self.q.put(("live", gen, None))
                else:
                    files = cm.session_files_from_transcript(target)
                    sess = cm.new_session(cm.session_id_from_path(target))
                    for path in files:
                        lf = live.get(path)
                        if lf is None:
                            lf = live[path] = cm.LiveFile(path, self.pricing)
                        lf.update()
                        try:
                            mtime = os.path.getmtime(path)
                        except OSError:
                            mtime = 0.0
                        cm.merge_record(sess, lf.snapshot(), mtime)
                    cm.finalize(sess, self.pricing, self.idle_gap)
                    sess["_status"] = status
                    self.q.put(("live", gen, sess))
            except Exception:
                self.q.put(("live", gen, None))
            tick += 1
            if stop.wait(LIVE_INTERVAL):
                return

    # -- coda -> UI (solo thread principale) --------------------------------- #

    def _pump(self):
        while True:
            try:
                kind, gen, payload = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "progress" and gen == self.gen:
                done, total = payload
                self._set_progress(done / total if total else 0)
            elif kind == "sessions" and gen == self.gen:
                self.scanning = False
                self.btn_refresh.set_text("Aggiorna")
                self._set_progress(0)
                self.sessions = payload
                # la ripartizione della quota va fatta su TUTTE le sessioni del mese,
                # non solo su quelle filtrate, altrimenti le quote non tornerebbero
                cm.allocate_real_cost(self.sessions, self.pricing)
                self.apply_filters()
                suffix = ""
                if self.auto_refresh_min > 0:
                    suffix = (f"  ·  prossimo aggiornamento automatico fra "
                              f"{int(self.auto_refresh_min)} min")
                self.l_status.config(
                    text=f"{len(self.filtered)} sessioni  ·  aggiornato "
                         f"{dt.datetime.now():%H:%M:%S}"
                         + ("  (automatico)" if self.auto_triggered else "") + suffix)
                self.auto_triggered = False
                if self.pending_detail:
                    needle = self.pending_detail.lower()
                    self.pending_detail = None
                    match = next((s for s in self.sessions
                                  if s["session_id"].lower().startswith(needle)), None)
                    if match:
                        self.open_detail(match)
                    else:
                        self.l_status.config(text=f"sessione '{needle}' non trovata")
            elif kind == "team" and gen == self.gen:
                self._render_team(*payload)
            elif kind == "error" and gen == self.gen:
                self.scanning = False
                self.btn_refresh.set_text("Aggiorna")
                self._set_progress(0)
                self.l_status.config(text="errore durante la scansione")
                messagebox.showerror(APP_TITLE, payload)
            elif kind == "export":
                what = payload[0]
                if what == "progress":
                    _, done, total, project = payload
                    self._set_progress(done / total if total else 0)
                    self.l_status.config(text=f"esporto {done}/{total}  ·  {project}")
                elif what == "done":
                    _, result, dove, _err = payload
                    self._set_progress(0)
                    self.b_export.set_text("Esporta  ▾")
                    persi = len(result["failed"])
                    self.l_status.config(
                        text=f"{result['written']} conversazioni in {result['projects']} "
                             f"progetti  ·  {dove}"
                             + (f"  ·  {persi} non leggibili" if persi else ""))
                    if messagebox.askyesno(APP_TITLE,
                                           f"Esportate {result['written']} conversazioni.\n\n"
                                           "Apro la cartella?"):
                        try:
                            os.startfile(dove)
                        except Exception:
                            pass
                else:
                    _, _r, _d, err = payload
                    self._set_progress(0)
                    self.b_export.set_text("Esporta  ▾")
                    messagebox.showerror(APP_TITLE, err)
            elif kind == "log":
                level, msg = payload
                self.l_status.config(text=("⚠ " if level == "warn" else "") + msg)
            elif kind == "live" and gen == self.live_gen and self.btn_live.active:
                self._render_live(payload)
        self._pump_job = self.root.after(PUMP_MS, self._pump)

    def _cancel_jobs(self):
        """Annulla i timer pendenti: senza questo Tk urla 'invalid command name'
        quando la finestra viene distrutta con dei callback ancora in coda."""
        for attr in ("_pump_job", "_auto_job", "_limits_job", "_boot_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _render_live(self, sess):
        if not sess:
            self.tiles["live"].set("—", "nessuna sessione attiva")
            return
        busy = sess.get("_status") == "busy"
        project = cm.trunc(sess["project"] or "?", 18)
        self.tiles["live"].set(
            Fmt.cost(sess["cost"]),
            f"{project} · {Fmt.dur(sess['active'])} · {sess['assistant_msgs']} msg",
            value_color=self.t["good"] if busy else None)

    # -- filtri --------------------------------------------------------------- #

    def _on_period(self, spec):
        self.period_spec = spec
        self.apply_filters()

    def _write_config(self, section: str, key: str, value) -> bool:
        """Scrive un singolo valore in config.json, lasciando intatto il resto.

        Rilegge il file invece di riversare `self.pricing`, che contiene i default
        iniettati al caricamento e non va scritto su disco.
        """
        path = self.pricing.get("_path")
        if not path or not os.path.isfile(path):
            return False
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            raw.setdefault(section, {})[key] = value
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
            self._config_mtime = os.path.getmtime(path)
            return True
        except Exception:
            return False

    def _on_billing(self, mode):
        """Switch abbonamento / consumo: cambia cosa significano le colonne di costo."""
        self.pricing.setdefault("billing", {})["mode"] = mode
        # è una configurazione, non un filtro di vista: deve sopravvivere alla chiusura
        self._write_config("billing", "mode", mode)
        global SUB_CURRENCY
        SUB_CURRENCY = cm.display_currency(self.pricing)
        cm.allocate_real_cost(self.sessions, self.pricing)
        self._relabel_costs()
        self.apply_filters()

    def _relabel_costs(self) -> None:
        """A consumo il costo per token È l'addebito; in abbonamento sono due cose."""
        api = cm.cost_columns(self.pricing)[1] is None
        for table in (self.projects, self.sessions_tbl):
            table.set_heading("cost", "Speso" if api else "Se fosse API")
        self.months_tbl.set_heading("hyp", "Speso" if api else "Se fosse API")
        self.months_tbl.set_heading("real", "" if api else "Hai pagato")
        self.tiles["paid"].l_label.config(text="SPESO" if api else "HAI PAGATO")
        self.tiles["cost"].l_label.config(text="—" if api else "SE FOSSE API")
        self.legend = ("sei a consumo: il prezzo dei token è l'addebito" if api else
                       "passa il mouse sulle intestazioni per la spiegazione  ·  "
                       "qui si misura il consumo, i soldi veri sono nella scheda Mesi")
        if self.segmented.index != 1:
            self.l_hint.config(text=self.legend)

    def _on_search(self, _value):
        self.project_filter = self.search.get()
        self.apply_filters()

    def apply_filters(self):
        rows = list(self.sessions)
        if self.period_spec:
            try:
                since = cm.parse_since(self.period_spec)
            except SystemExit:
                since = None
            if since:
                rows = [s for s in rows if (s["end"] or 0) >= since]
        needle = (self.project_filter or "").strip().lower()
        if needle:
            rows = [s for s in rows
                    if needle in (s["project"] or "").lower()
                    or needle in (s["cwd"] or "").lower()]
        rows = [s for s in rows if s["assistant_msgs"] or s["user_prompts"]]
        self.filtered = rows

        # Niente ripartizione della quota per riga: l'abbonamento si paga uguale,
        # e in un mese usato poco quasi tutta la quota resta inutilizzata — non
        # "consumata" dall'unico progetto che c'era. Qui si misura il consumo;
        # i soldi veri stanno nella scheda Mesi.
        tot_real = sum(r.get("real_cost", 0.0) for r in rows)
        projects = aggregate_by_project(rows)
        self.projects.set_rows(projects, {
            "project": f"{len(projects)} progetti",
            "cost": Fmt.cost(sum(r["cost"] for r in rows)),
            "sessions": str(len(rows)),
            "active": Fmt.dur(sum(r["active"] for r in rows)),
            "msgs": f"{sum(r['user_prompts'] for r in rows)}/"
                    f"{sum(r['assistant_msgs'] for r in rows)}",
        })
        self.sessions_tbl.set_rows(rows, {
            "project": f"{len(rows)} sessioni",
            "cost": Fmt.cost(sum(r["cost"] for r in rows)),
            "active": Fmt.dur(sum(r["active"] for r in rows)),
            "msgs": f"{sum(r['user_prompts'] for r in rows)}/"
                    f"{sum(r['assistant_msgs'] for r in rows)}",
        })

        help_texts = build_help(self.pricing, {"real": tot_real})
        for table in self._tables:
            table.help = help_texts

        months = aggregate_by_month(rows, self.pricing)
        self.months_tbl.set_rows(months, {
            "month": f"{len(months)} mesi",
            "hyp": Fmt.cost(sum(m["hyp"] for m in months)),
            "real": MONEY(sum(m["real"] for m in months)),
            "sessions": str(len(rows)),
            "tokens": Fmt.tokens(sum(m["tokens"] for m in months)),
            "output": Fmt.tokens(sum(m["output"] for m in months)),
        })

        total = sum(r["cost"] for r in rows)
        active = sum(r["active"] for r in rows)
        duration = sum(r["duration"] for r in rows)
        umsg = sum(r["user_prompts"] for r in rows)
        amsg = sum(r["assistant_msgs"] for r in rows)
        api = cm.cost_columns(self.pricing)[1] is None
        rate = cm.fx_usd_per_unit(self.pricing)
        resa = (total / (tot_real * rate)) if (tot_real and rate and not api) else 0
        if api:
            self.tiles["paid"].set(Fmt.cost(total), "a consumo, addebito reale")
            self.tiles["cost"].set("—", "sei già a consumo")
        else:
            n_mesi = len([m for m in months if m["real"] > 0]) if months else 0
            self.tiles["paid"].set(MONEY(tot_real),
                                   f"{n_mesi} mesi di abbonamento" if n_mesi else "")
            self.tiles["cost"].set(Fmt.cost(total),
                                   f"non pagato · {resa:.1f}× di quello che paghi"
                                   if resa else "non pagato")
        self.tiles["active"].set(Fmt.dur(active),
                                 f"su {Fmt.dur(duration)} totali")
        self.tiles["msgs"].set(f"{amsg:,}".replace(",", "."),
                               f"{umsg} tuoi · {sum(r['tool_calls'] for r in rows)} tool")
        scope = self.dd_period.value.lower()
        if needle:
            scope += f" · {needle}"
        self.l_scope.config(text=scope)

    def _render_team(self, righe, livello, nota, riepilogo=None):
        """Scheda Persone: consumo e spesa per postazione, dal raccoglitore."""
        self.team_rows = righe
        self.team_summary = riepilogo or {}
        etichetta = {
            "aggregato":  "aggregato · nessun identificativo di persona",
            "pseudonimo": "pseudonimo · codici a chiave, non indirizzi",
            "nominativo": "nominativo · indirizzi in chiaro",
        }.get(livello or "", "")

        parti = []
        r = self.team_summary
        if r.get("seats"):
            parti.append(f"{r['attive']}/{r['seats']} postazioni usate")
            # Il numero che interessa alla direzione: quota pagata e mai usata.
            if r.get("dormienti"):
                mesi = r["mesi"]
                parti.append(f"{r['dormienti']} dormienti = "
                             f"{team_money(r['pagato_a_vuoto'], r['currency'])} "
                             f"su {mesi} {'mese' if mesi == 1 else 'mesi'}")
        elif righe:
            parti.append("postazioni pagate non dichiarate — "
                         "dichiarale in Configura ▸ Team per vedere la spesa")
        # Da dove viene ogni riga: una postazione senza agente mostra solo
        # quello che la telemetria ha visto da quando e' stata accesa, che di
        # solito e' molto meno del vero. Meglio dirlo che lasciarlo intuire.
        sole_tel = [r_ for r_ in righe if r_.get("source") == "telemetria"]
        if sole_tel and len(sole_tel) != len(righe):
            parti.append(f"{len(sole_tel)} senza storico (manca cm_agent)")
        elif sole_tel:
            parti.append("solo telemetria: manca lo storico precedente")
        if etichetta:
            parti.append(f"riservatezza: {etichetta}")
        if righe:
            parti.insert(0, "doppio click per i progetti di una postazione")
        self.team_hint = nota or "  ·  ".join(parti)
        if self.segmented.index == 3:
            self.l_hint.config(text=self.team_hint)

        if not righe:
            self.team_tbl.set_rows([])
            self.team_tbl.set_placeholder(nota or "nessun dato di team")
            return

        # La colonna "Postazione" cambia intestazione col livello: chi guarda
        # deve capire senza chiedere se sta vedendo persone o codici.
        self.team_tbl.set_heading(
            "person", {"aggregato": "Insieme", "pseudonimo": "Postazione",
                       "nominativo": "Persona"}.get(livello or "", "Postazione"))
        totali = {
            "person":   f"{len(righe)} postazioni",
            "cost":     Fmt.cost(sum(r_["cost"] for r_ in righe)),
            "sessions": str(sum(r_["sessions"] for r_ in righe)),
            "projects": str(sum(r_.get("projects", 0) for r_ in righe)) or "—",
            "active":   Fmt.dur(sum(r_["active"] for r_ in righe)),
            "tok":      Fmt.tokens(sum(r_["total_tokens"] for r_ in righe)),
            "cr":       Fmt.tokens(sum(r_["tokens"]["cache_read"] for r_ in righe)),
        }
        if r.get("pagato_totale"):
            # Il totale pagato copre TUTTE le postazioni, dormienti comprese:
            # sommare la colonna riga per riga darebbe una cifra piu' bassa e
            # farebbe sparire proprio i soldi spesi per niente.
            totali["paid"] = team_money(r["pagato_totale"], r["currency"])
            totali["ratio"] = fmt_ratio(r.get("ratio", 0))
        self.team_tbl.set_rows(righe, totali)

    # -- azioni ---------------------------------------------------------------- #

    def open_project(self, project):
        """Doppio click su un progetto: scende alle sue conversazioni."""
        name = project.get("project") or ""
        self.search.set(name)
        self.project_filter = name
        self.apply_filters()
        self.segmented.select(1)          # scheda Sessioni
        self.l_status.config(
            text=f"{len(self.filtered)} conversazioni di «{name}»  ·  "
                 f"doppio click per rileggerne una  ·  svuota il filtro per tornare a tutte")

    def open_person(self, riga):
        """Doppio click su una postazione: su cosa ha lavorato."""
        PersonWindow(self, riga, self.pricing)

    def open_detail(self, session):
        DetailWindow(self, session)

    def open_config(self):
        """Pannello di configurazione. Il file JSON resta, ma non serve aprirlo."""
        SettingsWindow(self)

    def apply_config_change(self):
        """Dopo un salvataggio: rilegge tutto e ricalcola, senza riavviare."""
        self._config_mtime = None          # forza la rilettura
        self._schedule_auto_refresh()
        self.refresh()
        self.l_status.config(text="configurazione salvata")

    def restart(self):
        """Riavvio dell'applicazione: serve per i cambi che non si applicano a caldo.

        Nuovo processo e poi chiusura di questo, invece di execv: su Windows è più
        affidabile e mantiene pythonw (nessuna console che compare)."""
        try:
            self._save_state()
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv,
                             cwd=os.path.dirname(os.path.abspath(__file__)),
                             close_fds=True)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Riavvio non riuscito:\n{exc}")
            return
        self._cancel_jobs()
        if self.live_stop:
            self.live_stop.set()
        cm.LOG_HOOK = None
        self.root.destroy()

    def _export_menu(self):
        try:
            self.menu_export.tk_popup(self.b_export.winfo_rootx(),
                                      self.b_export.winfo_rooty() + self.b_export.bh + 2)
        finally:
            self.menu_export.grab_release()

    def export_conversations(self):
        """Esporta in Markdown le conversazioni attualmente filtrate.

        Una cartella per progetto più un indice. Gira su un thread perché ogni
        sessione va riletta per intero: il testo non sta nella cache.
        """
        if not self.filtered:
            messagebox.showinfo(APP_TITLE, "Nessuna conversazione da esportare.")
            return
        quante = len(self.filtered)
        dove = filedialog.askdirectory(
            parent=self.root,
            title=f"Cartella dove esportare {quante} conversazioni")
        if not dove:
            return
        if quante > 40 and not messagebox.askyesno(
                APP_TITLE,
                f"Stai per esportare {quante} conversazioni.\n\n"
                "Ognuna viene riletta per intero, quindi può volerci qualche "
                "minuto e occupare parecchi MB.\n\nProcedo?"):
            return
        sessions = list(self.filtered)
        self.b_export.set_text("…")
        threading.Thread(target=self._export_worker,
                         args=(sessions, dove), daemon=True).start()

    def _export_worker(self, sessions, dove):
        try:
            def progress(done, total, project):
                self.q.put(("export", self.gen, ("progress", done, total, project)))
            result = cm.export_conversations(sessions, self.base, self.pricing, dove,
                                             self.idle_gap, False, progress)
            self.q.put(("export", self.gen, ("done", result, dove, None)))
        except BaseException:
            self.q.put(("export", self.gen, ("error", None, dove, traceback.format_exc())))

    def export_relazione(self):
        """Il riepilogo di team, quello da allegare a una mail o stampare."""
        try:
            import cm_collector
        except ImportError:
            messagebox.showerror(APP_TITLE, "cm_collector.py non trovato "
                                            "accanto al pannello.")
            return
        percorso = next((p for p in team_db_candidates(self.pricing)
                         if os.path.isfile(p)), None)
        if not percorso:
            messagebox.showinfo(APP_TITLE,
                                "Nessun archivio di team: avvia prima il "
                                "raccoglitore con  python cm_collector.py")
            return
        dove = filedialog.asksaveasfilename(
            title="Salva il riepilogo del team", defaultextension=".md",
            initialfile="consumo-team.md",
            filetypes=[("Markdown", "*.md"), ("Tutti i file", "*.*")])
        if not dove:
            return
        try:
            store = cm_collector.Store(percorso)
            testo = cm_collector.relazione_markdown(
                store, self.pricing.get("team") or {},
                cm.fx_usd_per_unit(self.pricing),
                cm.parse_since(self.period_spec) if self.period_spec else None)
            store.con.close()
            with open(dove, "w", encoding="utf-8") as fh:
                fh.write(testo)
        except Exception as exc:
            messagebox.showerror(APP_TITLE,
                                 f"Non sono riuscito a scrivere:\n{exc}")
            return
        self.l_status.config(text=f"riepilogo scritto in {dove}")

    def export_json(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Esporta JSON", defaultextension=".json",
            initialfile="claude-monitor.json",
            filetypes=[("JSON", "*.json"), ("Tutti i file", "*.*")])
        if not path:
            return
        try:
            payload = cm.build_json_payload(self.filtered, self.pricing, 0)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            self.l_status.config(text=f"esportato in {path}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Esportazione fallita:\n{exc}")

    def _copy_selection(self, _event=None):
        text = self.current_table.selected_tsv()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.l_status.config(text="riga copiata negli appunti")

    # -- stato ------------------------------------------------------------------ #

    def _restore_state(self):
        st = None
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                st = json.load(fh)
        except Exception:
            pass
        geo = (st or {}).get("geometry")
        self.root.geometry(geo if isinstance(geo, str) and "x" in geo else "1220x720")
        for table, key, fallback in ((self.projects, "sort_projects", "cost"),
                                     (self.sessions_tbl, "sort_sessions", "cost"),
                                     (self.months_tbl, "sort_months", "month")):
            spec = (st or {}).get(key)
            if isinstance(spec, list) and len(spec) == 2 and spec[0]:
                table.sort_col, table.sort_desc = spec[0], bool(spec[1])
            else:
                table.sort_col = fallback

        st = st or {}
        label = st.get("period")
        if label:
            for name, spec in PERIODS:
                if name == label:
                    self.dd_period.value = name
                    self.dd_period.set_text(name + "  ▾")
                    self.period_spec = spec
        needle = st.get("project_filter")
        if needle:
            self.project_filter = needle
            self.search.set(needle)
        tab = st.get("tab")
        if isinstance(tab, int) and 0 <= tab <= 2:
            self.segmented.select(tab)
        self._restored_live = bool(st.get("live"))

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "geometry": self.root.geometry(),
                    "sort_projects": [self.projects.sort_col, self.projects.sort_desc],
                    "sort_sessions": [self.sessions_tbl.sort_col, self.sessions_tbl.sort_desc],
                    "sort_months": [self.months_tbl.sort_col, self.months_tbl.sort_desc],
                    # preferenze di vista: non sono configurazione, ma è seccante
                    # doverle rimettere a ogni avvio
                    "period": self.dd_period.value,
                    "project_filter": self.project_filter,
                    "tab": self.segmented.index,
                    "live": bool(self.btn_live.active),
                }, fh)
        except Exception:
            pass

    def _on_close(self):
        self._cancel_jobs()
        if self.live_stop:
            self.live_stop.set()
        cm.LOG_HOOK = None
        self._save_state()
        self.root.destroy()


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-monitor-gui",
        description="Interfaccia grafica di claude-monitor (Tkinter, solo stdlib).")
    p.add_argument("--base", default=cm.default_base(),
                   help="cartella dei transcript (default: %(default)s)")
    p.add_argument("--config", "--pricing", dest="pricing",
                   help="percorso di config.json (default: accanto allo script)")
    p.add_argument("--billing", choices=["subscription", "api"],
                   help="come viene pagato l'uso; sovrascrive config.json")
    p.add_argument("--idle-gap", type=float, default=None,
                   help="pausa oltre la quale il tempo non è 'attivo' (default da config.json)")
    p.add_argument("--theme", choices=["auto", "light", "dark"], default=None,
                   help="tema (default da config.json: segue Windows)")
    p.add_argument("--dark", dest="theme", action="store_const", const="dark",
                   help="forza il tema scuro")
    p.add_argument("--light", dest="theme", action="store_const", const="light",
                   help="forza il tema chiaro")
    p.add_argument("--tab", choices=["progetti", "sessioni", "mesi"], default="progetti",
                   help="scheda aperta all'avvio")
    p.add_argument("--live", action="store_true", help="attiva subito il live")
    p.add_argument("--auto-refresh", type=float, default=None, metavar="MIN",
                   help="riscansione automatica ogni MIN minuti, 0 per disattivarla "
                        "(default da config.json)")
    p.add_argument("--detail", metavar="UUID",
                   help="apre subito il dettaglio di una sessione (basta il prefisso)")
    p.add_argument("--locale", choices=["us", "it"], default=None,
                   help="separatori numerici (default da config.json)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config = cm.load_config(args.pricing)
    if args.billing:
        config.setdefault("billing", {})["mode"] = args.billing
    d = cm.defaults_of(config)
    if args.locale is None:
        args.locale = d.get("locale", "us")
    if args.theme is None:
        args.theme = d.get("theme", "auto")
    if args.idle_gap is None:
        args.idle_gap = float(d.get("idle_gap", 300))
    if args.auto_refresh is None:
        args.auto_refresh = float(d.get("auto_refresh_minutes", 5))

    global LIVE_INTERVAL, SUB_CURRENCY
    LIVE_INTERVAL = float(d.get("live_interval", 2.0))
    SUB_CURRENCY = cm.display_currency(config)
    Fmt.italian = args.locale == "it"

    if os.name == "nt":
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    if not os.path.isdir(args.base):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, f"Cartella dei transcript non trovata:\n{args.base}")
        return 2

    root = tk.Tk()
    App(root, args, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
