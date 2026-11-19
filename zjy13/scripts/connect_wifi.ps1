# 连接指定 WiFi（生成 XML 配置 + netsh 连接）
#
# 用法：.\connect_wifi.ps1 -Ssid "没实力了吧2" -Password "xxx"
#
# 注意：密码只写入临时 XML，用完立即删除，不留在仓库里。

param(
    [Parameter(Mandatory=$true)][string]$Ssid,
    [Parameter(Mandatory=$true)][string]$Password
)

$ErrorActionPreference = "Stop"
$xmlPath = Join-Path $env:TEMP "wifi-profile-$([guid]::NewGuid().ToString('N')).xml"

try {
    # 生成 WLAN 配置 XML。hex 编码密码是 Windows 的要求。
    $hex = ($Password.ToCharArray() | ForEach-Object { [int][char]$_ }) -join ","
    # 注意：密码的 hex 需要把 ASCII 字节用逗号连接
    $bytes = [System.Text.Encoding]::ASCII.GetBytes($Password)
    $hex = ($bytes | ForEach-Object { $_.ToString("X2") }) -join ""

    $xml = @"
<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>$Ssid</name>
    <SSIDConfig>
        <SSID>
            <name>$Ssid</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>$Password</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>
"@
    # 用 UTF8 无 BOM 写入（netsh 不接受 BOM）
    [System.IO.File]::WriteAllText($xmlPath, $xml, (New-Object System.Text.UTF8Encoding($false)))

    Write-Host "[1/3] 添加 WLAN 配置..." -ForegroundColor Yellow
    $r = netsh wlan add profile filename="$xmlPath" user=current 2>&1
    Write-Host "  $r"

    Write-Host "[2/3] 连接 $Ssid ..." -ForegroundColor Yellow
    $r2 = netsh wlan connect name="$Ssid" ssid="$Ssid" 2>&1
    Write-Host "  $r2"

    Write-Host "[3/3] 等待获取 IP（最多 20 秒）..." -ForegroundColor Yellow
    $ok = $false
    for ($i = 1; $i -le 20; $i++) {
        Start-Sleep -Seconds 1
        $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
               Where-Object { $_.InterfaceAlias -eq "WLAN" -and $_.IPAddress -notmatch '^169\.254\.' })
        if ($ip) {
            Write-Host "  [OK] 已获取 IP: $($ip.IPAddress)" -ForegroundColor Green
            $ok = $true
            break
        }
    }
    if (-not $ok) {
        Write-Host "  [失败] 20 秒内未获取到有效 IP" -ForegroundColor Red
        Write-Host "  当前 WLAN 状态：" -ForegroundColor Yellow
        Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.InterfaceAlias -eq "WLAN" } |
            Select-Object IPAddress, PrefixLength | Format-Table -AutoSize
    }

    Write-Host ""
    Write-Host "=== WLAN 连接状态 ===" -ForegroundColor Cyan
    netsh wlan show interfaces 2>&1 | Select-String -Pattern "SSID|状态|State|信号|Signal|接收速率|Receive" | ForEach-Object { "  $($_.Line.Trim())" }

} finally {
    if (Test-Path $xmlPath) { Remove-Item $xmlPath -Force }
}
