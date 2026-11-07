# 现有开源项目对照

做这个项目前调研过的项目，按对你可能有用的程度排。star 数为 2026-11-08 GitHub API 实测。

---

## 直接相关：Pocket 3 / DJI Osmo 控制

| 项目 | star | 能给什么 |
|---|---|---|
| **[`AbhijatSaxena/uvc-ptz-camera-mcp`](https://github.com/AbhijatSaxena/uvc-ptz-camera-mcp)** | 0 | **最相关**。MCP server，18 个工具，README 参考设备正是 DJI Osmo webcam `VID_2CA3/PID_0023`。核心设计：**不信设备回读，用前后帧比对判定是否真动了**（阈值 0.364）。`aim` 参数名是 `to` 不是 `value`。需 Python ≥3.10 + ffmpeg |
| [`Evan931001/Pocket-3-4-4P-PTZ`](https://github.com/Evan931001/Pocket-3-4-4P-PTZ) | 8 | Pocket 3 专用 Windows PTZ 控制台，DirectShow + NDI + OBS 输出 + Preset |
| [`yigitkonur/lib-osmo-ble`](https://github.com/yigitkonur/lib-osmo-ble) | 49 | BLE DUML 完整逆向。README 明写 **Pocket 3 会静默忽略纯 BLE 云台指令**——与我们实测一致 |
| [`datagutt/node-osmo`](https://github.com/datagutt/node-osmo) | 84 | BLE 设参数 + RTMP 推流 |
| [`drozdovoleg/DJI-Osmo-Action-command-research`](https://github.com/drozdovoleg/DJI-Osmo-Action-command-research) | 13 | **AmbaShell 全命令表**（我们抄了 7 个 txt 到 [`reference/`](reference/)）。2019 款 Action = Ambarella RTOS |
| [`CunningLogic/DUMLRacer`](https://github.com/CunningLogic/DUMLRacer) | 160 | DJI 无人机 root exploit（更新系统竞态）。要求设备跑 Android，Pocket 3 不适用 |
| [`hdnes/pyduml`](https://github.com/hdnes/pyduml) | 60 | DUML 协议工具，自动建 RNDIS 网络做 FTP 传输 |
| [`jezzab/DUMLdore`](https://github.com/jezzab/DUMLdore) | 315 | 固件刷写，含「ADB ENABLE: Instantly enable ADB root shell」。兼容 P4/Mavic/Spark 等无人机 |
| [`o-gs/dji-firmware-tools`](https://github.com/o-gs/dji-firmware-tools) | — | **DUML CRC 参照实现**（`comm_dat2pcap.py`）。我们就是从这里破解了帧校验 |

**DJI 官方**：[`developer.dji.com`](https://developer.dji.com/) **没有 Osmo SDK 入口**。
`dji-sdk` 组织 54 仓库里 Osmo 相关只有 `Osmo-GPS-Controller-Demo`（支持 Osmo 360 / Action 6 / 5 Pro / 4，**不含 Pocket 3**）。
`pc-sdk/` → 404。

---

## 工具化范式参考

用户推荐参考的两个项目，看懂它们怎么做「量化的工具化、系统化」：

### [`morluto/rea`](https://github.com/morluto/rea) — Reverse Engineer Anything

MCP server，207 个工具（数字来自传言，官方称目录动态变化），tool schemas 约 780 KB。

**核心思路**：工具集分层 + 按需加载 + 网关折叠，而非把所有 schema 一次性塞给模型。
`rea-gateway` 把 207 工具折成 `rea_list` / `rea_call` 两个入口，token 从 780KB → 1–2KB。

分析面覆盖：原生二进制 / JS / Electron / .NET / 网站 / **Firmware**（Binwalk / Unblob on Linux）。

衍生：[`czcdr/dsh-plugin-rea`](https://github.com/czcdr/dsh-plugin-rea)（★78，DSH 桥接）、
[`sqwu/rea-gems`](https://github.com/sqwu/rea-gems)（★56 案例库）。

### [`storytold/craft-anything`](https://github.com/storytold/craft-anything) — Craft Anything

Rust，MIT/Apache-2.0，★20139，v0.5.0 early alpha。
"看懂任意软件再让 AI 复刻"的 clean-room 逆向 + 复刻引擎，**约 302~320 个工具既是 CLI 又是 MCP 工具**。

**三个值得抄的设计**：

1. **统一 `ToolSpec` 注册** — `<area>_<object>_<action>` 命名，`cargo xtask tools` 生成式目录
   （`docs/tool-ids.md`、`docs/extending.md`）
2. **两层网关折叠** — `tools/list` 默认只列 Essentials + `list_toolsets`/`enable_toolset` 渐进披露；
   结果 summary-first + `core_evidence_browse` 分页 + 80KB 响应预算（`crates/mcp/src/lib.rs`）
3. **逐工具量化验证矩阵** — `docs/tool-status.md`：Verified/Partial/Fixed/Unverified + 真实二进制前后指标，
   `doctor` / `adb_device_features_list` 能力探测 + `standin.json` 录制回放测试

**事实核查**：REA 与 Craft Anything **不是姊妹项目**（两 README 零互引、不同作者），属竞合互补：
REA = 分析取证，CA = 分析 + 复刻闭环。

### 我们抄了什么

本项目 `p3server.py` 的 14 个工具就是按这个范式组织的：

| Craft Anything 的做法 | 本项目 |
|---|---|
| 两层网关折叠 | `/api/tools` 返回带参数表与文档的完整清单，而不是把 14 个 schema 全塞进模型 |
| `ToolSpec` 命名 + 注册表 | `@tool(name, category, params, doc)` 装饰器注册进 `TOOLS` |
| 能力探测 `doctor` | `/api/status` 返回范围边界（pan −35..215 / tilt −90..90 / zoom 100..400） |
| summary-first | `see` 返回元数据（清晰度/亮度/尺寸），大结果不塞文本 |
| 逐工具验证矩阵 | [LIMITS.md](LIMITS.md) 记了每个能力的实测证据 |

---

## 工具链

| 项目 | 用途 |
|---|---|
| [`rizinorg/cutter`](https://github.com/rizinorg/cutter) | ★20175，Rizin 的 RE GUI 平台。做固件/二进制分析的入口 |
| [`pbatard/libwdi`](https://github.com/pbatard/libwdi) | Zadig 的底层库，绑 WinUSB 用 |
| [`opencv/opencv_zoo`](https://github.com/opencv/opencv_zoo) | YuNet 人脸检测模型（`face_detection_yunet_2023mar.onnx`） |

---

## 想要但没找到的

调研过，**明确不存在**（2026-11-08）：

- DJI 官方 MCP server
- DJI 官方 function-calling / LLM 集成
- Pocket 3 + LLM 语音对话 demo
- Pocket 3 固件解包工具（issue #424 被打假、#123 七年未解）
- 用 USB 命令把 Pocket 3 切进 UVC 模式的方法（只能机身菜单选 Webcam）

---

## 下载注意事项

踩过的坑，省你时间：

| 源 | 坑 |
|---|---|
| `raw.githubusercontent.com` 直连 | 直连超时；大文件走 LFS 会拿到 131 字节指针文件 |
| `codeload.github.com/.../zip` | 会截断（下到 2.5MB 但 unzip 报 `End-of-central-directory signature not found`） |
| `github.com/.../releases/download/...` | 被限速到 ~5KB/s |
| **`gh-proxy.com/` 前缀** | ✅ 可用 |
| **`hf-mirror.com`** | ✅ OpenCV 模型从这里下最快 |
| `api.github.com` | ✅ 直连可用（搜索仓库用它） |
| `dji.retroroms.info` | ❌ 不可达（curl 000） |
