#!/usr/bin/env python3
"""Cloud/monitor-side receiver for 6-camera ZMQ stream."""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path
from typing import List

import cv2
import msgpack
import numpy as np
import zmq


WINDOW_NAMES = [
    "cam_front", "cam_front_left", "cam_front_right",
    "cam_back", "cam_back_left", "cam_back_right",
]

ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"


def colorize_ms(value_ms):
    if value_ms is None:
        return "N/A"
    text = f"{value_ms:.1f}ms"
    if value_ms > 200.0:
        return f"{ANSI_RED}{text}{ANSI_RESET}"
    if value_ms > 100.0:
        return f"{ANSI_YELLOW}{text}{ANSI_RESET}"
    return f"{ANSI_GREEN}{text}{ANSI_RESET}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Receive and display 6-camera stream")
    p.add_argument("--connect", required=True, help="PUB endpoint, e.g. tcp://100.x.x.x:5555")
    p.add_argument("--topic", default="sens", help="ZMQ topic to subscribe")
    p.add_argument("--rcvhwm", type=int, default=1, help="SUB receive high-water mark")
    p.add_argument("--save-dir", default=None, help="Optional directory to dump latest images")
    p.add_argument("--headless", action="store_true", help="Do not open OpenCV windows")
    p.add_argument("--latency-log", default=None, help="Optional CSV file to append timing metrics")
    p.add_argument("--print-every", type=int, default=30, help="Print rolling stats every N frames")
    p.add_argument("--print-data-every", type=int, default=120, help="Print raw receive summary every N frames")
    p.add_argument("--max-frames", type=int, default=0, help="Stop after N received frames, 0 means run forever")
    p.add_argument("--ack-bind", default="tcp://*:5556", help="ACK PUB endpoint, e.g. tcp://*:5556")
    p.add_argument("--ack-topic", default="ack", help="ACK topic when --ack-bind is enabled")
    return p.parse_args()


