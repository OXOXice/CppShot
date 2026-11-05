"""自定义控件：滚动容器、C++ 代码编辑器、异步任务。"""

from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .theme import MONO_FAMILY, Theme, UI_FAMILY


class ScrollFrame(tk.Frame):
    """右侧窄栏使用的可滚动 Frame。"""

    def __init__(self, parent: tk.Misc, *, bg: str = Theme.BG, width: int = 340) -> None:
        super().__init__(parent, bg=bg, width=width)
        self.pack_propagate(False)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=width)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        # 先给滚动条分配宽度，避免 Canvas 的 expand 把它挤成 0 像素。
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.inner.bind("<Configure>", self._update_region)
        self.canvas.bind("<Configure>", self._resize_inner)

    def _update_region(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_inner(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _on_scroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)

    def scroll_by(self, units: int) -> None:
        try:
            top, bottom = self.canvas.yview()
            if bottom - top >= 0.999:
                return
            self.canvas.yview_scroll(units, "units")
        except Exception:
            pass

    def scroll_to_top(self) -> None:
        try:
            self.canvas.yview_moveto(0)
        except Exception:
            pass


class CodeEditor(tk.Frame):
    """带行号和轻量 C++ 语法高亮的编辑器。"""

    _KEYWORDS = re.compile(
        r"\b(?:alignas|alignof|and|asm|auto|bitand|bitor|bool|break|case|catch|char|"
        r"char8_t|char16_t|char32_t|class|compl|concept|const|consteval|constexpr|"
        r"constinit|const_cast|continue|co_await|co_return|co_yield|decltype|default|"
        r"delete|do|double|dynamic_cast|else|enum|explicit|export|extern|false|float|"
        r"for|friend|goto|if|inline|int|long|mutable|namespace|new|noexcept|nullptr|"
        r"operator|or|private|protected|public|register|reinterpret_cast|requires|"
        r"return|short|signed|sizeof|static|static_assert|static_cast|struct|switch|"
        r"template|this|thread_local|throw|true|try|typedef|typeid|typename|union|"
        r"unsigned|using|virtual|void|volatile|wchar_t|while|xor)\b"
    )
    _TYPES = re.compile(
        r"\b(?:string|vector|map|set|pair|queue|stack|deque|list|array|shared_ptr|"
        r"unique_ptr|size_t|ostream|istream|stringstream|fstream|ifstream|ofstream|"
        r"cout|cin|cerr|endl|std)\b"
    )
    _NUMBER = re.compile(r"\b(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)[fFuUlL]*\b")
    _PREPROC = re.compile(r"^\s*#\s*\w+.*$", re.MULTILINE)
    _STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
    _COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_change: Callable[[], None] | None = None,
        on_cursor: Callable[[int, int], None] | None = None,
        on_run: Callable[[], None] | None = None,
        on_save: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, bg=Theme.EDITOR_BG, bd=0)
        self.on_change = on_change
        self.on_cursor = on_cursor
        self.on_run = on_run
        self.on_save = on_save
        self._readonly = False
        self._highlight_job: str | None = None
        self._line_count = 0

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.gutter = tk.Text(
            self, width=5, padx=8, pady=12, bg=Theme.EDITOR_GUTTER,
            fg=Theme.FAINT, font=(MONO_FAMILY, 10), bd=0, highlightthickness=0,
            wrap="none", state="disabled", cursor="arrow", takefocus=0,
            selectbackground=Theme.EDITOR_GUTTER,
        )
        self.gutter.grid(row=0, column=0, sticky="ns")

        self.text = tk.Text(
            self, bg=Theme.EDITOR_BG, fg=Theme.TEXT_SOFT, insertbackground=Theme.ACCENT,
            selectbackground=Theme.SELECT, selectforeground=Theme.TEXT,
            font=(MONO_FAMILY, 11), bd=0, highlightthickness=0,
            padx=10, pady=12, wrap="none", undo=True, maxundo=-1,
            spacing1=1, spacing3=1, tabs=("4c",),
        )
        self.text.grid(row=0, column=1, sticky="nsew", padx=(0, 0))

        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.vbar.grid(row=0, column=2, sticky="ns")
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.hbar.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.text.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self.hbar.set)

        self._configure_tags()
        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<KeyRelease>", self._update_cursor)
        self.text.bind("<ButtonRelease-1>", self._update_cursor)
        self.text.bind("<Tab>", self._tab)
        self.text.bind("<Shift-Tab>", self._shift_tab)
        self.text.bind("<Control-Return>", self._run_shortcut)
        self.text.bind("<Control-s>", self._save_shortcut)
        self.text.bind("<Control-S>", self._save_shortcut)
        self.text.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.gutter.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self._draw_gutter()

    def _configure_tags(self) -> None:
        self.text.tag_configure("keyword", foreground="#C4B5FD")
        self.text.tag_configure("type", foreground="#7DD3FC")
        self.text.tag_configure("number", foreground="#FDBA74")
        self.text.tag_configure("preproc", foreground="#FBBF24")
        self.text.tag_configure("string", foreground="#86EFAC")
        self.text.tag_configure("comment", foreground="#64748B")
        self.text.tag_configure("line_current", background="#101C2F")
        self.text.tag_raise("comment")

    # ----- content -----
    def set_code(self, code: str) -> None:
        self._readonly = False
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", code or "")
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._draw_gutter()
        self._highlight()
        self.text.mark_set("insert", "1.0")
        self.text.see("1.0")
        self._update_cursor()

    def get_code(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self.text.configure(state="disabled" if readonly else "normal")
        self.gutter.configure(cursor="arrow")

    def focus_editor(self) -> None:
        if not self._readonly:
            self.text.focus_set()

    # ----- gutter / scrolling -----
    def _draw_gutter(self) -> None:
        content = self.text.get("1.0", "end-1c")
        count = max(1, content.count("\n") + 1)
        if count == self._line_count:
            return
        self._line_count = count
        width = len(str(count)) + 2
        if int(self.gutter.cget("width")) != width:
            self.gutter.configure(width=width)
        self.gutter.configure(state="normal")
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(i) for i in range(1, count + 1)))
        self.gutter.tag_configure("right", justify="right")
        self.gutter.tag_add("right", "1.0", "end")
        self.gutter.configure(state="disabled")
        self.gutter.yview_moveto(self.text.yview()[0])

    def _on_yscroll(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        try:
            self.gutter.yview_moveto(float(first))
        except (ValueError, tk.TclError):
            pass

    def _on_mousewheel(self, event) -> str:
        self.text.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.gutter.yview_moveto(self.text.yview()[0])
        return "break"

    # ----- events -----
    def _on_modified(self, _event=None) -> None:
        try:
            modified = self.text.edit_modified()
        except tk.TclError:
            return
        if not modified:
            return
        self.text.edit_modified(False)
        if self._readonly:
            return
        self._draw_gutter()
        if self._highlight_job:
            self.after_cancel(self._highlight_job)
        self._highlight_job = self.after(110, self._highlight)
        if self.on_change:
            self.on_change()

    def _update_cursor(self, _event=None) -> None:
        try:
            index = self.text.index("insert")
            line, col = index.split(".")
            if self.on_cursor:
                self.on_cursor(int(line), int(col) + 1)
        except Exception:
            pass

    def _tab(self, _event=None) -> str:
        if self._readonly:
            return "break"
        try:
            if self.text.tag_ranges("sel"):
                start = self.text.index("sel.first linestart")
                end = self.text.index("sel.last lineend")
                block = self.text.get(start, end)
                shifted = "\n".join("    " + line for line in block.split("\n"))
                self.text.delete(start, end)
                self.text.insert(start, shifted)
                self.text.tag_add("sel", start, f"{start}+{len(shifted)}c")
            else:
                self.text.insert("insert", "    ")
        except tk.TclError:
            pass
        return "break"

    def _shift_tab(self, _event=None) -> str:
        if self._readonly:
            return "break"
        try:
            line_start = self.text.index("insert linestart")
            line = self.text.get(line_start, f"{line_start} lineend")
            if line.startswith("    "):
                self.text.delete(line_start, f"{line_start}+4c")
            elif line.startswith("\t"):
                self.text.delete(line_start, f"{line_start}+1c")
        except tk.TclError:
            pass
        return "break"

    def _run_shortcut(self, _event=None) -> str:
        if self.on_run:
            self.on_run()
        return "break"

    def _save_shortcut(self, _event=None) -> str:
        if self.on_save:
            self.on_save()
        return "break"

    # ----- highlighting -----
    def _highlight(self) -> None:
        self._highlight_job = None
        if self._readonly:
            return
        content = self.text.get("1.0", "end-1c")
        for tag in ("keyword", "type", "number", "preproc", "string", "comment"):
            self.text.tag_remove(tag, "1.0", "end")
        if not content:
            return

        masked = list(content)
        string_ranges: list[tuple[int, int]] = []
        comment_ranges: list[tuple[int, int]] = []

        for match in self._COMMENT.finditer(content):
            comment_ranges.append(match.span())
        for match in self._STRING.finditer(content):
            start, end = match.span()
            if self._inside(start, comment_ranges):
                continue
            string_ranges.append((start, end))
            for i in range(start, end):
                masked[i] = " "
        for start, end in comment_ranges:
            for i in range(start, end):
                if masked[i] != "\n":
                    masked[i] = " "
        masked_text = "".join(masked)

        for pattern, tag in (
            (self._PREPROC, "preproc"),
            (self._KEYWORDS, "keyword"),
            (self._TYPES, "type"),
            (self._NUMBER, "number"),
        ):
            for match in pattern.finditer(masked_text):
                start, end = match.span()
                self.text.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
        for start, end in string_ranges:
            self.text.tag_add("string", f"1.0+{start}c", f"1.0+{end}c")
        for start, end in comment_ranges:
            self.text.tag_add("comment", f"1.0+{start}c", f"1.0+{end}c")

    @staticmethod
    def _inside(position: int, ranges: list[tuple[int, int]]) -> bool:
        return any(start <= position < end for start, end in ranges)


def run_async(
    root: tk.Misc,
    work: Callable[[], object],
    on_success: Callable[[object], None],
    on_error: Callable[[Exception], None],
    *,
    poll_ms: int = 70,
) -> None:
    """在后台线程执行，结果回到 Tk 主线程。"""
    results: queue.Queue = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            results.put(("ok", work()))
        except Exception as exc:  # noqa: BLE001
            results.put(("err", exc))

    threading.Thread(target=worker, daemon=True).start()

    def poll() -> None:
        try:
            kind, value = results.get_nowait()
        except queue.Empty:
            try:
                root.after(poll_ms, poll)
            except tk.TclError:
                pass
            return
        try:
            if kind == "ok":
                on_success(value)
            else:
                on_error(value)  # type: ignore[arg-type]
        except Exception:
            import traceback
            traceback.print_exc()

    try:
        root.after(poll_ms, poll)
    except tk.TclError:
        pass


def make_text(parent: tk.Misc, *, height: int = 5, mono: bool = False,
              wrap: str = "word", bg: str = Theme.PANEL_ALT) -> tk.Text:
    frame = tk.Frame(parent, bg=bg, highlightbackground=Theme.BORDER, highlightthickness=1)
    text = tk.Text(
        frame, height=height, wrap=wrap, bg=bg, fg=Theme.TEXT_SOFT,
        insertbackground=Theme.ACCENT, selectbackground=Theme.SELECT,
        selectforeground=Theme.TEXT, bd=0, highlightthickness=0,
        padx=10, pady=8, relief="flat",
        font=(MONO_FAMILY if mono else UI_FAMILY, 10 if mono else 10),
    )
    scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    # 将 Frame 和 Text 一起暴露，调用方 pack/grid frame。
    frame.text = text  # type: ignore[attr-defined]
    return frame  # type: ignore[return-value]
