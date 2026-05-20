#!/usr/bin/env python3
"""Car-side sender: publish 6 camera frames over ZMQ.

Wire format uses multipart ZMQ message:
- frame 0: topic bytes (default: b"sens")
- frame 1: msgpack header (metadata only)
- frame 2..7: JPEG bytes for camera frames
"""

from __future__ import annotations

import argparse
import csv
import glob
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import msgpack
import zmq


@dataclass
class CameraSource:
    cap: cv2.VideoCapture
    name: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send 6-camera packets via ZMQ PUB")
    p.add_argument("--bind", default="tcp://*:5555", help="PUB bind endpoint")
    p.add_argument("--topic", default="sens", help="ZMQ topic")
    p.add_argument("--fps", type=float, default=10.0, help="Target send FPS")
    p.add_argument("--jpeg-quality", type=int, default=55, help="JPEG quality [1,100]")
    p.add_argument("--resize", default="640x384", help="Output image size WxH")
    p.add_argument("--sndhwm", type=int, default=1, help="PUB send high-water mark")

    p.add_argument("--cam-files", nargs="*", default=None, help="Exactly 6 image files for replay mode")
    p.add_argument("--cam-glob", default=None, help="Glob pattern containing >=6 images; first 6 are used")
    p.add_argument("--cam-devices", nargs="*", default=None, help="Exactly 6 camera device ids, e.g. 0 1 2 3 4 5")

    p.add_argument("--label", default="demo", help="Vehicle id / stream label")
    p.add_argument("--max-frames", type=int, default=0, help="Stop after N frames, 0 means run forever")

    p.add_argument("--ack-connect", default=None, help="Optional ACK SUB endpoint, e.g. tcp://100.x.x.x:5556")
    p.add_argument("--ack-topic", default="ack", help="ACK topic when --ack-connect is enabled")
    p.add_argument("--ack-timeout-s", type=float, default=1.0, help="Clear RTT/oneway if no ACK arrives for this many seconds")
    p.add_argument("--rtt-log", default=None, help="Optional CSV path for RTT records on sender side")
    p.add_argument("--pending-ack-window", type=int, default=2048, help="Max in-flight frame IDs to track for RTT")

    p.add_argument("--print-send-every", type=int, default=30, help="Print send summary every N sent frames")
    p.add_argument("--print-data-every", type=int, default=120, help="Print key data payload summary every N sent frames")
    p.add_argument("--print-rtt-every", type=int, default=30, help="Print RTT stats every N received ACKs")
    return p.parse_args()


def parse_resize(resize: str) -> Tuple[int, int]:
    try:
        w_str, h_str = resize.lower().split("x")
        w, h = int(w_str), int(h_str)
    except Exception as exc:
        raise ValueError(f"Invalid --resize {resize}, expected like 640x384") from exc
    if w <= 0 or h <= 0:
        raise ValueError("Resize width and height must be positive")
    return w, h


def resolve_cam_files(args: argparse.Namespace) -> Optional[List[str]]:
    if args.cam_files:
        if len(args.cam_files) != 6:
            raise ValueError("--cam-files must provide exactly 6 paths")
        files = args.cam_files
    elif args.cam_glob:
        files = sorted(glob.glob(args.cam_glob))[:6]
        if len(files) < 6:
            raise ValueError("--cam-glob matched fewer than 6 images")
    else:
        return None

    for fp in files:
        if not Path(fp).is_file():
            raise FileNotFoundError(fp)
    return files


def resolve_cam_devices(args: argparse.Namespace) -> Optional[List[int]]:
    if not args.cam_devices:
        return None
    if len(args.cam_devices) != 6:
        raise ValueError("--cam-devices must provide exactly 6 device IDs")
    return [int(x) for x in args.cam_devices]


def open_cameras(device_ids: Sequence[int]) -> List[CameraSource]:
    cams: List[CameraSource] = []
    for dev in device_ids:
        cap = cv2.VideoCapture(dev)
        if not cap.isOpened():
            for c in cams:
                c.cap.release()
            raise RuntimeError(f"Failed to open camera device {dev}")
        cams.append(CameraSource(cap=cap, name=f"cam_{dev}"))
    return cams


