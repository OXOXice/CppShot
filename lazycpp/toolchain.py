"""编译器发现、C++ 编译与运行。"""

from __future__ import annotations

import codecs
import glob
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .models import CodeItem, safe_filename


CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_ENV_CACHE: dict[str, dict[str, str]] = {}


@dataclass(frozen=True)
class Compiler:
    id: str
    label: str
    kind: str  # msvc | mingw
    version: str
    vcvars: str | None = None
    executable: str | None = None
    utf8_flag: bool = True
    cxx17_flag: bool = True
    arch: str = "x64"
    note: str = ""

    @property
    def short_label(self) -> str:
        if self.kind == "msvc":
            return f"VS {self.version} MSVC"
        return f"g++ {self.version}".strip()


@dataclass
class RunOutcome:
    ok: bool
    stage: str
    exit_code: int | None
    output: str
    error: str = ""
    elapsed: float = 0.0
    compiler: str = ""
    build_dir: str = ""
    command: str = ""

    @property
    def exit_code_display(self) -> str:
        if self.exit_code is None:
            return "无"
        code = self.exit_code
        if code < 0:
            return f"{code} (0x{code & 0xFFFFFFFF:08X})"
        return f"{code} (0x{code:08X})"


def _decode(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _run_hidden(args: list[str] | str, *, timeout: float = 30, env: dict[str, str] | None = None,
                cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        env=env,
        cwd=cwd,
        creationflags=CREATE_NO_WINDOW,
    )


def _vcvars_env(vcvars: str, arch: str = "x64") -> dict[str, str]:
    key = f"{vcvars}|{arch}"
    if key in _ENV_CACHE:
        return _ENV_CACHE[key].copy()
    if "vcvarsall" in vcvars.lower():
        inner = f'call "{vcvars}" {arch} >nul 2>&1 && set'
    else:
        inner = f'call "{vcvars}" >nul 2>&1 && set'
    # cmd.exe 的引号解析与普通程序不同；把整条命令作为字符串交给
    # CreateProcess，才能正确保留带空格的 vcvars 路径两端的引号。
    command = f'cmd.exe /d /s /c "{inner}"'
    result = _run_hidden(
        command,
        timeout=60,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError("初始化 Visual Studio 编译环境失败")
    text = _decode(result.stdout)
    env: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name:
            env[name] = value
    if not env or not any(k.upper() == "PATH" for k in env):
        raise RuntimeError("无法读取 Visual Studio 环境变量")
    # 环境变量名在 Windows 上不区分大小写；统一成 PATH 便于后续使用。
    normalized = {k.upper(): v for k, v in env.items()}
    for k, v in env.items():
        normalized.setdefault(k, v)
    normalized["PATH"] = next(v for k, v in env.items() if k.upper() == "PATH")
    normalized["VSLANG"] = "1033"
    _ENV_CACHE[key] = normalized
    return normalized.copy()


def _find_vswhere() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _vs_installations() -> list[dict]:
    vswhere = _find_vswhere()
    found: list[dict] = []
    if vswhere:
        try:
            result = _run_hidden(
                [str(vswhere), "-all", "-products", "*", "-format", "json", "-utf8"],
                timeout=20,
            )
            if result.returncode == 0:
                found = json.loads(_decode(result.stdout) or "[]")
        except Exception:
            found = []
    # vswhere 不可用时的后备搜索。
    fallback_roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft Visual Studio",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio",
    ]
    for root in fallback_roots:
        if not root.is_dir():
            continue
        for version_dir in root.iterdir():
            if not version_dir.is_dir():
                continue
            for edition in version_dir.iterdir():
                if not edition.is_dir():
                    continue
                if not any(str(Path(f.get("installationPath", "")).resolve()).lower() == str(edition.resolve()).lower()
                           for f in found):
                    found.append({"installationPath": str(edition), "installationVersion": "0"})
    return found


def _vs_label(version: str) -> str:
    major = 0
    try:
        major = int(str(version).split(".")[0])
    except Exception:
        pass
    mapping = {10: "2010", 11: "2012", 12: "2013", 14: "2015", 15: "2017", 16: "2019", 17: "2022", 18: "2026"}
    return mapping.get(major, str(version).split(".")[0] or "MSVC")


def _find_vs_compilers() -> list[Compiler]:
    compilers: list[Compiler] = []
    seen_major: set[int] = set()
    installs: list[tuple[int, dict]] = []
    seen_paths: set[str] = set()
    for info in _vs_installations():
        path = info.get("installationPath")
        if not path:
            continue
        key = str(Path(path)).lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        version_text = str(info.get("installationVersion", "0"))
        try:
            major = int(version_text.split(".")[0])
        except Exception:
            major = 0
        installs.append((major, info))
    # 同一大版本有多套安装时，优先选择完整、可启动的 Community；
    # 残缺的 BuildTools 可能连 vcvars64.bat 都无法初始化。
    def score(pair: tuple[int, dict]) -> tuple:
        major, info = pair
        path = str(info.get("installationPath", ""))
        return (
            major,
            bool(info.get("isComplete")),
            bool(info.get("isLaunchable")),
            "community" in path.lower(),
            str(info.get("installationVersion", "")),
        )
    installs.sort(key=score, reverse=True)
    for major, info in installs:
        if major in seen_major:
            continue
        install = Path(str(info.get("installationPath")))
        vcvars64 = install / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
        vcvarsall = install / "VC" / "vcvarsall.bat"
        vcvars = vcvars64 if vcvars64.exists() else vcvarsall
        if not vcvars.exists():
            continue
        version = _vs_label(str(info.get("installationVersion", major)))
        # 确认 VC 工具目录存在，避免只有 IDE 没装 C++ 工具链。
        tool_pattern = str(install / "VC" / "Tools" / "MSVC" / "*" / "bin" / "Hostx64" / "x64" / "cl.exe")
        if not glob.glob(tool_pattern):
            # VS2010 的目录结构不同
            if not (install / "VC" / "bin" / "cl.exe").exists():
                continue
        arch = "x64" if vcvars64.exists() else "x86"
        compilers.append(Compiler(
            id=f"vs{version}",
            label=f"Visual Studio {version}（MSVC {arch}）",
            kind="msvc",
            version=version,
            vcvars=str(vcvars),
            utf8_flag=major >= 14,
            cxx17_flag=major >= 14,
            arch=arch,
            note="老师同款 VS" if version == "2010" else "",
        ))
        seen_major.add(major)
    # VS2010 可能在 Program Files (x86)，且不被 vswhere 收录。
    vs2010 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio 10.0"
    if 10 not in seen_major and (vs2010 / "VC" / "vcvarsall.bat").exists():
        compilers.append(Compiler(
            id="vs2010",
            label="Visual Studio 2010（MSVC x86）",
            kind="msvc",
            version="2010",
            vcvars=str(vs2010 / "VC" / "vcvarsall.bat"),
            utf8_flag=False,
            cxx17_flag=False,
            arch="x86",
            note="老师同款 VS",
        ))
    return compilers


def _find_mingw() -> list[Compiler]:
    found: list[Compiler] = []
    candidates: list[str] = []
    for name in ("g++.exe", "g++"):
        path = shutil.which(name)
        if path:
            candidates.append(path)
    if os.environ.get("CXX"):
        candidates.insert(0, os.environ["CXX"].strip('"'))
    patterns = [
        r"C:\mingw64\bin\g++.exe",
        r"C:\mingw32\bin\g++.exe",
        r"C:\msys64\mingw64\bin\g++.exe",
        r"C:\msys64\ucrt64\bin\g++.exe",
        r"C:\ProgramData\chocolatey\bin\g++.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "mingw64" / "bin" / "g++.exe"),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        candidates.extend(matches)
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        version = ""
        try:
            res = _run_hidden([str(path), "--version"], timeout=10)
            first = _decode(res.stdout).splitlines()[0] if res.stdout else ""
            match = re.search(r"(\d+(?:\.\d+)+)", first)
            version = match.group(1) if match else ""
        except Exception:
            pass
        found.append(Compiler(
            id=f"mingw-{path.parent}",
            label=f"MinGW g++ {version}（{path.parent.parent.name if path.parent.parent else ''}）".strip(),
            kind="mingw",
            version=version or "?",
            executable=str(path),
        ))
    return found


def detect_compilers() -> list[Compiler]:
    """快速发现可用编译器（不加载 vcvars，速度较快）。"""
    compilers: list[Compiler] = []
    if sys.platform == "win32":
        compilers.extend(_find_vs_compilers())
        compilers.extend(_find_mingw())
    else:
        candidates: list[tuple[str, str]] = []
        for name in ("g++", "clang++", "c++"):
            path = shutil.which(name)
            if path:
                candidates.append((name, path))
        for name, gpp in candidates:
            version = ""
            try:
                res = _run_hidden([gpp, "--version"], timeout=10)
                first = _decode(res.stdout).splitlines()[0] if res.stdout else ""
                match = re.search(r"(\d+(?:\.\d+)+)", first)
                version = match.group(1) if match else ""
            except Exception:
                pass
            compilers.append(Compiler(
                id=f"{name}-{Path(gpp).parent}",
                label=f"{name} {version}（{sys.platform}）",
                kind="gnu",
                version=version or "?",
                executable=gpp,
            ))
    return compilers


def compiler_infos(compilers: list[Compiler]) -> list[tuple[str, str]]:
    infos = [("auto", "自动检测" if not compilers else f"自动检测（{compilers[0].short_label}）")]
    infos.extend((c.id, c.label) for c in compilers)
    return infos


def select_compiler(compilers: list[Compiler], compiler_id: str) -> Compiler | None:
    if not compilers:
        return None
    if compiler_id and compiler_id != "auto":
        for compiler in compilers:
            if compiler.id == compiler_id:
                return compiler
    return compilers[0]


def _msvc_environment(compiler: Compiler) -> dict[str, str]:
    if not compiler.vcvars:
        raise RuntimeError("未找到 Visual Studio 启动脚本")
    env = _vcvars_env(compiler.vcvars, compiler.arch)
    # Windows 环境变量不区分大小写。统一大写，避免 PATH/Path 同时存在时
    # 子进程只取到其中一份，导致 cl.exe 或运行库找不到。
    full_env = {key.upper(): value for key, value in os.environ.items()}
    for key, value in env.items():
        full_env[key.upper()] = value
    return full_env


def _build_workdir(item: CodeItem) -> Path:
    override = os.environ.get("CPPSHOT_BUILD_DIR")
    temp_root = Path(override) if override else Path(tempfile.gettempdir()) / "CppShot" / "build"
    temp_root.mkdir(parents=True, exist_ok=True)
    prefix = "run_" + safe_filename(item.name, "program")[:24] + "_"
    # 不用 tempfile.mkdtemp：它在 Windows 上会创建 0700 目录，
    # 某些受限令牌下反而失去当前用户的访问权。普通 mkdir 继承正常权限。
    path = temp_root / f"{prefix}{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _source_with_encoding(code: str, compiler: Compiler) -> bytes:
    data = code.encode("utf-8")
    if compiler.kind == "msvc" and not compiler.utf8_flag:
        # VS2010 对无 BOM 的 UTF-8 不友好，使用 BOM 让它正确读入。
        return b"\xef\xbb\xbf" + data
    return data


def _cleanup_build(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def compile_and_run(item: CodeItem, compiler: Compiler, timeout_sec: int = 15) -> RunOutcome:
    """编译并运行；编译失败或运行退出码非 0 都返回 ok=False。"""
    started = time.perf_counter()
    build_dir = _build_workdir(item)
    source_path = build_dir / "main.cpp"
    exe_path = build_dir / ("program.exe" if sys.platform == "win32" else "program")
    command_text = ""
    compiler_label = compiler.label
    try:
        source_path.write_bytes(_source_with_encoding(item.code, compiler))
        compile_tmp = build_dir / "tmp"
        compile_tmp.mkdir(exist_ok=True)
        original_dir = Path(item.source_path).parent if item.source_path else None
        run_cwd = original_dir if original_dir and original_dir.is_dir() else build_dir
        if compiler.kind == "msvc":
            env = _msvc_environment(compiler)
            for key in ("TMP", "TEMP", "TMPDIR"):
                env[key] = str(compile_tmp)
            cl_path = shutil.which("cl", path=env.get("PATH")) or shutil.which("cl", path=next(
                (v for k, v in env.items() if k.upper() == "PATH"), ""))
            if not cl_path:
                # 某些 vcvars 会延迟设置 PATH；从常见位置补一下。
                root = Path(compiler.vcvars).parent.parent.parent if compiler.vcvars else None
                if root:
                    matches = list(root.glob("Tools/MSVC/*/bin/Hostx64/x64/cl.exe"))
                    cl_path = str(matches[-1]) if matches else None
                if not cl_path:
                    raise RuntimeError("找到了 Visual Studio，但找不到 cl.exe")
            args = [cl_path, "/nologo", "/EHsc", "/W3"]
            if compiler.utf8_flag:
                args.append("/utf-8")
            if compiler.cxx17_flag:
                args.append("/std:c++17")
            args.extend([f"/Fe{exe_path}", str(source_path)])
            if original_dir:
                args.insert(3, f"/I{original_dir}")
            command_text = (
                f'cl /nologo /EHsc /W3 '
                f'{" ".join(a for a in ["/utf-8"] if compiler.utf8_flag)} '
                f'{" ".join(a for a in ["/std:c++17"] if compiler.cxx17_flag)} '
                f'/Fe:program.exe main.cpp'
            )
            try:
                result = _run_hidden(args, timeout=90, env=env, cwd=str(build_dir))
            except subprocess.TimeoutExpired:
                return RunOutcome(
                    ok=False, stage="compile", exit_code=None, output="",
                    error="编译超时（超过 90 秒），已中止。", elapsed=time.perf_counter() - started,
                    compiler=compiler_label, build_dir=str(build_dir), command=command_text,
                )
        else:
            env = os.environ.copy()
            for key in ("TMP", "TEMP", "TMPDIR"):
                env[key] = str(compile_tmp)
            gxx = compiler.executable or shutil.which("g++")
            if not gxx:
                raise RuntimeError("找不到 g++")
            if str(Path(gxx).parent) not in env.get("PATH", ""):
                env["PATH"] = str(Path(gxx).parent) + os.pathsep + env.get("PATH", "")
            args = [gxx, "-std=c++17", "-O0", "-pipe", "-fdiagnostics-color=never",
                    "-o", str(exe_path), str(source_path)]
            if original_dir:
                args.insert(1, f"-I{original_dir}")
            command_text = f"g++ -std=c++17 -O0 -o program.exe main.cpp"
            try:
                result = _run_hidden(args, timeout=90, env=env, cwd=str(build_dir))
            except subprocess.TimeoutExpired:
                return RunOutcome(
                    ok=False, stage="compile", exit_code=None, output="",
                    error="编译超时（超过 90 秒），已中止。", elapsed=time.perf_counter() - started,
                    compiler=compiler_label, build_dir=str(build_dir), command=command_text,
                )
        compile_output = _decode(result.stdout)
        if result.returncode != 0 or not exe_path.exists():
            message = compile_output.strip() or "编译器没有输出具体信息。"
            return RunOutcome(
                ok=False, stage="compile", exit_code=result.returncode,
                output=compile_output, error=message,
                elapsed=time.perf_counter() - started, compiler=compiler_label,
                build_dir=str(build_dir), command=command_text,
            )

        run_env = os.environ.copy()
        if compiler.kind == "msvc":
            run_env = _msvc_environment(compiler)
        else:
            gxx = compiler.executable or shutil.which("g++")
            if gxx:
                run_env["PATH"] = str(Path(gxx).parent) + os.pathsep + run_env.get("PATH", "")
        for key in ("TMP", "TEMP", "TMPDIR"):
            run_env[key] = str(compile_tmp)
        stdin_text = item.stdin
        if stdin_text and not stdin_text.endswith("\n"):
            stdin_text += "\n"
        stdin_bytes = stdin_text.encode("utf-8")
        try:
            process = subprocess.Popen(
                [str(exe_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(run_cwd),
                env=run_env,
                creationflags=CREATE_NO_WINDOW,
            )
            try:
                out_bytes, _ = process.communicate(input=stdin_bytes, timeout=max(1, timeout_sec))
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=CREATE_NO_WINDOW,
                    )
                else:
                    try:
                        process.kill()
                    except OSError:
                        pass
                try:
                    out_bytes, _ = process.communicate(timeout=3)
                except Exception:
                    out_bytes = b""
                partial = _decode(out_bytes)
                return RunOutcome(
                    ok=False, stage="timeout", exit_code=process.returncode,
                    output=partial,
                    error=f"运行超时（超过 {timeout_sec} 秒），已中止批量截图。\n"
                          "如果程序需要输入，请在“运行输入”里填写内容。",
                    elapsed=time.perf_counter() - started, compiler=compiler_label,
                    build_dir=str(build_dir), command=command_text,
                )
        except OSError as exc:
            return RunOutcome(
                ok=False, stage="run", exit_code=None, output="",
                error=f"无法启动编译后的程序：{exc}", elapsed=time.perf_counter() - started,
                compiler=compiler_label, build_dir=str(build_dir), command=command_text,
            )
        run_output = _decode(out_bytes)
        if process.returncode != 0:
            return RunOutcome(
                ok=False, stage="run", exit_code=process.returncode,
                output=run_output,
                error=run_output.strip() or "程序运行失败，但没有输出错误信息。",
                elapsed=time.perf_counter() - started, compiler=compiler_label,
                build_dir=str(build_dir), command=command_text,
            )
        return RunOutcome(
            ok=True, stage="done", exit_code=0, output=run_output,
            error="", elapsed=time.perf_counter() - started,
            compiler=compiler_label, build_dir=str(build_dir), command=command_text,
        )
    except Exception as exc:
        return RunOutcome(
            ok=False, stage="prepare", exit_code=None, output="",
            error=f"{type(exc).__name__}: {exc}", elapsed=time.perf_counter() - started,
            compiler=compiler_label, build_dir=str(build_dir), command=command_text,
        )
    finally:
        _cleanup_build(build_dir)


# ---------------------------------------------------------------------------
# 交互运行：编译一次，进程保持运行，stdin 由界面逐行写入。
# ---------------------------------------------------------------------------


@dataclass
class PreparedProgram:
    ok: bool
    exe_path: str = ""
    build_dir: str = ""
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    label: str = ""
    encoding: str = "utf-8"
    command: str = ""
    elapsed: float = 0.0
    output: str = ""
    error: str = ""
    exit_code: int | None = None
    stage: str = "compile"

    def cleanup(self) -> None:
        if self.build_dir:
            _cleanup_build(Path(self.build_dir))
            self.build_dir = ""


def prepare_interactive(item: CodeItem, compiler: Compiler) -> PreparedProgram:
    """编译程序但暂不运行，返回一个可由 InteractiveProcess 启动的句柄。"""
    started = time.perf_counter()
    build_dir = _build_workdir(item)
    source_path = build_dir / "main.cpp"
    exe_path = build_dir / ("program.exe" if sys.platform == "win32" else "program")
    compiler_label = compiler.label
    command_text = ""
    try:
        tmp_dir = build_dir / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        source_path.write_bytes(_source_with_encoding(item.code, compiler))
        original_dir = Path(item.source_path).parent if item.source_path else None
        run_cwd = original_dir if original_dir and original_dir.is_dir() else build_dir
        if compiler.kind == "msvc":
            env = _msvc_environment(compiler)
            cl_path = shutil.which("cl", path=env.get("PATH")) or shutil.which(
                "cl", path=next((v for k, v in env.items() if k.upper() == "PATH"), "")
            )
            if not cl_path:
                root = Path(compiler.vcvars).parent.parent.parent if compiler.vcvars else None
                if root:
                    matches = list(root.glob("Tools/MSVC/*/bin/Hostx64/x64/cl.exe"))
                    cl_path = str(matches[-1]) if matches else None
                if not cl_path:
                    raise RuntimeError("找到了 Visual Studio，但找不到 cl.exe")
            args = [cl_path, "/nologo", "/EHsc", "/W3"]
            if compiler.utf8_flag:
                args.append("/utf-8")
            if compiler.cxx17_flag:
                args.append("/std:c++17")
            args.extend([f"/Fe{exe_path}", str(source_path)])
            if original_dir:
                args.insert(3, f"/I{original_dir}")
            command_text = "cl /nologo /EHsc /W3 " + (
                "/utf-8 " if compiler.utf8_flag else ""
            ) + ("/std:c++17 " if compiler.cxx17_flag else "") + "/Fe:program.exe main.cpp"
            for key in ("TMP", "TEMP", "TMPDIR"):
                env[key] = str(tmp_dir)
            result = _run_hidden(args, timeout=90, env=env, cwd=str(build_dir))
            encoding = "gb18030" if not compiler.utf8_flag else "utf-8"
        else:
            env = os.environ.copy()
            gxx = compiler.executable or shutil.which("g++")
            if not gxx:
                raise RuntimeError("找不到 g++")
            if str(Path(gxx).parent) not in env.get("PATH", ""):
                env["PATH"] = str(Path(gxx).parent) + os.pathsep + env.get("PATH", "")
            args = [gxx, "-std=c++17", "-O0", "-pipe", "-fdiagnostics-color=never",
                    "-o", str(exe_path), str(source_path)]
            if original_dir:
                args.insert(1, f"-I{original_dir}")
            command_text = "g++ -std=c++17 -O0 -o program.exe main.cpp"
            for key in ("TMP", "TEMP", "TMPDIR"):
                env[key] = str(tmp_dir)
            result = _run_hidden(args, timeout=90, env=env, cwd=str(build_dir))
            encoding = "utf-8"
        compile_output = _decode(result.stdout)
        if result.returncode != 0 or not exe_path.exists():
            _cleanup_build(build_dir)
            return PreparedProgram(
                ok=False, build_dir="", label=compiler_label, encoding=encoding,
                command=command_text, elapsed=time.perf_counter() - started,
                output=compile_output, stage="compile", exit_code=result.returncode,
                error=compile_output.strip() or "编译器没有输出具体信息。",
            )
        return PreparedProgram(
            ok=True, exe_path=str(exe_path), build_dir=str(build_dir),
            cwd=str(run_cwd), env=env, label=compiler_label, encoding=encoding,
            command=command_text, elapsed=time.perf_counter() - started,
            output=compile_output,
        )
    except subprocess.TimeoutExpired:
        _cleanup_build(build_dir)
        return PreparedProgram(
            ok=False, label=compiler_label, command=command_text, stage="compile",
            elapsed=time.perf_counter() - started,
            error="编译超时（超过 90 秒），已中止。",
        )
    except Exception as exc:
        _cleanup_build(build_dir)
        return PreparedProgram(
            ok=False, label=compiler_label, command=command_text, stage="prepare",
            elapsed=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )


class InteractiveProcess:
    """保持子进程运行，通过队列把实时输出交给 Tk 主线程。"""

    def __init__(self, prepared: PreparedProgram) -> None:
        self.prepared = prepared
        self.events: queue.Queue = queue.Queue()
        self._closed = False
        self._reader: threading.Thread | None = None
        self.process = subprocess.Popen(
            [prepared.exe_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=prepared.cwd or None,
            env=prepared.env,
            creationflags=CREATE_NO_WINDOW,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder(self.prepared.encoding)(errors="replace")
        fd = self.process.stdout.fileno() if self.process.stdout else -1
        try:
            while fd >= 0:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    self.events.put(("output", text))
            rest = decoder.decode(b"", final=True)
            if rest:
                self.events.put(("output", rest))
        except OSError:
            pass
        try:
            code = self.process.wait()
        except Exception:
            code = self.process.poll()
        self.events.put(("exit", code))

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def write_line(self, text: str) -> bool:
        if self.process.poll() is not None or self.process.stdin is None:
            return False
        try:
            self.process.stdin.write((text + "\n").encode(self.prepared.encoding, errors="replace"))
            self.process.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW, timeout=8,
                )
            except Exception:
                pass
        else:
            try:
                self.process.kill()
            except OSError:
                pass
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except OSError:
            pass

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()
        try:
            if self.process.stdout:
                self.process.stdout.close()
        except OSError:
            pass
        self.prepared.cleanup()

