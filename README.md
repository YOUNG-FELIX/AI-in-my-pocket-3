# AI in my pocket 3

把 **DJI Osmo Pocket 3** 变成一台能被大模型调用的相机。

大模型看得到画面、转得动云台、说得出话、听得懂你说什么，还能把回复打在视频上。
云端 LLM 用 API key 接进来，局域网里任何设备打开一个网址就能用。

```
你说 → 相机麦克风 → 中文识别 → 云端 LLM → 调工具 → 云台转过去
                                        ↓
                                看一眼画面 → 描述给你 → 说话 + 视频上打字幕
```

---

## 功能全清单

### 一、视频取流

| 功能 | 细节 |
|---|---|
| 实时预览 | 浏览器面板持续刷新，约 75ms 一帧 |
| 分辨率 | `1280×720` / `1920×1080` / `3840×2160` |
| 竖屏 | `720×1280` / `1080×1920` 独立 profile（不是旋转，是相机随机身姿态切换） |
| 帧率 | 1080p 稳定 **30fps**；4K MJPG 约 **13fps** |
| 像素格式 | MJPG（唯一可靠）。H.264 在 OpenCV 下打不开；YUY2/NV12 会被静默降级回 MJPG |
| 抓帧 | `see` 工具出 JPEG，带清晰度（拉普拉斯方差）与平均亮度 |
| 4K 抗残缺 | 前 4 帧解码不全（只有左上角有内容），自动等到 `nonblack > 95%` 才返回 |
| 录像 | `record` 工具录 N 秒到 mp4（`mp4v`，1080p 30fps） |
| 画面下载 | `/snap` 直接给 JPEG 字节 |

### 二、云台控制

| 功能 | 细节 |
|---|---|
| **Pan**（左右） | 绝对角度 `-35° ~ +215°`，正数向右。**真驱动机身** |
| **Tilt**（上下） | `-90° ~ +90°`，正数=镜头向下 |
| **Roll** | `-30° ~ +30°` |
| **Zoom** | `100 ~ 400`（100 = 1.0×，400 = 4.0×） |
| 绝对定位 | `look` / `jump` 一次给目标角度 |
| 相对移动 | `look_rel` 给增量 |
| **丝滑运动** | 60Hz 逐帧下发目标位置，变异系数 **0.155**。见 [docs/MOTION.md](docs/MOTION.md) |
| 可调速度 | `speed` 参数（°/s），默认 12（最优带） |
| 回中 | `center` |
| 扫描 | `scan` 沿 pan 轴扫一段范围 |
| **姿态真值** | 蓝牙遥测 `04/05` 给真实云台四元数，`yaw = pan − 60.3°`，1:1 跟随 → 可做真闭环 |
| tilt 锁定 | `hold` 持续重写 tilt 值，抵消 follow 模式回弹 |
| 角度越界 | 自动截断到硬件边界，不会损坏 |

### 三、视觉 / AI 看画面

| 功能 | 细节 |
|---|---|
| **AI 看画面** | `see` 抓帧后**图片直接进模型上下文**，问「你看到什么 / 我穿什么颜色」能答 |
| 图片压缩 | 自动缩到长边 ≤768px、JPEG q72，token 成本降约 80% |
| 文本模型降级 | 接的是纯文本模型时自动只给 JSON 元数据，不报错 |
| **人脸检测** | YuNet（`yunet_2023mar.onnx`，228KB），返回最大脸的框 + 置信度 |
| **人脸追踪** | `track` 开启后 15Hz 比例控制，`dpan = cx × 40`、`dtilt = −cy × 30`，死区 0.4° |
| 追踪判据 | 不信设备回读，用**前后帧比对**判定是否真动了 |

### 四、语音

| 功能 | 细节 |
|---|---|
| **录音** | 走相机麦克风（`OsmoPocket3` USB 音频），48kHz 立体声 |
| **中文识别** | faster-whisper `small`，VAD 过滤，`language='zh'` |
| 识别速度 | 模型加载 ~2.5s，1 秒音频转写 ~0.5s |
| **TTS 说话** | Windows SAPI，中文，可调语速 |
| 语音输入 | Web 面板「🎤」按钮：录 4 秒 → 识别 → 自动发给 AI |
| 多模态对话 | 听 → 想 → 转云台/看画面 → 说 → 打字幕，一个回合自动跑完 |
| ⚠️ 扬声器 | **相机喇叭不出声**（实测增益 0.006~0.079 = 噪声级），声音从电脑出 |

### 五、画面上的字幕

| 功能 | 细节 |
|---|---|
| **中文渲染** | PIL + CJK 字体（微软雅黑/黑体，多级回退） |
| 自动换行 | 按画面宽度 86% 逐字符断行 |
| 半透明底 | 圆角底板 + 白字，底部居中 |
| 显示时长 | `seconds` 参数，到时自动消失 |
| 字号 | 自适应画面高度（约 4.2%） |
| 全出口生效 | 预览、抓帧、录像、追踪模式下都带字幕 |