def encode_jpeg(frame, wh: Tuple[int, int], quality: int) -> bytes:
    w, h = wh
    try:
        resized = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        ok, enc = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            return enc.tobytes()
    except Exception:
        pass
    
    # 编码失败返回纯黑图
    black = np.zeros((h, w, 3), dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", black, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return enc.tobytes()


def get_frames_from_files(paths: Sequence[str], target_size: Tuple[int, int]) -> List:
    """读取文件失败时返回纯黑帧，不抛异常退出"""
    import numpy as np
    frames = []
    w, h = target_size
    for idx, fp in enumerate(paths):
        try:
            frame = cv2.imread(fp, cv2.IMREAD_COLOR)
            if frame is not None:
                frames.append(frame)
                continue
        except Exception as e:
            pass
        
        # 读取失败 → 生成纯黑帧占位
        print(f"⚠️  [文件读取失败] cam{idx}: {fp}，使用纯黑帧替代")
        black_frame = np.zeros((h, w, 3), dtype=np.uint8)
        frames.append(black_frame)
    return frames


def get_frames_from_cams(cams: Sequence[CameraSource]) -> List:
    """摄像头读取失败时返回纯黑帧，不抛异常退出"""
    import numpy as np
    frames = []
    for cam in cams:
        try:
            ok, frame = cam.cap.read()
            if ok and frame is not None:
                frames.append(frame)
                continue
        except Exception as e:
            pass
        
        # 读取失败 → 纯黑帧占位
        print(f"⚠️  [摄像头读取失败] {cam.name}，使用纯黑帧替代")
        black_frame = np.zeros((480, 640, 3), dtype=np.uint8)  # 临时尺寸，后面会统一resize
        frames.append(black_frame)
    return frames


def fmt_ms(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:.1f}"


def main() -> int:
    import numpy as np
    args = parse_args()
    target_size = parse_resize(args.resize)
    cam_files = resolve_cam_files(args)
    cam_devices = resolve_cam_devices(args)

    if (cam_files is None) == (cam_devices is None):
        raise ValueError("Provide exactly one camera source: --cam-files/--cam-glob OR --cam-devices")

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.PUB)
    sock.setsockopt(zmq.SNDHWM, args.sndhwm)
    sock.bind(args.bind)

    ack_sock = None
    ack_topic_bytes = args.ack_topic.encode("utf-8")
    if args.ack_connect:
        ack_sock = ctx.socket(zmq.SUB)
        ack_sock.setsockopt(zmq.RCVHWM, 1024)
        ack_sock.setsockopt(zmq.SUBSCRIBE, ack_topic_bytes)
        ack_sock.connect(args.ack_connect)

    rtt_csv_file = None
    rtt_csv_writer = None
    if args.rtt_log:
        rtt_path = Path(args.rtt_log)
        rtt_path.parent.mkdir(parents=True, exist_ok=True)
        rtt_csv_file = rtt_path.open("a", newline="", encoding="utf-8")
        rtt_csv_writer = csv.writer(rtt_csv_file)
        if rtt_path.stat().st_size == 0:
            rtt_csv_writer.writerow(
                [
                    "ack_recv_ts_unix",
                    "frame_id",
                    "rtt_ms",
                    "oneway_ms",
                    "sender_ts_unix",
                    "receiver_recv_ts_unix",
                    "label",
                ]
            )

    cams: List[CameraSource] = []
    if cam_devices is not None:
        cams = open_cameras(cam_devices)

    period_s = 1.0 / args.fps if args.fps > 0 else 0.0
    topic_bytes = args.topic.encode("utf-8")
    pending_ts = OrderedDict()

    rtt_window: List[float] = []
    recv_ack_count = 0
    last_rtt_ms: Optional[float] = None
    last_oneway_ms: Optional[float] = None
    last_ack_recv_ts: Optional[float] = None

    print(f"[sender] bind={args.bind}, topic={args.topic}, fps={args.fps}, label={args.label}")
    frame_id = 0
    try:
        while True:
            t0 = time.time()
            if cam_files is not None:
                raw_frames = get_frames_from_files(cam_files, target_size)
            else:
                raw_frames = get_frames_from_cams(cams)

            jpg_list = [encode_jpeg(f, target_size, args.jpeg_quality) for f in raw_frames]
            if len(jpg_list) != 6:
                print(f"❌ 帧数量错误：{len(jpg_list)}，跳过此帧")
                elapsed = time.time() - t0
                if period_s > 0 and elapsed < period_s:
                    time.sleep(period_s - elapsed)
                continue

            now = time.time()
            if last_ack_recv_ts is not None and (now - last_ack_recv_ts) > args.ack_timeout_s:
                last_rtt_ms = None
                last_oneway_ms = None
                last_ack_recv_ts = None

            header = {
                "ver": 1,
                "label": args.label,
                "ts_unix": now,
                "frame_id": frame_id,
                "cam_count": 6,
                "img_fmt": "jpg",
                "img_size": [target_size[0], target_size[1]],
                "sender_last_rtt_ms": last_rtt_ms,
                "sender_last_oneway_ms": last_oneway_ms,
            }
            header_bytes = msgpack.packb(header, use_bin_type=True)

            multipart = [topic_bytes, header_bytes, *jpg_list]
            sock.send_multipart(multipart)

            pending_ts[frame_id] = now
            if len(pending_ts) > args.pending_ack_window:
                pending_ts.popitem(last=False)

            if args.print_send_every > 0 and frame_id % args.print_send_every == 0:
                print(
                    f"🚀 [sender] Sent Frame ID: {frame_id} | Time: {time.strftime('%H:%M:%S', time.localtime(now))}"
                )

            if args.print_data_every > 0 and frame_id % args.print_data_every == 0:
                print(
                    f"[sender] payload summary | cams=6 | img_size={target_size[0]}x{target_size[1]} | "
                    f"last_rtt_ms={fmt_ms(last_rtt_ms)} | last_oneway_ms={fmt_ms(last_oneway_ms)}"
                )

            frame_id += 1
            if args.max_frames > 0 and frame_id >= args.max_frames:
                print(f"[sender] reached max-frames={args.max_frames}, exiting")
                break

            if ack_sock is not None:
                while True:
                    try:
                        ack_parts = ack_sock.recv_multipart(flags=zmq.NOBLOCK)
                    except zmq.Again:
                        break
                    if len(ack_parts) < 2:
                        continue

                    try:
                        ack_header = msgpack.unpackb(ack_parts[1], raw=False)
                        ack_frame_id = int(ack_header.get("frame_id", -1))
                        sender_ts = pending_ts.pop(ack_frame_id, None)
                        if sender_ts is None:
                            continue

                        ack_now = time.time()
                        rtt_ms = max((ack_now - sender_ts) * 1000.0, 0.0)
                        oneway_ms = rtt_ms / 2.0
                        last_rtt_ms = rtt_ms
                        last_oneway_ms = oneway_ms
                        last_ack_recv_ts = ack_now
                        recv_ack_count += 1
                        rtt_window.append(rtt_ms)

                        if rtt_csv_writer is not None:
                            rtt_csv_writer.writerow(
                                [
                                    ack_now,
                                    ack_frame_id,
                                    f"{rtt_ms:.3f}",
                                    f"{oneway_ms:.3f}",
                                    sender_ts,
                                    ack_header.get("receiver_recv_ts_unix", ""),
                                    args.label,
                                ]
                            )
                            rtt_csv_file.flush()

                        if args.print_rtt_every > 0 and recv_ack_count % args.print_rtt_every == 0:
                            avg_rtt = sum(rtt_window) / len(rtt_window)
                            p95_rtt = sorted(rtt_window)[max(int(len(rtt_window) * 0.95) - 1, 0)]
                            print(
                                f"[sender][RTT Stats] ACKs={recv_ack_count} | "
                                f"avg_rtt={avg_rtt:.1f}ms | p95_rtt={p95_rtt:.1f}ms | "
                                f"last_oneway={oneway_ms:.1f}ms"
                            )
                            rtt_window = []
                    except Exception as e:
                        print(f"⚠️  ACK解析失败：{e}")
                        continue

            elapsed = time.time() - t0
            if period_s > 0 and elapsed < period_s:
                time.sleep(period_s - elapsed)
    except KeyboardInterrupt:
        print("\n[sender] stopped")
    finally:
        for c in cams:
            c.cap.release()
        if ack_sock is not None:
            ack_sock.close(0)
        sock.close(0)
        if rtt_csv_file is not None:
            rtt_csv_file.close()
        ctx.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
