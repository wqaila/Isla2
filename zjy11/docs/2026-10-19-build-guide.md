# 固件构建说明（已跑通）

日期：2026-10-19  
状态：**固件已编译成功**，产物 `build/zjy11_firmware.bin`（约 1 MB）；尚未烧录

## 1. 一句话结论

`idf.py build` 通过，退出码 0。ESP32-S3 固件可编译，但**没有上过板子**，烧录与运行仍待验证。

## 2. 环境从哪来（重要）

本机**原本就装好了 ESP-IDF 5.5.5**，在 `D:\esp\` 下。之前判断"没有工具链"是因为：

- 环境变量未配置
- 官方 `export.ps1` 硬编码去 `C:\Users\<user>\.espressif` 找 Python 环境

实际工具链在 `D:\esp\.espressif`，路径不匹配导致激活失败。

**所以不需要 EIM 安装器，也不需要手动下载那 6 个 zip。** `D:\ESP-S3\eim-gui-windows-x64.exe` 目前用不上，可以留着备用。

## 3. 构建步骤

```powershell
# 每次新开终端先激活环境
. .\firmware\idf-env.ps1

# 首次配置目标芯片
cd firmware
idf.py set-target esp32s3

# 编译
idf.py build
```

`idf-env.ps1` 做了三件事：设置 `IDF_PATH` 与 `IDF_TOOLS_PATH`、调用官方 `activate.py`、把结果注入当前会话。

> 注意：PowerShell 默认禁止运行 `export.ps1`（执行策略为 Restricted）。
> 若遇到拦截，用 `powershell -ExecutionPolicy Bypass -File 脚本.ps1` 运行，
> 或只对当前进程放行。不要为此改动系统级策略。

## 4. 已固定的板级配置

`sdkconfig.defaults` 按 B 型硬件固化：

| 配置 | 值 | 说明 |
|---|---|---|
| Flash | 16 MB | 默认 2 MB，**必须改**，否则浪费 14 MB |
| PSRAM | 启用，八线模式 80 MHz | ESP32-S3R8 的 8 MB 叠封 |
| 控制台 | USB Serial/JTAG | Type-C 直连，无需额外接线 |
| 任务看门狗 | 10 秒 | 串口读阻塞较长，放宽防误复位 |

## 5. 构建过程中遇到并解决的问题

| 问题 | 原因 | 处理 |
|---|---|---|
| `export.ps1` 无法加载 | PowerShell 执行策略 | 进程级绕过 |
| Python 环境找不到 | 路径硬编码到用户目录 | 直接调用 `activate.py --export` |
| 10 个子模块缺失 | 安装时未拉取子模块 | 用 `ghfast.top` 镜像逐个补齐 |
| `esp_wifi/lib` 空目录 | 状态不一致 | 删目录后按引用重新检出 |
| `lvgl` 组件找不到 | LVGL 不在 IDF 内置组件 | 加 `idf_component.yml` 由组件管理器拉取 |
| 缺 mbedtls 声明 | 实际用了 `esp_crt_bundle.h` | 加 `PRIV_REQUIRES mbedtls` |
| `snprintf` 截断告警 | 521 字节缓冲区接收 2048 字回复 | 去掉中间拷贝，直接用原缓冲 |
| `lv_font_montserrat_20` 不存在 | 引用了未启用的字号 | 改用 `lv_font_montserrat_14` |

后两项是**真实的代码 bug**，不是环境问题 —— 编译暴露了它们，说明这次编译有价值。

## 6. 仍未做与已知问题

| 项目 | 状态 |
|---|---|
| 烧录到实板 | 未做，板子未连接 |
| 屏幕实际显示 | 未做 |
| **中文字体** | **未解决**：当前用 LVGL 内置拉丁字体，中文会缺字 |
| 屏幕驱动初始化 | 未接入，`app_ui_init` 假设调用方已完成 BSP 初始化 |
| 端到端联调 | 未做 |

**中文字体是烧录前必须解决的问题**，否则屏幕显示效果不达标。

## 7. 下一步

1. 连板子，确认 COM 口。
2. 用 esptool 读芯片信息，核对 Flash 是否真为 16 MB。
3. 烧录并看串口日志能否正常启动。
4. 接官方 B 型屏显示例，确认屏幕与触摸。
5. 解决中文字体。
6. 串口接入网关，跑端到端文字对话。
