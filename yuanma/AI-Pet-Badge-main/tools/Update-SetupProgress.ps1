param(
    [Parameter(Mandatory=$true)][string]$Step,
    [Parameter(Mandatory=$true)][string]$Result,
    [string]$Current = '继续安装配置与验证'
)
$ErrorActionPreference = 'Stop'
$chinaTime = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, 'China Standard Time')
$timeLabel = $chinaTime.ToString('HH:mm')
$row = '| ' + $timeLabel + ' | ' + $Step.Replace('|', '/') + ' | ' + $Result.Replace('|', '/') + ' |'
$targets = @(
    'D:\AI Pet Badge\AI_二次元勋章_执行计划_V1.1.md',
    'D:\AI Pet Badge\docs\ENVIRONMENT.md'
)
foreach ($target in $targets) {
    $body = [IO.File]::ReadAllText($target)
    $marker = [regex]::Match($body, '(?m)^\| 进行中 \|.*$')
    if (-not $marker.Success) { throw ('Missing progress marker in ' + $target) }
    $replacement = $row + [Environment]::NewLine + '| 进行中 | ' + $Current.Replace('|', '/') + ' | 后续结果继续追加 |'
    $body = $body.Substring(0, $marker.Index) + $replacement + $body.Substring($marker.Index + $marker.Length)
    [IO.File]::WriteAllText($target, $body, [Text.UTF8Encoding]::new($false))
}
Write-Output $row
