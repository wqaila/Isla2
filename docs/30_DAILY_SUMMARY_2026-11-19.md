# 2026-11-19 当日总结：真机联调闭环 + 四个 P0 级静默失效修复 + 界面主题化

> 版本：**`4.12.0`**（次版本进位：新增「真机在环」能力 + 界面主题化）
> 涉及：`zjy13`（设备接入/对话编排）、`zjy14`（语音引擎）、板子固件（`yuanma/AI-Pet-Badge-main`）
> 测试：zjy13 **538 项**、zjy14 **479 项**，全部通过

---

## 一、一句话概括

今天做了三件事：**把长期挂着的「真机联调」P0 补完**、**修掉 4 个不报错只出错结果的静默缺陷**、
**给 Web 界面做了爱莉希雅主题**。

其中最有价值的不是"功能变多了"，而是**发现并修掉了 4 个"看起来正常但实际是错的"缺陷** ——
它们都不抛异常、不打错误日志，只是行为不对，这类问题排查成本最高。

---

## 二、成果

### 2.1 真机联调闭环（补完长期挂起的 P0）

`zjy14/docs/20`、`26` 的日报里，P0 一直是同一条：

> **真机联调（看监控页在真实板子上的表现）— 需要 ESP32 硬件**

`18_PHASE5_REPORT.md` 也明确记录 Phase 5 被 D7 收敛成「桥接验证」，
协议对齐 + FakeDevice 模拟通过，**没有接真板子**。

今天板子在线，把这条补完：

| 项 | 结果 |
|---|---|
| 板子 WiFi / WS 连接 | 在线，`ai-pet-badge`，心跳正常 |
| zjy14 引擎 | cosyvoice3 / CUDA / RTF 0.63 |
| zjy13 → zjy14 切换 | `TTS_BASE_URL=http://127.0.0.1:50001` 生效 |
| 协议契约 | v1 / 0xA5 / 4096B / 24kHz，逐项对齐 |
| 端到端音频流 | 44 帧，seq 无跳变，收尾正常 |
| **按按钮跑完整对话** | **人工确认出声** ✅ |

新增一键验收脚本 `zjy14/probe/run_device_e2e.sh`。

### 2.2 四个 P0 级静默失效修复

这四个缺陷的共同点：**不报错、只是结果不对**。

#### ① 天气不显示被误判成"没连 WiFi"

**现象**：板子屏幕天气卡片显示 `--°` + 「更新中」，用户以为板子没联网。

**实际**：WiFi 一直连着，是**天气拉不到**。串口实测：

```
E esp-tls: couldn't get hostname for :api.open-meteo.com:
           getaddrinfo() returns 202
```

`getaddrinfo() returns 202` = DNS 解析失败。根因是 **Windows ICS 的 DNS 代理坏了**：
进程在跑、端口在监听，但对查询直接回 RST。

| 查询目标 | 结果 |
|---|---|
| `192.168.137.1:53`（板子的 DNS） | ConnectionReset / 超时（3 次全失败） |
| `223.5.5.5` / `8.8.8.8` | 正常 |

**修复**（固件侧）：
1. 拿到 IP 后**显式覆盖 DNS** 为 `223.5.5.5` / `8.8.8.8`，绕开坏掉的代理
2. 界面区分三种状态：`未联网` / `校时中` / `天气无数据`（原来一律显示「更新中」）
3. `BADGE_DASHBOARD` 增加 `weather_failures` 字段，便于排查

**修复后实测**：`weather_valid: true, temperature: 19.9, weather_failures: 0`，屏幕显示 `20°C 阴`。

#### ② 填充音连响两三声（板子发出两声语气词）

**现象**：板子上会连着听到「嗯…」「这个嘛…」，不自然。

**根因**：`_send_fillers` 注释写着"边播边补"，但实现上**从来没有跟随播放进度** ——
它其实是一口气把最多 3 秒填充音全塞给板子。

两个事实叠加：

| 事实 | 后果 |
|---|---|
| `should_continue()` 只看累计时长，不看是否已播完 | 发完第 1 声（0.70s）后条件仍成立，立刻发第 2 声 |
| `send_audio` 对填充音**没有节流** | 一个 0.7s clip = 9 帧，几毫秒内全写进 socket |

服务端"认为"发了 3 秒，但这 3 秒是瞬间塞进去的，板子要花 3 秒才播完。

**关键对比**：正文那边（`server.py:1399-1413`）有 `lead_sec` 按播放进度节流，
**填充音路径漏掉了这段**。

