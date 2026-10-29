# 固定朝向与双角色固件 · 2026-10-29

用户已连接设备与 SD 卡，要求取消陀螺仪及屏幕跟随旋转，只保留摇晃时的头晕、摇头和星星；两套角色固件都保存，方便切换。

## 当前行为

- 屏幕朝向始终固定，姓名、时间、天气不跟随设备转动。轻倾斜不再使人物偏移或歪身。
- QMI8658 只启用 ±8g、125Hz 加速度计，100Hz 读取；关闭陀螺仪，连接后回读 CTRL7，必须确认只启用了加速度计。
- 来回摇晃触发静音头晕和星星，约 3 秒后以 400ms 恢复。期间忽略重复摇晃、取消当前和排队动作；恢复后冷却 2 秒并稳定至少 500ms 才重新触发。
- 爱莉希雅保持坐姿待机；青岚保持站姿待机；两者均不自动循环播放动作。
- 原正向校准值保留在 NVS，但固定朝向版不读取、不更新。IMU CALIBRATE 返回“固定屏幕无需校准”，IMU BENCH 也不再旋转屏幕。

加速度计估计缓变重力并滤出线性加速度；同一主轴在 40～600ms 内出现正负各超过 0.8g 的峰值，并且加速度模长明显偏离 1g，才算摇晃。模长条件用于拒绝单纯改变持握方向造成的重力方向变化。只有相反方向峰值才触发，单次拿起、单向冲击不触发。传感器连续读取失败会清除未完成的峰值并每 5 秒重连，时间、天气、触摸和固定朝向继续运行。

## 保存与切换

本机交付根目录：D:\AI Pet Badge\output\badge-firmware-2026-10-29。

- qinglan：青岚固件与校验清单。
- elysia：爱莉希雅固件与校验清单。
- Switch-Character.cmd：双击进入角色选择菜单，1 为青岚，2 为爱莉希雅，自动检测唯一的 ESP32 USB 串口。
- Switch-Character.ps1：也支持命令行指定角色与串口。

这里的切换方式是通过 USB 重新烧录已生成的固件，不是 SD 卡双启动或屏幕内热切换。先按每套包的 SHA256 清单校验，再使用它自己的 flash_args，不擦除整个 Flash。2026-10-28 的旧固件包保留。

```powershell
.\Switch-Character.ps1 -Character qinglan -Port COM9
.\Switch-Character.ps1 -Character elysia -Port COM9
# Only verify files, without accessing or flashing a board:
.\Switch-Character.ps1 -Character qinglan -VerifyOnly
```

COM9 是本次实机识别出的端口；其他电脑应使用实际端口。脚本默认使用本项目 ESP-IDF Python，换电脑可通过 -Python 指定其路径。

从源码构建并导出：

```powershell
.\tools\Build-Badge.ps1 -Character elysia
.\.venv\Scripts\python.exe tools\package_badge_firmware.py --character elysia --output '本地新目录\elysia'
.\tools\Build-Badge.ps1 -Character qinglan
.\.venv\Scripts\python.exe tools\package_badge_firmware.py --character qinglan --output '本地新目录\qinglan'
Copy-Item tools\Switch-Character.ps1, tools\Switch-Character.cmd '本地新目录'
```

导出工具拒绝错误角色、旧交互模式和已存在的输出目录。固件与原始素材保留本地，GitHub 只同步源码、脚本和说明。

## SD 卡与诊断

两套角色动画继续从各自的 Flash 素材包读取。SD 卡启动时尝试 FAT 挂载，明确设置 format_if_mount_failed=false；挂载失败不格式化，界面仍继续工作。本次不向卡写入或删除文件。

- STATUS：包含 character、imu_mode=shake_only、orientation_enabled=false、sd_mounted。
- IMU：包含 gyro_enabled=false、orientation_enabled=false、sensor_enable=1，angle、dx、dy、lean 恒为 0。
- SD：报告是否挂载、能否读取根目录、容量和剩余空间；不输出用户文件名。
- IMU SELFTEST：独立回放摇晃和动作状态机，并检查渲染混合，不向传感器注入数据。

SD 卡挂载成功不等于已实现语音播放；语音素材生成与播放器仍是后续任务。

## 验证进度

主机已对实际 C 源码回放，通过 37 项检查：方向变化时画面参数恒为零、角速度输入被忽略、水平/竖直/平放摇晃、单向冲击和过期峰值拒绝、冷却与重连、全轴饱和异常拒绝、两套角色的动作及头晕优先级。

实板初测暴露了原 BSP 初始化字节序与数据解码不匹配、MCU 重启后传感器旧状态未清理的问题。现显式执行 QMI8658 软件复位，关闭传感器后配置小端和地址自增（CTRL1=0x40）、±8g/125Hz（CTRL2=0x26），再只启用加速度计（CTRL7=0x01）并等待稳定。初测的所有轴饱和值不再视为正常数据；持续异常也会进入重连。修正后实板读取到近似 1g 的静止加速度，错误计数为 0。配置依据：[QMI8658A 数据手册](https://files.waveshare.com/upload/5/5f/QMI8658A_Datasheet_Rev_A.pdf)。

两套角色的修正版均通过构建与分区大小检查；爱莉希雅应用 7,899,872 字节，青岚应用 4,164,288 字节。双角色包的 SHA256 校验通过；错误角色包在烧录前被拒绝。切换脚本已实测通过 USB 烧录，并已在独立 PowerShell 进程验证校验入口。

当前板上为青岚修正版，37 项滤波/动作回放及 2 项真实渲染混合检查均在板端通过（共 39 项，failures=0）。实板诊断确认加速度计启用、陀螺仪关闭、角度和倾斜输出为 0、持续采样错误为 0；通过串口依次触发三个动作后均回到站姿静止。实体触摸计数也正常增长。

SD 文件系统挂载并读取根目录成功，总容量 2,677,604,352 字节、剩余 97,042,432 字节、根目录 27 项；前后剩余空间和目录项数一致，本轮未写入或删除用户文件。RTC/联网时间、深圳天气与姓名 2 字字库均正常，实机截图存 artifacts/hardware/2026-10-29/qinglan-fixed-screen.png。

2026-10-29 用户已完成当前青岚版本的手持验证，确认转动设备不再反向转正、轻倾斜不再偏移；明显来回摇晃能正常出现摇头、星星及头晕效果，并在约 3 秒后正常恢复静止站姿。此确认与程序回放、板端自检分开记录，当前请求的青岚交互修改已通过实物确认。

爱莉希雅修正版已构建和打包；该角色在传感器修正后的最终手持观感仍以实际切入后确认，不将青岚的手持结果直接当作另一角色的确认。
