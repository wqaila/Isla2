# 轻量网关运行说明

本项目在电脑上运行文字网关，为后续 ESP32-S3 固件提供统一接口。人格为朋友型爱莉希雅，只维护最近 4 轮成功对话，不接入旧项目数据库、画像或本地模型。

## 1. 启动与停止

在项目目录运行 PowerShell 7：

```powershell
.\.venv\Scripts\python.exe -m gateway
```

默认监听 `0.0.0.0:8111`，同一可信局域网内的板子可使用电脑的局域网 IP 访问。本机入口：

- 状态页面：`http://127.0.0.1:8111/`
- **设置页面：`http://127.0.0.1:8111/settings`**（仅本机，需管理令牌）
- 健康检查：`http://127.0.0.1:8111/health`

## 1.1 用设置页填写密钥（推荐）

打开 `http://127.0.0.1:8111/settings`，按三步操作：

1. **登录**：粘贴管理令牌。令牌在 `.local/admin_token` 文件里，用编辑器打开复制即可；它与设备令牌、模型密钥都不同。
2. **填写模型**：选 DeepSeek 预设，确认接口地址与模型标识，粘贴 API Key，勾选「允许网关调用云端」。
3. **先测试再保存**：「测试连接」会真实发一次极短请求验证密钥，不写入配置；确认可用后再「保存配置」。

保存后**必须重启网关**才生效。密钥只写入本机 `.env`，页面只显示末尾 4 位。

安全限制，务必知悉：

- 设置页**只允许本机访问**，局域网内其他设备访问会被拒绝（403）。
- 管理令牌存放在 `.local/admin_token`，`.local/` 已被 `.gitignore` 排除。
- 页面登录态用 HttpOnly + SameSite=Strict Cookie，脚本无法读取。
- 保存写入采用临时文件替换，避免中途断电留下损坏配置。
- 「测试连接」那次请求也会产生少量费用，但不会保存数据。

如果不走网页，也可以直接编辑 `.env`，见下一节。

前台运行时用 `Ctrl+C` 停止。若由 DSH 后台任务启动，则使用对应任务的停止操作。不要启动多个进程或多个 worker，因为会话、请求去重和限流状态均在当前进程内。

状态页与设置页都不会向模型发请求；只有设置页的「测试连接」和对 `/v1/chat` 的调用会真正请求云端。

## 2. 配置云端模型

安装依赖时只使用本项目的虚拟环境：

```powershell
python -m venv .venv
New-Item -ItemType Directory -Force .local/tmp | Out-Null
$env:TEMP = (Join-Path $PWD '.local/tmp')
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt --disable-pip-version-check --no-cache-dir --index-url https://pypi.org/simple
```

若环境已经建立，不必重复安装。锁定文件记录本次验证过的依赖版本。

本机已创建空密钥的 `.env`，直接编辑即可。其他机器首次配置时，先检查文件是否存在：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

不要覆盖已经存在的 `.env`。设置项说明：

| 配置 | 含义 |
|---|---|
| `LLM_BASE_URL` | 模型服务的 OpenAI 兼容根地址，包含需要的 `/v1` 等前缀，必须 HTTPS |
| `LLM_MODEL` | 账户可调用的模型标识，不按展示名称猜测 |
| `LLM_API_KEY` | 开发者 API Key，只保存在电脑端 |
| `GATEWAY_CLOUD_ENABLED` | 默认 `false`；确认模型、费用和数据外发后设置 `true` |
| `GATEWAY_DEVICE_TOKEN` | 设备访问令牌，至少 24 个 ASCII 字符；留空时读取或生成 `.local/device_token` |
| `GATEWAY_HOST` / `GATEWAY_PORT` | 监听地址和端口，默认 `0.0.0.0` / `8111` |
| `GATEWAY_RATE_LIMIT` | 每分钟新请求数，默认 20 |
| `LLM_TIMEOUT_SECONDS` | 单次云端调用总超时，默认 45 秒 |
| `LLM_MAX_TOKENS` | 输出 token 上限，默认 256 |
| `LLM_TEMPERATURE` | 采样温度，默认 0.7 |
| `LLM_TRUST_ENV` | 默认 `false`；确需使用系统代理时显式开启 |

模板以 DeepSeek 接口为示例，不代表已开通账户、确认价格或具有调用权限。DSH 登录凭据不会自动转为本项目的 API Key。更改配置或角色后重启网关。

当缺少模型配置或未开启调用时，服务可以启动，但 `ready=false`。聊天返回明确的 `503`，不会生成模拟角色回答。`ready=true` 仅表示配置齐全且已允许调用，不代表已验证服务商联网、余额或模型效果。

## 3. 设备接口

请求携带：

```text
Authorization: Bearer <device-token>
Content-Type: application/json
```