**日志证据**（修复前每轮 4~5 声）：
```
正文接入填充音会话 turn=...-t76163 填充音=4 个 填充字节=137134
正文接入填充音会话 turn=...-t13973 填充音=5 个 填充字节=174956
```

**修复**：
1. 发完一个就等它播完（`_wait_filler_played`，用 `asyncio.wait` 而非 `sleep`，
   保证正文到达能**立刻打断**）
2. 新增声数上限 `MAX_FILLER_CLIPS = 1` 兜底

**实测**：修复前 4 声 → 修复后 1 声（33796 字节 = 正好一个 `filler_um.pcm`）。

#### ③ 排队超时被伪装成"成功但无音频"

**现象**：推理队列满时，流式接口返回 HTTP 200 + 空音频，日志无错。

**根因**：排队超时只 `yield` 一个「长度 0」的结束标记就 `return`，
而 zjy13 把长度 0 理解为**正常完成**。于是排队超时被当成"合成成功"，
板子静默、调用方照常收尾。

**修复过程中踩的坑（值得记录）**：第一版把 `raise` 写在 `generate()` 生成器**内部**，
注释还写着"此时尚未发送响应体"。**测试直接推翻**：

```
A) 生成器内部抛异常  → status = 200，body = b''
B) return 之前抛异常  → status = 503
```

因为 Starlette 的 `StreamingResponse` **先发 `http.response.start` 再迭代 body**。
最终把排队检查移到 handler 层。

#### ④ stub 引擎下流式接口永远无声

**根因**：`TTSEngine` 协议允许两种实现风格：

- **A 真流式**：多个 `is_final=False` 音频块 + 最后 `is_final=True` 空块（CosyVoice3）
- **B 一次性**：**只** yield 一个 `is_final=True` **且带完整 pcm** 的块（StubEngine）

路由层写的是 `if chunk.is_final: break`，在 A 下正常，在 B 下把**唯一一块带音频的数据整个丢掉**。

而 `configs/gpu.yaml` 的默认 engine 正是 `stub`。

**更糟的是**：`x-engine` 头硬编码 `"cosyvoice3"`，**恰好把这个故障彻底掩盖** ——
排查时看到 cosyvoice3 就以为引擎没问题。

**修复**：先看有没有 pcm 要发，再看要不要结束；`x-engine` 改为读 `engine.stats()` 如实上报。

### 2.3 三个 P1/P2 修复

| 缺陷 | 根因 | 修法 |
|---|---|---|
| 流式路径完全不落库 | 只有非流式调 `_record_synthesis`，而 zjy13 **只走流式** | 新增 `_record_stream_synthesis`，生成器完成时落库 |
| zjy13 的 503 提示误导 | 硬编码「TTS 模型未加载」，但 503 也可能是排队超时 | 解析响应体的 `code`/`message` |
| 固件配置生成脚本有 bug | ① 只写 SSID/密码，会抹掉 `BADGE_VOICE_AUTOCONNECT_*`/`BADGE_DNS_*`<br>② `json.dumps(ensure_ascii=True)` 把中文 SSID 写成 `\u6ca1...`，而 **C 编译器不解析 `\u`** | 新增 `c_string()` 只转义 C 真正需要的字符；补全所有宏；加 SSID 变更提醒 |

第 3 条是典型的**静默失效**：中文 SSID 写成 `\u` 转义后，Wi-Fi 永远连不上，且日志无异常。

### 2.4 七项集成缺陷修复（第二轮）

| 项 | 问题本质 | 修法 |
|---|---|---|
| P1 `turn_id` 每 100 秒重复 | 毫秒对 100000 取模；碰撞导致去重误拒，还会让固件**丢弃新帧** | 改用会话内递增序号 |
| P1 去重集合无限增长 | 无界 set，`reset_turn` 从未被调用 | 改成有界 `OrderedDict`（保留 64 条） |
| P2 整轮预算 60s 与队列等待 60s 相等 | 队列拥塞时 zjy14 独占 zjy13 全部预算 | `TURN_TIMEOUT_S` 60 → 120 |
| P2 422 未映射 | zjy14 用 pydantic，校验失败是 422 而非 400 | `(400, 422)` 都映射为参数错误 |
| P2 流式无 voice_id 预校验 | 响应头已发出时抛异常改不了状态码，只能表现为"流被截断" | 移到 handler 层预校验 |
| P2 `pause_profile` 默认改变韵律 | zjy14 默认 `natural`（180ms 分段），zjy13 原引擎是整段合成 | 显式传 `none`，可用 `TTS_PAUSE_PROFILE` 切换 |
| P3 分块长度无上限 | 损坏长度会让缓冲区无界增长，直到 120s 超时 | 加 96KB 上限，超限立刻报错 |

