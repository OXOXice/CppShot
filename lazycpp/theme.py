"""界面主题、字体和通用小工具。"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Iterable


if sys.platform == "win32":
    UI_FAMILY = "Microsoft YaHei UI"
    MONO_FAMILY = "Consolas"
elif sys.platform == "darwin":
    UI_FAMILY = "PingFang SC"
    MONO_FAMILY = "Menlo"
else:
    UI_FAMILY = "Noto Sans CJK SC"
    MONO_FAMILY = "DejaVu Sans Mono"


class Theme:
    BG = "#0B1220"
    BG_SOFT = "#0E1728"
    PANEL = "#111B2D"
    PANEL_ALT = "#0D1626"
    PANEL_HOVER = "#17253D"
    BORDER = "#24314A"
    BORDER_SOFT = "#1B2740"
    TEXT = "#E7EEFA"
    TEXT_SOFT = "#C6D3E6"
    MUTED = "#8194B2"
    FAINT = "#5E718E"
    ACCENT = "#38BDF8"
    ACCENT_HOVER = "#7DD3FC"
    ACCENT_DARK = "#0B2C42"
    GREEN = "#34D399"
    GREEN_DARK = "#073B2B"
    RED = "#F87171"
    RED_DARK = "#431B23"
    YELLOW = "#FBBF24"
    BLUE = "#60A5FA"
    PURPLE = "#C4B5FD"
    EDITOR_BG = "#0A111E"
    EDITOR_GUTTER = "#0A111E"
    SELECT = "#1E3A5F"


def resource_path(relative: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parent.parent / relative


def setup_root(root: tk.Tk) -> None:
    root.configure(bg=Theme.BG)
    root.option_add("*Font", (UI_FAMILY, 10))
    root.option_add("*TCombobox*Listbox.background", Theme.PANEL)
    root.option_add("*TCombobox*Listbox.foreground", Theme.TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", Theme.SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", Theme.TEXT)
    root.option_add("*Text.selectBackground", Theme.SELECT)
    try:
        root.tk.call("tk", "scaling", max(1.0, root.winfo_fpixels("1i") / 72.0))
    except Exception:
        pass


def setup_styles(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    base = {"background": Theme.PANEL, "foreground": Theme.TEXT,
            "font": (UI_FAMILY, 10), "borderwidth": 0, "focuscolor": Theme.PANEL}
    style.configure(".", **base)
    style.configure("TFrame", background=Theme.PANEL)
    style.configure("BG.TFrame", background=Theme.BG)
    style.configure("Panel.TFrame", background=Theme.PANEL)
    style.configure("TLabel", background=Theme.PANEL, foreground=Theme.TEXT,
                    font=(UI_FAMILY, 10))
    style.configure("Muted.TLabel", background=Theme.PANEL, foreground=Theme.MUTED,
                    font=(UI_FAMILY, 9))
    style.configure("Bold.TLabel", background=Theme.PANEL, foreground=Theme.TEXT,
                    font=(UI_FAMILY, 10, "bold"))
    style.configure("Title.TLabel", background=Theme.PANEL, foreground=Theme.TEXT,
                    font=(UI_FAMILY, 11, "bold"))

    # 按钮
    style.configure(
        "Primary.TButton", background=Theme.ACCENT, foreground="#062033",
        font=(UI_FAMILY, 10, "bold"), padding=(14, 9), borderwidth=0, relief="flat",
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", "#1D3B51"), ("pressed", "#0EA5E9"), ("active", Theme.ACCENT_HOVER)],
        foreground=[("disabled", "#71879B"), ("active", "#041A29")],
    )
    style.configure(
        "Secondary.TButton", background="#1B2A43", foreground=Theme.TEXT,
        font=(UI_FAMILY, 10), padding=(12, 8), borderwidth=0, relief="flat",
    )
    style.map(
        "Secondary.TButton",
        background=[("disabled", "#152033"), ("pressed", "#243B5D"), ("active", "#263A58")],
        foreground=[("disabled", "#657792")],
    )
    style.configure(
        "Ghost.TButton", background=Theme.PANEL, foreground=Theme.MUTED,
        font=(UI_FAMILY, 9), padding=(8, 5), borderwidth=0, relief="flat",
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", Theme.PANEL), ("active", Theme.PANEL_HOVER), ("pressed", "#1D2D47")],
        foreground=[("disabled", "#4F617C"), ("active", Theme.TEXT)],
    )
    style.configure(
        "Danger.TButton", background=Theme.RED_DARK, foreground="#FCA5A5",
        font=(UI_FAMILY, 9), padding=(10, 6), borderwidth=0,
    )
    style.map("Danger.TButton", background=[("active", "#5C222C")], foreground=[("active", "#FECACA")])
    style.configure(
        "Tiny.TButton", background=Theme.PANEL_ALT, foreground=Theme.MUTED,
        font=(UI_FAMILY, 8), padding=(6, 3), borderwidth=0,
    )
    style.map("Tiny.TButton", background=[("active", Theme.PANEL_HOVER)], foreground=[("active", Theme.TEXT)])
    style.configure(
        "Link.TButton", background=Theme.PANEL, foreground=Theme.ACCENT,
        font=(UI_FAMILY, 9), padding=(2, 2), borderwidth=0,
    )
    style.map("Link.TButton", background=[("active", Theme.PANEL)], foreground=[("active", Theme.ACCENT_HOVER)])

    # 输入框
    style.configure(
        "TEntry", fieldbackground=Theme.PANEL_ALT, foreground=Theme.TEXT,
        insertcolor=Theme.ACCENT, bordercolor=Theme.BORDER, lightcolor=Theme.BORDER,
        darkcolor=Theme.BORDER, padding=(8, 6), borderwidth=1, relief="flat",
    )
    style.map("TEntry", bordercolor=[("focus", Theme.ACCENT)], lightcolor=[("focus", Theme.ACCENT)],
              darkcolor=[("focus", Theme.ACCENT)])
    style.configure(
        "Readonly.TEntry", fieldbackground=Theme.PANEL_ALT, foreground=Theme.TEXT_SOFT,
        bordercolor=Theme.BORDER, lightcolor=Theme.BORDER, darkcolor=Theme.BORDER,
        padding=(8, 6), borderwidth=1, relief="flat",
    )
    style.map("Readonly.TEntry", fieldbackground=[("readonly", Theme.PANEL_ALT)],
              foreground=[("readonly", Theme.TEXT_SOFT)])
    style.configure(
        "TCombobox", fieldbackground=Theme.PANEL_ALT, background=Theme.PANEL_ALT,
        foreground=Theme.TEXT, arrowcolor=Theme.MUTED, bordercolor=Theme.BORDER,
        lightcolor=Theme.BORDER, darkcolor=Theme.BORDER, padding=(6, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", Theme.PANEL_ALT), ("disabled", Theme.PANEL_ALT)],
        foreground=[("readonly", Theme.TEXT), ("disabled", Theme.FAINT)],
        arrowcolor=[("active", Theme.ACCENT)],
        bordercolor=[("focus", Theme.ACCENT)],
    )
    style.configure(
        "TSpinbox", fieldbackground=Theme.PANEL_ALT, foreground=Theme.TEXT,
        arrowcolor=Theme.MUTED, bordercolor=Theme.BORDER, padding=(6, 5),
    )
    style.map("TSpinbox", bordercolor=[("focus", Theme.ACCENT)])
    style.configure(
        "Dark.TCheckbutton", background=Theme.PANEL, foreground=Theme.MUTED,
        font=(UI_FAMILY, 9), indicatorbackground=Theme.PANEL_ALT,
        indicatorforeground=Theme.ACCENT, focuscolor=Theme.PANEL,
    )
    style.map(
        "Dark.TCheckbutton",
        background=[("active", Theme.PANEL)],
        foreground=[("active", Theme.TEXT)],
        indicatorbackground=[("selected", Theme.ACCENT), ("active", Theme.PANEL_HOVER)],
        indicatorforeground=[("selected", "#062033")],
    )
    style.configure("TRadiobutton", background=Theme.PANEL, foreground=Theme.TEXT_SOFT,
                    font=(UI_FAMILY, 10), indicatorcolor=Theme.PANEL_ALT)
    style.map("TRadiobutton", background=[("active", Theme.PANEL)],
              foreground=[("active", Theme.TEXT)], indicatorcolor=[("selected", Theme.ACCENT)])

    # 滚动条
    style.configure(
        "Vertical.TScrollbar", background=Theme.BORDER, troughcolor=Theme.PANEL_ALT,
        bordercolor=Theme.PANEL_ALT, arrowcolor=Theme.MUTED, width=12,
        relief="flat", borderwidth=0,
    )
    style.map("Vertical.TScrollbar", background=[("active", "#354866")])
    style.configure(
        "Horizontal.TScrollbar", background=Theme.BORDER, troughcolor=Theme.PANEL_ALT,
        bordercolor=Theme.PANEL_ALT, arrowcolor=Theme.MUTED, relief="flat", borderwidth=0,
    )

    # 进度条
    style.configure(
        "Shot.Horizontal.TProgressbar", background=Theme.ACCENT,
        troughcolor=Theme.PANEL_ALT, bordercolor=Theme.PANEL_ALT,
        lightcolor=Theme.ACCENT, darkcolor=Theme.ACCENT, thickness=6,
    )

    # 队列
    style.configure(
        "Queue.Treeview", background=Theme.PANEL, fieldbackground=Theme.PANEL,
        foreground=Theme.TEXT_SOFT, rowheight=36, borderwidth=0, font=(UI_FAMILY, 9),
    )
    style.map(
        "Queue.Treeview",
        background=[("selected", Theme.SELECT)],
        foreground=[("selected", Theme.TEXT)],
    )
    style.configure(
        "Queue.Treeview.Heading", background=Theme.PANEL, foreground=Theme.FAINT,
        font=(UI_FAMILY, 8), borderwidth=0, relief="flat", padding=(2, 6),
    )
    style.map("Queue.Treeview.Heading", background=[("active", Theme.PANEL)])
    return style


def make_card(parent: tk.Misc, *, bg: str = Theme.PANEL, padx: int = 16,
              pady: int = 14) -> tuple[tk.Frame, tk.Frame]:
    outer = tk.Frame(parent, bg=bg, highlightbackground=Theme.BORDER,
                     highlightthickness=1, bd=0)
    inner = tk.Frame(outer, bg=bg, padx=padx, pady=pady)
    inner.pack(fill="both", expand=True)
    return outer, inner


def card_heading(parent: tk.Misc, title: str, *, subtitle: str = "",
                 badge: str = "", bg: str = Theme.PANEL) -> tk.Frame:
    row = tk.Frame(parent, bg=bg)
    tk.Label(row, text=title, bg=bg, fg=Theme.TEXT,
             font=(UI_FAMILY, 11, "bold")).pack(side="left")
    if badge:
        tk.Label(row, text=badge, bg=Theme.ACCENT_DARK, fg=Theme.ACCENT,
                 font=(UI_FAMILY, 8), padx=7, pady=2).pack(side="left", padx=(8, 0))
    if subtitle:
        tk.Label(row, text=subtitle, bg=bg, fg=Theme.MUTED,
                 font=(UI_FAMILY, 8)).pack(side="right")
    return row


def set_dark_titlebar(window: tk.Misc) -> None:
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
            if result == 0:
                break
    except Exception:
        pass


def center_window(window: tk.Toplevel, width: int, height: int) -> None:
    window.update_idletasks()
    parent = window.master.winfo_toplevel() if window.master else None
    if parent and parent.winfo_viewable():
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    else:
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def copy_to_clipboard(root: tk.Misc, text: str) -> None:
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update_idletasks()


def bind_mousewheel_recursive(widget: tk.Misc, callback) -> None:
    widget.bind("<MouseWheel>", callback)
    for child in widget.winfo_children():
        bind_mousewheel_recursive(child, callback)


def separator(parent: tk.Misc, *, bg: str = Theme.BORDER, pady: int = 0) -> tk.Frame:
    frame = tk.Frame(parent, bg=bg, height=1)
    frame.pack(fill="x", pady=pady)
    return frame
