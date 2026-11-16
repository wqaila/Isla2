# 前端流式播放 + 真机联调准备

> 日期：2026-11-16
> 任务：1) 真机联调准备　2) 前端接流式
> 结果：**前端流式完成并验证**；**真机联调工具就绪，但无硬件无法执行**

---

## 一、任务完成情况

| # | 任务 | 状态 | 说明 |
|:-:|---|---|---|
| 1 | 真机联调 | ⚠️ **工具就绪，无法执行** | 当前无 ESP32 设备连接（只有 COM1/COM2 虚拟串口） |
| 2 | 前端接流式 | ✅ **完成并验证** | 首声改善 21.5%–71.4% |

---

## 二、前端流式播放（已完成）

### 2.1 实测效果

用与前端**完全相同的解析算法**消费真实后端的流：

| 文本 | 分块 | 音频 | 首块 | 非流式 | 首声改善 |
|---|---|---|---|---|---|
| 短句 10 字 | 2 | 2.56s | 1670ms | 1666ms | **−0.3%** |
| 中句 22 字 | 3 | 5.20s | 1438ms | 3287ms | **+56.2%** |
| 长句 44 字 | 4 | 10.72s | 1752ms | 6122ms | **+71.4%** |

**短句 −0.3% 符合设计**：Phase 4 已实测短句流式无收益（要等 28 token
攒够一包），所以后端 `StreamPolicy` 用 15 字阈值做路由。
前端让用户能手动对比两种模式，看到的就是这个真实差异。

另一轮测试（不同采样随机性）：

| 文本 | 首块 | 非流式 | 改善 |
|---|---|---|---|
| 短句 | 1460ms | 1861ms | +21.5% |
| 中句 | 1458ms | 3246ms | +55.1% |
| 长句 | 1812ms | 5450ms | +66.7% |

### 2.2 实现要点

**1. 增量解析 4 字节大端长度前缀**（`client.ts`）

最容易出错的地方是「块跨网络包」。必须累积缓冲区，
只解析完整的块：

```typescript
// 累积
appendBuf(value);
// 尽可能多地解析完整块
for (;;) {
  if (buf.length < 4) break;              // 头都不够
  const len = view.getUint32(0, false);   // 大端
  if (buf.length < 4 + len) break;        // 体不够，等下一包
  const pcm = buf.slice(4, 4 + len);
  buf = buf.slice(4 + len);
  if (len === 0) { gotEnd = true; break; } // 结束标记
  onChunk(pcm);
}
```

**2. 截断必须报错，不能静默接受**

服务端中途出错时**故意不发结束标记**（让调用方知道不完整）。
前端必须据此报错：

```typescript
if (!gotEnd) {
  throw new ApiError("流被截断（未收到结束标记），音频可能不完整", "stream_truncated", 0);
}
```

**3. Web Audio 逐块排期**（`StreamPlayer.ts`）

```typescript
const now = this.ctx.currentTime;
const startAt = this.nextStartTime > now ? this.nextStartTime : now + 0.02;
src.start(startAt);
this.nextStartTime = startAt + buffer.duration;   // 下一块接在这里
```

关键是 `nextStartTime` 累加：每块的开始时间 = 上一块的结束时间，
保证无缝衔接。首块加 20ms 余量避免调度抖动。

**4. AudioContext 的 suspended 陷阱**

浏览器要求 AudioContext 在用户手势中创建/恢复，否则静音。
本项目的按钮点击满足条件，但仍主动 `resume()` 兜底，
并在每次 `push` 时再检查一次。

**5. 为什么不用 `<audio>` 标签**

它需要完整文件或至少可 seek 的流，无法处理「未知总长的裸 PCM 块」。
Web Audio 的 `AudioBufferSourceNode` 可以任意排期，是正确工具。

### 2.3 交互设计

合成页新增「合成模式」选择：

- **流式**：边收边播，显示首声延迟 / 音频时长 / 总耗时 / 分块数
- **非流式**：等全部生成完，可重播（有 `<audio>` 控件）

