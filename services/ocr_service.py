import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
from config import TEMP_DIR

TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    "tesseract",
]

MAGICK_CANDIDATES = [
    r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe",
    r"C:\Program Files\ImageMagick-7.1.2-Q16\magick.exe",
    "magick",
]


def find_binary(candidates):
    for candidate in candidates:
        try:
            candidate_path = Path(candidate)
            if candidate_path.is_file():
                return str(candidate_path)
            result = subprocess.run(
                [candidate, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 or result.stdout or result.stderr:
                return candidate
        except Exception:
            continue
    return None


def tesseract_langs():
    tessdata_dirs = [
        Path(r"C:\Users\khoa0\.openclaw\tessdata"),
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
    ]
    langs = set()
    for tessdata_dir in tessdata_dirs:
        if not tessdata_dir.exists():
            continue
        for file in tessdata_dir.glob("*.traineddata"):
            langs.add(file.stem)
    return langs


def best_lang():
    langs = tesseract_langs()
    if "deu" in langs and "eng" in langs:
        return "deu+eng"
    if "deu" in langs:
        return "deu"
    return "eng"


def enhance_handwriting_image(image_path: Path) -> Optional[Path]:
    """
    Tạo bản ảnh 'Kính lúp kép' (High-Contrast Shadow-Free Pen Enhancement) chuyên trị:
    - Chữ viết tay xấu, nét bút mờ, chụp bị bóng tối điện thoại đè lên trang vở.
    - Làm rõ sự khác biệt giữa nét chữ thật và nét gạch xóa / viết chèn mà KHÔNG làm mất dấu Umlaut (ä, ö, ü).
    """
    try:
        out_path = TEMP_DIR / f"{image_path.stem}_enhanced.jpg"
        with Image.open(image_path) as img:
            # 1. Tự động xoay đúng chiều ảnh chụp từ iPhone/điện thoại
            img = ImageOps.exif_transpose(img)

            # 2. Chuyển sang thang xám chất lượng cao
            gray = img.convert("L")

            # 3. Phóng lớn nếu ảnh có độ phân giải thấp để nét chữ rõ hơn
            w, h = gray.size
            if max(w, h) < 1600:
                scale = 1600 / max(w, h)
                gray = gray.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

            # 4. Khử bóng râm trang giấy (Shadow Removal bằng Background Division nhẹ)
            bg = gray.filter(ImageFilter.GaussianBlur(radius=35))
            diff = ImageChops.invert(ImageChops.subtract(bg, gray))
            norm = ImageOps.autocontrast(diff, cutoff=1)

            # 5. Làm sắc nét nét mực (UnsharpMask) và tăng tương phản vừa phải để giữ rõ nét gạch xóa
            sharpened = norm.filter(ImageFilter.UnsharpMask(radius=1.8, percent=175, threshold=3))
            enhanced = ImageEnhance.Contrast(sharpened).enhance(1.35)

            enhanced.save(out_path, format="JPEG", quality=92)
            return out_path
    except Exception:
        return None


def run_local_ocr_hint(image_path: Path) -> str:
    """
    Chạy tiền xử lý ảnh bằng ImageMagick và Tesseract OCR (nếu có trong máy)
    để tạo gợi ý văn bản bổ trợ cho Gemini Vision.
    """
    tesseract = find_binary(TESSERACT_CANDIDATES)
    magick = find_binary(MAGICK_CANDIDATES)
    if not tesseract or not magick:
        return ""

    processed = TEMP_DIR / f"{image_path.stem}.preprocessed.png"
    out_base = TEMP_DIR / f"{image_path.stem}.ocr"

    try:
        subprocess.run(
            [
                magick,
                str(image_path),
                "-auto-orient",
                "-colorspace",
                "Gray",
                "-deskew",
                "40%",
                "-normalize",
                "-contrast-stretch",
                "0.5%x0.5%",
                "-sharpen",
                "0x1.0",
                str(processed),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=True,
        )

        lang = best_lang()
        env = os.environ.copy()
        custom_tessdata = Path(r"C:\Users\khoa0\.openclaw\tessdata")
        if custom_tessdata.exists():
            env["TESSDATA_PREFIX"] = str(custom_tessdata.parent)
        else:
            env["TESSDATA_PREFIX"] = r"C:\Program Files\Tesseract-OCR"

        subprocess.run(
            [
                tesseract,
                str(processed),
                str(out_base),
                "--oem",
                "1",
                "--psm",
                "6",
                "-l",
                lang,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=True,
            env=env,
        )

        txt_path = Path(str(out_base) + ".txt")
        if txt_path.exists():
            raw = txt_path.read_text(encoding="utf-8", errors="ignore")
            return re.sub(r"\n{3,}", "\n\n", raw).strip()
    except Exception:
        pass
    finally:
        for f in [processed, Path(str(out_base) + ".txt")]:
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass
    return ""
