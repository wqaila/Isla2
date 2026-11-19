# 开启 Windows 移动热点（WinRT Tethering API）
#
# 为什么用 WinRT 而不是 netsh：
#   本机驱动 `Hosted network supported: No`（旧 API 不支持），
#   但 WinRT 的 NetworkOperatorTetheringManager 是 Win10/11「移动热点」
#   的实际实现，可用。
#
# 用法（必须管理员）：
#   .\enable_hotspot.ps1 -Ssid "zjy14-test" -Password "12345678"
#   .\enable_hotspot.ps1 -Stop        # 关闭

param(
    [string]$Ssid = "zjy14-test",
    [string]$Password = "12345678",
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

# 检查管理员
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[错误] 需要管理员权限" -ForegroundColor Red
    Write-Host "  请用管理员身份运行 PowerShell，再执行本脚本" -ForegroundColor Yellow
    exit 1
}

# 加载 WinRT 类型
# 注意：PowerShell 5.1 的类型字面量必须写在同一行，
# 跨行会被解析器当成语法错误（实测踩过）。
try {
    $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType=WindowsRuntime]
    $null = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType=WindowsRuntime]
} catch {
    Write-Host "[错误] 无法加载 WinRT 网络类型: $_" -ForegroundColor Red
    exit 1
}

# 取当前连接的 profile（热点要共享这个连接）
#
# 2026-11-19 修正：原来只用 GetInternetConnectionProfile()，
# 它返回「系统默认连接」。实测踩坑：主机同时有以太网和手机 WiFi 时，
# 它取到了 Wi-Fi；一旦手机 WiFi 断开，热点会自动关闭（表现为板子
# 突然 reason 201 掉线）。
#
# 现在优先选以太网：有线比手机 WiFi 稳定得多。
$conns = $null

# 优先：以太网类型的连接
$profiles = [Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles()
foreach ($p in $profiles) {
    $ct = $p.NetworkAdapter.IanaInterfaceType
    # 6 = Ethernet, 71 = IEEE80211(WiFi)
    if ($ct -eq 6 -and $p.GetNetworkConnectivityLevel() -ne [Windows.Networking.Connectivity.NetworkConnectivityLevel]::None) {
        $conns = $p
        Write-Host "[信息] 共享源: 以太网 ($($p.ProfileName))" -ForegroundColor Green
        break
    }
}

# 回退：系统默认连接
if ($null -eq $conns) {
    $conns = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if ($null -ne $conns) {
        Write-Host "[信息] 共享源: 默认连接 ($($conns.ProfileName))" -ForegroundColor Yellow
        Write-Host "  [提示] 若这是手机 WiFi，一旦手机 WiFi 断开热点会跟着关闭，" -ForegroundColor Yellow
        Write-Host "         建议改用以太网作为共享源。" -ForegroundColor Yellow
    }
}

if ($null -eq $conns) {
    Write-Host "[错误] 没有可共享的 Internet 连接（主机需先连上网）" -ForegroundColor Red
    exit 1
}

$mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($conns)

if ($Stop) {
    Write-Host "[1/1] 关闭热点..." -ForegroundColor Yellow
    $result = $mgr.StopTetheringAsync()
    # WinRT IAsyncOperation 需要等待
    $result.AsTask().Wait(15000) | Out-Null
    Write-Host "  已关闭，当前状态: $($mgr.TetheringOperationalState)" -ForegroundColor Green
    exit 0
}

Write-Host "[1/3] 配置热点 SSID=$Ssid ..." -ForegroundColor Yellow
$cfg = $mgr.GetCurrentAccessPointConfiguration()
$cfg.Ssid = $Ssid
$cfg.Passphrase = $Password
$cfgOp = $mgr.ConfigureAccessPointAsync($cfg)
$cfgOp.AsTask().Wait(15000) | Out-Null
Write-Host "  配置完成"

Write-Host "[2/3] 启动热点..." -ForegroundColor Yellow
$startOp = $mgr.StartTetheringAsync()
$startOp.AsTask().Wait(30000) | Out-Null
Write-Host "  启动请求已返回"

Write-Host "[3/3] 等待热点就绪（最多 20 秒）..." -ForegroundColor Yellow
$ok = $false
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep -Seconds 1
    $state = $mgr.TetheringOperationalState
    if ($state -eq [Windows.Networking.NetworkOperators.TetheringOperationalState]::On) {
        Write-Host "  [OK] 热点已开启（第 $i 秒）" -ForegroundColor Green
        $ok = $true
        break
    }
}
if (-not $ok) {
    Write-Host "  [失败] 热点未就绪，当前状态: $($mgr.TetheringOperationalState)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== 热点状态 ===" -ForegroundColor Cyan
Write-Host "  SSID     : $($mgr.GetCurrentAccessPointConfiguration().Ssid)"
Write-Host "  客户端数 : $($mgr.ClientCount)"
Write-Host "  状态     : $($mgr.TetheringOperationalState)"

Write-Host ""
Write-Host "=== 热点网卡 IP ===" -ForegroundColor Cyan
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -match '本地连接\*|Local Area Connection' -and $_.IPAddress -notmatch '^169\.254\.' } |
    Select-Object IPAddress, InterfaceAlias | Format-Table -AutoSize
