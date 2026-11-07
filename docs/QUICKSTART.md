# 快速上手

照着做，10 分钟跑通。

---

## 0. 前置条件

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（用了 DirectShow + Windows SAPI） |
| Python | 3.9 或更高 |
| 相机 | DJI Osmo Pocket 3 |
| 线 | USB-C（数据线，不是纯充电线） |
| 大模型 | 任意 OpenAI 兼容的 API key（DeepSeek / Kimi / 智谱 / 百炼） |

---

## 1. 装依赖

```bash
pip install opencv-python-headless numpy pillow comtypes pygrabber sounddevice faster-whisper
```

装完自检：

```bash
python -c "import cv2, numpy, PIL, comtypes, pygrabber, sounddevice, faster_whisper; print('all good')"
```

> 人脸检测模型 `yunet_2023mar.onnx` 已经在仓库根目录，不用另下。
> 如果没有，从 `https://hf-mirror.com/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx` 下。
> （GitHub raw 走 LFS 会拿到 131 字节的指针文件，别用。）

---

## 2. 接上相机

**关键：必须选「网络摄像头」。**

1. 用 USB-C 线把 Pocket 3 插到电脑
2. 相机屏幕弹出三个选项
3. 选 **网络摄像头**

三种模式的区别：

| 选项 | 画面 | 云台 | SD 卡 | 用途 |
|---|---|---|---|---|
| **网络摄像头** | ✅ | ✅ | ❌ | **本项目用这个** |
| 文件传输 | ❌ | ❌ | ✅ | 拷照片视频 |
| 取消 | ❌ | ❌ | ❌ | 相机当相机用，USB 断开 |

验证接对了：

```bash
python -c "
import cv2
for i in range(4):
    c = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if not c.isOpened(): continue
    c.set(cv2.CAP_PROP_FRAME_WIDTH, 3840); c.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
    ok, f = c.read()
    print(i, '->', f.shape if ok else 'no frame')
    c.release()
"
```

**索引 1 返回 3840×2160** 就是 Pocket 3（索引 0 通常是笔记本内置摄像头，它也谎报 1080p）。

---

## 3. 起服务

```bash
python p3server.py --port 8770 --key mypassword --host 0.0.0.0
```

看到这行就是好了：

```
camera open: OsmoPocket3 1280x720
serving on 0.0.0.0:8770
```

> `--host 0.0.0.0` 是让局域网里其他设备也能访问。只本机用就写 `127.0.0.1`。

---

## 4. 打开面板

先查本机 IP：

```bash
ipconfig | findstr IPv4
```

然后手机 / 平板 / 另一台电脑的浏览器开：

```
http://<那个IP>:8770/?key=mypassword
```

你会看到：实时画面 + 云台按钮 + 对话框 + 🎤 语音按钮。

---

## 5. 接大模型

### 5.1 拿个 key

到 [platform.deepseek.com](https://platform.deepseek.com/) 或 [platform.moonshot.cn](https://platform.moonshot.cn/) 注册，创建 API key。

### 5.2 填进去

```bash
curl -X POST http://127.0.0.1:8770/api/agent/key \
  -H 'Content-Type: application/json' \
  -d '{"provider":"deepseek","key":"sk-xxxxxxxx"}'
```

返回：

```json
{"ok": true, "provider": "deepseek", "model": "deepseek-chat",
 "ready": true, "reachable": 200}
```

`reachable: 200` = key 通了。**不用重启服务，随时换 key。**

支持的 provider：

| provider | base | 默认模型 |
|---|---|---|
| `deepseek` | api.deepseek.com | deepseek-chat |
| `moonshot` | api.moonshot.cn | moonshot-v1-8k |
| `zhipu` | open.bigmodel.cn | glm-4-flash |
| `dashscope` | dashscope.aliyuncs.com | qwen-plus |
| `openai` | api.openai.com | gpt-4o-mini |

也可以 `base` + `model` 自己指定任意 OpenAI 兼容端点。

### 5.3 对话

```bash
curl -X POST 'http://127.0.0.1:8770/api/agent/chat?key=mypassword' \
  -H 'Content-Type: application/json' \
  -d '{"text":"转过去看看右边有什么"}'
```

返回：

```json
{"ok": true,
 "reply": "转过来看到一个书架，上面有几本书和一个水瓶。",
 "actions": [{"tool": "look_rel", "args": {"dpan": 45}},
             {"tool": "see", "args": {}}]}
```

**注意**：中文 body 用 `curl -d` 在 MSYS/Git Bash 里会传坏。用文件：

```bash
printf '%s' '{"text":"转过去看看右边有什么"}' > req.json
curl -X POST 'http://127.0.0.1:8770/api/agent/chat?key=mypassword' \
  -H 'Content-Type: application/json' --data-binary @req.json
```

---

## 6. 直接调工具（不过 LLM）

```bash
# 看看有哪些工具
curl 'http://127.0.0.1:8770/api/tools?key=mypassword'

# 转到 pan=60
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=look&pan=60'

# 往右转 30 度
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=look_rel&dpan=30'

# 回中
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=center'

# 拍一帧
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=see'

# 打字幕
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=caption&text=你好&seconds=5'

# 说话
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=say&text=你好呀'

# 录 5 秒
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=record&seconds=5'

# 看状态
curl 'http://127.0.0.1:8770/api/status?key=mypassword'
```

---

## 7. 语音对话

面板上按 🎤 → 录 4 秒 → 识别成文字 → 自动发给 AI → AI 回复并说话。

或者手动：

```bash
# 录 4 秒并识别
curl 'http://127.0.0.1:8770/api/call?key=mypassword&name=listen&seconds=4'
```

返回 `{"text": "转到左边看看"}`，再把文字发给 `/api/agent/chat`。

---

## 常见问题

**Q: 画面黑的**
检查是不是选了「文件传输」或「取消」。必须是「网络摄像头」。

**Q: 云台不动**
关掉别的占用相机的软件（OBS、Teams、浏览器的摄像头权限）。UVC 只能一个进程开。

**Q: 说「往右转」AI 转反了**
`pan` 正数是**镜头向右**（画面里的东西往左移）。这是硬件定义，不是 bug。

**Q: 声音出不来**
声音从**电脑音箱**出，不是相机（相机喇叭是哑的）。检查电脑音量。

**Q: AI 说看不到画面**
`see` 抓的是当前帧。如果相机被别的进程占了，服务会拿不到帧。看服务日志。

**Q: 中文乱码**
服务端 stdout 在 Windows GBK 终端会乱。启动时加 `set PYTHONIOENCODING=utf-8`。