### 六、软件画质（硬件不给，自己补）

| 参数 | 范围 | 说明 |
|---|---|---|
| `gamma` | 0.3 ~ 3.0 | 提亮/压暗 |
| `contrast` | 0.0 ~ 2.0 | 线性对比 |
| `brightness` | −100 ~ 100 | 亮度偏移 |
| `saturation` | 0.0 ~ 2.0 | HSV 的 S 通道 |

> 硬件层的曝光/快门/ISO/白平衡/对焦/锐度/降噪**全部不暴露**，只有这四个能做。

### 七、大模型接入

| 功能 | 细节 |
|---|---|
| **填 API key** | `POST /api/agent/key`，**不用重启**，随时换 |
| 支持厂商 | DeepSeek / Kimi（月之暗面）/ 智谱 / 阿里百炼 / OpenAI 兼容 |
| **AI 自主调工具** | function-calling 多轮循环（最多 4 轮），AI 自己决定调哪个 |
| 视觉多模态 | 图片随工具结果进上下文 |
| 工具 schema | 自动从服务端工具表生成，加工具不用改 agent |
| 连通性探测 | 填 key 时返回 `reachable`，不用猜有没有接上 |
| 对话历史 | 保留最近 20 条，跨回合记忆 |
| **mock 模式** | 没 key 也能跑通全链路（确定性假模型，用于测试） |

### 八、HTTP API（不走 LLM 也能用）

```
GET  /api/tools                       工具清单（含参数表与文档）
GET  /api/call?name=look&pan=60       直接执行任一工具
GET  /api/status                      位置 / fps / 能力边界 / 追踪状态
GET  /snap                            当前帧 JPEG
GET  /stream                          MJPEG 连续流
POST /api/agent/key                   设/换 API key
POST /api/agent/chat                  让 LLM 决定并执行
GET  /                                Web 面板
```

支持 `?key=` 或 `X-Api-Key` 鉴权。绑 `0.0.0.0` 后局域网任何设备可访问。

### 九、Web 面板

- 实时画面持续刷新
- 云台按钮：◀◀ ◀ ▶ ▶▶ / ▲ ▼ / 变焦 ± / ⌂ 回中
- 人脸追踪、tilt 锁定开关
- **对话抽屉**：打字或按 🎤 说话，看 AI 回什么、调了哪些工具
- 状态栏：fps / pan / tilt / zoom / 在线状态

---

## 功能边界：做不到什么

