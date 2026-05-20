# Edge-to-Cloud Sensor Streaming Demo

这个 Demo 用于验证 **车端到云端的 6 路相机 + 1 路 LiDAR 数据传输链路**。  
当前版本已经完成了 **基于文件回放的稳定联调**，并保留了后续对接 **实际部署数据流** 的接口与结构。

---

## 1. 当前阶段与后续阶段

### 当前阶段：文件回放验证
当前代码主要用于 **联调与链路验证**，输入是：

- 6 张图像文件（`cam0.jpg` ~ `cam5.jpg`）
- 1 个 LiDAR 文件（例如 `sample.pcd`）

发送端周期性读取这组固定文件，编码后通过 ZMQ PUB/SUB 发往接收端。  
这一阶段的目标是验证：

- 多 part ZMQ 协议是否正确
- 6 路图像 + 1 路 LiDAR 是否能稳定传输
- ACK 回传链路是否正常
- sender 端 RTT / oneway 估计是否正常
- receiver 端统计、日志、raw 调试输出是否正常

### 后续阶段：对接实际部署数据流
后续真正部署时，输入不再是文件，而是：

- 连续相机帧流
- 连续 LiDAR 数据流 / 点云流 / packet 流

也就是说，当前版本是 **验证通信链路与协议正确性** 的 demo；  
后续只需要把 sender 的输入部分从“文件读取”替换为“真实传感器数据输入”，而发送协议、ACK 机制、监控逻辑、日志结构都可以继续沿用。

---

## 2. 当前代码结构

- `sender.py`  
  车端发送端。当前支持：
  - 文件回放模式（当前验证主模式）
  - 预留的多相机设备输入模式（后续可接实机）

- `receiver.py`  
  云端 / 监控端接收端。当前支持：
  - ZMQ 接收 6 路图像 + 1 路 LiDAR
  - ACK 回传
  - oneway / machine clock diff 统计
  - raw multipart 调试输出
  - CSV 日志记录
  - headless 模式和可视化模式

- `sender.sh`  
  文件回放模式下的发送启动脚本

- `receiver.sh`  
  文件回放模式下的接收启动脚本

---

## 3. 当前协议说明

当前 sender 使用 ZMQ multipart message，固定格式如下：

- `part 0`: topic bytes，默认 `b"sens"`
- `part 1`: msgpack header
- `part 2..7`: 6 路 JPEG 图像
- `part 8`: LiDAR 原始字节

### header 主要字段
当前 header 中包含：

- `ver`
- `label`
- `ts_unix`
- `frame_id`
- `cam_count`
- `img_fmt`
- `img_size`
- `lidar_len`
- `lidar_name`
- `sender_last_rtt_ms`
- `sender_last_oneway_ms`

其中：

- `sender_last_rtt_ms`：发送端根据 ACK 计算得到的最近一次 RTT
- `sender_last_oneway_ms`：发送端估计的最近一次单程延时（`RTT / 2`）

---

## 4. 当前验证模式：文件回放

### 4.0 依赖安装

```bash
pip install pyzmq opencv-python msgpack numpy
```

### 4.1 输入准备
在 sender 所在目录准备以下文件：

- `cam0.jpg`
- `cam1.jpg`
- `cam2.jpg`
- `cam3.jpg`
- `cam4.jpg`
- `cam5.jpg`
- `sample.pcd`

### 4.2 启动 receiver
在接收端机器上运行：

```bash
bash receiver.sh
```

当前 `receiver.sh` 默认配置为：

- 从 sender 的 `3721` 端口接收数据
- 在本机 `2137` 端口提供 ACK 回传
- 使用 `--headless`
- 保存接收到的图像 / LiDAR 到 `./received_data`
- 保存统计日志到 `logs/latency.csv`
- 每 30 帧打印一组统计
- 每 120 帧打印一次 raw multipart 信息

修改sh脚本中的IP地址为对应接收机：

- `TARGET_IP`：sender IP

一般不需要修改 Port：
- `PORT`
- `ACK_PORT`

### 4.3 启动 sender
在发送端机器上运行：

```bash
bash sender.sh
```

当前 `sender.sh` 默认配置为：

- 从本地文件读取 6 张图像和 1 个 PCD
- 通过 `tcp://*:3721` 发布数据
- 连接 receiver 的 ACK 地址 `tcp://<receiver_ip>:2137`
- `fps = 10`
- `label = car_A`
- 每 30 帧打印发送摘要
- 每 120 帧打印一次 payload summary
- 每 30 个 ACK 打印一次 RTT 统计

修改脚本中的IP地址为接收机的IP地址：
- `ACK_TARGET_IP`

---

## 5. 当前输出说明

### 5.1 sender 终端输出
sender 主要输出三类信息：

#### 发送摘要
例如：

```text
🚀 [sender] Sent Frame ID: 1200 | Time: 12:16:08
```

#### payload summary
例如：

```text
[sender] payload summary | cams=6 | img_size=640x384 | lidar_bytes=1481906 | last_rtt_ms=88.5 | last_oneway_ms=44.3
```