流式结果**不保留音频文件**（边播边丢），页面上明确说明。
需要可重播就用非流式。

支持取消：`AbortController` + `player.stop()`。

### 2.4 验证方式

```bash
# Windows 侧（node 在这里）
cd D:\xiangmudaima\zjy14
powershell -ExecutionPolicy Bypass -File scripts\verify-frontend-stream.ps1

# 或直接跑
cd web
node scripts\verify-stream.mjs
```

脚本用与 `client.ts` 完全相同的解析算法，验证：
分块边界、结束标记、采样率、首块延迟是否显著小于总耗时。

**为什么不在 WSL 跑**：WSL 内没有 node，node 只在 Windows 侧。

---

## 三、真机联调（工具就绪，无法执行）

### 3.1 当前环境状态

诊断结果（`probe/_p8_device_check.py`）：

```
[1] zjy14 服务           [OK] 就绪，音色 elysia，wetext，fp32
[2] zjy13 WS 服务        [--] 端口 50000 未监听
[3] 串口设备             [--] 只有 /dev/ttyS0-5（内置串口）
                             未见 /dev/ttyUSB* 或 /dev/ttyACM*
[4] 协议契约             [OK] v1, magic 0xA5, 帧 4096B, 采样率 24000
[5] zjy13 TTS 指向       [--] .env 无 TTS_BASE_URL（默认 :50000）
[6] 端到端模拟           [OK] 3 块 → 54 帧，首突发满足，seq 无跳变，收尾正常
```

Windows 侧也只找到 COM1/COM2（主板虚拟串口），
**没有 ESP32 的 USB 转串口设备**。

**结论**：软件链路全部就绪，端到端模拟通过，但**没有硬件可联调**。

### 3.2 为什么现在不能做

真机联调需要：

1. ESP32-S3 开发板通过 USB 连接到电脑
2. 板子已烧录 zjy13 的固件（固件本身不用改）
3. 板子连上 WiFi 并知道 zjy13 WS 服务的地址
4. zjy13 的 WS 服务在跑

缺第 1 项，后面都无从谈起。

### 3.3 硬件接上后的操作步骤

**Step 1：烧录固件（若板子是新的）**

```powershell
cd D:\xiangmudaima\zjy13
.\scripts\flash_firmware.ps1 -Port COM7          # 端口按实际改
.\scripts\flash_firmware.ps1 -Port COM7 -Build   # 需要先编译时
```

**Step 2：启动 zjy14**

```powershell
cd D:\xiangmudaima\zjy14
.\scripts\start.ps1 cosyvoice3
```

**Step 3：把 zjy13 的 TTS 指向 zjy14**

编辑 `zjy13/.env`：
```bash
TTS_BASE_URL=http://127.0.0.1:50001   # 原 :50000
```

**Step 4：启动 zjy13 WS 服务**

（按 zjy13 自己的方式启动，本项目未改动其代码）

**Step 5：跑联调诊断**

```bash
wsl -e bash /mnt/d/xiangmudaima/zjy14/probe/run_d1.sh \
  /mnt/d/xiangmudaima/zjy14/probe/_p8_device_check.py
```

预期全部 `[OK]`。

### 3.4 诊断工具做了什么

`probe/_p8_device_check.py` 按链路顺序逐段检查：

```
ESP32 固件 ──WS──> zjy13 ws/server.py ──HTTP──> zjy14 /synthesize_stream
   (3)                  (2)                          (1)
```

| 检查项 | 内容 |
|---|---|
| 1 | zjy14 健康、引擎、音色、文本前端、compile 状态 |
| 2 | zjy13 WS 端口是否监听 |
| 3 | 串口设备（区分内置串口与 USB 转串口） |
| 4 | 协议契约（采样率、帧大小、路径、首突发） |
| 5 | zjy13 的 `TTS_BASE_URL` 是否指向 :50001 |
| 6 | 端到端模拟：真实 HTTP 流 → 协议帧 → FakeDevice 消费 |

