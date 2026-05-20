#!/bin/bash
PYTHON_EXEC="python3"
SENDER_SCRIPT="sender.py"

PORT="3721"
ACK_PORT="2137"

# 改成 receiver 那台机器的实际 IP
ACK_TARGET_IP="100.68.223.102"

echo "🚀 正在启动：文件回放模式..."

$PYTHON_EXEC $SENDER_SCRIPT \
  --bind tcp://*:${PORT} \
  --cam-files cam0.jpg cam1.jpg cam2.jpg cam3.jpg cam4.jpg cam5.jpg \
  --fps 10 \
  --label car_A \
  --ack-connect tcp://$ACK_TARGET_IP:$ACK_PORT \
  --print-send-every 30 \
  --print-data-every 120 \
  --print-rtt-every 30
