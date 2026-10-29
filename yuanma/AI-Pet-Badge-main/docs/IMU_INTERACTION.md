# QMI8658 摇晃星星与固定朝向

2026-10-29 起以 [固定朝向版](FIXED_SCREEN_2026-10-29.md) 为准：关闭陀螺仪、屏幕转正和倾斜偏移，只保留加速度摇晃检测、头晕及星星。IMU CALIBRATE 不再写入校准，IMU BENCH 也保持屏幕固定。下面记录的是 2026-10-21 连续转正旧版，供已有归档固件对照，不代表当前行为。

## 行为

整屏按重力连续转正，包含人物、名字、时间、深圳天气。平放时保留最后可靠朝向；平放开机采用默认正向。该功能使用重力方向，不检测观察者的位置。

轻倾斜使人物短暂偏移和歪身：水平最多 4px、垂直 3px、歪身 3°；稳定 500ms 后约 300ms 内归位。正常触摸动作期间体感减弱，保持原有起身、执行动作、坐下的流程。

明显来回摇晃时，头晕抢占当前动作并清空排队动作。250ms 混合到坐姿，坐姿晃动、头顶星星和“头晕中”持续到触发后 3 秒；400ms 恢复后坐姿静止。期间忽略触摸和重复摇晃，屏幕转正继续运行。恢复后冷却 2 秒并稳定至少 500ms 才能再次触发。新增交互静音。

## 配置与数据流

- 现有 QMI8658 驱动，I²C 地址 0x6B，±8g、±1024dps、125Hz 输出；独立任务每 10ms 读取。
- 时间使用 esp_timer_get_time，避开驱动内 24 位传感器时间戳回绕问题。先设置量程，再设置 ODR，避免驱动设置量程时覆盖 ODR。
- 陀螺仪传播重力向量，加速度在接近 1g 时作互补修正；时间常数 500ms。朝向显示平滑 120ms、死区 0.5°，用最短角差跨越 ±180°。
- 屏幕平面投影低于 0.25g 锁定；超过 0.35g 持续 200ms 恢复。线性加速度经 15ms 低通，600ms 内同一轴出现正负各超过 0.8g 的峰值触发摇晃；峰值至少相隔 40ms。
- IMU 任务只发布最新快照，不持有 LVGL 锁。动画控制器统一拥有动作优先级及头晕状态。
- 传感器连续 5 次读取失败后固定最后朝向、人物缓动归位，每 5 秒重连；不阻塞时间、天气与触摸。

## 素材与渲染

资源包为 BDGZ565 v2：48 字节小端头部（8 字节 magic，10 个 uint32：version、width、height、fps、clip_count、frame_count、roi_x、roi_y、background_offset、background_length），随后为原有 24 字节动作描述表和 8 字节帧索引表。所有偏移均相对包首。背景先存为 zlib 压缩的 360×360 RGB565；每帧为独立 zlib，解压后依次为 160×256 RGB565 小端平面、同尺寸 A8 平面，ROI 原点为 (104,80)。完全透明像素 RGB 为零。固件明确拒绝旧包。

背景与实时文字在离屏 LVGL 场景中绘制，只在文字或颜色变化时更新 RGB565 缓存。朝向变化时，使用定点增量旋转界面；所有文字覆盖区域及对比明显的边缘使用原分辨率双线性采样，低变化背景使用 2×2 像素块采样。判定区域扩展一个源像素，保证块边缘接触文字时也走精细路径。连续转动时背景直接写入下一显示缓冲，避免整帧额外拷贝；朝向稳定后复用旋转缓存。人物以 16×16 小块处理，局部变换与整屏旋转合并后只采样一次，再叠加星星，LVGL 显示最终 RGB565 画面。人物变换以 (180,245) 为支点，整屏以 (180,180) 为中心，依据实际透明边界减小局部角度并限制位置，避免遮挡卡片文字或名字。转场及人物边缘采样按预乘透明度贡献混合，再恢复颜色，避免半透明边缘发暗。

仪表盘以原子修订号通知变化，仅更新缓存和提交画面时取得显示锁；正常合成不等待上一帧绘制。LVGL 管理任务和 IMU 采样在 CPU0，合成在 CPU1；IMU 优先级 5，LVGL 管理和合成优先级 4，使用一个 LVGL 绘图线程。实际 sdkconfig 与默认配置保持一致。

