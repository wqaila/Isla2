# WSL2 验证与 CosyVoice 部署指南

> 目的：确认 WSL2 可用，并在其中部署 CosyVoice 3.0。
> 本机现状已核实，见第一节。

---

## 一、本机 WSL 现状（已核实）

| 项 | 实测结果 | 说明 |
| --- | --- | --- |
| WSL 版本 | **2.7.14.0** | 已安装，版本较新 |
| WSL 内核 | 6.18.33.2-2 | 正常 |
| **已装发行版** | **Ubuntu-24.04** | ✅ 注册表中存在 |
| 安装位置 | `C:\Users\user\AppData\Local\wsl\{13069b22-...}` | |
| vmcompute 服务 | Running | ✅ 虚拟化服务正常 |
| `wsl --status` | `Wsl/E_ACCESS_DENIED` | ❌ 被当前沙箱拦截 |

**关键判断**：

- **WSL 和 Ubuntu 已经装好了**，不需要重新安装
- `E_ACCESS_DENIED` 是**当前执行环境（沙箱）**阻止了 WSL 的 RPC 通信
- 你在**自己的普通 PowerShell 窗口**里运行应该正常

---

## 二、第一步：验证 WSL 是否真的能用

请在你**自己的** PowerShell 窗口（不是通过我）执行：

```powershell
wsl --status
wsl -l -v
wsl -e uname -a
```

**预期结果**：

```
# wsl -l -v
  NAME            STATE           VERSION
* Ubuntu-24.04    Stopped         2

# wsl -e uname -a
Linux DESKTOP-XXXX 6.18.33.2-2 ... x86_64 GNU/Linux
```

**如果报 `E_ACCESS_DENIED`**，说明你的终端也在受限环境里，请换一个：
- 直接按 `Win + X` → 选「Windows 终端」或「PowerShell」
- 或按 `Win + R` → 输入 `powershell` → 回车

**如果报「没有已安装的发行版」**，则需要安装：

```powershell
wsl --install -d Ubuntu-24.04
```

安装后重启，设置用户名和密码。

---

## 三、第二步：验证 WSL 能访问 GPU

CosyVoice 需要 GPU 加速。WSL2 使用 GPU 需要 Windows 端驱动支持（NVIDIA 驱动自带 WSL 支持，你的 610.62 应该可以）。

在 WSL 里执行：

```bash
nvidia-smi
```

**预期**：能看到 RTX 5070 和 12227 MiB 显存。

如果提示 `nvidia-smi: command not found`，说明驱动未透传，需要：
1. 确认 Windows 端 `nvidia-smi` 正常（已验证）
2. 更新 NVIDIA 驱动到支持 WSL 的版本
3. 在 WSL 内**不要**装 Linux 版 NVIDIA 驱动（会和透传冲突）

---

## 四、第三步：在 WSL 里装 CosyVoice

### 4.1 确认 Python 版本

CosyVoice 官方要求 **Python 3.10**。Ubuntu 24.04 默认是 3.12，需要装 3.10：

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3.10-dev
python3.10 --version
```

### 4.2 安装系统依赖

```bash
sudo apt install -y sox libsox-dev git git-lfs build-essential cmake
git lfs install
```

> `sox` 和 `libsox-dev` 是 CosyVoice 的必需依赖，这也是官方 README 里
> 只有 Linux 安装说明的原因。

### 4.3 克隆仓库

```bash
cd ~
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice
git submodule update --init --recursive
```

> `--recursive` 很重要，CosyVoice 依赖 Matcha-TTS 子模块。

### 4.4 创建虚拟环境

```bash
python3.10 -m venv ~/cosyvoice-venv
source ~/cosyvoice-venv/bin/activate
pip install --upgrade pip
```

### 4.5 安装依赖（关键：替换 torch 版本）

官方 `requirements.txt` 锁的是 `torch==2.3.1`，但 **RTX 5070 是 sm_120，
torch 2.3.1 不支持**。必须先装支持 sm_120 的 torch，再装其余依赖。

```bash
# 先装 CUDA 12.8 版 torch（支持 sm_120）
pip install torch==2.8.0 torchaudio==2.8.0 \
  --index-url https://download.pytorch.org/whl/cu128

