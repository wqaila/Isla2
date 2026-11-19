# 实时监控板子状态变化，直到完成一轮对话
#
# 用法：.\watch_board.ps1 -Seconds 90
#
# 为什么需要：板子的对话流程只有几秒，人工盯监控页容易错过。
# 这个脚本每 0.5 秒拉一次快照，状态变化时打印，并在结束时给出
# 完整的一轮耗时分解。

param(
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$Seconds = 90,
    [double]$IntervalSec = 0.5
)

$ErrorActionPreference = "Continue"

Write-Host "=== 监控板子状态（$Seconds 秒）===" -ForegroundColor Cyan
Write-Host "  请在板子上：长按屏幕 → 说一句话 → 松开" -ForegroundColor Yellow
Write-Host ""

$deadline = (Get-Date).AddSeconds($Seconds)
$lastState = $null
$lastTurn = $null
$seenStates = [System.Collections.Generic.List[string]]::new()
$turnStart = $null
$firstAudioSeen = $false
$peakFrames = 0
$completed = $false
$lastSnapshot = $null

while ((Get-Date) -lt $deadline) {
    try {
        $d = (Invoke-WebRequest -Uri "$BaseUrl/api/devices" -TimeoutSec 3 -ErrorAction Stop).Content | ConvertFrom-Json
    } catch {
        Write-Host "  [服务不可达] $($_.Exception.Message)" -ForegroundColor Red
        Start-Sleep -Seconds 1
        continue
    }

    $lastSnapshot = $d

    if ($d.clients.Count -eq 0) {
        if ($lastState -ne "<disconnected>") {
            Write-Host "  [$(Get-Date -Format HH:mm:ss)] 无设备连接" -ForegroundColor Red
            $lastState = "<disconnected>"
        }
        Start-Sleep -Milliseconds ([int]($IntervalSec * 1000))
        continue
    }

    $c = $d.clients[0]
    $state = $c.state
    $turn = $c.turn_id

    # 轮次变化：新一轮开始
    if ($turn -and $turn -ne $lastTurn) {
        $turnStart = Get-Date
        $firstAudioSeen = $false
        $peakFrames = 0
        $completed = $false
        Write-Host ""
        Write-Host "  [$(Get-Date -Format HH:mm:ss)] === 新一轮 $turn ===" -ForegroundColor Magenta
        $lastTurn = $turn
    }

    # 状态变化
    if ($state -ne $lastState) {
        $extra = ""
        switch ($state) {
            "recording"       { $extra = "（板子在录音上传）" }
            "asr_processing"  { $extra = "（识别中）" }
            "llm_processing"  { $extra = "（LLM 生成中）" }
            "tts_processing"  { $extra = "（TTS 合成中）" }
            "filler"          { $extra = "（播填充音）" }
            "playing"         { $extra = "（板子播放中）" }
            "idle"            { $extra = "（空闲）" }
            "error"           { $extra = "（错误！）" }
        }
        Write-Host "  [$(Get-Date -Format HH:mm:ss)] $state $extra" -ForegroundColor Green
        $seenStates.Add($state)
        $lastState = $state
    }

    # 音频进度
    $frames = $c.audio.sent_frames
    if ($frames -gt $peakFrames) {
        if (-not $firstAudioSeen) {
            $firstAudioSeen = $true
            $elapsed = if ($turnStart) { [math]::Round(((Get-Date) - $turnStart).TotalMilliseconds) } else { 0 }
            Write-Host "        首帧音频！距本轮开始 ${elapsed}ms" -ForegroundColor Yellow
        }
        $peakFrames = $frames
    }

    # 文本到达
    if ($c.last_asr_text -and -not $script:asrShown) {
        Write-Host "        ASR: $($c.last_asr_text)" -ForegroundColor Cyan
        $script:asrShown = $true
    }
    if ($c.last_reply_text -and -not $script:replyShown) {
        Write-Host "        LLM: $($c.last_reply_text)" -ForegroundColor Cyan
        $script:replyShown = $true
    }

    # 错误
    if ($c.errors_recent.Count -gt 0) {
        $e = $c.errors_recent[-1]
        Write-Host "        [错误] $($e.code): $($e.message)" -ForegroundColor Red
    }

    # 一轮完成：回到 idle 且已有轮次
    if ($state -eq "idle" -and $turnStart -and -not $completed -and $c.turns_done -gt 0) {
        $completed = $true
        $total = [math]::Round(((Get-Date) - $turnStart).TotalMilliseconds)
        Write-Host ""
        Write-Host "  === 本轮完成 ===" -ForegroundColor Green
        Write-Host "    总耗时: ${total}ms"
        Write-Host "    ASR:    $($c.timings.asr_ms) ms"
        Write-Host "    LLM首:  $($c.timings.llm_first_ms) ms  (总 $($c.timings.llm_total_ms) ms)"
        Write-Host "    TTS首:  $($c.timings.tts_first_ms) ms"
        Write-Host "    播放:   $($c.timings.playback_ms) ms"
        Write-Host "    帧数:   $peakFrames"
        Write-Host "    累计:   $($c.turns_done) 轮"
        Write-Host ""
        $script:asrShown = $false
        $script:replyShown = $false
    }

    Start-Sleep -Milliseconds ([int]($IntervalSec * 1000))
}

Write-Host ""
Write-Host "=== 监控结束 ===" -ForegroundColor Cyan
Write-Host "  经历状态: $($seenStates -join ' -> ')"

if ($lastSnapshot) {
    $ct = $lastSnapshot.counters
    Write-Host "  累计: 连接 $($ct.total_connections)  轮次 $($ct.total_turns)  错误 $($ct.total_errors)"
    if ($ct.total_turns -eq 0) {
        Write-Host ""
        Write-Host "  [提示] 本轮没有完成任何对话。" -ForegroundColor Yellow
        Write-Host "    请确认：① 板子屏幕已亮 ② 长按屏幕说话 ③ 松开后等待" -ForegroundColor Yellow
    }
}
