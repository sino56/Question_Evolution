from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = Path(r"D:\downloads\PPT2_逐页图片")
RENDERED_DIR = ROOT / "rendered_slides"
OUTPUT_DIR = ROOT / "tools" / "ppt2_validation"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    refs = sorted(REFERENCE_DIR.glob("*PPT2*.png"))
    renders = sorted(RENDERED_DIR.glob("*.png"))
    if len(refs) != len(renders):
        raise RuntimeError(f"page count mismatch: refs={len(refs)} renders={len(renders)}")

    rows: list[str] = []
    for index, (ref_path, render_path) in enumerate(zip(refs, renders), start=1):
        reference = Image.open(ref_path).convert("RGB").resize((592, 333), Image.Resampling.LANCZOS)
        rendered = Image.open(render_path).convert("RGB").resize((592, 333), Image.Resampling.LANCZOS)

        # The source captures include viewer-only page badges, scrollbars, a pen button,
        # and tiled watermarks. Metrics are still useful as a coarse regression signal.
        ref_np = np.asarray(reference, dtype=np.float32)
        out_np = np.asarray(rendered, dtype=np.float32)
        mae = float(np.abs(ref_np - out_np).mean())
        rmse = float(np.sqrt(np.square(ref_np - out_np).mean()))
        rows.append(f"{index:03d}: MAE={mae:.2f}, RMSE={rmse:.2f}")

        overlay = Image.blend(reference, rendered, 0.5)
        overlay.save(OUTPUT_DIR / f"{index:03d}_overlay.png")

        diff = ImageChops.difference(reference, rendered)
        diff = diff.point(lambda p: min(255, p * 4))
        diff.save(OUTPUT_DIR / f"{index:03d}_diff.png")

        sheet = Image.new("RGB", (1184, 686), "white")
        sheet.paste(reference, (0, 20))
        sheet.paste(rendered, (592, 20))
        sheet.paste(overlay, (0, 353))
        sheet.paste(diff, (592, 353))
        draw = ImageDraw.Draw(sheet)
        draw.text((4, 3), "REFERENCE", fill="black")
        draw.text((596, 3), "RENDERED", fill="black")
        draw.text((4, 336), "50% OVERLAY", fill="black")
        draw.text((596, 336), f"DIFF x4 | MAE={mae:.2f}", fill="black")
        sheet.save(OUTPUT_DIR / f"{index:03d}_comparison.png")

    report = "\n".join(rows) + "\n"
    (OUTPUT_DIR / "metrics.txt").write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
