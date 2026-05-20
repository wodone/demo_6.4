#!/usr/bin/env python3
"""
多摄像头采集程序（强制 UYVY 格式，自动兼容通道，无报错 + 图像压缩）
"""

import cv2
import glob
import os
import re
import time
import threading
import argparse
from datetime import datetime


def detect_video_devices() -> list[int]:
    """扫描 /dev/video* 并返回实际可读取帧的设备编号列表。"""
    paths = sorted(glob.glob("/dev/video*"), key=lambda p: int(re.search(r"\d+", p).group()))
    available = []
    for path in paths:
        idx = int(re.search(r"\d+", path).group())
        cap = cv2.VideoCapture(path)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available.append(idx)
        cap.release()
    return available


class CameraCapture:
    def __init__(self, device_index: int, output_dir: str, fps_limit: float = 10.0,
                 width: int = 1920, height: int = 1080, jpg_quality: int = 70):
        self.device_index = device_index
        self.device_path = f"/dev/video{device_index}"
        self.output_dir = output_dir
        self.fps_limit = fps_limit
        self.width = width
        self.height = height
        self.jpg_quality = jpg_quality  # JPG 压缩质量（0-100）
        self.frame_count = 0
        self.running = False
        self.cap = None
        self.thread = None
        self.error = None

    def open(self) -> bool:
        self.cap = cv2.VideoCapture(self.device_path)
        if not self.cap.isOpened():
            self.error = f"{self.device_path} 打开失败"
            return False

        # 设置分辨率
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        
        # 强制 UYVY 格式
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('U', 'Y', 'V', 'Y'))

        # 自动曝光 / 白平衡
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
        self.cap.set(cv2.CAP_PROP_AUTO_WB, 1)

        print(f"[camera{self.device_index}] 已打开 {self.device_path} | {self.width}x{self.height} | UYVY")
        return True

    def _capture_loop(self):
        interval = 1.0 / self.fps_limit
        while self.running:
            start = time.time()
            ret, frame = self.cap.read()
            if not ret:
                print(f"[camera{self.device_index}] 读帧失败，跳过")
                time.sleep(interval)
                continue

            # 自动兼容通道：只有2通道才做UYVY转换，3通道BGR直接保存
            try:
                if len(frame.shape) == 3 and frame.shape[2] == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_UYVY)
            except:
                pass

            # ===================== 图像压缩保存 =====================
            filename = os.path.join(self.output_dir, f"cam{self.device_index}.jpg")
            # JPG 质量参数：[cv2.IMWRITE_JPEG_QUALITY, 0~100]
            cv2.imwrite(
                filename,
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), self.jpg_quality]
            )
            # =======================================================

            self.frame_count += 1

            elapsed = time.time() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3.0)
        if self.cap:
            self.cap.release()
        print(f"[camera{self.device_index}] 已停止，共保存 {self.frame_count} 帧")


def main():
    parser = argparse.ArgumentParser(description="多摄像头采集程序 (UYVY 格式 + 图像压缩)")
    parser.add_argument("--output", "-o", default="captured_frames", help="输出目录")
    parser.add_argument("--fps", "-f", type=float, default=10.0, help="帧率上限")
    parser.add_argument("--devices", nargs="+", type=int, default=None, help="手动指定摄像头编号")
    parser.add_argument("--width", "-w", type=int, default=1920, help="宽度")
    parser.add_argument("--height", "-ht", type=int, default=1080, help="高度")
    parser.add_argument("--quality", "-q", type=int, default=70,
                        help="JPG图像质量 0-100（越小压缩率越高，默认70）")
    args = parser.parse_args()

    if args.devices is None:
        print("正在扫描可用摄像头...")
        args.devices = detect_video_devices()
        if not args.devices:
            print("错误：未找到可用摄像头")
            return
        print(f"自动检测到设备：{[f'/dev/video{i}' for i in args.devices]}")

    print(f"输出目录：{os.path.abspath(args.output)}")
    print(f"分辨率：{args.width}x{args.height}")
    print(f"像素格式：UYVY")
    print(f"JPG压缩质量：{args.quality}（数值越小，文件越小）")
    print("-" * 50)

    os.makedirs(args.output, exist_ok=True)
    cameras = [CameraCapture(i, args.output, args.fps, args.width, args.height, args.quality) for i in args.devices]

    active = [cam for cam in cameras if cam.open()]
    if not active:
        print("错误：无可用摄像头")
        return

    for cam in active:
        cam.start()

    print(f"\n{len(active)} 个摄像头正在采集，按 Ctrl+C 停止\n")

    try:
        while True:
            time.sleep(5)
            status = " | ".join(f"cam{cam.device_index}: {cam.frame_count}帧" for cam in active)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {status}")
    except KeyboardInterrupt:
        print("\n正在停止...")

    for cam in active:
        cam.stop()

    print("\n采集完成！")


if __name__ == "__main__":
    main()
