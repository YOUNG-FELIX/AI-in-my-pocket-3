# 已排除的路径

每一条都做过实测或有据可查的排除。不是「我觉得不行」。

---

## 一、机身屏幕显示文字 —— 不可行

**用户想做的**：AI 回复的文字直接显示在 Pocket 3 那块 2.0 英寸触摸屏上。

### 为什么不行

| 条件 | 状态 |
|---|---|
| 浏览器 / WebView | ❌ 没有 |
| 开发者模式 / ADB | ❌ 没有 |
| App 安装 / sideload | ❌ 没有通道 |
| 固件升级 | 只能走 DJI Mimo App OTA，**DJI 对 Pocket 3 不发任何固件包** |
| DJI SDK 的 UI API | 四个 SDK 全都不含「在相机屏上画 UI」 |

能 sideload APK 的是**带屏遥控器**（那是 Android），不是手持相机。

### 但不是「物理不可能」

这点要说清楚。DJI 相机**确实有**屏幕底层控制：

```
t dji lcd bklight set 100        背光
t dji lcd channel switch 1 40 0 320 320 1    建 OSD 窗口 (id,x,y,w,h,OSD_Enable)
t dji lcd reg write addr value num            直接写 LCD 寄存器
t dji gui fw osdwin [id][x][y][w][h]          建 OSD 窗口
t dji gui fw base_addr [x][y][w][h]           读写帧缓冲
t dji gui tp [finger] n pairs{[x][y][action]} 模拟触摸
t dji gui key [code][action][value]           模拟按键
```

完整命令表在 [`reference/`](reference/)（Gimbal / dji / gui / lcd / log / tp / usb）。
来源：`drozdovoleg/DJI-Osmo-Action-command-research`。

### 但那是 Ambarella 平台

这些命令是 **Ambarella RTOS 的 AmbaShell**（2019 款 Osmo Action）：
SD 卡根目录放 `autoexec.ash`，开机执行 `t dji` 命令进 shell。

**Pocket 3 上这个入口不存在。** 三轮 `autoexec.ash` 实验（v1 只读 / v3 自证 / v4 路径修正）**零可观测效果**：
- v3/v4 用 `t dji rename c:\X.TXT c:\Y.TXT` 造自证标记 → 文件名没变
- v3 里裸调 `t dji Gimbal State` / `ZoomCtrl` / `mode`（都需参数）→ 用户观察到「云台展开变慢」，
  是脚本被执行的弱证据，但**没有任何可观测输出**

所以准确表述是：**Pocket 3 上该路径【未验证】，不是【已证伪】**。但三次实验都没走通。

---

## 二、机身跑第三方代码 —— 不可行

| 路径 | 结论 |
|---|---|
| **root / adb / su** | 零证据。Pocket 3 是 RTOS，不是 Linux/Android |
| **chroot 装 Debian** | 需要 `CAP_SYS_CHROOT`，Pocket 3 不是 Linux 内核 |
| **刷通用 Linux/Android** | 物理不可能 |

### 最硬的卡点

**DJI 对无人机公开固件 zip，对 Pocket 3 一个固件包都不发。**

官方下载页只有说明书 / QSG / Release Notes / LUT / Mic 2 固件；相机固件只走 Mimo OTA。
⇒ **连样本都拿不到**，无从解包。

再加上 `o-gs/dji-firmware-tools` README 原文：「private keys are rarely available」（IM*H 非对称签名）。

### 「Action 能跑项目」的真相

用户提到的先例是 `drozdovoleg/DJI-Osmo-Action-command-research`（2019，13★）：
2019 款 Osmo Action 是 Ambarella RTOS + 完整 AmbaShell，`ver.txt` 写
`Built amba_ssp_app.elf by jenkins@rdcmp63-in at Fri Sep 27 17:05:01 CST 2019`。

**Ambarella 平台特产，Pocket 3 零证据。**

### 固件解包尝试全失败

- issue #424 有人贴 hexdump 声称是 Pocket 3 固件 → 维护者打假：「**PM430 is Matrice 30, not Pocket 3.**」
- issue #123 Osmo Action 只做到认出 xV4 + LZ4，**七年未解、仍 open**
- 仓库搜 `AC004+dji` / `pocket 3 firmware dji` / `osmo pocket firmware` → **total_count 全 0**

### SoC 定不出来，且有据可查

FCC ID **2ANDR-PP1012023**（2023-10-18，Type Designation PP-101）的
Block Diagram / Schematics / Operational Description / **Software Security Description** / Parts List
**全部「Metadata only」**。Internal Photos 已下载放大解析，丝印不可辨。

中文拆机全部止步于「看用料做工」，官方规格页全文无「处理器/芯片/CPU」。

⚠️ **对比**：用户看到的 chroot 案例是 **Action 6 / QCS8625（高通）+ Android**。
Pocket 3 **不是高通、不是 Android**。两个平台没有可比性。

---

## 三、图像参数（曝光/白平衡/对焦）—— 硬件不暴露

三个接口全部试过：

| 接口 | 结果 |
|---|---|
| `IAMVideoProcAmp` | ❌ **不支持**（`-2147467262`）⇒ 亮度/对比度/色相/饱和度/锐度/伽马/白平衡/背光/增益全没有 |
| `IAMCameraControl` 的 Exposure/Iris/Focus | ❌ `GetRange` 报「找不到元素」 |
| UVC 厂商扩展单元（XU） | ❌ Pocket 4 有 Unit 6（GUID `41769EA2-04DE-E347-8B2B-F4341AFF003B`），**Pocket 3 没开** |