如果 ACK 断开超过超时阈值，`last_rtt_ms` 与 `last_oneway_ms` 会自动清空为 `N/A`。

#### RTT 统计
例如：

```text
[sender][RTT Stats] ACKs=300 | avg_rtt=92.1ms | p95_rtt=110.4ms | last_oneway=45.7ms
```

---

### 5.2 receiver 终端输出
receiver 当前不再逐帧打印简短状态，而是保留两类信息：

#### 统计信息
例如：

```text
[receiver][stats] avg_oneway=50.4ms p95_oneway=60.1ms last_oneway=38.5ms
[receiver][stats] avg_machine_clock_diff=28.0ms p95_machine_clock_diff=49.8ms
```

说明：

- `avg_oneway`：当前统计窗口内单程估计延时均值
- `p95_oneway`：当前统计窗口内单程估计延时的 95 分位
- `last_oneway`：最近一次 one-way 值
- `avg_machine_clock_diff` / `p95_machine_clock_diff`：两机时钟差的观测量，仅作参考监控

#### raw multipart 调试输出
例如：

```text
[receiver][raw] multipart received | parts=9 | frame_id=1588 | lidar=1447.2KB
```

这用于确认当前协议结构正常。

---

## 6. 当前日志说明

### receiver 日志：`logs/latency.csv`
当前字段为：

- `recv_ts_unix`
- `sender_ts_unix`
- `frame_id`
- `sender_est_oneway_ms`
- `machine_clock_diff_ms`
- `lidar_bytes`
- `label`

说明：

- `sender_est_oneway_ms` 来自 sender header
- `machine_clock_diff_ms = receiver_time - sender_time`
- `machine_clock_diff_ms` **不是严格网络单向时延**，只用于辅助观察

### sender 日志：可选 `--rtt-log`
如果启用 sender 的 RTT 日志，会输出：

- `ack_recv_ts_unix`
- `frame_id`
- `rtt_ms`
- `oneway_ms`
- `sender_ts_unix`
- `receiver_recv_ts_unix`
- `label`

这个日志更适合分析真实链路延时表现。

---

## 7. 关于延时指标的解释

### `sender_last_oneway_ms`
这是当前最建议关注的指标。  
它由 sender 基于 ACK 链路计算：

```text
RTT = ack_now - sender_ts
oneway ≈ RTT / 2
```

优点：

- 不依赖两台机器严格时钟同步
- 对链路监控更直接
- 比 `machine_clock_diff_ms` 更可信

### `machine_clock_diff_ms`
这是：

```text
receiver_recv_time - sender_ts
```

它会受到以下因素影响：

- 两台机器时钟偏差
- 应用层编码 / 解码时间
- 网络传输时间
- 调度与排队

所以它不应直接等同于真实单向网络时延，只应作为辅助观测量。

### `p95`
`p95` 是 95 分位数。  
意思是：95% 的样本都不超过该值。  
它通常高于平均值 `avg`，用于观察链路尾部抖动是否明显。

---

## 8. 当前验证结论

当前版本已经完成了以下验证：

- sender / receiver 协议一致
- multipart 长度固定为 9
- topic 过滤正常
- ACK 回传链路正常
- sender 端 RTT / oneway 可计算、可超时刷新
- receiver 端统计和 CSV 日志正常
- 文件回放模式下可以持续稳定运行

因此，当前这套代码可以认为已经完成了 **文件驱动的链路验证阶段**。

---

## 9. 后续如何对接真实部署数据流

后续如果切换到真实部署，不需要推翻现在的通信结构。  
主要只需要替换 sender 的输入来源。

### 9.1 相机侧
当前文件模式：

- `cv2.imread(camX.jpg)`

后续真实部署可替换为：

- 多路 USB camera
- ROS 图像 topic
- GStreamer / RTSP
- 共享内存图像缓冲

只要最终仍然整理成：

- 6 路图像
- JPEG 编码字节

后面的 multipart 发送逻辑可以保持不变。

### 9.2 LiDAR 侧
当前文件模式：

- `sample.pcd` 直接作为原始字节发送

后续真实部署可替换为：

- 实时点云帧
- 传感器驱动输出的 packet
- ROS PointCloud2 / 自定义二进制结构

只要最终整理成一段 LiDAR 字节 payload，当前协议同样可继续使用。

### 9.3 保持不变的部分
后续接真实数据流时，以下模块可以直接复用：

- ZMQ multipart 协议
- topic 机制
- ACK 回传机制
- RTT / oneway 统计机制
- receiver 端 raw / stats / logging 机制

---

## 10. 依赖安装

```bash
pip install pyzmq opencv-python msgpack numpy
```

---

## 11. 备注

1. 当前阶段推荐继续使用 **文件回放模式** 做协议和网络验证。  
2. 真实部署阶段再逐步把 sender 输入改为真实传感器流。  
3. 不建议恢复 `CONFLATE`，因为当前消息是 multipart；当前版本使用 HWM 控制队列即可。  
4. 如果后续需要更严格的端到端时延评估，可以进一步接入统一时钟同步或硬件时间戳。  