当前素材为 248 帧、24FPS，生成包 5,935,348 字节；不依赖 TF 卡。人物稳定待机时不重复解码或重绘，仪表盘变化仍正常显示。

非混合帧通过交换人物原始缓冲的所有权更新，避免每帧额外复制 120KiB；原始帧缓存与混合帧分别标记，待机不重复解压。解压器状态明确分配在片内内存。完整界面下，旋转叠加头晕特效实测约 20.7FPS；普通视频动作在约 17°倾角下约 18.8FPS。帧计数为提交值，实测记录见 IMU_VALIDATION_2026-10-21.md。

## 诊断与校准

`IMU` 输出 available、flat、stable、calibrated、angle、dx、dy、lean、accel_g、gyro_rads、gravity、shakes、errors。`STATUS` 增加 imu_available、angle、flat、shakes、dizzy，保留原有字段。frames 统计向 LVGL 提交的更新，不等同于面板实际刷新次数。

首次板测先验证左右 90°及倒置时补偿方向。把设备按希望的默认正向竖直拿稳，执行 `IMU CALIBRATE`；最多等待 10 秒寻找稳定窗口，然后采集约 1 秒。收到 saved 后正向偏置保存在 NVS。窗口容忍少量手持微动，要求至少 80% 采样的平滑角速度不超过 0.25rad/s、线性加速度不超过 0.18g，重力方向漂移不超过 3°、瞬时角速度不超过 0.6rad/s。平放、明显移动、读取失败或 NVS 失败会拒绝或终止校准并保留旧值。校准不擦除联网设置。

```powershell
. .\tools\Enter-Idf.ps1
python tools\board_console.py --port COM9 --command IMU --seconds 2 --output artifacts\imu.log
python tools\board_console.py --port COM9 --command 'IMU CALIBRATE' --seconds 13 --output artifacts\imu-calibration.log
python tools\board_console.py --port COM9 --command 'IMU SELFTEST' --seconds 5 --output artifacts\imu-selftest.log
python tools\board_console.py --port COM9 --command 'IMU BENCH' --seconds 12 --output artifacts\imu-benchmark.log
.\.venv\Scripts\python.exe tools\validate_badge_assets.py firmware\ai_pet_badge\main\animations.badge
```

`IMU SELFTEST` 对独立局部状态回放，不向实机注入动作，覆盖角度跨界、平放、采样中断、触发与冷却、头晕抢占及恢复，并在板端检查 RGB565 插值与透明混合。可用主机 C 编译器定义 BADGE_HOST_SELFTEST，编译 badge_imu_filter.c、badge_motion.c、badge_selftest.c 运行传感器与状态机回放；渲染混合检查由板端执行。

`IMU BENCH` 为显式 USB 渲染诊断：临时以 120°/秒旋转整屏并叠加坐姿晃动及星星，10 秒后自动恢复实时姿态，输出提交帧率、分段渲染耗时和精细采样单元数。它不修改传感器数据、摇晃计数、校准或 NVS，也不证明实际手持方向正确；轴向与交互观感仍需手持验收。实际 sdkconfig 与 sdkconfig.defaults 统一使用性能级别 -O2，包含图形库，保留断言与调试符号。

`tools/soak_badge.py` 每秒采集 STATUS，每 5 秒采集 IMU 和 DASH，保存原始日志、JSON 样本与总结。使用 --seconds 1800 进行完整测试；可通过 --control-file 追加上述诊断命令，--pause-file 存在时释放 USB 供截图工具使用。

## 实机验收

- 正向、左右 90°、倒置和连续转圈补偿正确，跨界无突然反转；平放锁定、竖起平滑恢复。
- 名字四字、时间、天气、人物及星星无裁切；轻倾斜有响应，稳定后归位。
- 正常拿起放下、慢转不触发；明显来回摇晃能触发，动作中途平滑抢占、清空排队并恢复静止坐姿。
- 旋转和特效期间平均提交至少 20FPS，另由用户确认观感；连续运行 30 分钟，记录内存、错误、重启和仪表盘状态。
- 硬件测试结果与截图存 artifacts，本目录与生成固件均不上传仓库。实际完成情况以本次验收记录为准，不将未进行的手持测试记为通过。
