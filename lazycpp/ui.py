"""CppShot 主窗口。"""

from __future__ import annotations

import copy
import queue
import re
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_TITLE, __version__
from .ai import AIConfig, AIError, ai_diagnose
from .config import Store, initial_items
from .dialogs import AIGenerateDialog, AIResultDialog, FailureDialog, InteractiveRunDialog, SettingsDialog
from .imaging import open_path, save_screenshot
from .models import CodeItem, DEFAULT_CODE, STATUS_LABELS, safe_filename, unique_name
from .theme import (
    MONO_FAMILY,
    UI_FAMILY,
    Theme,
    bind_mousewheel_recursive,
    card_heading,
    center_window,
    copy_to_clipboard,
    make_card,
    resource_path,
    set_dark_titlebar,
    setup_root,
    setup_styles,
)
from .toolchain import (
    Compiler,
    RunOutcome,
    compile_and_run,
    compiler_infos,
    detect_compilers,
    select_compiler,
)
from .widgets import CodeEditor, ScrollFrame, run_async


class CppShotApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        setup_root(root)
        setup_styles(root)
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.px = lambda value: int(round(value * self.scale))
        self.store = Store()
        self.config = self.store.load_config()
        items, selected = self.store.load_session()
        self.items: list[CodeItem] = items or initial_items()
        self.current_index = max(0, min(selected, len(self.items) - 1))
        self.compilers = detect_compilers()
        ttk.Style(self.root).configure("Queue.Treeview", rowheight=self.px(38))
        self._loading = True
        self._busy = False
        self._ignore_select = False
        self._save_job: str | None = None
        self._toast_job: str | None = None
        self._run_messages: queue.Queue = queue.Queue()
        self._run_poll_active = False
        self._pending_failure: tuple[int, RunOutcome, int] | None = None
        self._run_total = 0
        self._run_successes: list[str] = []
        self._run_indices: list[int] = []
        self._ai_busy = False
        self.interactive_dialog: InteractiveRunDialog | None = None
        self._compiler_label_to_id: dict[str, str] = {}
        self._compiler_id_to_label: dict[str, str] = {}

        self._setup_window()
        self._build_header()
        self._build_footer()
        self._build_body()
        self._bind_events()
        self.root.after_idle(self.side.scroll_to_top)
        self._refresh_queue()
        self._load_item(self.current_index)
        self._loading = False
        self._update_api_status()
        self._update_compiler_badge()
        self._update_action_states()
        self._update_last_screenshot()
        self._update_progress(0, 0)
        self._set_status("就绪 · 导入代码后，点右侧按钮开始运行截图")
        self.root.after(120, lambda: set_dark_titlebar(self.root))
        if not self.compilers:
            self.root.after(500, self._warn_no_compiler)

    # ------------------------------------------------------------------ window
    def _setup_window(self) -> None:
        self.root.title(APP_TITLE)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # 窗口尺寸按 DPI 缩放：UI 字体由 Tk 按 DPI 放大，固定像素布局也必须
        # 同步放大，否则在 150%/200% 缩放的屏幕上会显得拥挤。
        width = min(self.px(1480), max(self.px(1100), screen_w - self.px(64)))
        height = min(self.px(900), max(self.px(680), screen_h - self.px(80)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - self.px(8))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(self.px(1080), width), min(self.px(680), height))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        icon = resource_path("assets/icon.ico")
        if icon.exists() and sys.platform == "win32":
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass
        png = resource_path("assets/icon.png")
        if png.exists():
            try:
                self._icon_image = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._icon_image)
            except tk.TclError:
                pass

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=Theme.BG, height=self.px(78))
        header.pack(fill="x", padx=self.px(20), pady=(self.px(10), 2))
        header.pack_propagate(False)

        logo = tk.Label(header, text="C++", bg=Theme.ACCENT, fg="#062033",
                        font=(MONO_FAMILY, 13, "bold"), padx=10, pady=8)
        logo.pack(side="left", pady=8)
        title_box = tk.Frame(header, bg=Theme.BG)
        title_box.pack(side="left", padx=14)
        tk.Label(title_box, text="CppShot", bg=Theme.BG, fg=Theme.TEXT,
                 font=(UI_FAMILY, 17, "bold")).pack(anchor="w")
        tk.Label(title_box, text="懒人 C++ 截图器  ·  批量运行 · 自动截图 · AI 辅助",
                 bg=Theme.BG, fg=Theme.MUTED, font=(UI_FAMILY, 9)).pack(anchor="w")

        right = tk.Frame(header, bg=Theme.BG)
        right.pack(side="right", pady=12)
        self.api_badge = tk.Label(right, text="● API 未配置", bg=Theme.PANEL_ALT,
                                  fg=Theme.FAINT, font=(UI_FAMILY, 9),
                                  padx=10, pady=6)
        self.api_badge.pack(side="right", padx=(8, 0))
        ttk.Button(right, text="AI 设置", style="Ghost.TButton",
                   command=self.open_settings).pack(side="right", padx=(8, 0))
        self.compiler_badge = tk.Label(right, text="● 检测编译器…", bg=Theme.PANEL_ALT,
                                       fg=Theme.MUTED, font=(UI_FAMILY, 9),
                                       padx=10, pady=6)
        self.compiler_badge.pack(side="right")
        ttk.Button(right, text="重新检测", style="Ghost.TButton",
                   command=self.redetect_compilers).pack(side="right", padx=(8, 0))

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=Theme.BG, height=self.px(28))
        footer.pack(side="bottom", fill="x", padx=self.px(22), pady=(0, self.px(7)))
        footer.pack_propagate(False)
        self.status_label = tk.Label(footer, text="● 就绪", bg=Theme.BG, fg=Theme.MUTED,
                                     font=(UI_FAMILY, 8), anchor="w")
        self.status_label.pack(side="left")
        tk.Label(footer, text=f"本地编译 · 截图不上传 · v{__version__}", bg=Theme.BG,
                 fg=Theme.FAINT, font=(UI_FAMILY, 8)).pack(side="right")

    def _build_body(self) -> None:
        self.body = tk.Frame(self.root, bg=Theme.BG)
        self.body.pack(fill="both", expand=True, padx=self.px(18), pady=(2, self.px(4)))
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(1, weight=1)
        self.body.grid_columnconfigure(0, minsize=self.px(292))
        self.body.grid_columnconfigure(2, minsize=self.px(332))

        left = tk.Frame(self.body, bg=Theme.BG, width=self.px(292))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(6)))
        left.grid_propagate(False)
        self._build_queue(left)

        center = tk.Frame(self.body, bg=Theme.BG)
        center.grid(row=0, column=1, sticky="nsew", padx=self.px(6))
        center.grid_rowconfigure(0, weight=1)
        center.grid_columnconfigure(0, weight=1)
        self.center = center
        self._build_editor(center)
        self._build_log(center)

        self.side = ScrollFrame(self.body, bg=Theme.BG, width=self.px(332))
        self.side.grid(row=0, column=2, sticky="nsew", padx=(self.px(8), 0))
        self._build_side(self.side.inner)

    # ------------------------------------------------------------------ queue
    def _build_queue(self, parent: tk.Frame) -> None:
        outer, inner = make_card(parent, padx=self.px(14), pady=self.px(14))
        outer.pack(fill="both", expand=True)
        heading = tk.Frame(inner, bg=Theme.PANEL)
        heading.pack(fill="x")
        tk.Label(heading, text="代码队列", bg=Theme.PANEL, fg=Theme.TEXT,
                 font=(UI_FAMILY, 11, "bold")).pack(side="left")
        self.queue_count = tk.Label(heading, text="0", bg=Theme.ACCENT_DARK, fg=Theme.ACCENT,
                                    font=(UI_FAMILY, 8, "bold"), padx=8, pady=2)
        self.queue_count.pack(side="right")
        tk.Label(inner, text="支持批量导入 .cpp，逐份运行并截图", bg=Theme.PANEL,
                 fg=Theme.MUTED, font=(UI_FAMILY, 8), anchor="w").pack(fill="x", pady=(4, 10))

        tree_frame = tk.Frame(inner, bg=Theme.PANEL)
        tree_frame.pack(fill="both", expand=True)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        self.queue_tree = ttk.Treeview(
            tree_frame, columns=("idx", "name", "state"), show="headings",
            style="Queue.Treeview", selectmode="browse",
        )
        self.queue_tree.heading("idx", text="#")
        self.queue_tree.heading("name", text="程序")
        self.queue_tree.heading("state", text="状态")
        self.queue_tree.column("idx", width=self.px(30), minwidth=self.px(26), stretch=False, anchor="center")
        self.queue_tree.column("name", width=self.px(158), minwidth=self.px(110), stretch=True, anchor="w")
        self.queue_tree.column("state", width=self.px(64), minwidth=self.px(58), stretch=False, anchor="center")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=scroll.set)
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.queue_tree.tag_configure("success", foreground=Theme.GREEN)
        self.queue_tree.tag_configure("failed", foreground=Theme.RED)
        self.queue_tree.tag_configure("running", foreground=Theme.ACCENT)
        self.queue_tree.tag_configure("pending", foreground=Theme.TEXT_SOFT)

        primary_row = tk.Frame(inner, bg=Theme.PANEL)
        primary_row.pack(fill="x", pady=(12, 6))
        primary_row.grid_columnconfigure(0, weight=1)
        primary_row.grid_columnconfigure(1, weight=1)
        self.new_button = ttk.Button(primary_row, text="＋ 新建代码", style="Secondary.TButton",
                                     command=self.new_item)
        self.new_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.import_button = ttk.Button(primary_row, text="导入 CPP", style="Secondary.TButton",
                                        command=self.import_cpp)
        self.import_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        action_row = tk.Frame(inner, bg=Theme.PANEL)
        action_row.pack(fill="x")
        for idx in range(4):
            action_row.grid_columnconfigure(idx, weight=1)
        self.delete_button = ttk.Button(action_row, text="删除", style="Ghost.TButton",
                                        command=self.delete_item)
        self.delete_button.grid(row=0, column=0, sticky="ew")
        self.up_button = ttk.Button(action_row, text="↑ 上移", style="Ghost.TButton",
                                    command=lambda: self.move_item(-1))
        self.up_button.grid(row=0, column=1, sticky="ew")
        self.down_button = ttk.Button(action_row, text="↓ 下移", style="Ghost.TButton",
                                      command=lambda: self.move_item(1))
        self.down_button.grid(row=0, column=2, sticky="ew")
        self.clear_button = ttk.Button(action_row, text="清空", style="Ghost.TButton",
                                       command=self.clear_items)
        self.clear_button.grid(row=0, column=3, sticky="ew")

        tk.Label(inner, text="小提示：双击可重命名，代码修改会自动保存", bg=Theme.PANEL,
                 fg=Theme.FAINT, font=(UI_FAMILY, 8), anchor="w").pack(fill="x", pady=(10, 0))
        self.queue_tree.bind("<Double-1>", self._rename_from_tree)

    # ------------------------------------------------------------------ editor
    def _build_editor(self, parent: tk.Frame) -> None:
        outer, inner = make_card(parent, padx=self.px(16), pady=self.px(14))
        outer.grid(row=0, column=0, sticky="nsew")
        self.editor_card = outer
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_rowconfigure(3, weight=1)

        header = tk.Frame(inner, bg=Theme.PANEL)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        tk.Label(header, text="当前程序", bg=Theme.PANEL, fg=Theme.MUTED,
                 font=(UI_FAMILY, 8)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.name_var = tk.StringVar()
        self.name_entry = tk.Entry(
            header, textvariable=self.name_var, bg=Theme.PANEL_ALT, fg=Theme.TEXT,
            insertbackground=Theme.ACCENT, disabledbackground=Theme.PANEL_ALT,
            disabledforeground=Theme.FAINT, relief="flat", highlightthickness=1,
            highlightbackground=Theme.BORDER, highlightcolor=Theme.ACCENT,
            font=(UI_FAMILY, 11, "bold"), bd=0,
        )
        self.name_entry.grid(row=0, column=1, sticky="ew", ipady=5)
        self.status_pill = tk.Label(header, text="待运行", bg=Theme.PANEL_ALT, fg=Theme.MUTED,
                                    font=(UI_FAMILY, 8), padx=9, pady=3)
        self.status_pill.grid(row=0, column=2, padx=(10, 8))
        self.copy_code_button = ttk.Button(header, text="复制代码", style="Ghost.TButton",
                                           command=self.copy_code)
        self.copy_code_button.grid(row=0, column=3)
        self.export_button = ttk.Button(header, text="导出 .cpp", style="Ghost.TButton",
                                        command=self.export_code)
        self.export_button.grid(row=0, column=4, padx=(2, 0))

        meta = tk.Frame(inner, bg=Theme.PANEL)
        meta.grid(row=1, column=0, sticky="ew", pady=(6, 8))
        self.source_label = tk.Label(meta, text="来源：手动输入 / 编辑器", bg=Theme.PANEL,
                                     fg=Theme.FAINT, font=(UI_FAMILY, 8), anchor="w")
        self.source_label.pack(side="left")
        tk.Label(meta, text="UTF-8  ·  Ctrl+Enter 运行", bg=Theme.PANEL, fg=Theme.FAINT,
                 font=(UI_FAMILY, 8)).pack(side="right")
        self.cursor_label = tk.Label(meta, text="Ln 1, Col 1", bg=Theme.PANEL,
                                     fg=Theme.FAINT, font=(MONO_FAMILY, 8))
        self.cursor_label.pack(side="right", padx=(0, 12))

        self.editor = CodeEditor(
            inner,
            on_change=self._on_editor_changed,
            on_cursor=self._on_cursor,
            on_run=self.run_current,
            on_save=self.save_session_now,
        )
        self.editor.grid(row=3, column=0, sticky="nsew")

        stdin_box = tk.Frame(inner, bg=Theme.PANEL)
        stdin_box.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        stdin_head = tk.Frame(stdin_box, bg=Theme.PANEL)
        stdin_head.pack(fill="x")
        tk.Label(stdin_head, text="运行输入（stdin）", bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=(UI_FAMILY, 9, "bold")).pack(side="left")
        tk.Label(stdin_head, text="有 cin 就填，不需要留空",
                 bg=Theme.PANEL, fg=Theme.FAINT, font=(UI_FAMILY, 8)).pack(side="left", padx=8)
        self.stdin_frame = tk.Frame(stdin_box, bg=Theme.PANEL_ALT,
                                    highlightbackground=Theme.BORDER, highlightthickness=1)
        self.stdin_frame.pack(fill="x", pady=(5, 0))
        self.stdin_text = tk.Text(
            self.stdin_frame, height=3, bg=Theme.PANEL_ALT, fg=Theme.TEXT_SOFT,
            insertbackground=Theme.ACCENT, selectbackground=Theme.SELECT,
            selectforeground=Theme.TEXT, font=(MONO_FAMILY, 9), bd=0,
            highlightthickness=0, padx=9, pady=6, wrap="none", undo=True,
        )
        stdin_scroll = ttk.Scrollbar(self.stdin_frame, orient="vertical", command=self.stdin_text.yview)
        self.stdin_text.configure(yscrollcommand=stdin_scroll.set)
        self.stdin_text.pack(side="left", fill="x", expand=True)
        stdin_scroll.pack(side="right", fill="y")
        self.stdin_text.bind("<<Modified>>", self._on_stdin_modified)
        self.stdin_text.bind("<Control-Return>", lambda _e: (self.run_current(), "break")[1])

    def _build_log(self, parent: tk.Frame) -> None:
        outer = tk.Frame(parent, bg=Theme.PANEL, highlightbackground=Theme.BORDER,
                         highlightthickness=1, height=self.px(180))
        outer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        outer.grid_propagate(False)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)
        heading = tk.Frame(outer, bg=Theme.PANEL)
        heading.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 3))
        self.log_title = tk.Label(heading, text="运行日志", bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                                  font=(UI_FAMILY, 9, "bold"))
        self.log_title.pack(side="left")
        self.progress_label = tk.Label(heading, text="就绪", bg=Theme.PANEL, fg=Theme.FAINT,
                                       font=(UI_FAMILY, 8))
        self.progress_label.pack(side="left", padx=10)
        ttk.Button(heading, text="复制", style="Tiny.TButton",
                   command=self.copy_log).pack(side="right")
        ttk.Button(heading, text="清空", style="Tiny.TButton",
                   command=self.clear_log).pack(side="right", padx=4)
        self.log_text = tk.Text(
            outer, height=5, bg=Theme.EDITOR_BG, fg=Theme.TEXT_SOFT,
            selectbackground=Theme.SELECT, selectforeground=Theme.TEXT,
            font=(MONO_FAMILY, 9), bd=0, highlightthickness=0,
            padx=12, pady=7, wrap="word", state="disabled",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self.log_text.tag_configure("info", foreground="#93A7C4")
        self.log_text.tag_configure("ok", foreground=Theme.GREEN)
        self.log_text.tag_configure("fail", foreground=Theme.RED)
        self.log_text.tag_configure("warn", foreground=Theme.YELLOW)
        self.log_text.tag_configure("accent", foreground=Theme.ACCENT)

    # ------------------------------------------------------------------ side
    def _build_side(self, parent: tk.Frame) -> None:
        parent.configure(bg=Theme.BG)
        self._build_save_card(parent)
        self._build_run_card(parent)
        self._build_ai_card(parent)
        self._build_help_card(parent)

    def _side_card(self, parent: tk.Frame) -> tuple[tk.Frame, tk.Frame]:
        outer, inner = make_card(parent, padx=self.px(14), pady=self.px(13))
        outer.pack(fill="x", pady=(0, self.px(10)))
        return outer, inner

    def _build_save_card(self, parent: tk.Frame) -> None:
        outer, inner = self._side_card(parent)
        card_heading(inner, "截图保存", subtitle="PNG").pack(fill="x")
        tk.Label(inner, text="每张截图自动命名，失败时不会生成错误截图。", bg=Theme.PANEL,
                 fg=Theme.MUTED, font=(UI_FAMILY, 8), anchor="w", justify="left").pack(
            fill="x", pady=(4, 9))
        self.save_var = tk.StringVar(value=self.config.save_dir)
        path_box = tk.Frame(inner, bg=Theme.PANEL_ALT, highlightbackground=Theme.BORDER,
                            highlightthickness=1)
        path_box.pack(fill="x")
        self.save_path_label = tk.Label(
            path_box, textvariable=self.save_var, bg=Theme.PANEL_ALT, fg=Theme.TEXT_SOFT,
            anchor="w", justify="left", font=(MONO_FAMILY, 8),
            wraplength=self.px(275), padx=self.px(8), pady=self.px(6),
        )
        self.save_path_label.pack(fill="x")
        row = tk.Frame(inner, bg=Theme.PANEL)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="选择文件夹", style="Secondary.TButton",
                   command=self.choose_save_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="复制路径", style="Ghost.TButton",
                   command=self.copy_save_dir).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="打开", style="Ghost.TButton",
                   command=self.open_save_dir).pack(side="left", padx=(2, 0))
        self.auto_open_var = tk.BooleanVar(value=self.config.open_folder_after)
        ttk.Checkbutton(inner, text="全部完成后自动打开截图文件夹",
                        variable=self.auto_open_var, style="Dark.TCheckbutton",
                        command=self._on_auto_open_changed).pack(anchor="w", pady=(8, 4))
        self.last_shot_button = ttk.Button(inner, text="打开上次截图", style="Link.TButton",
                                           command=self.open_last_screenshot)
        self.last_shot_button.pack(anchor="w")
        self.last_shot_button.state(["disabled"])

    def _build_run_card(self, parent: tk.Frame) -> None:
        outer, inner = self._side_card(parent)
        card_heading(inner, "运行与截图").pack(fill="x")
        tk.Label(inner, text="自动发现 Visual Studio / MinGW，编译后运行真实程序。",
                 bg=Theme.PANEL, fg=Theme.MUTED, font=(UI_FAMILY, 8), anchor="w",
                 justify="left", wraplength=self.px(290)).pack(fill="x", pady=(4, 9))
        self.run_current_button = ttk.Button(inner, text="▶  运行当前并截图",
                                             style="Primary.TButton", command=self.run_current)
        self.run_current_button.pack(fill="x")
        self.run_all_button = ttk.Button(inner, text="批量运行全部并截图",
                                         style="Secondary.TButton", command=self.run_all)
        self.run_all_button.pack(fill="x", pady=(6, 6))
        self.run_interactive_button = ttk.Button(
            inner, text="交互运行（边跑边输入）", style="Secondary.TButton",
            command=self.run_interactive_current,
        )
        self.run_interactive_button.pack(fill="x", pady=(0, 8))
        tk.Label(inner, text="交互模式适合需要边运行边输入的程序；正常结束会自动截图。",
                 bg=Theme.PANEL, fg=Theme.FAINT, font=(UI_FAMILY, 8), anchor="w",
                 justify="left", wraplength=self.px(290)).pack(fill="x", pady=(0, 8))

        settings = tk.Frame(inner, bg=Theme.PANEL)
        settings.pack(fill="x")
        settings.grid_columnconfigure(1, weight=1)
        tk.Label(settings, text="编译器", bg=Theme.PANEL, fg=Theme.MUTED,
                 font=(UI_FAMILY, 8)).grid(row=0, column=0, sticky="w", pady=3)
        self.compiler_var = tk.StringVar()
        self.compiler_combo = ttk.Combobox(settings, textvariable=self.compiler_var,
                                           state="readonly", font=(UI_FAMILY, 8), values=[])
        self.compiler_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.compiler_combo.bind("<<ComboboxSelected>>", self._on_compiler_selected)
        self._refresh_compiler_choices()
        tk.Label(settings, text="超时", bg=Theme.PANEL, fg=Theme.MUTED,
                 font=(UI_FAMILY, 8)).grid(row=1, column=0, sticky="w", pady=3)
        timeout_row = tk.Frame(settings, bg=Theme.PANEL)
        timeout_row.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)
        self.timeout_var = tk.StringVar(value=str(self.config.timeout_sec))
        self.timeout_spin = ttk.Spinbox(timeout_row, from_=1, to=300, width=5,
                                        textvariable=self.timeout_var,
                                        command=self._on_timeout_changed)
        self.timeout_spin.pack(side="left")
        self.timeout_spin.bind("<FocusOut>", lambda _e: self._on_timeout_changed())
        self.timeout_spin.bind("<Return>", lambda _e: self._on_timeout_changed())
        tk.Label(timeout_row, text="秒 / 个程序", bg=Theme.PANEL, fg=Theme.FAINT,
                 font=(UI_FAMILY, 8)).pack(side="left", padx=5)

        self.progress = ttk.Progressbar(inner, style="Shot.Horizontal.TProgressbar",
                                        mode="determinate", maximum=1, value=0)
        self.progress.pack(fill="x", pady=(10, 5))
        self.progress_text = tk.Label(inner, text="等待运行", bg=Theme.PANEL, fg=Theme.FAINT,
                                      font=(UI_FAMILY, 8), anchor="w")
        self.progress_text.pack(fill="x")
        tk.Label(inner, text="失败即中止：编译错误、非 0 退出码、超时都会停止后续截图。",
                 bg=Theme.PANEL, fg=Theme.YELLOW, font=(UI_FAMILY, 8), anchor="w",
                 justify="left", wraplength=self.px(290)).pack(fill="x", pady=(7, 0))

    def _build_ai_card(self, parent: tk.Frame) -> None:
        outer, inner = self._side_card(parent)
        card_heading(inner, "AI 助手", badge="可选").pack(fill="x")
        self.ai_status_label = tk.Label(inner, text="● 未配置 API", bg=Theme.PANEL,
                                        fg=Theme.FAINT, font=(UI_FAMILY, 8), anchor="w")
        self.ai_status_label.pack(fill="x", pady=(5, 3))
        tk.Label(inner, text="诊断报错、按题目生成代码、从 Word/TXT 中拆分多段代码。",
                 bg=Theme.PANEL, fg=Theme.MUTED, font=(UI_FAMILY, 8), anchor="w",
                 justify="left", wraplength=self.px(300)).pack(fill="x", pady=(0, 8))
        row1 = tk.Frame(inner, bg=Theme.PANEL)
        row1.pack(fill="x")
        row1.grid_columnconfigure(0, weight=1)
        row1.grid_columnconfigure(1, weight=1)
        self.diagnose_button = ttk.Button(row1, text="诊断当前代码", style="Secondary.TButton",
                                          command=self.diagnose_current)
        self.diagnose_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.generate_button = ttk.Button(row1, text="生成 / 拆题", style="Secondary.TButton",
                                          command=self.open_ai_generate)
        self.generate_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.ai_settings_button = ttk.Button(inner, text="AI 设置  →", style="Link.TButton",
                                             command=self.open_settings)
        self.ai_settings_button.pack(anchor="w", pady=(7, 0))

    def _build_help_card(self, parent: tk.Frame) -> None:
        outer, inner = self._side_card(parent)
        card_heading(inner, "三步搞定").pack(fill="x")
        steps = [
            ("1", "导入或粘贴 C++ 代码"),
            ("2", "有 cin 的话填运行输入"),
            ("3", "点运行，截图自动存好"),
        ]
        for num, text in steps:
            row = tk.Frame(inner, bg=Theme.PANEL)
            row.pack(fill="x", pady=(7, 0))
            tk.Label(row, text=num, bg=Theme.ACCENT_DARK, fg=Theme.ACCENT,
                     font=(UI_FAMILY, 8, "bold"), width=2).pack(side="left")
            tk.Label(row, text=text, bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                     font=(UI_FAMILY, 8)).pack(side="left", padx=7)
        tk.Label(inner, text="Ctrl+Enter 运行当前 · Ctrl+Shift+Enter 运行全部",
                 bg=Theme.PANEL, fg=Theme.FAINT, font=(UI_FAMILY, 8), anchor="w").pack(
            fill="x", pady=(10, 0))

    # ------------------------------------------------------------------ events
    def _bind_events(self) -> None:
        self.queue_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.name_var.trace_add("write", self._on_name_changed)
        self.root.bind("<Control-Return>", lambda _e: self.run_current())
        self.root.bind("<Control-Shift-Return>", lambda _e: self.run_all())
        self.root.bind("<Control-n>", lambda _e: self.new_item())
        self.root.bind("<Control-o>", lambda _e: self.import_cpp())
        self.root.bind("<Control-s>", lambda _e: self.save_session_now())
        self.root.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.log("欢迎使用 CppShot。导入代码 → 运行 → 截图。", "accent")
        self.log("提示：批量运行遇到编译错误或非 0 退出码会立即停止，并显示完整报错。", "info")

    def _on_global_mousewheel(self, event) -> str | None:
        """让鼠标停在右侧配置栏的任意子控件上时都能滚动。"""
        try:
            x, y = self.root.winfo_pointerxy()
            left = self.side.winfo_rootx()
            top = self.side.winfo_rooty()
            right = left + self.side.winfo_width()
            bottom = top + self.side.winfo_height()
            if left <= x < right and top <= y < bottom:
                self.side.scroll_by(int(-1 * (event.delta / 120)))
                return "break"
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ queue data
    def _current_item(self) -> CodeItem | None:
        if 0 <= self.current_index < len(self.items):
            return self.items[self.current_index]
        return None

    def _refresh_queue(self) -> None:
        self._ignore_select = True
        selected_id = self._current_item().id if self._current_item() else None
        self.queue_tree.delete(*self.queue_tree.get_children())
        for idx, item in enumerate(self.items):
            self.queue_tree.insert(
                "", "end", iid=item.id,
                values=(f"{idx + 1:02d}", item.name or "未命名", f"{item.status_icon} {item.status_label}"),
                tags=(item.status,),
            )
        if selected_id and self.queue_tree.exists(selected_id):
            self.queue_tree.selection_set(selected_id)
            self.queue_tree.focus(selected_id)
        self.queue_count.configure(text=str(len(self.items)))
        self._ignore_select = False
        self._update_action_states()

    def _update_tree_row(self, index: int) -> None:
        if not (0 <= index < len(self.items)):
            return
        item = self.items[index]
        if not self.queue_tree.exists(item.id):
            return
        self.queue_tree.item(
            item.id,
            values=(f"{index + 1:02d}", item.name or "未命名",
                    f"{item.status_icon} {item.status_label}"),
            tags=(item.status,),
        )

    def _on_tree_select(self, _event=None) -> None:
        if self._ignore_select or self._busy:
            return
        selection = self.queue_tree.selection()
        if not selection:
            return
        item_id = selection[0]
        index = next((i for i, item in enumerate(self.items) if item.id == item_id), None)
        if index is None or index == self.current_index:
            return
        self.current_index = index
        self._load_item(index)
        self._schedule_session_save()

    def _load_item(self, index: int) -> None:
        if not self.items:
            self._show_empty_editor()
            return
        index = max(0, min(index, len(self.items) - 1))
        self.current_index = index
        item = self.items[index]
        self._loading = True
        try:
            self.editor.set_readonly(False)
            self.editor.set_code(item.code)
            self.name_var.set(item.name)
            self.stdin_text.configure(state="normal")
            self.stdin_text.delete("1.0", "end")
            self.stdin_text.insert("1.0", item.stdin)
            self.stdin_text.edit_modified(False)
            self.stdin_text.configure(state="normal" if not self._busy else "disabled")
            self.name_entry.configure(state="normal" if not self._busy else "disabled")
            source = f"来源：{item.source_path}" if item.source_path else "来源：手动输入 / 编辑器"
            if len(source) > 78:
                source = source[:36] + " … " + source[-34:]
            self.source_label.configure(text=source)
            self._update_status_pill()
        finally:
            self._loading = False
        if not self._busy:
            self.editor.set_readonly(False)
        self._update_action_states()

    def _show_empty_editor(self) -> None:
        self._loading = True
        try:
            self.editor.set_code("")
            self.editor.set_readonly(True)
            self.name_var.set("")
            self.name_entry.configure(state="disabled")
            self.stdin_text.configure(state="normal")
            self.stdin_text.delete("1.0", "end")
            self.stdin_text.configure(state="disabled")
            self.source_label.configure(text="队列为空，点击左侧「新建代码」")
            self.status_pill.configure(text="无代码", bg=Theme.PANEL_ALT, fg=Theme.FAINT)
        finally:
            self._loading = False

    def _update_status_pill(self) -> None:
        item = self._current_item()
        if not item:
            self.status_pill.configure(text="无代码", bg=Theme.PANEL_ALT, fg=Theme.FAINT)
            return
        colors = {
            "pending": (Theme.PANEL_ALT, Theme.MUTED),
            "running": (Theme.ACCENT_DARK, Theme.ACCENT),
            "success": (Theme.GREEN_DARK, Theme.GREEN),
            "failed": (Theme.RED_DARK, Theme.RED),
        }
        bg, fg = colors.get(item.status, (Theme.PANEL_ALT, Theme.MUTED))
        prefix = "● " if item.status == "running" else ""
        self.status_pill.configure(text=prefix + item.status_label, bg=bg, fg=fg)

    def _on_editor_changed(self) -> None:
        if self._loading or self._busy:
            return
        item = self._current_item()
        if not item:
            return
        item.code = self.editor.get_code()
        if item.status in ("success", "failed"):
            item.status = "pending"
            self._update_tree_row(self.current_index)
            self._update_status_pill()
        self._schedule_session_save()

    def _on_stdin_modified(self, _event=None) -> None:
        try:
            if not self.stdin_text.edit_modified():
                return
            self.stdin_text.edit_modified(False)
        except tk.TclError:
            return
        if self._loading or self._busy:
            return
        item = self._current_item()
        if not item:
            return
        item.stdin = self.stdin_text.get("1.0", "end-1c")
        if item.status in ("success", "failed"):
            item.status = "pending"
            self._update_tree_row(self.current_index)
            self._update_status_pill()
        self._schedule_session_save()

    def _on_name_changed(self, *_args) -> None:
        if self._loading or self._busy:
            return
        item = self._current_item()
        if not item:
            return
        item.name = self.name_var.get()
        self._update_tree_row(self.current_index)
        self._schedule_session_save()

    def _on_cursor(self, line: int, col: int) -> None:
        if hasattr(self, "cursor_label"):
            self.cursor_label.configure(text=f"Ln {line}, Col {col}")

    def _rename_from_tree(self, event) -> None:
        if self._busy:
            return
        row = self.queue_tree.identify_row(event.y)
        if not row:
            return
        try:
            self.queue_tree.selection_set(row)
        except tk.TclError:
            return
        self.name_entry.focus_set()
        self.name_entry.selection_range(0, "end")

    # ------------------------------------------------------------------ actions
    def _sync_current(self) -> None:
        item = self._current_item()
        if not item or self._loading:
            return
        item.code = self.editor.get_code()
        item.stdin = self.stdin_text.get("1.0", "end-1c")
        item.name = self.name_var.get()

    def _unique_item_name(self, base: str) -> str:
        return unique_name([item.name for item in self.items], base)

    def add_item(self, item: CodeItem, *, select: bool = True, log: bool = True) -> int:
        item.name = unique_name([existing.name for existing in self.items], item.name)
        self.items.append(item)
        index = len(self.items) - 1
        self._refresh_queue()
        if select:
            self.current_index = index
            self._refresh_queue()
            self._load_item(index)
        if log:
            self.log(f"＋ 已加入：{item.name}", "ok")
        self._schedule_session_save()
        return index

    def new_item(self) -> None:
        if self._busy:
            return
        self.add_item(CodeItem(name=self._unique_item_name("新代码"), code=DEFAULT_CODE))
        self.root.after(30, lambda: (self.name_entry.focus_set(),
                                     self.name_entry.selection_range(0, "end")))

    def import_cpp(self) -> None:
        if self._busy:
            return
        paths = filedialog.askopenfilenames(
            parent=self.root, title="导入 C++ 文件（可多选）",
            filetypes=[("C++ 源文件", "*.cpp *.cc *.cxx *.c"), ("所有文件", "*.*")],
        )
        if not paths:
            return
        added = 0
        first_index = None
        for raw in paths:
            path = Path(raw)
            try:
                data = path.read_bytes()
                code = None
                for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                    try:
                        code = data.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                if code is None:
                    code = data.decode("utf-8", errors="replace")
            except OSError as exc:
                messagebox.showerror("读取失败", f"{path.name}\n{exc}", parent=self.root)
                continue
            item = CodeItem(name=self._unique_item_name(path.stem), code=code,
                            source_path=str(path))
            self.items.append(item)
            added += 1
            if first_index is None:
                first_index = len(self.items) - 1
        if added:
            self._refresh_queue()
            if first_index is not None:
                self.current_index = first_index
                self._refresh_queue()
                self._load_item(first_index)
            self.log(f"⇩ 已导入 {added} 个 C++ 文件，自动排入队列。", "ok")
            self.toast(f"已导入 {added} 个文件")
            self._schedule_session_save()

    def delete_item(self) -> None:
        if self._busy or not self.items:
            return
        item = self._current_item()
        if item is None:
            return
        if not messagebox.askyesno("删除代码", f"确定删除「{item.name or '未命名'}」吗？",
                                   parent=self.root):
            return
        index = self.current_index
        self.items.pop(index)
        if self.items:
            self.current_index = min(index, len(self.items) - 1)
        else:
            self.current_index = 0
        self._refresh_queue()
        if self.items:
            self._load_item(self.current_index)
        else:
            self._show_empty_editor()
        self._schedule_session_save()
        self.log(f"－ 已删除「{item.name or '未命名'}」", "info")

    def clear_items(self) -> None:
        if self._busy or not self.items:
            return
        if not messagebox.askyesno("清空队列", "确定清空所有代码吗？此操作无法撤销。",
                                   parent=self.root):
            return
        self.items.clear()
        self.current_index = 0
        self._refresh_queue()
        self._show_empty_editor()
        self._schedule_session_save()
        self.log("清空了代码队列。", "warn")

    def move_item(self, direction: int) -> None:
        if self._busy or not self.items:
            return
        index = self.current_index
        target = index + direction
        if not (0 <= target < len(self.items)):
            return
        self.items[index], self.items[target] = self.items[target], self.items[index]
        self.current_index = target
        self._refresh_queue()
        self._load_item(target)
        self._schedule_session_save()

    def copy_code(self) -> None:
        item = self._current_item()
        if not item:
            return
        self._sync_current()
        copy_to_clipboard(self.root, item.code)
        self.toast("代码已复制到剪贴板")

    def export_code(self) -> None:
        item = self._current_item()
        if not item:
            return
        self._sync_current()
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出 C++ 文件", defaultextension=".cpp",
            initialfile=safe_filename(item.name or "program") + ".cpp",
            filetypes=[("C++ 源文件", "*.cpp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(item.code, encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return
        self.toast("已导出 .cpp 文件")

    # ------------------------------------------------------------------ save dir
    def choose_save_dir(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="选择截图保存位置",
                                           initialdir=self.config.save_dir)
        if not selected:
            return
        self.config.save_dir = selected
        self.save_var.set(selected)
        self.store.save_config(self.config)
        self.toast("截图保存位置已更新")

    def copy_save_dir(self) -> None:
        copy_to_clipboard(self.root, self.config.save_dir)
        self.toast("保存路径已复制")

    def open_save_dir(self) -> None:
        self.open_folder(self.config.save_dir)

    def open_last_screenshot(self) -> None:
        path = self.config.last_screenshot
        if path and Path(path).exists():
            try:
                open_path(path)
            except Exception as exc:
                messagebox.showerror("打开失败", str(exc), parent=self.root)
        else:
            self.toast("还没有生成过截图")

    def _on_auto_open_changed(self) -> None:
        self.config.open_folder_after = bool(self.auto_open_var.get())
        self.store.save_config(self.config)

    def _update_last_screenshot(self) -> None:
        path = self.config.last_screenshot
        if path and Path(path).exists():
            self.last_shot_button.state(["!disabled"])
        else:
            self.last_shot_button.state(["disabled"])

    def open_folder(self, path: str | Path) -> None:
        try:
            target = Path(path)
            if target.suffix and target.is_file():
                open_path(target)
            else:
                target.mkdir(parents=True, exist_ok=True)
                open_path(target)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)

    # ------------------------------------------------------------------ compiler
    def _refresh_compiler_choices(self) -> None:
        infos = compiler_infos(self.compilers)
        labels = [label for _id, label in infos]
        self._compiler_label_to_id = {label: cid for cid, label in infos}
        self._compiler_id_to_label = {cid: label for cid, label in infos}
        self.compiler_combo.configure(values=labels)
        desired = self._compiler_id_to_label.get(self.config.compiler_id)
        if not desired:
            desired = labels[0] if labels else ""
            if self.config.compiler_id != "auto":
                self.config.compiler_id = "auto"
        if desired:
            self.compiler_var.set(desired)

    def _on_compiler_selected(self, _event=None) -> None:
        self.config.compiler_id = self._compiler_label_to_id.get(self.compiler_var.get(), "auto")
        self.store.save_config(self.config)
        self._update_compiler_badge()
        self.toast(f"已切换编译器：{self.compiler_var.get()}")

    def _resolve_compiler(self) -> Compiler | None:
        return select_compiler(self.compilers, self.config.compiler_id)

    def _update_compiler_badge(self) -> None:
        compiler = self._resolve_compiler()
        if compiler:
            self.compiler_badge.configure(text=f"● {compiler.short_label}", fg=Theme.GREEN)
        else:
            self.compiler_badge.configure(text="● 未检测到编译器", fg=Theme.RED)

    def redetect_compilers(self) -> None:
        if self._busy:
            return
        self.compiler_badge.configure(text="● 检测中…", fg=Theme.MUTED)
        self.root.update_idletasks()
        self.compilers = detect_compilers()
        self._refresh_compiler_choices()
        self._update_compiler_badge()
        self._update_action_states()
        if self.compilers:
            self.toast(f"检测到 {len(self.compilers)} 个编译器")
            self.log(f"✓ 编译器检测完成：{', '.join(c.short_label for c in self.compilers)}", "ok")
        else:
            self.toast("没有检测到 C++ 编译器")
            self._warn_no_compiler()

    def _warn_no_compiler(self) -> None:
        if self.compilers:
            return
        messagebox.showwarning(
            "没有找到 C++ 编译器",
            "CppShot 需要本机的 C++ 编译器才能真正运行代码。\n\n"
            "推荐安装 Visual Studio Build Tools（勾选「使用 C++ 的桌面开发」），"
            "或安装 MinGW-w64 并把 g++ 加入 PATH，然后点右上角「重新检测」。",
            parent=self.root,
        )

    def _on_timeout_changed(self) -> None:
        try:
            value = int(float(self.timeout_var.get()))
        except ValueError:
            value = self.config.timeout_sec
        value = max(1, min(300, value))
        self.timeout_var.set(str(value))
        self.config.timeout_sec = value
        self.store.save_config(self.config)

    # ------------------------------------------------------------------ run
    def _update_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.configure(maximum=1, value=0)
            self.progress_text.configure(text="等待运行")
            return
        self.progress.configure(maximum=total, value=done)
        self.progress_text.configure(text=f"{done} / {total} 个程序")

    def run_current(self) -> None:
        if self._busy or not self.items:
            return
        self._sync_current()
        self._start_run([self.current_index], batch=False)

    def run_all(self) -> None:
        if self._busy or not self.items:
            return
        self._sync_current()
        self._start_run(list(range(len(self.items))), batch=True)

    def run_interactive_current(self) -> None:
        """打开实时交互控制台，边运行边输入。"""
        if self._busy or not self.items:
            return
        self._sync_current()
        compiler = self._resolve_compiler()
        if compiler is None:
            self._warn_no_compiler()
            return
        item = self._current_item()
        if item is None or not item.code.strip():
            messagebox.showwarning("代码为空", "当前条目没有可运行的代码。", parent=self.root)
            return
        save_dir = Path(self.config.save_dir).expanduser()
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            probe = save_dir / ".cppshot_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            messagebox.showerror(
                "截图目录不可写",
                f"无法写入：{save_dir}\n{exc}\n\n请在右侧重新选择一个保存位置。",
                parent=self.root,
            )
            return
        snapshot = copy.deepcopy(item)
        self._set_busy(True)
        try:
            dialog = InteractiveRunDialog(self, snapshot, compiler)
            self.interactive_dialog = dialog
            self.log(f"▶ 交互运行：{item.name or '未命名'} · {compiler.short_label}", "accent")
        except Exception as exc:
            self._set_busy(False)
            messagebox.showerror("无法打开交互运行", f"{type(exc).__name__}: {exc}",
                                 parent=self.root)

    def on_interactive_result(self, item_id: str, status: str, exit_code: int | None,
                              transcript: str, image_path: str, error: str) -> None:
        index = next((i for i, item in enumerate(self.items) if item.id == item_id), None)
        if index is None:
            return
        item = self.items[index]
        item.status = status
        item.last_exit_code = exit_code
        item.last_output = transcript
        item.last_error = error
        if image_path:
            item.last_image = image_path
            self.config.last_screenshot = image_path
            self.store.save_config(self.config)
            self._update_last_screenshot()
            self.log(
                f"✓ 交互运行完成：{item.name or '未命名'} · 截图 {Path(image_path).name}",
                "ok",
            )
            self.toast(f"交互截图已保存：{Path(image_path).name}")
        else:
            message = error.strip().splitlines()[0] if error.strip() else "交互运行失败"
            self.log(f"✕ 交互运行失败：{item.name or '未命名'} · {message}", "fail")
        self._update_tree_row(index)
        if index == self.current_index:
            self._update_status_pill()
        self._schedule_session_save()

    def on_interactive_closed(self) -> None:
        self.interactive_dialog = None
        if self._busy:
            self._set_busy(False)
        self._update_action_states()
        self.log("交互运行窗口已关闭。", "info")

    def _start_run(self, indices: list[int], *, batch: bool) -> None:
        if self._busy or not indices:
            return
        compiler = self._resolve_compiler()
        if compiler is None:
            self._warn_no_compiler()
            return
        # 每份代码都必须有内容。
        empty = [self.items[i].name or "未命名" for i in indices if not self.items[i].code.strip()]
        if empty:
            messagebox.showwarning("代码为空", f"这些条目没有代码：{', '.join(empty)}", parent=self.root)
            return
        save_dir = Path(self.config.save_dir).expanduser()
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            probe = save_dir / ".cppshot_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            messagebox.showerror(
                "截图目录不可写",
                f"无法写入：{save_dir}\n{exc}\n\n请在右侧重新选择一个保存位置。",
                parent=self.root,
            )
            return

        timeout = self.config.timeout_sec
        snapshots = [copy.deepcopy(self.items[i]) for i in indices]
        self._run_indices = indices
        self._run_total = len(indices)
        self._run_successes = []
        self._pending_failure = None
        self._run_messages = queue.Queue()
        self._set_busy(True)
        self._update_progress(0, self._run_total)
        for i in indices:
            self.items[i].status = "pending"
            self._update_tree_row(i)
        self.progress_label.configure(text="准备编译…", fg=Theme.ACCENT)
        self.log("─" * 46, "info")
        mode = "批量" if batch else "单次"
        self.log(f"▶ {mode}运行开始：共 {len(indices)} 个程序 · {compiler.short_label}", "accent")

        def worker() -> None:
            success_count = 0
            try:
                for position, (index, item) in enumerate(zip(indices, snapshots), 1):
                    self._run_messages.put(("status", index, position, item.name))
                    try:
                        outcome = compile_and_run(item, compiler, timeout)
                    except Exception as exc:  # 保底：任何异常都按失败处理
                        outcome = RunOutcome(
                            ok=False, stage="prepare", exit_code=None, output="",
                            error=f"{type(exc).__name__}: {exc}", compiler=compiler.label,
                        )
                    if not outcome.ok:
                        self._run_messages.put(("failed", index, position, outcome))
                        return
                    try:
                        path = save_screenshot(
                            item, outcome.output, save_dir, compiler.label,
                            index=position if batch else None,
                        )
                        self._run_successes.append(str(path))
                    except Exception as exc:
                        outcome = RunOutcome(
                            ok=False, stage="screenshot", exit_code=outcome.exit_code,
                            output=outcome.output,
                            error=f"截图保存失败：{type(exc).__name__}: {exc}",
                            elapsed=outcome.elapsed, compiler=compiler.label,
                        )
                        self._run_messages.put(("failed", index, position, outcome))
                        return
                    success_count += 1
                    self._run_messages.put(("success", index, position, outcome, str(path)))
                self._run_messages.put(("done", success_count, self._run_total, None))
            except Exception as exc:  # pragma: no cover - 最后保险
                self._run_messages.put(("fatal", f"{type(exc).__name__}: {exc}"))

        thread = threading.Thread(target=worker, daemon=True)
        self._run_poll_active = True
        thread.start()
        self.root.after(80, self._poll_run)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for button in (
            self.new_button, self.import_button, self.delete_button, self.clear_button,
            self.up_button, self.down_button, self.run_current_button, self.run_all_button,
            self.run_interactive_button, self.diagnose_button, self.generate_button,
        ):
            button.configure(state=state)
        self.compiler_combo.configure(state="disabled" if busy else "readonly")
        self.timeout_spin.configure(state=state)
        self.name_entry.configure(state="disabled" if busy else "normal")
        self.stdin_text.configure(state="disabled" if busy else "normal")
        try:
            self.editor.set_readonly(busy)
        except Exception:
            pass
        try:
            self.queue_tree.configure(selectmode="none" if busy else "browse")
        except tk.TclError:
            pass
        self._update_action_states()

    def _poll_run(self) -> None:
        done_received = False
        while True:
            try:
                message = self._run_messages.get_nowait()
            except queue.Empty:
                break
            kind = message[0]
            if kind == "status":
                _, index, position, name = message
                self.items[index].status = "running"
                self._update_tree_row(index)
                if index == self.current_index:
                    self._update_status_pill()
                self.progress_label.configure(text=f"正在运行第 {position}/{self._run_total} 个",
                                              fg=Theme.ACCENT)
                self.log(f"[{position}/{self._run_total}] {name}：编译并运行…", "info")
            elif kind == "success":
                _, index, position, outcome, path = message
                item = self.items[index]
                item.status = "success"
                item.last_exit_code = outcome.exit_code
                item.last_output = outcome.output
                item.last_image = path
                item.last_error = ""
                self._update_tree_row(index)
                if index == self.current_index:
                    self._update_status_pill()
                    self._update_last_screenshot()
                self.progress_label.configure(text=f"第 {position} 个完成", fg=Theme.GREEN)
                self.log(
                    f"✓ {item.name or '未命名'} 运行成功 · 用时 {outcome.elapsed:.1f}s · "
                    f"截图 {Path(path).name}",
                    "ok",
                )
                self.config.last_screenshot = path
                self.store.save_config(self.config)
                self._update_last_screenshot()
            elif kind == "failed":
                _, index, position, outcome = message
                item = self.items[index]
                item.status = "failed"
                item.last_exit_code = outcome.exit_code
                item.last_output = outcome.output
                item.last_error = outcome.error
                self._update_tree_row(index)
                if index == self.current_index:
                    self._update_status_pill()
                self.progress_label.configure(text=f"第 {position} 个失败，已中止", fg=Theme.RED)
                self.log(
                    f"✕ {item.name or '未命名'} 失败 · 阶段 {outcome.stage} · "
                    f"退出代码 {outcome.exit_code_display}",
                    "fail",
                )
                detail = outcome.output.strip() or outcome.error.strip()
                if detail:
                    for line in detail.splitlines()[:12]:
                        self.log("   " + line, "fail")
                else:
                    self.log("   " + (outcome.error or "没有更多信息"), "fail")
                self._pending_failure = (index, outcome, position)
                done_received = True
            elif kind == "done":
                _, success_count, total, _ = message
                self.progress_label.configure(
                    text=f"完成 {success_count}/{total}", fg=Theme.GREEN if success_count == total else Theme.YELLOW)
                self.log(f"■ 运行结束：成功 {success_count}/{total} 个。", "ok" if success_count == total else "warn")
                done_received = True
            elif kind == "fatal":
                self.log(f"✕ 内部错误：{message[1]}", "fail")
                done_received = True
        if self._pending_failure:
            done_received = True
        if done_received:
            self._run_poll_active = False
            self._set_busy(False)
            self._update_action_states()
            self._update_progress(len(self._run_successes), self._run_total)
            if self._run_successes:
                self.config.last_screenshot = self._run_successes[-1]
                self.store.save_config(self.config)
                self._update_last_screenshot()
                self.toast(f"已生成 {len(self._run_successes)} 张截图")
            if self._pending_failure:
                index, outcome, position = self._pending_failure
                self._pending_failure = None
                dialog = FailureDialog(
                    self, self.items[index].name or "未命名", outcome,
                    position=position, total=self._run_total,
                    screenshot_dir=self.config.save_dir,
                )
                self.root.after(80, dialog._grab)
            elif self.config.open_folder_after and self._run_successes:
                self.open_folder(self.config.save_dir)
            self._save_session_now_internal()
            return
        if self._run_poll_active:
            self.root.after(80, self._poll_run)

    # ------------------------------------------------------------------ AI
    def ai_config(self) -> AIConfig:
        return AIConfig.from_app_config(self.config)

    def _update_api_status(self) -> None:
        configured = bool(self.config.api_key.strip())
        if configured:
            self.api_badge.configure(text="● API 已配置", fg=Theme.GREEN)
            self.ai_status_label.configure(text=f"● 已连接配置 · {self.config.api_model}",
                                           fg=Theme.GREEN)
        else:
            self.api_badge.configure(text="● API 未配置", fg=Theme.FAINT)
            self.ai_status_label.configure(text="● 未配置 API Key（可选）", fg=Theme.FAINT)

    def on_config_changed(self) -> None:
        self._update_api_status()
        self._refresh_compiler_choices()
        self._update_compiler_badge()

    def open_settings(self) -> None:
        SettingsDialog(self)

    def open_ai_generate(self) -> None:
        if self._busy:
            return
        AIGenerateDialog(self)

    def diagnose_current(self) -> None:
        if self._busy:
            return
        item = self._current_item()
        if item is None:
            messagebox.showinfo("没有代码", "请先在左侧新建或导入一份代码。", parent=self.root)
            return
        if not self.ai_config().configured:
            messagebox.showinfo(
                "AI 功能需要 API Key",
                "诊断代码需要先配置一个 OpenAI 兼容接口。\n"
                "点「AI 设置」填入 API Key 即可，不配置也不影响截图功能。",
                parent=self.root,
            )
            self.open_settings()
            return
        if self._ai_busy:
            return
        self._sync_current()
        self._ai_busy = True
        self.diagnose_button.configure(state="disabled", text="诊断中…")
        self.generate_button.configure(state="disabled")
        self.ai_status_label.configure(text="● AI 正在诊断代码…", fg=Theme.ACCENT)
        snapshot = copy.deepcopy(item)
        config = self.ai_config()
        self.log(f"AI 正在诊断「{snapshot.name or '未命名'}」…", "accent")

        def ok(result: object) -> None:
            self._ai_busy = False
            self.diagnose_button.configure(state="normal", text="诊断当前代码")
            self.generate_button.configure(state="normal")
            self._update_api_status()
            self.log("AI 诊断完成。", "ok")
            AIResultDialog(
                self, "AI 代码诊断", str(result),
                replace_callback=lambda code: self.replace_item_code(snapshot.id, code),
            )

        def error(exc: Exception) -> None:
            self._ai_busy = False
            self.diagnose_button.configure(state="normal", text="诊断当前代码")
            self.generate_button.configure(state="normal")
            self._update_api_status()
            self.log(f"AI 诊断失败：{exc}", "fail")
            messagebox.showerror("AI 诊断失败", str(exc), parent=self.root)

        run_async(self.root, lambda: ai_diagnose(config, snapshot, snapshot.last_error), ok, error)

    def import_ai_blocks(self, blocks: list[dict[str, str]], source_name: str = "AI拆题") -> None:
        if not blocks:
            return
        first_new = None
        for block in blocks:
            title = str(block.get("title", "") or "AI代码").strip()
            code = str(block.get("code", "")).strip()
            if not code:
                continue
            item = CodeItem(name=self._unique_item_name(title), code=code)
            self.items.append(item)
            if first_new is None:
                first_new = len(self.items) - 1
        self._refresh_queue()
        if first_new is not None:
            self.current_index = first_new
            self._refresh_queue()
            self._load_item(first_new)
        self.log(f"⇩ 文档拆分完成，已加入 {len(blocks)} 段代码。", "ok")
        self._schedule_session_save()

    def add_generated_code(self, code: str, title: str = "AI生成代码") -> None:
        item = CodeItem(name=self._unique_item_name(title), code=code)
        self.add_item(item)
        self.log("AI 代码已加入队列。运行前请检查并补齐输入。", "warn")

    def replace_current_code(self, code: str) -> None:
        item = self._current_item()
        if item:
            self.replace_item_code(item.id, code)
        else:
            self.add_generated_code(code, "AI修复代码")

    def replace_item_code(self, item_id: str, code: str) -> None:
        if self._busy:
            return
        index = next((i for i, item in enumerate(self.items) if item.id == item_id), None)
        if index is None:
            return
        item = self.items[index]
        item.code = code
        item.status = "pending"
        is_current = index == self.current_index
        if is_current:
            self._loading = True
            try:
                self.editor.set_code(code)
                self.name_var.set(item.name)
            finally:
                self._loading = False
        self._update_tree_row(index)
        if is_current:
            self._update_status_pill()
        self._schedule_session_save()
        self.toast("已替换为 AI 修复版代码")
        self.log(f"AI 修复版已写入「{item.name or '未命名'}」，请检查后运行。", "warn")

    # ------------------------------------------------------------------ log/status
    def log(self, message: str, tag: str = "info") -> None:
        if not hasattr(self, "log_text"):
            return
        try:
            self.log_text.configure(state="normal")
            stamp = time.strftime("%H:%M:%S")
            self.log_text.insert("end", f"[{stamp}] ", "info")
            self.log_text.insert("end", message + "\n", tag)
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 400:
                self.log_text.delete("1.0", f"{lines - 400}.0")
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
        except tk.TclError:
            pass

    def copy_log(self) -> None:
        text = self.log_text.get("1.0", "end-1c")
        copy_to_clipboard(self.root, text)
        self.toast("运行日志已复制")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.log("日志已清空。", "info")

    def _set_status(self, text: str, fg: str | None = None) -> None:
        self.status_label.configure(text=text, fg=fg or Theme.MUTED)

    def toast(self, text: str, fg: str | None = None) -> None:
        self._set_status("● " + text, fg or Theme.GREEN)
        if self._toast_job:
            try:
                self.root.after_cancel(self._toast_job)
            except tk.TclError:
                pass
        self._toast_job = self.root.after(
            4000, lambda: self._set_status("就绪 · 支持批量运行与自动截图")
        )

    # ------------------------------------------------------------------ persistence
    def _schedule_session_save(self) -> None:
        if self._loading:
            return
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.root.after(700, self._save_session_now_internal)

    def _save_session_now_internal(self) -> None:
        self._save_job = None
        self._sync_current()
        try:
            self.store.save_session(self.items, self.current_index)
        except Exception:
            pass

    def save_session_now(self) -> None:
        self._save_session_now_internal()
        self.toast("代码队列已保存")

    def _update_action_states(self) -> None:
        has_items = bool(self.items) and self._current_item() is not None
        has_compiler = bool(self.compilers)
        can_run = has_items and has_compiler and not self._busy
        self.run_current_button.configure(state="normal" if can_run else "disabled")
        self.run_all_button.configure(state="normal" if can_run else "disabled")
        self.run_interactive_button.configure(state="normal" if can_run else "disabled")
        self.delete_button.configure(state="normal" if has_items and not self._busy else "disabled")
        self.copy_code_button.configure(state="normal" if has_items else "disabled")
        self.export_button.configure(state="normal" if has_items else "disabled")
        self.up_button.configure(state="normal" if has_items and not self._busy else "disabled")
        self.down_button.configure(state="normal" if has_items and not self._busy else "disabled")
        self.clear_button.configure(state="normal" if has_items and not self._busy else "disabled")
        self.diagnose_button.configure(state="normal" if has_items and not self._busy else "disabled")
        if not has_items:
            self.editor.set_readonly(True)
            self.stdin_text.configure(state="disabled")

    def on_close(self) -> None:
        if self._busy:
            messagebox.showinfo("正在运行", "程序还在编译/运行，请等这一轮结束后再关闭。", parent=self.root)
            return
        self._save_session_now_internal()
        self.store.save_config(self.config)
        self.root.destroy()
