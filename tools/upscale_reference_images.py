from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter


SOURCE = Path(r"D:\downloads\PPT2_逐页图片")
OUTPUT = Path(__file__).resolve().parent / "ppt2_reference_upscaled"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for path in sorted(SOURCE.glob("*PPT2*.png")):
        image = Image.open(path).convert("RGB")
        enlarged = image.resize((image.width * 4, image.height * 4), Image.Resampling.LANCZOS)
        enlarged = ImageEnhance.Contrast(enlarged).enhance(1.12)
        enlarged = enlarged.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
        enlarged.save(OUTPUT / path.name)


if __name__ == "__main__":
    main()
