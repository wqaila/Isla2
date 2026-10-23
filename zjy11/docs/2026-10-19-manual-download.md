# 手动下载 ESP32-S3 工具链

> 用途：这台机器直连 GitHub 与部分 PyPI 源不稳定（TLS 中断、连接重置），
> 由你手动下载工具包，放入本机固定目录，我再继续安装与验证。
>
> 清单数据**来自本机已克隆的 ESP-IDF v5.5.3 的 `tools/tools.json`**，
> 不是猜测的版本号。

## 1. 存放目录（先建好）

```text
D:\xiangmudaima\zjy11\.toolchain\espressif\dist\
```

工具脚本会从这个目录找已下载的压缩包，并按 SHA256 校验；校验通过就直接解压安装，**不会重复下载**。

文件名必须保持原始名字，不要改名，不要解压。

## 2. 必须下载的 6 个文件

| 顺序 | 文件名 | 大小 | 必需性 |
|---|---|---|---|
| 1 | `xtensa-esp-elf-14.2.0_20251107-x86_64-w64-mingw32.zip` | 377.7 MB | 编译器，必需 |
| 2 | `cmake-3.30.2-windows-x86_64.zip` | 43.3 MB | 构建工具，必需 |
| 3 | `xtensa-esp-elf-gdb-16.3_20250913-x86_64-w64-mingw32.zip` | 32.3 MB | 调试器，建议 |
| 4 | `esp32ulp-elf-2.38_20240113-win64.zip` | 15.4 MB | ULP 汇编，建议 |
| 5 | `openocd-esp32-win64-0.12.0-esp32-20251215.zip` | 3.0 MB | 烧录调试，建议 |
| 6 | `ninja-win.zip` | 0.3 MB | 构建器，必需 |

合计约 **472 MB**。

### 下载地址

1. 编译器（最重要）
https://github.com/espressif/crosstool-NG/releases/download/esp-14.2.0_20251107/xtensa-esp-elf-14.2.0_20251107-x86_64-w64-mingw32.zip

2. CMake
https://github.com/Kitware/CMake/releases/download/v3.30.2/cmake-3.30.2-windows-x86_64.zip

3. GDB 调试器
https://github.com/espressif/binutils-gdb/releases/download/esp-gdb-v16.3_20250913/xtensa-esp-elf-gdb-16.3_20250913-x86_64-w64-mingw32.zip

4. ULP 工具
https://github.com/espressif/binutils-gdb/releases/download/esp32ulp-elf-2.38_20240113/esp32ulp-elf-2.38_20240113-win64.zip

5. OpenOCD
https://github.com/espressif/openocd-esp32/releases/download/v0.12.0-esp32-20251215/openocd-esp32-win64-0.12.0-esp32-20251215.zip

6. Ninja
https://github.com/ninja-build/ninja/releases/download/v1.12.1/ninja-win.zip

如果 GitHub 直连慢，可在地址前加镜像前缀再试，例如：

- `https://ghfast.top/` + 原地址
- `https://ghproxy.net/` + 原地址

镜像可用性会变化，哪个能用用哪个；只改前缀，不要改后面的路径。

## 3. 不建议现在下载的

| 文件 | 原因 |
|---|---|
| `riscv32-esp-elf-*.zip`（665 MB） | RISC-V 架构，ESP32-S3 用不到 |
| `clang-*.tar.xz`（444 MB） | 可选编译器，默认不用 |
| `qemu-*.tar.xz` | 模拟器，本项目不需要 |

## 4. 校验值（下载后可自检）

```text
xtensa-esp-elf-14.2.0_20251107-x86_64-w64-mingw32.zip
b0de5062da2f05d1773d1537421134e2a7517bd74a06c3b5763b07f94e38bece

cmake-3.30.2-windows-x86_64.zip
48bf4b3dc2d668c578e0884cac7878e146b036ca6b5ce4f8b5572f861b004c25

xtensa-esp-elf-gdb-16.3_20250913-x86_64-w64-mingw32.zip
99a2243b9a75bbac95a672cc3ab4b36013429ab5b4583e7a28339e3015a3fdfa

esp32ulp-elf-2.38_20240113-win64.zip
3a7627008ac92d1580542b95c696449e56aaa1d0881dc3ef5fd5c60afc77a49d

openocd-esp32-win64-0.12.0-esp32-20251215.zip
d406be70d26098ced57eefcf636c4b4c184cd8c151204a459bb6ccbe4aa47eca

ninja-win.zip
f550fec705b6d6ff58f2db3c374c2277a37691678d6aba463adcbb129108467a
```

在 PowerShell 里核对某个文件：

```powershell
Get-FileHash 'D:\xiangmudaima\zjy11\.toolchain\espressif\dist\ninja-win.zip' -Algorithm SHA256
```

哈希一致即可，不一致说明下载损坏或不完整，重新下载。

## 5. 下载完后

告诉我一声，我会执行：

```text
idf_tools.py install --targets esp32s3
```

它会优先使用 `dist` 里的压缩包，只下载还缺的 Python 依赖。

## 6. 另一个可能更省事的办法

如果你更愿意直接装官方安装器而不是手动凑文件，可以下载 **ESP-IDF Installation Manager（EIM）离线安装包**：

- 安装器页面：https://dl.espressif.com/dl/eim/
- 官方安装教程：https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85B/ESP-IDF

EIM 自带工具链与依赖，安装时选 ESP-IDF v5.5.3、目标芯片 `esp32s3` 即可。装完告诉我安装路径，我把本项目指向它，不用再手动管理 `dist` 目录。

两条路选一条即可，不必都做。
