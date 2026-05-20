#!/bin/bash
PYTHON_EXEC="python3"
SENDER_SCRIPT="sender.py"

PORT="3721"
ACK_PORT="2137"

# 改成 receiver 那台机器的实际 IP
ACK_TARGET_IP="100.64.13.64"

echo "🚀 正在启动：文件回放模式..."

$PYTHON_EXEC $SENDER_SCRIPT \
  --bind tcp://*:${PORT} \
  --cam-files captured_frames/cam1.jpg captured_frames/cam2.jpg captured_frames/cam4.jpg captured_frames/cam5.jpg captured_frames/cam6.jpg captured_frames/cam7.jpg \
  --fps 10 \
  --label car_A \
  --ack-connect tcp://$ACK_TARGET_IP:$ACK_PORT \
  --print-send-every 30 \
  --print-data-every 120 \
  --print-rtt-every 30 \
  --max-kb-per-cam 10
