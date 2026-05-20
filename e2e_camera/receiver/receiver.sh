#!/bin/bash
PYTHON_EXEC="python3"
RECEIVER_SCRIPT="receiver.py"

PORT="3721"
ACK_PORT="2137"

# 改成 sender 那台机器的实际 IP
TARGET_IP="100.67.69.62"

echo "📡 正在尝试连接发送端: $TARGET_IP:$PORT ..."

$PYTHON_EXEC $RECEIVER_SCRIPT \
  --connect tcp://$TARGET_IP:$PORT \
  --ack-bind tcp://*:$ACK_PORT \
  --headless \
  --save-dir ./received_data \
  --latency-log logs/latency.csv \
  --max-frames 3000 \
  --print-every 30 \
  --print-data-every 120
