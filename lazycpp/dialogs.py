"""应用中的对话框。"""

from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .ai import (
    AIConfig,
    AIError,
    ai_diagnose,
    ai_generate_code,
    ai_split_document,
    test_connection,
)
from .documents import clean_doc_text, looks_like_cpp, read_document, split_code_locally
from .imaging import save_screenshot
from .models import CodeItem
from .theme import (
    MONO_FAMILY,
    UI_FAMILY,
    Theme,
    center_window,
    copy_to_clipboard,
    set_dark_titlebar,
)
from .toolchain import Compiler, InteractiveProcess, PreparedProgram, prepare_interactive
from .widgets import run_async


class BaseDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, width: int, height: int) -> None:
        super().__init__(parent)
        self.parent = parent
        self.title(title)
        self.configure(bg=Theme.BG)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())
        self.header = tk.Frame(self, bg=Theme.BG)
        self.footer = tk.Frame(self, bg=Theme.BG)
        self.body = tk.Frame(self, bg=Theme.BG)
        # 先给底部按钮留位置，再让正文扩展，否则正文会把按钮挤出窗口。
        self.header.pack(fill="x", padx=24, pady=(20, 12))
        self.footer.pack(side="bottom", fill="x", padx=24, pady=(0, 18))
        self.body.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        scale = max(1.0, parent.winfo_fpixels("1i") / 96.0)
        width = min(int(width * scale), self.winfo_screenwidth() - 40)
        height = min(int(height * scale), self.winfo_screenheight() - 60)
        center_window(self, width, height)
        self.after(40, lambda: set_dark_titlebar(self))
        self.after(30, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def add_heading(self, title: str, subtitle: str = "") -> None:
        tk.Label(self.header, text=title, bg=Theme.BG, fg=Theme.TEXT,
                 font=(UI_FAMILY, 16, "bold"), anchor="w").pack(fill="x")
        if subtitle:
            tk.Label(self.header, text=subtitle, bg=Theme.BG, fg=Theme.MUTED,
                     font=(UI_FAMILY, 9), anchor="w", justify="left").pack(fill="x", pady=(4, 0))

    def add_footer_button(self, text: str, command, *, style: str = "Secondary.TButton",
                          side: str = "right", padx: tuple[int, int] = (8, 0)) -> ttk.Button:
        button = ttk.Button(self.footer, text=text, command=command, style=style,
                            cursor="hand2")
        button.pack(side=side, padx=padx)
        return button


class SettingsDialog(BaseDialog):
    def __init__(self, app) -> None:
        super().__init__(app.root, "AI 设置", 700, 640)
        self.app = app
        self.config = app.config
        self.add_heading("AI 设置（可选）",
                         "配置一个 OpenAI 兼容接口后，可诊断代码、按题目生成代码、从文档中拆题。\n"
                         "不配置也能正常使用批量运行和截图。")

        # ---- 接口 ----
        section = tk.Frame(self.body, bg=Theme.PANEL, highlightbackground=Theme.BORDER,
                           highlightthickness=1, padx=16, pady=14)
        section.pack(fill="x", pady=(0, 12))
        tk.Label(section, text="OpenAI 兼容接口", bg=Theme.PANEL, fg=Theme.TEXT,
                 font=(UI_FAMILY, 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(section, text="DeepSeek、OpenAI、通义千问等兼容 /chat/completions 的服务都可用。",
                 bg=Theme.PANEL, fg=Theme.MUTED, font=(UI_FAMILY, 8)).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))
        section.grid_columnconfigure(1, weight=1)
        self.base_var = tk.StringVar(value=self.config.api_base)
        self.model_var = tk.StringVar(value=self.config.api_model)
        self.key_var = tk.StringVar(value=self.config.api_key)
        self.show_key = tk.BooleanVar(value=False)

        self._field_label(section, "API 地址", 2)
        self.base_entry = ttk.Entry(section, textvariable=self.base_var)
        self.base_entry.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=4)

        self._field_label(section, "模型名", 3)
        ttk.Entry(section, textvariable=self.model_var).grid(
            row=3, column=1, sticky="ew", padx=(12, 0), pady=4)
        tk.Label(section, text="例如 deepseek-chat、gpt-4o-mini、qwen-plus",
                 bg=Theme.PANEL, fg=Theme.FAINT, font=(UI_FAMILY, 8)).grid(
            row=4, column=1, sticky="w", padx=(12, 0))

        self._field_label(section, "API Key", 5)
        key_frame = tk.Frame(section, bg=Theme.PANEL)
        key_frame.grid(row=5, column=1, sticky="ew", padx=(12, 0), pady=(6, 4))
        key_frame.grid_columnconfigure(0, weight=1)
        self.key_entry = ttk.Entry(key_frame, textvariable=self.key_var, show="•")
        self.key_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(key_frame, text="显示", variable=self.show_key, style="Dark.TCheckbutton",
                        command=self._toggle_key).grid(row=0, column=1, padx=(8, 0))
        tk.Label(section, text="Key 使用 Windows 当前用户加密后保存在本机，不会上传到别处。",
                 bg=Theme.PANEL, fg=Theme.FAINT, font=(UI_FAMILY, 8)).grid(
            row=6, column=1, sticky="w", padx=(12, 0))

        test_row = tk.Frame(section, bg=Theme.PANEL)
        test_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.test_button = ttk.Button(test_row, text="测试连接", style="Secondary.TButton",
                                      command=self._test)
        self.test_button.pack(side="left")
        self.test_status = tk.Label(test_row, text="", bg=Theme.PANEL, fg=Theme.MUTED,
                                    font=(UI_FAMILY, 9))
        self.test_status.pack(side="left", padx=12)

        # ---- 数据 ----
        data = tk.Frame(self.body, bg=Theme.BG)
        data.pack(fill="x", pady=(0, 4))
        tk.Label(data, text=f"配置目录：{self.app.store.base}", bg=Theme.BG, fg=Theme.FAINT,
                 font=(UI_FAMILY, 8), anchor="w").pack(fill="x")
        ttk.Button(data, text="打开配置文件夹", style="Link.TButton",
                   command=lambda: self.app.open_folder(self.app.store.base)).pack(
            anchor="w", pady=(3, 0))
        tk.Label(self.body, text=f"CppShot v{__version__} · 截图由真实运行结果生成，失败会立即中止并保留报错。",
                 bg=Theme.BG, fg=Theme.FAINT, font=(UI_FAMILY, 8),
                 justify="left", anchor="w").pack(fill="x", side="bottom")

        self.add_footer_button("保存设置", self._save, style="Primary.TButton")
        self.add_footer_button("取消", self.close, padx=(8, 0))

    def _field_label(self, parent: tk.Misc, text: str, row: int) -> None:
        tk.Label(parent, text=text, bg=Theme.PANEL, fg=Theme.TEXT_SOFT,
                 font=(UI_FAMILY, 9)).grid(row=row, column=0, sticky="w", pady=4)

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def _candidate_config(self) -> AIConfig:
        return AIConfig(
            base=self.base_var.get().strip(),
            key=self.key_var.get().strip(),
            model=self.model_var.get().strip(),
        )

    def _test(self) -> None:
        config = self._candidate_config()
        if not config.key:
            messagebox.showwarning("还缺 API Key", "请先填入 API Key，再测试连接。", parent=self)
            return
        if not config.base.startswith("http"):
            messagebox.showerror("地址格式不对", "API 地址应以 http:// 或 https:// 开头。", parent=self)
            return
        self.test_button.configure(state="disabled")
        self.test_status.configure(text="正在连接…", fg=Theme.ACCENT)
        run_async(
            self,
            lambda: test_connection(config),
            self._test_ok,
            self._test_error,
        )

    def _test_ok(self, reply: object) -> None:
        if not self.winfo_exists():
            return
        self.test_button.configure(state="normal")
        self.test_status.configure(text=f"连接成功：{str(reply)[:40]}", fg=Theme.GREEN)

    def _test_error(self, exc: Exception) -> None:
        if not self.winfo_exists():
            return
        self.test_button.configure(state="normal")
        self.test_status.configure(text="连接失败", fg=Theme.RED)
        messagebox.showerror("连接失败", str(exc), parent=self)

    def _save(self) -> None:
        base = self.base_var.get().strip()
        model = self.model_var.get().strip()
        if not base.startswith("http"):
            messagebox.showerror("地址格式不对", "API 地址应以 http:// 或 https:// 开头。", parent=self)
            return
        if not model:
            messagebox.showerror("缺少模型名", "请填写模型名，例如 deepseek-chat。", parent=self)
            return
        self.config.api_base = base
        self.config.api_model = model
        self.config.api_key = self.key_var.get().strip()
        self.app.store.save_config(self.config)
        self.app.on_config_changed()
        self.app.toast("AI 设置已保存")
        self.close()


