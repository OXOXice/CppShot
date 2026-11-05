"""重新生成 assets/icon.png / icon.ico / icon.icns。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([8, 8, 248, 248], radius=52, fill=(11, 18, 32, 255),
                           outline=(56, 189, 248, 255), width=8)
    draw.rounded_rectangle([28, 28, 228, 228], radius=36, fill=(13, 22, 38, 255))
    try:
        large = ImageFont.truetype(r"C:\Windows\Fonts\consolab.ttf", 96)
        small = ImageFont.truetype(r"C:\Windows\Fonts\consolab.ttf", 52)
    except Exception:
        large = ImageFont.load_default()
        small = large
    draw.text((58, 100), ">", font=large, fill=(56, 189, 248, 255), anchor="ls")
    draw.rectangle([113, 142, 178, 152], fill=(231, 238, 250, 255))
    draw.text((112, 188), "_", font=small, fill=(52, 211, 153, 255), anchor="ls")
    image.save(ASSETS / "icon.png")
    image.save(ASSETS / "icon.ico", sizes=[(256, 256), (128, 128), (64, 64),
                                           (48, 48), (32, 32), (16, 16)])
    image.save(ASSETS / "icon.icns", format="ICNS",
               sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512)])
    print("icons written to", ASSETS)


if __name__ == "__main__":
    main()
