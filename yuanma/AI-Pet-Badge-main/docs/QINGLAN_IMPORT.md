# 青岚导入实体勋章 · 2026-10-28

2026-10-28 用户确认导入 Pets 中的“青岚”，采用站姿静止待机，当时先交付固件和预览。2026-10-29 用户接入板子和 SD 卡，并要求关闭陀螺仪及屏幕转正；当前版本以 [固定朝向版](FIXED_SCREEN_2026-10-29.md) 为准。

## 行为与素材

- 不自动循环动作；触摸依次执行跑步、跳跃、挥手，每次执行后平滑回到静止站姿。
- 当前屏幕固定朝向，关闭倾斜反馈，只保留静音头晕和星星。摇晃打断当前动作并清空排队动作，恢复后站立待机。
- 姓名使用“青岚”，补齐两个汉字的字库；时间、深圳天气与现有界面沿用。
- 当前图集没有坐姿，不将站立动作标为坐下。爱莉希雅仍使用原来的坐姿逻辑。
- 从 Pets 下载的原始 RGBA 图集留在 artifacts/pets/qinglan/spritesheet.png，不修改 Pets 中的角色或启用状态。

输入图集采用 Pets v2：1536×2288，8 列×11 行，每格 192×208。转换直接保留透明通道，不使用绿幕去色，避免把青色衣服擦除。按同一比例和脚底基准缩放，所有实际像素均检查是否落在 160×256 的人物区域内，并保留安全边距。

| 板端动作 | 来源 | 输出帧数 |
| --- | --- | --- |
| walk | 第 1 行 running-right，两次原有步态循环 | 50 |
| jump | 第 4 行 jumping | 20 |
| wave | 第 3 行 waving，前后加入同一中立帧 | 19 |

按来源每帧时长重采样至 24 FPS。资源包继续使用 BDGZ565-v2、RGB565＋A8，89 帧、2,199,810 字节。格式版本不变，构建时按角色校验动作名称和顺序；不会将青岚包误配给爱莉希雅控制逻辑。

两个角色分别内嵌 main/qinglan.badge 和 main/animations.badge，切换构建不会覆盖另一个角色的包。本机保留的原爱莉希雅包及导入前固件位于 artifacts/releases/elysia-before-qinglan-2026-10-28。

## 构建与预览

已提交两套姓名字库。使用现成的本地青岚包构建：

```powershell
Set-Location D:\ai_pet_badge_dev
.\tools\Build-Badge.ps1 -Character qinglan
```

从原图重新转换并构建：

```powershell
.\tools\Build-Badge.ps1 -Character qinglan -PetSpritesheet 'D:\ai_pet_badge_dev\artifacts\pets\qinglan\spritesheet.png'
```

桌面预览位于 artifacts/pets/qinglan/preview：idle.png、walk.webp、jump.webp、wave.webp 和 contact-sheet.png。预览用占位时间和天气展示布局，不是实板截图。实际固件继续使用 RTC/NTP 时间与深圳天气数据。

板子接回后，先确定实际 USB 串口，再烧录；下例 COM9 仅作命令示例：

```powershell
.\tools\Build-Badge.ps1 -Character qinglan -Flash -Port COM9
```

需要恢复爱莉希雅时，改用 -Character elysia。若另一台电脑缺少旧包，使用原始 walk.mp4、jump.mp4、sit.mp4，通过原有 -AssetSource 参数重新生成。

## 验证与待办

- 21 项真实 C 动作状态机回放通过，包括原坐姿流程、站姿三个动作的帧边界、回到中立、头晕抢占和取消动作不恢复；主机测试编译 badge_motion.c 与 badge_selftest.c，未重写一份 Python 状态机。
- 4 项素材导入测试通过：空帧拒绝、青色衣服保留、越界拒绝、最后一帧时长保留。
- 已完整解压校验青岚资源包，检查透明像素 RGB 为零、帧表连续、首尾中立帧一致；原爱莉希雅包 SHA256 保持不变。
- 两套角色均已通过 ESP-IDF 5.5.3 构建与分区大小检查；青岚应用为 0x3c3250 字节，现有 0x900000 应用分区剩余约 58%。构建保留原板级驱动的宏警告，未出现新的编译错误。
- 10 月 28 日本地交付目录：D:\AI Pet Badge\output\qinglan-badge；包含当时的固件和桌面预览。该旧包保留；10 月 29 日取消转正的双角色固件与切换菜单位于 D:\AI Pet Badge\output\badge-firmware-2026-10-29。构建目录会随角色测试切换，请通过 -Character 明确选择或使用导出的包。
- 待接板验证：姓名、触摸三个动作、静止时不循环、旋转和头晕恢复、音频和网络无回归。当前没有声称实板验收完成或达到新的 FPS 指标。

原始图集、预览、资源包和含本地联网配置的固件均保留在本机；GitHub 只同步导入工具、源码与说明。
