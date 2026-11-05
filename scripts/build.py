"""构建 CppShot 的免安装发行包（Windows / macOS / Linux）。

用法：
    python scripts/build.py

需要先安装 Pillow 和 PyInstaller：
    python -m pip install -r requirements.txt pyinstaller
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lazycpp import __version__  # noqa: E402

APP_NAME = "CppShot"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
RELEASE_DIR = ROOT / "release"


def run(argv: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(argv))
    result = subprocess.run(argv, cwd=str(cwd))
    if result.returncode != 0:
        raise SystemExit(f"命令失败，退出码 {result.returncode}")


def build_app() -> Path:
    system = platform.system()
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir",
        "--name", APP_NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR / "spec"),
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "yaml",
        "--exclude-module", "matplotlib",
    ]
    if system == "Windows":
        # 控制台子系统 + 启动时自动隐藏控制台：比 PyInstaller 的 windowed
        # bootloader 更容易在受限/杀软环境中稳定启动，也便于 --selftest。
        args.extend(["--console", "--icon", str(ROOT / "assets" / "icon.ico")])
    elif system == "Darwin":
        args.extend(["--windowed", "--osx-bundle-identifier", "io.github.cppshot"])
        icns = ROOT / "assets" / "icon.icns"
        if icns.exists():
            args.extend(["--icon", str(icns)])
    else:
        args.extend(["--windowed"])
    args.append(str(ROOT / "app.py"))
    run(args)
    if system == "Darwin":
        return DIST_DIR / f"{APP_NAME}.app"
    return DIST_DIR / APP_NAME


def copy_common(target: Path) -> None:
    for name in ("README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTORS.md"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, target / name)
    docs = ROOT / "docs"
    if docs.is_dir():
        shutil.copytree(docs, target / "docs", dirs_exist_ok=True)


def make_windows_bundle() -> Path:
    src = DIST_DIR / APP_NAME
    if not (src / f"{APP_NAME}.exe").exists():
        raise SystemExit("找不到构建产物 dist/CppShot/CppShot.exe")
    target = RELEASE_DIR / f"{APP_NAME}-{__version__}-Windows-Portable"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    copy_common(target)
    for name in ("启动 CppShot.bat", "启动 CppShot.vbs", "run.bat"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, target / name)
    archive = shutil.make_archive(str(target), "zip", root_dir=str(target))
    return Path(archive)


def make_mac_bundle() -> Path:
    src = DIST_DIR / f"{APP_NAME}.app"
    if not src.exists():
        raise SystemExit("找不到构建产物 dist/CppShot.app")
    target = RELEASE_DIR / f"{APP_NAME}-{__version__}-macOS"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copytree(src, target / f"{APP_NAME}.app", symlinks=True)
    copy_common(target)
    archive = Path(str(target) + ".zip")
    archive.unlink(missing_ok=True)
    ditto = shutil.which("ditto")
    if ditto:
        run([ditto, "-c", "-k", "--sequesterRsrc", "--keepParent",
             str(target), str(archive)])
    else:
        archive = Path(shutil.make_archive(str(target), "zip", root_dir=str(target)))
    return archive


def make_linux_bundle() -> Path:
    src = DIST_DIR / APP_NAME
    binary = src / APP_NAME
    if not binary.exists():
        raise SystemExit("找不到构建产物 dist/CppShot/CppShot")
    target = RELEASE_DIR / f"{APP_NAME}-{__version__}-Linux"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    copy_common(target)
    launcher = target / "启动 CppShot.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "cd \"$(dirname \"$0\")\" || exit 1\n"
        "./CppShot \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    binary.chmod(0o755)
    archive = Path(str(target) + ".tar.gz")
    archive.unlink(missing_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(target, arcname=target.name)
    return archive


def main() -> int:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    build_app()
    system = platform.system()
    if system == "Windows":
        archive = make_windows_bundle()
    elif system == "Darwin":
        archive = make_mac_bundle()
    else:
        archive = make_linux_bundle()
    print()
    print("构建完成：", archive)
    print("大小：", round(archive.stat().st_size / 1024 / 1024, 1), "MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
