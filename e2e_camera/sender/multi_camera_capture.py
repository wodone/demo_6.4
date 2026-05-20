#!/usr/bin/env python3
"""
多摄像头采集程序
自动扫描 /dev/video* 可用设备，或手动指定编号，将每帧图像保存到同一文件夹。
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
    def __init__(self, device_index: int, output_dir: str, fps_limit: float = 10.0):
        self.device_index = device_index
        self.device_path = f"/dev/video{device_index}"
        self.output_dir = output_dir
        self.fps_limit = fps_limit
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
        print(f"[camera{self.device_index}] 已打开 {self.device_path}")
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

            filename = os.path.join(self.output_dir, f"cam{self.device_index}.jpg")
            cv2.imwrite(filename, frame)
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
    parser = argparse.ArgumentParser(description="多摄像头采集程序")
    parser.add_argument(
        "--output", "-o",
        default="captured_frames",
        help="输出根目录（默认：captured_frames）",
    )
    parser.add_argument(
        "--fps", "-f",
        type=float,
        default=10.0,
        help="每秒保存帧数上限（默认：10）",
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        type=int,
        default=None,
        help="手动指定设备编号（如：--devices 0 2 4），不指定则自动扫描 /dev/video*",
    )
    args = parser.parse_args()

    if args.devices is None:
        print("正在扫描可用摄像头...")
        args.devices = detect_video_devices()
        if not args.devices:
            print("错误：未找到任何可用摄像头，退出。")
            return
        print(f"自动检测到设备：{[f'/dev/video{i}' for i in args.devices]}")
    else:
        print(f"手动指定设备：{[f'/dev/video{i}' for i in args.devices]}")

    print(f"输出目录：{os.path.abspath(args.output)}")
    print(f"目标帧率：{args.fps} fps")
    print("-" * 50)

    os.makedirs(args.output, exist_ok=True)
    cameras = [CameraCapture(i, args.output, args.fps) for i in args.devices]

    # 打开摄像头
    active = [cam for cam in cameras if cam.open()]
    if not active:
        print("错误：没有可用的摄像头，退出。")
        return

    failed = [cam for cam in cameras if cam not in active]
    for cam in failed:
        print(f"[camera{cam.device_index}] 跳过：{cam.error}")

    # 启动所有线程
    for cam in active:
        cam.start()

    print(f"\n{len(active)} 颗摄像头正在采集，图像统一保存至 {os.path.abspath(args.output)}，按 Ctrl+C 停止...\n")

    try:
        while True:
            time.sleep(5)
            status = " | ".join(
                f"cam{cam.device_index}: {cam.frame_count}帧" for cam in active
            )
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {status}")
    except KeyboardInterrupt:
        print("\n收到停止信号，正在退出...")

    for cam in active:
        cam.stop()

    print("\n采集完成。")
    print(f"图像已保存至：{os.path.abspath(args.output)}")


if __name__ == "__main__":
    main()