其中 **`turn_id` 那个比看起来严重**：它不只是"去重误拒"，而是 `turn_id_short` 的 CRC32
会相同，导致固件把两轮当成同一轮而**丢弃新帧** —— 表现为"偶尔没声音"，
且要会话活过 100 秒才出现，是最难排查的那类偶发问题。

### 2.5 Web 界面主题化（爱莉希雅）

依据官方设定（[百度百科](https://baike.baidu.com/item/%E7%88%B1%E8%8E%89%E5%B8%8C%E9%9B%85)）：
"首先就想到要用**粉色**作为这个角色的主色调"，最终定调"粉白"；
英桀制服是白色 + 紫色装饰；台词几乎句句带 **♪**；
意象是[花、蝴蝶、群星](https://www.bilibili.com/opus/695904321116569616)。

| 元素 | 应用位置 |
|---|---|
| 粉色主调 `#e88bb5` | 主按钮、激活态、焦点环 |
| 紫白过渡 `#a78bfa` | 渐变、次强调 |
| ♪ 音符 | 侧栏品牌区 |
| 菱形水晶 | 卡片标题前缀 |
| 粉紫光晕 | 背景氛围、指标块 |
| 花 `✿`/`❀` | 空状态、题词装饰 |
| 台词「愿群星为你闪耀，百花为你绽放 ♪」 | 侧栏底部 |

**关键取舍**：这是运维监控界面，不是角色展示页。所以保持暗色底保证数字可读，
粉色只用于强调不大面积铺色。

顺带修掉滑块/单选按钮的系统蓝，改为粉色（`accent-color` + 自定义轨道）。

### 2.6 诊断工具与脚本

| 文件 | 用途 |
|---|---|
| `zjy14/probe/run_device_e2e.sh` | 真机联调一键验收（诊断 + 生产代码实打） |
| `zjy14/probe/_verify_zjy13_tts.py` | 用 zjy13 生产代码的 `TTSClient` 实打 |
| `zjy14/probe/_check_win_port.sh` | WSL→Windows 端口连通性探测 |
| `zjy13/scripts/build_firmware.ps1` | 编译固件（含 IDF 路径修正） |
| `zjy13/scripts/connect_wifi.ps1` | 连接指定 WiFi |
| `zjy13/scripts/enable_hotspot.ps1` | 开启 Windows 移动热点（WinRT） |
| `zjy13/scripts/start_voice_server.ps1` | 前台启动语音服务 |
| `zjy13/scripts/start_voice_server_detached.ps1` | 后台常驻启动 |
| `zjy13/scripts/watch_board.ps1` | 实时监控板子状态 |
| `zjy14/scripts/start_tts_detached.ps1` | 后台常驻启动 zjy14 |

---

## 三、缺陷（诚实记录）

### 3.1 修了但值得反思的

| 问题 | 反思 |
|---|---|
| 第一版把 `raise` 写在生成器内部 | **读代码想当然**。注释写得很自信（"此时尚未发送响应体"），实际是错的。是测试抓到的，不是代码审查 |
| 一开始看错固件目录 | 排查 WiFi 时先看了 `zjy13/firmware/`，但真正烧录的是 `yuanma/AI-Pet-Badge-main`。两者 device_id 都不同（`esp32s3-01` vs `ai-pet-badge`） |
| 用 PowerShell 测二进制接口 | `Invoke-WebRequest` 对二进制流不可靠，误判"流式只返回 4 字节"。改用 Python 才拿到真实数据（426KB） |

### 3.2 审查提出但**实测推翻**的结论

记录这些同样重要，避免以后重复怀疑。

| 结论 | 实测结果 |
|---|---|
| "固件没实现 0 长帧结束协议，每轮卡 120s 看门狗" | **不成立**。`voice_client.c:691` 明确处理了 `plen == 0`。审查看的是 `zjy13/firmware/`，不是真正烧录的那份 |
| "流式接口只返回 4 字节，链路不通" | **不成立**。是 `Invoke-WebRequest` 的问题 |

### 3.3 仍未解决 / 设计取舍

| 项 | 说明 |
|---|---|
| 流式路径仍无去重 | `_active_turns` 只在非流式的 `synthesize()` 里检查。生产只走流式，去重实际不生效。若要加，需明确"重试是否允许"的语义 —— 属新功能而非修 bug |
| `x-first-audio-ms` 头缺失 | 非流式首音频指标恒为 `None`。zjy14 未提供该头，属未实现功能 |
| 板子固件依赖 ICS DNS | 已加兜底（覆盖成公共 DNS），但**根因在 Windows ICS 服务本身**。彻底解法是修 ICS 或换热点方案 |
| 情绪控制无效 | `docs/13_D1_EMOTION_VERDICT.md` 已实测确认 CosyVoice3-0.5B 的情绪指令无效，改走路线乙。非本日问题，但仍是能力上限 |
| 音色非完美 | 客观相似度 0.9208（超官方基准 0.7559），剩余差距可能是参考素材或 0.5B 模型上限 |

### 3.4 环境层面的坑

| 坑 | 现象 | 影响 |
|---|---|---|
| WSL 会话结束时后台进程被终止 | `setsid nohup` 也无效，`sleep 300` 都活不过 `wsl.exe` 调用结束 | 只能用 PowerShell 后台 job 或 `Start-Process` 维持服务 |
| Vite dev server 只绑 IPv6 | 5173 只监听 `::1`，`127.0.0.1` 连不上 | 必须用 `localhost` 或加 `--host 127.0.0.1` |
| Vite 文件监听与写入冲突 | 改 `styles.css` 时报 `EBUSY`，watcher 崩溃 | 需先停 dev server 再改文件 |

---

## 四、数据汇总

### 4.1 代码改动

| 项 | 数量 |
|---|---|
| 修改的已跟踪文件 | 17 |
| 新增文件 | 15 |
| 净增行数 | +1,409 / −156（不含新增文件） |
| 新增文档 | 4 篇（`27`、`28`、`29`、`FILLER_DOUBLE_SOUND_FIX`） |

### 4.2 测试

| 套件 | 结果 |
|---|---|
| zjy13 | **538 项全通过**（原 525 + 新增 13） |
| zjy14 | **479 项全通过**（原 472 + 新增 7） |

新增的回归锁：

| 文件 | 项数 | 锁住什么 |
|---|---|---|
| `zjy13/tests/test_integration_fixes.py` | 13 | turn_id 唯一性、去重有界、422 映射、分块上限、pause_profile、超时预算 |
| `zjy13/tests/test_filler_audio.py` | +3 | 单声上限、跟随播放进度、等待可打断 |
| `zjy14/tests/test_queue_timeout.py` | 5 | 排队超时 503、stub 引擎音频、x-engine 如实、流式落库 |
| `zjy14/tests/test_errors.py` | +1 | QueueTimeoutError 映射 |

**并验证了测试本身有效**：临时改回旧实现后，`turn_id` 相关 2 项立刻失败；
恢复修复版后通过。测试能失败才有价值。

### 4.3 实测数据

| 指标 | 值 |
|---|---|
| zjy14 RTF | 0.63（比实时快） |
| 流式首块 | ~1.5s |
| 填充音 | 1 声（原 4~5 声） |
| 天气 | 20°C 阴（已恢复） |
| 板子心跳 | 正常（`heartbeat_ok: true`） |

---

## 五、相关文档索引

| 文档 | 内容 |
|---|---|
| [`zjy14/docs/27_WIFI_FALSE_ALARM_DIAGNOSIS.md`](../zjy14/docs/27_WIFI_FALSE_ALARM_DIAGNOSIS.md) | "板子没连 WiFi"误判排查（实为天气 DNS 失败） |
| [`zjy14/docs/28_DEVICE_E2E_REPORT.md`](../zjy14/docs/28_DEVICE_E2E_REPORT.md) | 真机联调报告（Phase 5 补完） |
| [`zjy14/docs/29_INTEGRATION_BUG_AUDIT.md`](../zjy14/docs/29_INTEGRATION_BUG_AUDIT.md) | zjy13↔zjy14 集成缺陷排查（7 修 + 2 项实测推翻） |
| [`zjy13/docs/FILLER_DOUBLE_SOUND_FIX.md`](../zjy13/docs/FILLER_DOUBLE_SOUND_FIX.md) | 填充音连发缺陷根因与修复 |

---

## 六、下一步建议

| 优先级 | 任务 | 前置条件 |
|:--:|---|---|
| P0 | 跑 10+ 轮真实对话，验证 `turn_id` 修复（尤其间隔 >100 秒的轮次） | 无 |
| P1 | 修 Windows ICS DNS 代理（根因，目前只是固件兜底） | 需管理员权限 |
| P1 | 流式路径去重（需先定义"重试是否允许"语义） | 需求确认 |
| P2 | 多角色素材，验证"跨角色不串音色"（决策 D9） | 需新素材 |
| P2 | `x-first-audio-ms` 头补齐 | 无 |
| P3 | 换引擎评估（若情绪是硬需求） | 需重新验证 |