XU 四种寻址变体全试过：
- `IKsPropertySet::Get(Set=XU)` → `-2147023726 属性集不存在`
- `IKsControl::KsProperty(Set=XU, GET)` → 同上
- `GET|TOPOLOGY` → `-2147023112 用户缓冲区无效`
- `KSPROPSETID_VIDCAP_EXTENSION_UNIT` + inst → 同上

OpenCV `CAP_PROP_*` 23 项：16 项 `not implemented`，只有 PAN/TILT 真生效。

**替代方案**：软件后处理（`grade` 工具的 gamma/contrast/brightness/saturation）。

---

## 四、手势控制 / 语音控制 —— Pocket 3 没这功能

手册 + FAQ **全文无相关条目**。网上说法是把它与 **Pocket 2 / Osmo Action** 搞混。

---

## 五、RNDIS 网络控制 —— 相机没 IP 栈

文件传输模式下 RNDIS 网卡 `以太网 3` 状态 Up、LinkSpeed 426 Mbps、IP `169.254.x.x/16`。

但：
- `arp -a` **零邻居**（只有 224.x / 255.255.255.255 静态项）
- `ping` 对 `169.254.1.1` / `169.254.0.1` / `169.254.182.1` 等全部无响应
- DHCP 续租报「没有为适配器启用 DHCP」

⇒ **相机 RNDIS 侧没有 IP 栈**。之前 ping 通 `192.168.2.1` 是校园网里真有那台机器，与相机无关。

---

## 六、蓝牙控制云台/拍摄 —— 只读

**这是最反直觉的一条**，但实测很干净：

| 测试 | 结果 |
|---|---|
| 虚拟摇杆 `04/01` | 4 个中继地址 × 2 方向 = **8 次全部 0.00° 位移**，遥测全程活（362 帧） |
| 拍照 `02/01` | `e0` |
| 录像 `02/02` | `e0` |
| 切模式 `02/E1` | `e0` |
| Camera 模块 `0x01` | 完全静默（接收方不存在） |
| Gimbal 模块 `0x03` | 完全静默 |

`cmd_type` 试过 `0x00`(PUSH) 和 `0x40`(REQUEST)，PUSH 全静默，REQUEST 只有 DM368 回 `e0`。
**Video 模式 + SD 卡插好 + 79 分钟可录，照样全拒。**

`tmp/media_proto.md:726` 的警告与我们症状一致：
> Works on the Nano and Pocket 3. **On an Xtra Edge Pro commands to receiver `0x01` get no reply at all**,
> while pairing, wake and status pushes work normally.

但我们测的就是 Pocket 3，且 `0x28`/`0x48`（DM368）规范地回 `0xC0` + 正确回显 ⇒ **协议栈健康，`e0` 是业务层拒绝**。

**能做的**：配对（`07/45`）、注册（`00/88`）、订阅（`00/99`）、读遥测（`02/80` / `02/DC` / `04/05`）。

---

## 七、UVC 与无线控制互斥

「UVC 还开着时去连 Mimo 会超时；退出 UVC 后 Mimo 才恢复。」

⇒ 想要 WiFi DUML 拍摄命令，就得退 UVC，画面和云台随之消失。二选一。

---

## 八、云台原生缓动 —— 固件不插值

`Pan 0→120` 单次跳转后**首帧位移 153.5px，第二帧立即 0.0–0.3px**。
⇒ 瞬间到位，没有 easing 过程。丝滑必须靠外部逐帧下发。见 [MOTION.md](MOTION.md)。

---

## 九、相机扬声器 —— 哑的

USB 音频输出端点（`USB\VID_2CA3&PID_0023&MI_02`）能打开、能接受数据，但**机身喇叭不出声**。

对照实验（归一化互相关 gain）：

| 输出 | gain |
|---|---|
| **PC 音箱（对照组）** | **0.1864** |
| 相机 MME idx4 | 0.0227 |
| 相机 WDM-KS idx28 | 0.0322 |
| 相机 WDM-KS idx31 | 0.0795 |
| 静音基线 | 0.0000 |

相机各端点 = 噪声级，比对照组低 2~24 倍。

⚠️ 教训：早先「播放已实测成功 → Pocket 3 能做双向语音对话」是**误判**——
只证明流能打开，没验证发声。与 UVC pan 回读「set 返回 True 不代表动了」是同一个坑。

**替代方案**：声音从电脑出（用户本来就在电脑前，更清楚）。

---

## 排除方法论

1. **不信设备回读**：`Set` 返回成功 ≠ 真动了。用**前后帧画面比对**判定（这是 `uvc-ptz-camera-mcp` 的核心设计）。
2. **要有对照组**：扬声器测试先测 PC 音箱，证明方法本身能测到真声音。
3. **否定结论要复核**：`cv2.phaseCorrelate` 测 mp4 得「97% 帧停滞」是假结果，换列均值互相关才发现是连续的。
4. **别在没实测前宣布「物理不可行」**：Action 的 AmbaShell 先例足以推翻我的否证。
5. **危险值空间不 sweep**：`02/E1` 会冻死相机。