# 验证 sm_120 支持
python -c "
import torch
print('torch:', torch.__version__)
print('cuda:', torch.cuda.is_available())
print('device:', torch.cuda.get_device_name(0))
print('cap:', torch.cuda.get_device_capability(0))
print('arch:', torch.cuda.get_arch_list())
"

# 再装其余依赖（跳过 torch 相关行，避免被降级）
pip install -r requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cu128
```

**如果 pip 把 torch 降回 2.3.1**，改用：

```bash
# 注释掉 requirements.txt 里的 torch/torchaudio 行，再装
sed -i '/^torch==/d; /^torchaudio==/d' requirements.txt
pip install -r requirements.txt
```

### 4.6 下载模型

```bash
mkdir -p ~/models
python -c "
from modelscope import snapshot_download
snapshot_download(
    'FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
    local_dir='$HOME/models/Fun-CosyVoice3-0.5B-2512'
)
"
```

模型大小约 1-2 GB，需要一些时间。

### 4.7 最小验证

```bash
cd ~/CosyVoice
source ~/cosyvoice-venv/bin/activate

python -c "
import sys
sys.path.append('third_party/Matcha-TTS')
from cosyvoice.cli.cosyvoice import CosyVoice3
import torchaudio

cosyvoice = CosyVoice3('$HOME/models/Fun-CosyVoice3-0.5B-2512')
print('模型加载成功')

for i, out in enumerate(cosyvoice.inference_sft(
        '今天天气不错，我们下午去公园散步吧。', '中文女')):
    torchaudio.save(f'output_{i}.wav', out['tts_speech'], cosyvoice.sample_rate)
    print(f'已生成 output_{i}.wav')
    break
"
```

**成功标志**：生成 `output_0.wav`，能播放出中文语音。

---

## 五、第四步：暴露 HTTP 接口给 Windows

CosyVoice 自带 `runtime/python/fastapi/server.py`。启动：

```bash
cd ~/CosyVoice
source ~/cosyvoice-venv/bin/activate

# 只监听回环（安全要求）
python runtime/python/fastapi/server.py \
  --port 50000 \
  --model_dir $HOME/models/Fun-CosyVoice3-0.5B-2512
```

在 Windows 端测试（WSL2 的 localhost 会自动转发到 Windows）：

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:50000/docs" -UseBasicParsing
```

能看到 FastAPI 文档页 = 通了。

---

## 六、预期风险（提前说明）

| 风险 | 可能性 | 应对 |
| --- | --- | --- |
| `pynini` 在 Linux 编译失败 | 中 | `conda install -c conda-forge pynini` |
| torch 被依赖降级到 2.3.1 | 高 | 按 4.5 的 sed 方法注释掉 |
| 模型需要 `trust_remote_code` | 中 | 加 `trust_remote_code=True` |
| 显存不足 | 低 | 0.5B 模型约需 2-3 GB，你有 12 GB |
| WSL2 GPU 不透传 | 低 | 更新 NVIDIA 驱动 |
| `E_ACCESS_DENIED` | 中 | 换普通终端窗口 |

---

## 七、需要你反馈的信息

请按顺序执行并把结果贴给我：

1. `wsl --status` 和 `wsl -l -v` 的输出
2. WSL 里 `nvidia-smi` 的输出
3. 第 4.7 步是否成功生成 `output_0.wav`
4. 如果报错，贴完整错误信息

有了这些，我就能针对性地写 Windows 端的 TTS 客户端，把 CosyVoice 接进语音交互链路。

---

## 八、备选方案（如果 WSL 走不通）

如果 WSL2 确实无法使用，CosyVoice 还有一条路：**Windows 原生 + 改依赖**。

难点：
- `sox` 需要手工装 Windows 版（https://sox.sourceforge.net/）
- `pyworld` 需要 MSVC 编译
- 同样要把 torch 换成 2.8.0+cu128

可行性未知，**不建议优先尝试**，除非 WSL 彻底不可用。
