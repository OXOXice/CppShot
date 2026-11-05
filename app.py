"""CppShot 启动入口。"""

from __future__ import annotations

import ctypes
import sys
import traceback
from pathlib import Path


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CppShot.LazyCppScreenshot.1")
    except Exception:
        pass


def _hide_own_console() -> None:
    """打包成控制台程序时，独立双击启动后隐藏黑框；从已有终端启动则保留。"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        buffer = (ctypes.c_ulong * 2)()
        count = kernel32.GetConsoleProcessList(buffer, 2)
        if count <= 1:
            hwnd = kernel32.GetConsoleWindow()
            if hwnd:
                user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def _write_crash_log(exc_type, exc_value, exc_tb) -> str:
    from lazycpp.config import app_data_dir
    try:
        log_dir = app_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "crash.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n===== CppShot crash =====\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
        return str(path)
    except Exception:
        return ""


def self_test() -> int:
    """无界面的核心自检：编译、运行、截图各一次。"""
    import tempfile
    from lazycpp.imaging import save_screenshot
    from lazycpp.models import CodeItem
    from lazycpp.toolchain import compile_and_run, detect_compilers, select_compiler

    compilers = detect_compilers()
    print(f"检测到 {len(compilers)} 个编译器")
    for compiler in compilers:
        print(" -", compiler.label)
    compiler = select_compiler(compilers, "auto")
    if not compiler:
        print("FAIL: 未找到编译器")
        return 2
    item = CodeItem(
        name="CppShot自检",
        code='#include <iostream>\nint main(){ std::cout << "CppShot OK" << std::endl; return 0; }',
    )
    outcome = compile_and_run(item, compiler, 10)
    print("编译运行:", "OK" if outcome.ok else "FAIL", outcome.stage, outcome.exit_code_display)
    if outcome.output:
        print("输出:", outcome.output.strip())
    if not outcome.ok:
        print(outcome.error)
        return 3
    out_dir = Path(tempfile.gettempdir()) / "CppShotSelfTest"
    path = save_screenshot(item, outcome.output, out_dir, compiler.label)
    print("截图:", path, path.stat().st_size, "bytes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return self_test()
    if "--version" in sys.argv:
        from lazycpp import __version__
        print(__version__)
        return 0
    _enable_dpi_awareness()
    _hide_own_console()
    import tkinter as tk
    from tkinter import messagebox

    from lazycpp.ui import CppShotApp

    root = tk.Tk()

    def report_callback_exception(exc_type, exc_value, exc_tb) -> None:
        path = _write_crash_log(exc_type, exc_value, exc_tb)
        try:
            messagebox.showerror(
                "程序遇到错误",
                f"{exc_type.__name__}: {exc_value}\n\n"
                + (f"错误日志：{path}" if path else "错误日志写入失败。"),
                parent=root,
            )
        except Exception:
            pass

    root.report_callback_exception = report_callback_exception
    try:
        CppShotApp(root)
        root.mainloop()
    except Exception:
        path = _write_crash_log(*sys.exc_info())
        try:
            messagebox.showerror(
                "启动失败",
                f"{sys.exc_info()[1]}\n\n" + (f"错误日志：{path}" if path else ""),
                parent=root,
            )
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