class AIGenerateDialog(BaseDialog):
    def __init__(self, app) -> None:
        super().__init__(app.root, "AI 生成 / 文档拆题", 790, 720)
        self.app = app
        self.source_name = ""
        self.add_heading("AI 生成 / 文档拆题",
                         "把作业题目粘贴进来，让 AI 写一份完整代码；或载入含有多段代码的 Word/TXT，"
                         "让 AI 依次拆开并加入队列。")
        self.mode = tk.StringVar(value="generate")
        self.api = app.ai_config()
        if self.api.configured:
            api_text = f"当前模型：{self.api.model}"
            api_color = Theme.GREEN
        else:
            api_text = "未配置 API Key：可先用「本地拆分」，生成代码需要先配置 Key"
            api_color = Theme.YELLOW
        self.api_label = tk.Label(self.body, text=api_text, bg=Theme.BG, fg=api_color,
                                  font=(UI_FAMILY, 9), anchor="w")
        self.api_label.pack(fill="x", pady=(0, 10))

        mode_row = tk.Frame(self.body, bg=Theme.BG)
        mode_row.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(mode_row, text="按需求生成一份代码", variable=self.mode,
                        value="generate", command=self._mode_changed).pack(side="left")
        ttk.Radiobutton(mode_row, text="把文档拆成多份代码", variable=self.mode,
                        value="split", command=self._mode_changed).pack(side="left", padx=(18, 0))

        self.hint = tk.Label(self.body, text="", bg=Theme.BG, fg=Theme.MUTED,
                             font=(UI_FAMILY, 9), anchor="w", justify="left")
        self.hint.pack(fill="x", pady=(0, 8))

        text_wrap = tk.Frame(self.body, bg=Theme.PANEL_ALT, highlightbackground=Theme.BORDER,
                             highlightthickness=1)
        text_wrap.pack(fill="both", expand=True)
        self.text = tk.Text(
            text_wrap, bg=Theme.PANEL_ALT, fg=Theme.TEXT_SOFT, insertbackground=Theme.ACCENT,
            selectbackground=Theme.SELECT, selectforeground=Theme.TEXT,
            font=(UI_FAMILY, 10), bd=0, highlightthickness=0, padx=12, pady=10,
            wrap="word", undo=True,
        )
        scroll = ttk.Scrollbar(text_wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        load_row = tk.Frame(self.body, bg=Theme.BG)
        load_row.pack(fill="x", pady=(10, 2))
        ttk.Button(load_row, text="载入 Word / TXT", style="Secondary.TButton",
                   command=self._load_file).pack(side="left")
        self.file_label = tk.Label(load_row, text="未载入文件", bg=Theme.BG, fg=Theme.FAINT,
                                   font=(UI_FAMILY, 8))
        self.file_label.pack(side="left", padx=10)

        self.progress = ttk.Progressbar(self.body, style="Shot.Horizontal.TProgressbar",
                                        mode="indeterminate")
        self.status = tk.Label(self.body, text="", bg=Theme.BG, fg=Theme.MUTED,
                               font=(UI_FAMILY, 9), anchor="w")
        self.status.pack(fill="x")

        self.start_button = self.add_footer_button("开始生成", self._start, style="Primary.TButton")
        self.cancel_button = self.add_footer_button("取消", self.close, padx=(8, 0))
        self._mode_changed()

    def _mode_changed(self) -> None:
        if self.mode.get() == "split":
            self.hint.configure(text="把含有多段代码的文档载入后，点开始。配置了 API 时由 AI 智能拆分；"
                                    "未配置或 AI 失败时自动改用本地代码块识别。")
            self.start_button.configure(text="拆分并加入队列")
        else:
            self.hint.configure(text="粘贴作业要求或载入 Word/TXT 后，点开始。AI 会生成一份完整 C++ 代码，"
                                    "并作为一个新条目加入左侧队列。")
            self.start_button.configure(text="生成代码")

    def _load_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="载入题目或代码文档",
            filetypes=[("Word / 文本", "*.docx *.txt *.md"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            content = clean_doc_text(read_document(path))
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)
            return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.source_name = Path(path).stem
        self.file_label.configure(text=Path(path).name, fg=Theme.TEXT_SOFT)
        blocks = split_code_locally(content)
        if len(blocks) >= 2:
            self.status.configure(text=f"文档中识别到 {len(blocks)} 段代码，建议使用「拆分」模式。",
                                  fg=Theme.YELLOW)
        else:
            self.status.configure(text=f"已载入 {len(content)} 个字符。", fg=Theme.MUTED)

    def _start(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("还没有内容", "请先粘贴题目/文档内容，或载入 Word/TXT。", parent=self)
            return
        self.api = self.app.ai_config()
        mode = self.mode.get()
        if mode == "generate" and not self.api.configured:
            messagebox.showwarning("需要配置 API", "生成代码需要先配置 API Key。\n可以在「AI 设置」里填写。",
                                   parent=self)
            self.app.open_settings()
            return
        self._set_busy(True)

        def work():
            if mode == "split":
                warning = ""
                if self.api.configured:
                    try:
                        return ai_split_document(self.api, text, self.source_name), warning
                    except AIError as exc:
                        warning = str(exc)
                local = split_code_locally(text)
                if not local:
                    if warning:
                        raise AIError(f"AI 拆分失败，本地也没有识别到代码。\n\n{warning}")
                    raise AIError("没有识别到完整的 C++ 代码。请确认文档里包含 #include 和 main 函数。")
                return local, warning
            code = ai_generate_code(self.api, text)
            return code, ""

        run_async(self, work, lambda result: self._done(result, mode), self._error)

    def _done(self, result, mode: str) -> None:
        if not self.winfo_exists():
            return
        payload, warning = result  # type: ignore[misc]
        if mode == "split":
            blocks = payload  # type: ignore[assignment]
            self.app.import_ai_blocks(blocks, source_name=self.source_name or "AI拆题")
            added = len(blocks)
            self.app.toast(f"已拆分并加入 {added} 段代码")
            if warning:
                messagebox.showwarning(
                    "已使用本地拆分",
                    f"AI 拆分没有成功，已自动改用本地识别。\n\n原因：{warning[:800]}",
                    parent=self,
                )
        else:
            code = str(payload)
            self.app.add_generated_code(code, title=self.source_name or "AI生成代码")
            self.app.toast("AI 代码已加入左侧队列")
        self.close()

    def _error(self, exc: Exception) -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False)
        if isinstance(exc, AIError):
            messagebox.showerror("AI 生成失败", str(exc), parent=self)
        else:
            messagebox.showerror("处理失败", f"{type(exc).__name__}: {exc}", parent=self)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.start_button.configure(state=state)
        self.cancel_button.configure(state=state)
        if busy:
            self.progress.pack(fill="x", pady=(10, 4), before=self.status)
            self.progress.start(12)
            self.status.configure(text="正在处理，请稍候…", fg=Theme.ACCENT)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.status.configure(text="", fg=Theme.MUTED)


class AIResultDialog(BaseDialog):
    def __init__(self, app, title: str, content: str, *, replace_callback=None) -> None:
        super().__init__(app.root, title, 880, 720)
        self.app = app
        self.replace_callback = replace_callback
        self.add_heading(title, "AI 的建议仅供参考，提交前请自己检查一遍。")
        wrap = tk.Frame(self.body, bg=Theme.PANEL_ALT, highlightbackground=Theme.BORDER,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        self.text = tk.Text(
            wrap, bg=Theme.PANEL_ALT, fg=Theme.TEXT_SOFT, insertbackground=Theme.ACCENT,
            selectbackground=Theme.SELECT, selectforeground=Theme.TEXT,
            font=(UI_FAMILY, 10), bd=0, highlightthickness=0, padx=14, pady=12,
            wrap="word", spacing1=2, spacing3=3,
        )
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.insert("1.0", content)
        self.text.configure(state="disabled")
        self.code = self._extract_code(content)

        self.add_footer_button("关闭", self.close, padx=(8, 0))
        self.add_footer_button("复制全文", lambda: self._copy(content))
        if self.code and replace_callback:
            self.add_footer_button("替换当前代码", self._replace, style="Primary.TButton")

    @staticmethod
    def _extract_code(content: str) -> str:
        matches = re.findall(r"```(?:cpp|c\+\+|cc|cxx)?[ \t]*\n(.*?)```", content,
                             flags=re.IGNORECASE | re.DOTALL)
        if matches:
            return matches[-1].strip()
        if looks_like_cpp(content) and "#include" in content:
            return content[content.find("#include"):].strip()
        return ""

    def _copy(self, text: str) -> None:
        copy_to_clipboard(self, text)
        self.app.toast("已复制到剪贴板")

    def _replace(self) -> None:
        if not self.code or not self.replace_callback:
            return
        ok = messagebox.askyesno(
            "替换当前代码",
            "将用 AI 返回的最后一段代码替换当前编辑器内容。\n原来的代码会被覆盖，确定继续吗？",
            parent=self,
        )
        if not ok:
            return
        self.replace_callback(self.code)
        self.close()


class InteractiveRunDialog(BaseDialog):
    """编译后在应用内实时运行，允许用户按行输入 stdin。"""

    def __init__(self, app, item: CodeItem, compiler: Compiler) -> None:
        super().__init__(app.root, "交互运行", 980, 720)
        self.app = app
        self.item = item
        self.compiler = compiler
        self.process: InteractiveProcess | None = None
        self.prepared: PreparedProgram | None = None
        self._transcript = ""
        self._finished = False
        self._closed = False
        self._auto_saved = False
        self._exit_code: int | None = None
        self._poll_job: str | None = None
        self._stopped = False
        self._notified_closed = False
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.bind("<Escape>", lambda _e: self._request_close())

        self.add_heading(
            "交互运行 · 实时输入",
            "程序运行时会实时显示输出；在下方输入框按 Enter，把一行数据发送给 cin。\n"
            "程序正常结束后会自动保存一张包含输入和输出的截图。",
        )
        tk.Label(
            self.body,
            text=f"当前程序：{item.name or '未命名'}    ·    编译器：{compiler.short_label}",
            bg=Theme.BG, fg=Theme.MUTED, font=(UI_FAMILY, 9), anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(1, weight=1)

        console_frame = tk.Frame(self.body, bg=Theme.EDITOR_BG,
                                 highlightbackground=Theme.BORDER, highlightthickness=1)
        console_frame.grid(row=1, column=0, sticky="nsew")
        console_frame.grid_rowconfigure(0, weight=1)
        console_frame.grid_columnconfigure(0, weight=1)
        self.console = tk.Text(
            console_frame, bg=Theme.EDITOR_BG, fg="#D6E2F0",
            insertbackground=Theme.ACCENT, selectbackground=Theme.SELECT,
            selectforeground=Theme.TEXT, font=(MONO_FAMILY, 10), bd=0,
            highlightthickness=0, padx=14, pady=12, wrap="word",
            state="disabled",
        )
        console_scroll = ttk.Scrollbar(console_frame, orient="vertical",
                                       command=self.console.yview)
        self.console.configure(yscrollcommand=console_scroll.set)
        self.console.grid(row=0, column=0, sticky="nsew")
        console_scroll.grid(row=0, column=1, sticky="ns")
        self.console.tag_configure("info", foreground=Theme.FAINT)
        self.console.tag_configure("error", foreground="#FCA5A5")
        self.console.tag_configure("ok", foreground=Theme.GREEN)
        self.console.tag_configure("input", foreground=Theme.ACCENT)
        self._append_ui("[CppShot] 正在准备编译器，请稍候…\n", "info")

        input_row = tk.Frame(self.body, bg=Theme.BG)
        input_row.grid(row=2, column=0, sticky="ew", pady=(10, 5))
        input_row.grid_columnconfigure(1, weight=1)
        self.prompt_label = tk.Label(input_row, text="›", bg=Theme.PANEL_ALT,
                                     fg=Theme.GREEN, font=(MONO_FAMILY, 14, "bold"),
                                     padx=10, pady=6)
        self.prompt_label.grid(row=0, column=0, sticky="ns")
        self.input_var = tk.StringVar()
        self.input_entry = tk.Entry(
            input_row, textvariable=self.input_var, bg=Theme.PANEL_ALT, fg=Theme.TEXT,
            insertbackground=Theme.ACCENT, disabledbackground=Theme.PANEL_ALT,
            disabledforeground=Theme.FAINT, relief="flat", highlightthickness=1,
            highlightbackground=Theme.BORDER, highlightcolor=Theme.ACCENT,
            font=(MONO_FAMILY, 12), bd=0, state="disabled",
        )
        self.input_entry.grid(row=0, column=1, sticky="ew", ipady=7)
        self.send_button = ttk.Button(input_row, text="发送", style="Primary.TButton",
                                      command=self._send_line, state="disabled")
        self.send_button.grid(row=0, column=2, padx=(8, 0))
        self.input_entry.bind("<Return>", self._send_line)

        self.status_label = tk.Label(self.body, text="正在编译…", bg=Theme.BG,
                                     fg=Theme.ACCENT, font=(UI_FAMILY, 9), anchor="w")
        self.status_label.grid(row=3, column=0, sticky="ew")

        self.stop_button = self.add_footer_button(
            "停止运行", self._stop, style="Danger.TButton", side="left", padx=(0, 0)
        )
        self.stop_button.state(["disabled"])
        self.close_button = self.add_footer_button("关闭", self._request_close,
                                                   side="right", padx=(8, 0))
        self.save_button = self.add_footer_button("保存截图", self._manual_save,
                                                  side="right")
        self.save_button.state(["disabled"])

        run_async(
            self,
            lambda: prepare_interactive(self.item, self.compiler),
            self._compiled,
            self._compile_exception,
        )

    # ------------------------------------------------------------------ UI helpers
    def _append_ui(self, text: str, tag: str | None = None) -> None:
        if self._closed:
            return
        try:
            self.console.configure(state="normal")
            self.console.insert("end", text, tag or ())
            self.console.configure(state="disabled")
            self.console.see("end")
        except tk.TclError:
            pass

    def _append_program(self, text: str) -> None:
        if not text:
            return
        self._transcript += text
        self._append_ui(text)

    def _set_status(self, text: str, color: str = Theme.MUTED) -> None:
        try:
            self.status_label.configure(text=text, fg=color)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ compile
    def _compiled(self, prepared: PreparedProgram) -> None:
        if self._closed or not self.winfo_exists():
            prepared.cleanup()
            return
        self.prepared = prepared
        if not prepared.ok:
            self._finished = True
            self._exit_code = prepared.exit_code
            self._append_ui("\n[CppShot] 编译失败。\n", "error")
            detail = prepared.output.strip() or prepared.error.strip()
            if detail:
                self._append_ui(detail + "\n", "error")
            self._set_status("编译失败，没有生成截图。", Theme.RED)
            self.app.on_interactive_result(
                self.item.id, "failed", prepared.exit_code, self._transcript, "", prepared.error
            )
            return
        self._append_ui("[CppShot] 编译成功，程序开始运行。\n", "ok")
        try:
            self.process = InteractiveProcess(prepared)
        except Exception as exc:
            self._finished = True
            self._append_ui(f"[CppShot] 无法启动程序：{exc}\n", "error")
            self._set_status("启动失败，没有生成截图。", Theme.RED)
            self.app.on_interactive_result(
                self.item.id, "failed", None, self._transcript, "",
                f"无法启动程序：{type(exc).__name__}: {exc}",
            )
            prepared.cleanup()
            self.prepared = None
            return
        self.input_entry.configure(state="normal")
        self.send_button.configure(state="normal")
        self.stop_button.state(["!disabled"])
        self._set_status("运行中 · 输入一行后按 Enter 发送", Theme.GREEN)
        self.input_entry.focus_set()
        self._poll()

    def _compile_exception(self, exc: Exception) -> None:
        if self._closed and not self.winfo_exists():
            return
        self._finished = True
        self._append_ui(f"\n[CppShot] 编译环境异常：{type(exc).__name__}: {exc}\n", "error")
        self._set_status("编译环境异常，没有生成截图。", Theme.RED)
        self.app.on_interactive_result(
            self.item.id, "failed", None, self._transcript, "",
            f"{type(exc).__name__}: {exc}",
        )

    # ------------------------------------------------------------------ live loop
    def _poll(self) -> None:
        self._poll_job = None
        if self._closed or not self.winfo_exists():
            return
        exit_code: int | None = None
        if self.process is not None:
            while True:
                try:
                    kind, value = self.process.events.get_nowait()
                except Exception:
                    break
                if kind == "output":
                    self._append_program(str(value))
                elif kind == "exit":
                    exit_code = int(value) if value is not None else None
                    break
        if exit_code is not None:
            self._on_exit(exit_code)
            return
        if self.process is not None and not self._finished:
            self._poll_job = self.after(60, self._poll)

    def _send_line(self, _event=None) -> str:
        if self.process is None or not self.process.running:
            return "break"
        text = self.input_var.get()
        self._transcript += text + "\n"
        self._append_ui(text + "\n", "input")
        self.input_var.set("")
        self.process.write_line(text)
        self.input_entry.focus_set()
        return "break"

    def _on_exit(self, exit_code: int) -> None:
        if self._finished:
            return
        self._finished = True
        self._exit_code = exit_code
        try:
            self.input_entry.configure(state="disabled")
            self.send_button.configure(state="disabled")
            self.stop_button.state(["disabled"])
        except tk.TclError:
            pass
        if exit_code == 0:
            self._append_ui("\n[CppShot] 程序正常结束，退出代码 0。\n", "ok")
            self._set_status("运行成功，正在保存截图…", Theme.GREEN)
            self._save(auto=True)
        else:
            self._append_ui(f"\n[CppShot] 程序结束，退出代码 {exit_code}，未自动生成截图。\n",
                            "error")
            self._set_status(f"运行结束，退出代码 {exit_code}；失败结果不截图。", Theme.RED)
            self.app.on_interactive_result(
                self.item.id, "failed", exit_code, self._transcript, "",
                f"程序退出代码 {exit_code}",
            )

    def _save(self, auto: bool = False) -> str | None:
        if not self._transcript.strip():
            self._set_status("没有程序输出，无法截图。", Theme.RED)
            return None
        try:
            path = save_screenshot(
                self.item, self._transcript, self.app.config.save_dir,
                self.compiler.label,
            )
        except Exception as exc:
            self._set_status(f"截图保存失败：{exc}", Theme.RED)
            self.app.on_interactive_result(
                self.item.id, "failed", self._exit_code, self._transcript, "",
                f"截图保存失败：{type(exc).__name__}: {exc}",
            )
            return None
        self._auto_saved = True
        self.save_button.state(["!disabled"])
        self._append_ui(f"\n[CppShot] 截图已保存：{path}\n", "ok")
        self._set_status(f"运行成功 · 截图：{path.name}", Theme.GREEN)
        self.app.on_interactive_result(
            self.item.id, "success", self._exit_code, self._transcript, str(path), ""
        )
        return str(path)

    def _manual_save(self) -> None:
        if self._finished and self._exit_code == 0:
            self._save(auto=False)

    def _stop(self) -> None:
        if self.process is None or not self.process.running:
            return
        self._stopped = True
        self.stop_button.state(["disabled"])
        self.input_entry.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self._set_status("正在停止程序…", Theme.YELLOW)
        self.process.stop()

    def _request_close(self) -> None:
        if self._closed:
            return
        if self.process is not None and self.process.running:
            if not messagebox.askyesno(
                "程序仍在运行",
                "程序还没有结束。停止它并关闭交互窗口吗？\n本次不会自动生成截图。",
                parent=self,
            ):
                return
        self._closed = True
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        if self.process is not None:
            self.process.cleanup()
            self.process = None
        elif self.prepared is not None:
            self.prepared.cleanup()
            self.prepared = None
        if not self._notified_closed:
            self._notified_closed = True
            self.app.on_interactive_closed()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class FailureDialog(BaseDialog):
    def __init__(self, app, item_name: str, outcome, *, position: int = 0,
                 total: int = 0, screenshot_dir: str = "") -> None:
        super().__init__(app.root, "运行失败", 840, 640)
        self.app = app
        self.item_name = item_name
        self.outcome = outcome
        self.screenshot_dir = screenshot_dir
        stage_text = {
            "compile": "编译失败",
            "run": "运行失败",
            "timeout": "运行超时",
            "prepare": "编译环境/准备失败",
            "screenshot": "截图保存失败",
        }.get(outcome.stage, outcome.stage)

        tk.Label(self.header, text="✕  已中止截图", bg=Theme.BG, fg=Theme.RED,
                 font=(UI_FAMILY, 18, "bold"), anchor="w").pack(fill="x")
        tk.Label(self.header, text=f"「{item_name}」{stage_text}。为避免提交错误结果，后续代码没有继续运行。",
                 bg=Theme.BG, fg=Theme.TEXT_SOFT, font=(UI_FAMILY, 9),
                 anchor="w").pack(fill="x", pady=(4, 0))
        if position and total:
            progress_text = f"批量进度：第 {position}/{total} 个"
        else:
            progress_text = "单次运行"
        meta = (
            f"{progress_text}    ·    阶段：{stage_text}    ·    "
            f"退出代码：{outcome.exit_code_display}    ·    编译器：{outcome.compiler or '未知'}"
        )
        tk.Label(self.body, text=meta, bg=Theme.BG, fg=Theme.MUTED,
                 font=(UI_FAMILY, 9), anchor="w").pack(fill="x", pady=(0, 8))

        wrap = tk.Frame(self.body, bg=Theme.PANEL_ALT, highlightbackground=Theme.BORDER,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        self.text = tk.Text(
            wrap, bg=Theme.PANEL_ALT, fg="#FCA5A5", insertbackground=Theme.ACCENT,
            selectbackground=Theme.SELECT, selectforeground=Theme.TEXT,
            font=(MONO_FAMILY, 10), bd=0, highlightthickness=0,
            padx=12, pady=10, wrap="word",
        )
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        body = self._report_text()
        self.text.insert("1.0", body)
        self.text.configure(state="disabled")

        self.add_footer_button("关闭", self.close, padx=(8, 0))
        if self.screenshot_dir:
            self.add_footer_button("打开截图文件夹",
                                   lambda: self.app.open_folder(self.screenshot_dir))
        self.add_footer_button("复制报错", self._copy, style="Primary.TButton")

    def _report_text(self) -> str:
        outcome = self.outcome
        reason = {
            "compile": "编译器返回了非 0 退出码，代码没有通过编译，因此没有截图。",
            "run": "程序运行结束但退出码不是 0，因此没有截图。",
            "timeout": "程序运行超过时限，已被强制结束，因此没有截图。",
            "prepare": "编译环境或临时文件准备失败，因此没有截图。",
            "screenshot": "程序运行成功，但截图文件保存失败。",
        }.get(outcome.stage, "运行没有成功，因此没有截图。")
        parts = [
            reason,
            "",
            f"退出代码：{outcome.exit_code_display}",
            f"耗时：{outcome.elapsed:.2f} 秒",
            f"编译器：{outcome.compiler or '未知'}",
            "",
        ]
        if outcome.output.strip():
            parts.extend(["===== 编译器 / 程序输出 =====", outcome.output.rstrip(), ""])
        if outcome.error.strip() and outcome.error.strip() not in outcome.output:
            parts.extend(["===== 错误信息 =====", outcome.error.rstrip(), ""])
        parts.append("提示：先修正报错，再运行批量截图。报错行号通常写在文件名后面的括号里。")
        return "\n".join(parts)

    def _copy(self) -> None:
        copy_to_clipboard(self, self._report_text())
        self.app.toast("报错已复制到剪贴板")
