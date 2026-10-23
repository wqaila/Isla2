# 开发环境搭建记录（进行中）

> 本文件记录 zjy11 固件工具链的实际搭建过程、失败与处置，供复现和排错。
> 完整说明见 [2026-10-19-dev-prep.md](2026-10-19-dev-prep.md)。

## 1. 安装位置

全部在项目内，不写系统环境：

```text
zjy11/.toolchain/
  esp-idf/       ESP-IDF 源码（v5.5.3）
  espressif/     工具链与 IDF 专用 Python 环境
  idf-clone.log  克隆日志
  tools-install.log
```

临时文件统一放在 `zjy11/.local/tmp`，因为这台机器上 pip 写用户级缓存会停滞。

## 2. 已知网络问题与处置

| 目标 | 现象 | 处置 |
|---|---|---|
| GitHub Git 协议 | `schannel` 凭据错误，换 openssl 后连接被重置 | 不使用，改镜像 |
| GitHub 页面 / codeload | 连接超时 | 不依赖 |
| Gitee ESP-IDF 镜像 | 可用，克隆成功 | 源码来源 |
| 乐鑫官方下载站 `dl.espressif.com` | 可用 | 工具链来源 |
| 乐鑫中国站 `dl.espressif.cn` | 可用且更快 | 优先镜像 |
| PyPI | 可用，但需关闭缓存 | 依赖来源 |

结论：这台机器直连 GitHub 不稳定，后续升级与拉取官方示例都优先用镜像，不要反复直连重试。

## 3. 安装步骤与当前状态

| 步骤 | 状态 | 说明 |
|---|---|---|
| 克隆 ESP-IDF v5.5.3 | 完成 | 深度 1，来自 Gitee 镜像 |
| 创建 IDF 专用 venv | 进行中 | 避免与网关共用环境 |
| 安装 Python 核心依赖 | 待完成 | 失败时查看日志尾部 |
| `idf_tools.py install --targets esp32s3` | 待完成 | 只装 S3 架构 |
| 导出环境变量 | 待完成 | 每次构建需先执行 export |
| `idf.py --version` | 待验证 | 应为 5.5.3 |
| 编译官方示例 | 待验证 | 无板子也可先编译 |

## 4. 必须知道的约束

- **只安装 esp32s3 目标**，避免下载无关架构工具链。
- `idf_tools.py` 是 Python 脚本，PowerShell 不能当作可执行文件直接调用，必须用 Python 解释器执行。
- 工具链安装后需要执行 `export.ps1` 才能在当前会话使用 `idf.py`，这不是永久环境变量。
- 安装过程会下载数百兆到数 GB，未完成前不要重复启动同一安装任务。

## 5. 验证命令

设置好环境后依次运行：

```powershell
$env:IDF_PATH   = 'D:\xiangmudaima\zjy11\.toolchain\esp-idf'
$env:IDF_TOOLS_PATH = 'D:\xiangmudaima\zjy11\.toolchain\espressif'
& "$env:IDF_PATH\export.ps1"
idf.py --version
idf.py set-target esp32s3
```

`set-target` 必须在具体项目目录内执行，不能用于验证全局环境。

## 6. 尚未完成的部分

- 板子尚未连接，无法验证 COM 口与烧录。
- 完整型号与 PCB 修订待确认，屏幕驱动参数未定。
- 未编译任何官方示例，环境可用性未证实。