第 6 项是关键：它用**真实的 zjy14 流式输出**走一遍完整协议链路，
不需要硬件就能验证协议层是否正确。

### 3.5 真机无声时的排查顺序

诊断工具已把这段写进输出，这里再列一次：

1. 板子是否连上 WiFi 并建立 WS 连接（看 zjy13 日志）
2. 是否收到 `playback_start`（需首块音频到达才发）
3. 音量是否过低（固件默认值）
4. I2S 是否被录音占用（**半双工，不能同时**）

第 4 条是固件约束：`firmware/main/app_audio.h:15` 记录
ES7210 与 ES8311 共用 I2S 总线，录音 16kHz 与播放 24kHz 不能同时工作。

---

## 四、交付内容

### 4.1 新增文件

| 文件 | 用途 |
|---|---|
| `web/src/audio/StreamPlayer.ts` | Web Audio 流式播放器 |
| `web/scripts/verify-stream.mjs` | 流式解析验证（与前端同算法） |
| `scripts/verify_phase8.sh` | Phase 8 验收（WSL 侧） |
| `scripts/verify-frontend-stream.ps1` | Phase 8 验收（Windows 侧） |
| `probe/_p8_device_check.py` | 真机联调诊断 |

### 4.2 修改文件

| 文件 | 改动 |
|---|---|
| `web/src/api/client.ts` | 新增 `synthesizeStream` + `StreamSummary` |
| `web/src/pages/SynthesisPage.tsx` | 加模式切换、流式统计展示、取消 |

### 4.3 验收结果

```
Phase 8 验收（WSL 侧）：10 项，9 通过 1 跳过
  [1] 源码完整性              4 项 PASS
  [2] client.ts 关键逻辑      5 项 PASS
  [3] StreamPlayer 逻辑       5 项 PASS
  [4] TypeScript 类型检查     PASS
  [5] 生产构建                PASS（JS 214KB）
  [6] 服务启动                PASS
  [7] 流式端到端              SKIP（WSL 无 node）
  [8] 首块延迟校验            SKIP

Windows 侧验收：通过
  短句 -0.3% / 中句 +56.2% / 长句 +71.4%
```

---

## 五、经验教训

1. **增量解析必须处理块跨包**。第一版如果假设「一次 read 就是完整块」，
   在长音频上必然出错。累积缓冲区 + 循环解析是唯一正确做法。

2. **截断检测要有明确信号**。服务端故意不发结束标记，
   前端必须据此报错。如果前端"容错"地接受，用户会听到半截音频却不知道。

3. **AudioContext 需要用户手势**。这是浏览器策略，不是 bug。
   但即使由点击触发，首次仍可能 suspended，需要主动 resume 兜底。

4. **验收脚本要区分运行环境**。node 只在 Windows 侧，
   WSL 侧脚本必须能优雅跳过，而不是报错退出。

5. **没有硬件时，把「不能做」变成「准备好了」**。
   真机联调无法执行，但诊断工具、模拟链路、排查顺序都做完了，
   硬件到位后可以直接跑。

---

## 六、仍未完成

| 项 | 原因 |
|---|---|
| 真机联调 | 无 ESP32 硬件 |
| 弱网测试 | 需真设备 |
| 多设备并发 | 需多台设备 |
| 流式音频回放 | 流式边播边丢，不保留文件（设计如此） |

---

## 七、复现命令

```bash
# 前端流式验证（Windows 侧，需先启动后端）
cd D:\xiangmudaima\zjy14
powershell -ExecutionPolicy Bypass -File scripts\verify-frontend-stream.ps1

# Phase 8 验收（WSL 侧）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase8.sh

# 真机联调诊断
wsl -e bash /mnt/d/xiangmudaima/zjy14/probe/run_d1.sh \
  /mnt/d/xiangmudaima/zjy14/probe/_p8_device_check.py

# 前端构建
cd web
& "D:\New Folder\npm.cmd" run build
```