设备只需要网关访问令牌，不需要模型 API Key。自动令牌可由本机读取 `.local/device_token` 后配置到自己的设备；不要贴到公开日志或截图。

### 3.1 对话

`POST /v1/chat`：

```json
{
  "request_id": "esp32-unique-request-001",
  "session_id": null,
  "message": "今天有点累，想和你聊聊。"
}
```

成功后保存返回的 `session_id`，下一轮使用它，`request_id` 每轮新建。成功响应包括 `reply`、`character_id`、`character_version`、`model`、`response_ms` 与 `usage`。服务商未返回用量时 `usage` 为 `null`，不是零。

限制：用户消息最多 512 个 UTF-8 字节，中文一个常见汉字通常占 3 个字节；完整请求体最多 2 KiB；响应最多 8 KiB；同时最多一个云端调用。默认最多维护 64 个会话，1 小时无活动过期。

同一个 `request_id`、相同正文在 10 分钟去重窗口内返回原结果，不再调用模型；同一标识的正文变化返回 `409`。窗口最多保留 256 条近期请求，达到上限时拒绝新增请求而非提前驱逐未到期记录；重启会丢失去重状态。超时也缓存错误，不自动重试可能已计费的调用。进程重启之后不得盲目重发旧请求。

### 3.2 重置

`POST /v1/session/reset`：

```json
{
  "session_id": "上一次返回的会话标识"
}
```

重置清空此会话的成功历史，并禁止旧请求结果在去重窗口内重新放回对话。网关忙时返回错误，避免重置与生成互相覆盖。

### 3.3 错误

```json
{
  "error": {
    "code": "cloud_not_configured",
    "message": "请先配置云端 API 地址、模型与 API Key。"
  }
}
```

`401` 表示设备鉴权失败，`404` 表示会话不存在，`409` 表示标识冲突或请求进行中，`413` / `422` 表示输入无效，`429` 表示网关忙或限流，`502` 表示云端响应异常，`503` 表示配置未就绪或云端限流，`504` 表示云端超时。客户端按状态显示可理解的提示，不将错误当成角色回复。

## 4. 在电脑上验证接口

无需把设备令牌贴到命令行：示例从本地文件读取。真实聊天示例会在已开启云端时产生一次服务商请求和相应费用，请确认后手动运行。

```powershell
$deviceToken = (Get-Content .local/device_token -Raw).Trim()
$headers = @{ Authorization = "Bearer $deviceToken" }
$body = @{
    request_id = [guid]::NewGuid().ToString()
    session_id = $null
    message = '你好，我想和你聊聊今天的事。'
} | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8111/v1/chat' -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

若配置了自定义环境令牌，应使用该令牌，而非自动文件。手机和 ESP32 使用电脑实际局域网 IP；`127.0.0.1` 在板子上指向板子自身。Windows 防火墙、访客 Wi-Fi 隔离可能阻止外部设备访问，本项目不会自动改防火墙或开放公网。

## 5. 角色与测试

人格与示例在 `gateway/character.json`。默认短回复、少用表情，尊重称呼与表达偏好，保持朋友边界。不保证任何模型都能达到同等角色效果。

离线测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用 HTTP 传输替身验证真实消息组装、鉴权、上下文、去重、重置、限流和异常响应，不访问真实云端、不消耗 token。通过离线测试不代表已经完成模型效果或 ESP32 实测。

## 6. 当前范围与数据说明

- 会话只存在内存，重启即清空；只有成功的成对消息进入短期历史。
- 默认日志不输出聊天正文、拼接提示词、设备令牌或 API Key；模型调用仍会外发当前消息、角色示例及最近历史。
- 设备令牌保存在本机或由环境提供；`.env`、`.local` 和虚拟环境已排除出版本控制。
- 只有一个设备凭据域，所有持有该凭据的设备属于同一个受信使用者，不是多用户账户系统。
- 局域网 HTTP 没有传输加密，只用于可信网络原型。公网或不可信网络需要 HTTPS、证书校验及相应部署设计。
- 尚未实现 ESP32 固件、板端显示、录音、ASR、TTS 或长期记忆。

## 7. 本次实际验证记录

- 离线回归：27 项通过；`pip check` 无依赖冲突。
- 网关已由 DSH 后台任务 `pwsh-46` 启动，监听 `0.0.0.0:8111`。
- 本机状态页与健康检查返回 `200`。
- 无设备令牌的聊天请求返回 `401`。
- 使用正确设备令牌时，因未配置云端 API 返回 `503 / cloud_not_configured`；未发起真实模型请求。
- 本机通过 `http://192.168.42.42:8111/health` 访问成功；此 IP 可能随网络变化，不代表已验证手机或 ESP32 可穿过防火墙访问。
- 启动状态为 `ready=false`、`cloud_configured=false`、`cloud_enabled=false`。配置并重启后再进行真实角色效果验证。
