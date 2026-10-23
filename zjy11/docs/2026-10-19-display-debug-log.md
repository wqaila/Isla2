# ESP32-S3 屏幕接入过程记录

日期：2026-10-19  
板型：ESP32-S3-Touch-LCD-1.85B（用户确认）  
状态：**屏幕点亮成功，但渲染任务存在崩溃，问题未完全解决**

## 1. 先说结论

| 项目 | 结果 |
|---|---|
| 板子识别与烧录 | ✅ 完全打通 |
| 屏幕背光与面板初始化 | ✅ 成功 |
| 纯色图形渲染 | ✅ 正确（位置、形状、颜色都对） |
| 文字渲染 | ❌ 只显示空心方框，字形未绘制 |
| 固件稳定性 | ❌ 无限重启（`StoreProhibited`），屏幕表现为「一闪一闪」 |
| 根本原因 | ❓ 未最终定位，已排除多项假设 |

**这是一份如实记录，包含失败的尝试，不宣称已完成。**

## 2. 已确认可用的部分

### 2.1 硬件实测参数

通过 esptool 读取，与官方规格完全一致：

```text
Chip:  ESP32-S3 (QFN56) revision v0.2
PSRAM: 8MB Embedded (Octal, 80MHz)
Flash: 16MB
MAC:   44:bd:8d:78:14:68
USB:   USB-Serial/JTAG，端口 COM7
```

### 2.2 屏幕面板初始化

串口日志证明面板识别正确：

```text
Register 0x04 data: 00 02 7f 7f
Vendor-specific initialization for case 2.
st77916: version: 2.0.2
LCD panel create success
```

BSP 有两套初始化指令，按屏幕寄存器值选择。我们的屏读到 `00 02 7f 7f`，正确命中 case 2。

### 2.3 渲染链路是通的

这一步用纯色矩形验证成功，是本次最重要的正面结论：

- 红色矩形在 (20,20)，100×60 —— 位置正确
- 绿色矩形在 (200,200)，120×120 —— 位置正确
- 蓝色横条在 (20,300)，320×20 —— 位置正确

**图形没有错位、没有倾斜、颜色正确。** 说明 LVGL → 适配器 → ST77916 → 面板这条链路是好的。

## 3. 发现的问题

### 3.1 文字只显示空心方框

现象：用英文 `ZJY11 TEST` 和 `Hello 123` 测试，屏幕上出现红色和绿色**空心方框**，沿斜线排列，没有真正的字形。

这说明：

- 文字的**位置**被正确计算了
- 文字的**字形**没有被绘制出来

猜测方向（尚未验证）：

- 字体缺少对应的 glyph 数据
- 抗锯齿渲染时的 alpha 混合失败
- LVGL 版本与 BSP 适配器的渲染接口不匹配

### 3.2 无限重启

串口监测 20 秒内统计到：

```text
REBOOTS = 13~14
GURU    = 13~14
```

崩溃类型 `StoreProhibited`，发生在 `UI 初始化完成` 之后，即**渲染任务第一次执行绘制时**。

这就是用户说的「一闪一闪」：每次重启都会重新初始化屏幕、清屏、重绘，视觉上就是闪烁。

## 4. 排查过程与已排除的假设

按尝试顺序记录，避免今后重复走弯路。

| # | 假设 | 做法 | 结果 |
|---|---|---|---|
| 1 | LVGL 未初始化导致空指针 | 加 `lv_is_initialized()` 检查 | 修复了最初的 `LoadProhibited`，但引出新问题 |
| 2 | 未设置默认显示 | 加 `lv_display_set_default(disp)` | 显示对象确认正确（`disp == default`），但没解决 |
| 3 | 锁超时为 0 拿不到锁 | 改为 `bsp_display_lock(1000/2000)` | 参数类型确认无误（`int32_t timeout_ms`），未解决 |
| 4 | 持锁时重绘与渲染任务冲突 | 移除 `lv_refr_now()` | 消除了一个崩溃点，但仍重启 |
| 5 | PSRAM 缓冲问题 | `use_psram` 改 `false`、`buffer_height` 改 20 | 仍重启，排除 |
| 6 | UI 对象太复杂 | 只建一个矩形 | 仍重启，排除复杂度因素 |
| 7 | 官方示例可作对照 | 尝试编译微雪官方 `02_lvgl_demo` | **失败**：依赖 `espressif/gmf_fft@1.0.0` 下载不到 |

排查到此停止，尚未定位根因。

## 5. 当前代码状态（重要）

**`firmware/main/app_ui.c` 目前停在诊断状态**，不是可交付的实现：

- 不创建任何 UI 对象（`s_title = NULL`）
- 保留了诊断日志
- 保留了加锁逻辑

也就是说：**现在的固件屏幕是黑的（或停在蓝色默认态），没有正常界面。**

后续接手时，不要误以为这是可用版本。

## 6. 环境信息（可复用）

本机路径与命令，避免重复摸索：

| 项目 | 值 |
|---|---|
| ESP-IDF | `D:\esp\esp-idf-5.5.5`（v5.5.5） |
| 工具链 | `D:\esp\.espressif` |
| IDF Python | `D:\esp\.espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe` |
| 串口 | `COM7` |
| 激活脚本 | `firmware/idf-env.ps1` |
| 微雪官方示例 | `.toolchain/vendor/waveshare-1.85b/` |

构建命令：

```powershell
$env:IDF_PATH = 'D:\esp\esp-idf-5.5.5'
$env:IDF_TOOLS_PATH = 'D:\esp\.espressif'
$env:ESP_IDF_VERSION = '5.5'
$py = 'D:\esp\.espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'
$act = & $py "$env:IDF_PATH\tools\activate.py" --export 2>$null
. $act
cd D:\xiangmudaima\zjy11\firmware
idf.py build
idf.py -p COM7 -b 460800 flash
```

注意：PowerShell 默认执行策略会拦截脚本，需要 `powershell -ExecutionPolicy Bypass -File 脚本.ps1`。

## 7. 建议的下一步

按可能性排序：

1. **禁用 LVGL 渲染任务，改用同步渲染**
   保留 `bsp_display_start()`，但不让适配器启动独立任务，改由主循环调用 `lv_timer_handler()`。
   这样可以排除「多任务并发渲染」这个最大嫌疑。

2. **检查 LVGL 版本兼容性**
   适配器 `esp_lvgl_adapter` 声明的 LVGL 版本范围是 `>=8,<10`。
   当前用的是 LVGL 9 系，需确认适配器对 9 的渲染接口是否完全适配。
   可行做法是降到官方示例验证过的确切版本。

3. **拿一份能编译的官方示例**
   官方 demo 卡在 `gmf_fft` 组件下载不到。可尝试预先手动放置该组件，
   或用 `01_comprehensive_example` 等依赖更少的示例做对照。

4. **用面板直写绕过 LVGL**
   直接调 `esp_lcd_panel_draw_bitmap()` 写一屏纯色。
   若成功，进一步确认问题在 LVGL 层；若失败，说明问题在适配器层。

**建议优先做第 1 项**，它最能区分「并发问题」与「渲染 API 不兼容」，且改动最小。

## 8. 本次踩过的坑（供参考）

- **误判屏幕状态**：早期以为「屏幕没刷新」，实际背景色一直是对的，绕了弯路。教训是先用纯色块验证链路，再看文字。
- **诊断代码本身引入 bug**：加的 `lv_refr_now()` 造成新的崩溃，诊断手段反而变成新问题源，必须一次只改一处。
- **网络受限**：GitHub、部分镜像、组件仓库都可能不通，涉及下载的排查方案要先验证可达性。
