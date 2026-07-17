from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    png_path = args.destination.with_suffix(".png")
    if args.source.suffix.lower() == ".svg":
        renderer = QSvgRenderer(str(args.source))
        if not renderer.isValid():
            raise RuntimeError(f"Invalid SVG icon: {args.source}")
        image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, 1024, 1024))
        painter.end()
        if not image.save(str(png_path), "PNG"):
            raise RuntimeError(f"Could not render application icon: {png_path}")
    else:
        with Image.open(args.source) as source:
            source.convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS).save(png_path)
    with Image.open(png_path) as icon:
        icon.save(
            args.destination,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
