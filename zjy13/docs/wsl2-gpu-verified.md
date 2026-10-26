# WSL2 GPU 验证结果与网络配置

> 记录时间：2026-10-26
> 结论：**WSL2 + GPU 完全可用**，只差网络配置。

---

## 一、GPU 验证：通过 ✅

### 实测命令与输出

```powershell
> wsl -e bash -c "ls -l /dev/dxg; ls /usr/lib/wsl/lib/ | head -20"
crw-rw-rw- 1 root root 10, 258 Oct 26 09:16 /dev/dxg
libcuda.so
libcuda.so.1
libcuda.so.1.1
libcudadebugger.so.1
libd3d12.so
libd3d12core.so
libdxcore.so
libnvcuvid.so
libnvcuvid.so.1
libnvdxdlkernels.so
libnvidia-encode.so
libnvidia-encode.so.1
libnvidia-gpucomp.so
libnvidia-gpucomp.so.610.43.02
libnvidia-ml.so.1
libnvidia-ngx.so.1
libnvidia-opticalflow.so
libnvidia-opticalflow.so.1
libnvoptix.so.1
libnvoptix_loader.so.1
```

```powershell
> wsl -e /usr/lib/wsl/lib/nvidia-smi
NVIDIA-SMI 610.43.02    KMD Version: 610.62    CUDA UMD Version: 13.3
GPU: NVIDIA GeForce RTX 5070
Memory: 2006MiB / 12227MiB
```

### 判断

| 检查项 | 结果 |
| --- | --- |
| `/dev/dxg` 设备节点 | ✅ 存在 |
| `libcuda.so` 等库 | ✅ 齐全 |
| `nvidia-smi` 可用 | ✅ 用绝对路径可运行 |
| 显存可见 | ✅ 12227 MiB |

**GPU 透传正常，CosyVoice 可以在 WSL 里用 GPU 加速。**

### 为什么 `nvidia-smi` 直接敲会失败

`/usr/lib/wsl/lib` 不在默认 PATH 里。两种解决方式：

```bash
# 方式一：用绝对路径
/usr/lib/wsl/lib/nvidia-smi

# 方式二：加到 PATH（推荐，写进 ~/.bashrc）
echo 'export PATH=/usr/lib/wsl/lib:$PATH' >> ~/.bashrc
source ~/.bashrc
nvidia-smi
```

---

## 二、网络问题：待解决 ⚠️

### 现象

```
wsl: 检测到 localhost 代理配置，但未镜像到 WSL。NAT 模式下的 WSL 不支持 localhost 代理。
```

### 根因

Windows 端开着代理：

```
ProxyEnable : 1
ProxyServer : 127.0.0.1:7877
```

WSL2 默认是 **NAT 模式**，`127.0.0.1` 在 WSL 里指向 WSL 自己，不是 Windows。
所以 WSL 里访问不了这个代理，导致：
- `pip install` 失败
- `git clone` 失败
- ModelScope 下载模型失败

### 方案 A：关闭 Windows 代理（最简单）

如果 CosyVoice 部署期间不需要代理上网：

```powershell
# 临时关闭（当前会话）
$env:HTTP_PROXY=""
$env:HTTPS_PROXY=""

# 或关闭系统代理
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable -Value 0
```

WSL 里用国内镜像：

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 方案 B：让 WSL 走 Windows 代理（保留代理）

**步骤 1**：创建 `C:\Users\user\.wslconfig`

```ini
[wsl2]
# 让 WSL 能访问 Windows 的 localhost 服务
localhostForwarding=true

[experimental]
# 镜像网络模式，WSL 与 Windows 共享网络栈
autoMemoryReclaim=gradual
```

**步骤 2**：启用镜像网络（关键）

在 `C:\Users\user\.wslconfig` 里改为：

```ini
[wsl2]
networkingMode=mirrored
dnsTunneling=true
autoProxy=true
```

`networkingMode=mirrored` 让 WSL 和 Windows 共享网络，`autoProxy=true` 会自动继承 Windows 代理设置。

**步骤 3**：重启 WSL

```powershell
wsl --shutdown
wsl -e bash -c "curl -I https://www.baidu.com"
```

**步骤 4**：验证代理生效

```bash
wsl -e bash -c "env | grep -i proxy"
```

应该能看到 `HTTP_PROXY=http://127.0.0.1:7877`。

> ⚠️ `networkingMode=mirrored` 需要 WSL 2.0+，你的 2.7.14.0 满足。
> 但该模式在某些环境可能有兼容问题，如果导致 WSL 起不来，删除 `.wslconfig` 即可恢复。

### 方案 C：在 WSL 里手动指定 Windows 主机 IP

如果不想改网络模式：

```bash
# 获取 Windows 主机 IP（NAT 模式下通常是 172.x.x.1）
export WIN_IP=$(ip route show default | awk '{print $3}')
echo "Windows IP: $WIN_IP"

# 设置代理
export HTTP_PROXY="http://$WIN_IP:7877"
export HTTPS_PROXY="http://$WIN_IP:7877"

# 测试
curl -I https://www.baidu.com
```

**但前提是代理软件允许来自局域网的连接**（Clash/v2ray 等需要在设置里开启"允许局域网连接"）。

---

## 三、推荐执行顺序

1. **先按方案 A**（关代理 + 国内镜像）跑通 CosyVoice 安装
2. 如果必须用代理，再试方案 B（mirrored 模式）
3. 方案 C 作为兜底

---

## 四、下一步：装 CosyVoice

网络通了之后，按 `docs/wsl2-cosyvoice-guide.md` 第四节执行。

关键提醒（再说一次）：

```bash
# 必须先装支持 sm_120 的 torch
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128

# 验证（这一步不能跳过）
python -c "import torch; print(torch.cuda.get_arch_list())"
# 必须包含 sm_120
```

官方 requirements 锁的 torch 2.3.1 **不支持 sm_120**，直接装会导致 GPU 不可用。

---

## 五、需要你反馈

按方案 A 或 B 配好网络后，执行：

```powershell
wsl -e bash -c "curl -sI https://pypi.tuna.tsinghua.edu.cn | head -1"
```

看到 `HTTP/2 200` 就说明网络通了，可以开始装 CosyVoice。