def decode_jpeg(blob: bytes):
    arr = np.frombuffer(blob, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Failed to decode JPEG frame")
    return img


def annotate(img, text: str):
    cv2.putText(img, text, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)


def save_payload(base_dir: Path, images: List[np.ndarray], header: dict):
    base_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(header.get("ts_unix", time.time()) * 1000)
    frame_dir = base_dir / f"frame_{stamp}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for i, img in enumerate(images):
        cv2.imwrite(str(frame_dir / f"cam_{i}.jpg"), img)
    (frame_dir / "header.msgpack").write_bytes(msgpack.packb(header, use_bin_type=True))


def main() -> int:
    args = parse_args()

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.RCVHWM, args.rcvhwm)
    sock.setsockopt(zmq.SUBSCRIBE, args.topic.encode("utf-8"))
    sock.connect(args.connect)

    ack_pub = None
    ack_topic_bytes = args.ack_topic.encode("utf-8")
    if args.ack_bind:
        ack_pub = ctx.socket(zmq.PUB)
        ack_pub.setsockopt(zmq.SNDHWM, 1024)
        ack_pub.bind(args.ack_bind)

    print(f"[receiver] connect={args.connect}, topic={args.topic}, ack_bind={args.ack_bind}")
    csv_file = None
    csv_writer = None
    if args.latency_log:
        latency_path = Path(args.latency_log)
        latency_path.parent.mkdir(parents=True, exist_ok=True)
        csv_file = latency_path.open("a", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        if latency_path.stat().st_size == 0:
            csv_writer.writerow([
                "recv_ts_unix",
                "sender_ts_unix",
                "frame_id",
                "sender_est_oneway_ms",
                "machine_clock_diff_ms",
                "label",
            ])

    if not args.headless:
        for name in WINDOW_NAMES:
            cv2.namedWindow(name, cv2.WINDOW_NORMAL)

    recv_count = 0
    oneway_window = []
    machine_diff_window = []

    try:
        while True:
            parts = sock.recv_multipart()
            if len(parts) < 8:
                print(f"[receiver][warn] invalid multipart len={len(parts)}")
                continue

            recv_ts = time.time()
            topic, header_b = parts[0], parts[1]
            if topic.decode("utf-8", errors="ignore") != args.topic:
                continue

            header = msgpack.unpackb(header_b, raw=False)
            cam_count = int(header.get("cam_count", 6))
            image_blobs = parts[2:2 + cam_count]

            if cam_count != 6 or len(image_blobs) != 6:
                print(f"[receiver][warn] expected 6 cams, got {cam_count}/{len(image_blobs)}")
                continue

            if args.print_data_every > 0 and recv_count % args.print_data_every == 0:
                print(
                    f"\n[receiver][raw] multipart received | "
                    f"parts={len(parts)} | "
                    f"frame_id={header.get('frame_id', -1)}"
                )

            images = [decode_jpeg(x) for x in image_blobs]
            recv_count += 1

            sender_ts = float(header.get("ts_unix", recv_ts))
            machine_clock_diff_ms = max((recv_ts - sender_ts) * 1000.0, 0.0)

            sender_last_rtt_ms = header.get("sender_last_rtt_ms", None)
            if isinstance(sender_last_rtt_ms, (int, float)):
                sender_last_rtt_ms = float(sender_last_rtt_ms)
            else:
                sender_last_rtt_ms = None

            sender_est_oneway_ms = header.get("sender_last_oneway_ms", None)
            if isinstance(sender_est_oneway_ms, (int, float)):
                sender_est_oneway_ms = float(sender_est_oneway_ms)
            elif sender_last_rtt_ms is not None:
                sender_est_oneway_ms = sender_last_rtt_ms / 2.0
            else:
                sender_est_oneway_ms = None

            machine_diff_window.append(machine_clock_diff_ms)
            if sender_est_oneway_ms is not None:
                oneway_window.append(sender_est_oneway_ms)

            if csv_writer is not None:
                csv_writer.writerow([
                    recv_ts,
                    sender_ts,
                    header.get("frame_id", ""),
                    "" if sender_est_oneway_ms is None else f"{sender_est_oneway_ms:.3f}",
                    f"{machine_clock_diff_ms:.3f}",
                    header.get("label", ""),
                ])
                csv_file.flush()

            if ack_pub is not None:
                ack_header = {
                    "frame_id": header.get("frame_id", -1),
                    "sender_ts_unix": sender_ts,
                    "receiver_recv_ts_unix": recv_ts,
                }
                ack_pub.send_multipart([ack_topic_bytes, msgpack.packb(ack_header, use_bin_type=True)])

            if args.print_every > 0 and recv_count % args.print_every == 0:
                avg_machine_diff = sum(machine_diff_window) / len(machine_diff_window)
                p95_machine_diff = sorted(machine_diff_window)[max(int(len(machine_diff_window) * 0.95) - 1, 0)]

                avg_oneway = None
                p95_oneway = None
                last_oneway = None
                if oneway_window:
                    avg_oneway = sum(oneway_window) / len(oneway_window)
                    p95_oneway = sorted(oneway_window)[max(int(len(oneway_window) * 0.95) - 1, 0)]
                    last_oneway = oneway_window[-1]

                stats_msg = (
                    f"\n[receiver][stats]"
                    f"avg_oneway={colorize_ms(avg_oneway)} "
                    f"p95_oneway={colorize_ms(p95_oneway)} "
                    f"last_oneway={colorize_ms(last_oneway)}\n"
                    f"[receiver][stats] avg_machine_clock_diff={colorize_ms(avg_machine_diff)} "
                    f"p95_machine_clock_diff={colorize_ms(p95_machine_diff)}"
                )
                print(stats_msg)

                oneway_window = []
                machine_diff_window = []

            if args.save_dir:
                save_payload(Path(args.save_dir), images, header)

            if not args.headless:
                overlay_text = (
                    f"oneway={sender_est_oneway_ms:.1f}ms" if sender_est_oneway_ms is not None else "oneway=N/A"
                )
                for i, img in enumerate(images):
                    annotate(img, overlay_text)
                    cv2.imshow(WINDOW_NAMES[i], img)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break

            if args.max_frames > 0 and recv_count >= args.max_frames:
                print(f"[receiver] reached max-frames={args.max_frames}, exiting")
                break
    except KeyboardInterrupt:
        print("\n[receiver] stopped")
    finally:
        sock.close(0)
        if ack_pub is not None:
            ack_pub.close(0)
        ctx.term()
        if csv_file is not None:
            csv_file.close()
        if not args.headless:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
