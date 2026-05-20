#!/usr/bin/env python3
"""Test encode_jpeg compression: save compressed images and print size stats.

Compression target is expressed as a percentage of each image's original file
size (TARGET_RATIO_PCT).  The encoder first lowers JPEG quality; if the minimum
quality is still too large it proportionally downscales the resolution.
Images are encoded at their original resolution (no forced resize).
"""

import sys
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np


def encode_jpeg(frame, quality: int, max_bytes: int = 0,
                max_wh: Tuple[int, int] = None) -> bytes:
    """Encode frame as JPEG at original resolution.

    max_wh: optional (W, H) cap — proportionally downscale to fit, never upscale.
    max_bytes: if > 0, first lower JPEG quality to _MIN_QUALITY; if still too large,
               proportionally halve resolution until the target is met.
    """
    _MIN_QUALITY = 5
    h_orig, w_orig = frame.shape[:2]

    working = frame
    if max_wh is not None:
        mw, mh = max_wh
        scale = min(mw / w_orig, mh / h_orig, 1.0)
        if scale < 1.0:
            working = cv2.resize(frame, (int(w_orig * scale), int(h_orig * scale)),
                                 interpolation=cv2.INTER_AREA)

    try:
        q = quality
        data = b""
        while True:
            ok, enc = cv2.imencode(".jpg", working, [cv2.IMWRITE_JPEG_QUALITY, q])
            if not ok:
                break
            data = enc.tobytes()
            if max_bytes <= 0 or len(data) <= max_bytes:
                return data
            if q <= _MIN_QUALITY:
                break  # quality floor — enter proportional resize phase
            ratio = max_bytes / len(data)
            next_q = max(int(q * ratio) - 2, _MIN_QUALITY)
            if next_q >= q:
                next_q = q - 5
            q = max(next_q, _MIN_QUALITY)

        # Proportional resolution reduction
        if max_bytes > 0 and data:
            h_w, w_w = working.shape[:2]
            scale = 0.75
            while scale >= 0.1:
                nw = max(1, int(w_w * scale))
                nh = max(1, int(h_w * scale))
                small = cv2.resize(working, (nw, nh), interpolation=cv2.INTER_AREA)
                ok, enc = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, _MIN_QUALITY])
                if ok:
                    data = enc.tobytes()
                    if len(data) <= max_bytes:
                        return data
                scale *= 0.75

        if data:
            return data
    except Exception:
        pass

    black = np.zeros((h_orig, w_orig, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", black, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return enc.tobytes()


# ---------- 配置 ----------
INPUT_DIR        = Path(__file__).parent / "captured_frames"
OUTPUT_DIR       = Path(__file__).parent / "compressed_output"
JPEG_QUALITY     = 55     # 初始 JPEG 质量（自适应降低）
TARGET_RATIO_PCT = 10.0   # 目标压缩率：占原始文件大小的百分比
# MAX_WH = (640, 384)     # 可选：等比例分辨率上限，注释掉则使用原图分辨率
MAX_WH = None
# --------------------------


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    image_paths = sorted(INPUT_DIR.glob("*.jpg")) + sorted(INPUT_DIR.glob("*.png"))
    if not image_paths:
        print(f"[ERROR] No images found in {INPUT_DIR}")
        sys.exit(1)

    max_wh_str = f"{MAX_WH[0]}x{MAX_WH[1]}" if MAX_WH else "original"
    print(f"Resolution cap : {max_wh_str}")
    print(f"JPEG quality   : {JPEG_QUALITY}  (may be lowered adaptively)")
    print(f"Target ratio   : {TARGET_RATIO_PCT}% of original file size")
    print(f"Input dir      : {INPUT_DIR}")
    print(f"Output dir     : {OUTPUT_DIR}")
    print("-" * 70)
    print(f"  {'Filename':<20}  {'Orig':>8}  {'Compressed':>10}  {'Ratio':>7}  {'Status'}")
    print("-" * 70)

    total_orig = 0
    total_comp = 0

    for idx, src_path in enumerate(image_paths):
        frame = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
        if frame is None:
            print(f"  [WARN] Cannot read {src_path.name}, skipped")
            continue

        orig_bytes = src_path.stat().st_size
        max_bytes = max(1, int(orig_bytes * TARGET_RATIO_PCT / 100))
        total_orig += orig_bytes

        jpeg_bytes = encode_jpeg(frame, JPEG_QUALITY, max_bytes, MAX_WH)
        total_comp += len(jpeg_bytes)

        out_path = OUTPUT_DIR / f"cam{idx}_{src_path.stem}_compressed.jpg"
        out_path.write_bytes(jpeg_bytes)

        actual_pct = len(jpeg_bytes) / orig_bytes * 100
        ok = len(jpeg_bytes) <= max_bytes
        status = "✓" if ok else f"✗ ({actual_pct:.1f}% > {TARGET_RATIO_PCT}%)"
        print(
            f"  {src_path.name:<20}  {orig_bytes/1024:>6.1f} KB  "
            f"{len(jpeg_bytes)/1024:>8.1f} KB  {actual_pct:>6.1f}%  {status}"
        )

    print("-" * 70)
    overall_pct = total_comp / total_orig * 100
    print(
        f"  {'Total':<20}  {total_orig/1024:>6.1f} KB  "
        f"{total_comp/1024:>8.1f} KB  {overall_pct:>6.1f}%"
    )
    print(f"\nCompressed images saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