| 能力 | 为什么不行 |
|---|---|
| **拍照 / 录像 / 切模式** | 蓝牙只读，须走 WiFi DUML（UDP 9004）。代码已备好，见 [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| **机身屏幕上显示文字** | RTOS 封闭 UI，无浏览器 / WebView / App 安装通道。DJI 相机确实有 `lcd`/`gui`/`tp` 底层命令（能建 OSD 窗口、模拟触摸），但那是 Ambarella 平台（2019 款 Osmo Action）。Pocket 3 上三轮 `autoexec.ash` 实验零可观测效果 |
| **机身跑第三方代码** | DJI 对 Pocket 3 **不公开任何固件包**（只走 Mimo OTA），连样本都没有；再加非对称签名。FCC 文档全部「Metadata only」 |
| 曝光 / 快门 / ISO / 白平衡 / 对焦 | `IAMVideoProcAmp` 接口不支持；UVC 厂商扩展单元（Pocket 4 有 Unit 6，Pocket 3 这个固件没开） |
| 手势控制 / 语音控制 | Pocket 3 **没这功能**（是 Pocket 2 / Osmo Action 的，常被搞混） |
| 云台原生缓动 | 固件不插值，单条指令瞬间到位。丝滑必须靠外部逐帧下发 |
| RNDIS 网络控制 | 链路本地地址、零 ARP 邻居、无 IP 栈 |

完整证据链见 [docs/LIMITS.md](docs/LIMITS.md)。

---

## 快速开始

需要：Windows 10/11、Python 3.9+、DJI Osmo Pocket 3、USB-C 线。

### 1. 装依赖

```
pip install opencv-python-headless numpy pillow comtypes pygrabber sounddevice faster-whisper
```

### 2. 把相机切到「网络摄像头」模式

插 USB → 相机弹三选一 → 选 **网络摄像头**。

| 相机弹的选项 | 你会得到 | 会失去 |
|---|---|---|
| **网络摄像头** | ✅ 画面 + 云台控制 | SD 卡 |
| 文件传输 | SD 卡里的文件 | 画面、云台 |
| 取消 | 相机留在拍摄模式 | USB 完全不枚举 |

### 3. 起服务

```
python p3server.py --port 8770 --key 你设的密码 --host 0.0.0.0
```

### 4. 打开面板

浏览器开 `http://<电脑IP>:8770/?key=你设的密码`

### 5. 接大模型

```bash
curl -X POST http://127.0.0.1:8770/api/agent/key \
  -H 'Content-Type: application/json' \
  -d '{"provider":"deepseek","key":"sk-你的key"}'
```

然后对话：

```bash
curl -X POST 'http://127.0.0.1:8770/api/agent/chat?key=你设的密码' \
  -H 'Content-Type: application/json' \
  -d '{"text":"转过去看看右边有什么"}'
```

AI 自己决定转哪、看哪、怎么说。详细步骤见 [docs/QUICKSTART.md](docs/QUICKSTART.md)。

---

## 目录

```
AI-in-my-pocket-3/
├── README.md              ← 你在看的这个
├── p3server.py            主服务：相机 + 14 工具 + AI 端点 + Web 面板
├── agent.py               AI 层：function-calling 循环 + 视觉
├── duml.py                DJI DUML 协议编解码（CRC 已破解）
├── p3ctl.py               UVC 控制库
├── yunet_2023mar.onnx     人脸检测模型（228KB）
├── docs/
│   ├── QUICKSTART.md      逐步上手
│   ├── MOTION.md          云台平滑度（踩坑踩出来的参数）
│   ├── PROTOCOL.md        DUML / UVC / BLE 协议实测
│   ├── LIMITS.md          已排除的路径 + 证据链
│   └── REFERENCE.md       现有开源项目对照
└── reference/             AmbaShell 全命令表（DJI 相机逆向参考）
```

---

## 它是怎么工作的

```
┌──────────────────────────────────────────────────────────┐
│  p3server.py  (独占相机，一个进程)                         │
│                                                          │
│   UVC/DirectShow ──┬─→ 帧 ──→ JPEG ──→ /snap  浏览器     │
│                    │        └─→ PIL 字幕叠加              │
│                    │        └─→ YuNet 人脸检测 → 云台闭环 │
│                    │                                      │
│                    └─→ IAMCameraControl ──→ 云台 Pan/Tilt │
│                                                          │
│   sounddevice ──→ 相机麦克风 ──→ faster-whisper ──→ 文字  │
│   Windows SAPI ──→ 电脑音箱                                 │
│                                                          │
│   HTTP /api/tools  /api/call  /api/status  /api/agent/*   │
└──────────────────────────────────────────────────────────┘
                          ↑
                    agent.py
                          ↑
              云端 LLM (DeepSeek / Kimi / 智谱 / ...)
```

**关键设计**：相机只能被一个进程打开，所以所有能力收在一个服务里，AI 和浏览器都通过 HTTP 调用。
这样既能让 LLM 走 function-calling，也能让你手动点按钮，两边不打架。

---

## 已知坑（照着省一晚上时间）

1. **UVC 控制回读会骗人**。`IAMCameraControl.Set` 返回成功**不代表真的动了**，pan 轴尤其明显
   （`set 100` 后回读可能还是 0）。必须用**前后帧画面比对**判定。tilt 轴可靠，pan 轴不可靠。

2. **摄像头索引不能假设是 0**。笔记本内置摄像头也自报 1920×1080。
   正确判法：请求 3840×2160，看**实际返回帧尺寸** ≥2560×1440 才是 Pocket 3。

3. **4K 前 4 帧是残缺解码**（只有左上角有内容，其余全黑），第 5 帧起才正常。

4. **相机麦克风必须 48000 Hz**。44100 / 16000 会报 `PaErrorCode -9997`。

5. **相机扬声器是哑的**。USB 音频输出端点能打开、能收数据，但机身喇叭不出声。声音从电脑出。

6. **UVC 模式与 Mimo/WiFi 控制互斥**。要 DUML 拍摄命令就得退 UVC。

7. **中文 body 用 `curl -d` 在 MSYS bash 里会传坏**（服务端回 `bad json`）。用文件 + `--data-binary @file`。

8. **蓝牙是只读的**。能读遥测、能订阅参数，但云台指令和拍摄指令一律无效（实测 4 个地址 × 2 方向 = 8 次全零位移）。

---

## 协议研究

`duml.py` 是完整可用的 DJI DUML 编解码器：

- **帧头 CRC8**：poly `0x8C`，init `0x77`，反射，xorout 0
- **帧尾 CRC16**：CRC-16/X.25，poly `0x8408` 反射，**种子 `0x3692` 必须作寄存器初值**（不是最后 xor）
- 自测：8/8 参考帧校验通过，5/5 帧重建字节级一致

`reference/` 是 DJI 相机 AmbaShell 全命令表（60+ 模块），包括 `lcd`/`gui`/`tp`/`usb`/`Gimbal` 等——
目前公开资料里对 DJI 相机底层最完整的命令参考。

细节见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。

---

## 许可

MIT
