# 音色复刻与试听

老师提供的 API Key 若能访问百炼官方语音接口，可以通过本地程序使用，不要求操作者另注册主账号。API Key 不等同于控制台登录账号；地域、业务空间与模型授权由提供方确认。仅凭 Key 文本无法判断这些信息，也不能据一次鉴权失败认定 Key 无效。

本项目先创建定制音色、得到音色 ID，再提交文字生成声音。原配置采用 CosyVoice；现有 Token Plan Key 已实测可用的模型是 qwen-audio-3.0-tts-plus，普通语音合成已通过，复刻音色注册尚未通过。参考样本建议 10～20 秒，单人清晰说话，避免音乐、杂音、其他人声和唱歌。创建和合成必须使用同一个目标模型。

## 配置

在开发目录打开 PowerShell。已安装的桌面 .venv 包含所需依赖；换电脑后先执行：

```powershell
Set-Location D:\ai_pet_badge_dev
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

将 backend/.env.example 复制为 backend/.env，在本地编辑 DASHSCOPE_API_KEY。不要覆盖已有 .env，不要把密钥发到聊天或提交仓库。DASHSCOPE_REGION 默认 cn-beijing，TTS_MODEL 默认 cosyvoice-v3.5-flash；这是待验证的初始配置，并不代表已识别老师的 Key。

本次已保留原 Key，把本地 .env 的 DASHSCOPE_SERVICE 更新为 token-plan、地域设为 cn-beijing、TTS_MODEL 更新为 qwen-audio-3.0-tts-plus，与实际可访问模型匹配。配置样例继续保留通用接口默认值，换机器时按具体凭证选择。

默认 DASHSCOPE_SERVICE=standard；如果老师提供业务空间 ID，填写 DASHSCOPE_WORKSPACE_ID，工具会使用该地域的专属域名；否则使用官方既有 DashScope 域名。Token Plan 需显式指定 token-plan 模式，使用北京套餐专属域名。工具不把 Key 发往第三方接口，不跟随 API 的重定向。系统环境变量优先于 .env。

## 先查询，再创建

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py check
```

check 只查询第一页音色列表，不上传音频、不创建或删除音色、不合成语音。成功仅证明该地域的列表接口可访问，不证明目标模型的创建或合成权限。失败只报告 HTTP 状态与错误码，不打印密钥、完整响应或音频数据。401 可能是 Key、地域不匹配或学校中转凭证；403 需提供方检查权限与服务状态；网络错误不能证明 Key 无效。若老师确认是学校/第三方接口，需要其接口地址与语音 API 文档，仅提供聊天接口可能无法复刻音色。

通用接口模式会在联网前识别套餐 Key 并提示入口不匹配，不据此前缀断言整个套餐没有语音能力。Token Plan 的 OpenAI 文本地址为 https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1，Anthropic 文本地址为 https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic；两者是协议不同的文本端点，不应直接作为音色复刻地址。

官方文档说明 Token Plan 支持在 AI 工具扩展内调用独立的语音合成端点，路径为 /api/v1/services/audio/tts/SpeechSynthesizer。语音合成支持不等于音色注册接口必然可用。工具中的 token-plan 模式用于 Agent 交互素材验证，不能据此认定已打通勋章常驻后端；正式应用后端仍按适用的通用 API 方案接入。参考：[Token Plan 文本接入与使用范围](https://help.aliyun.com/zh/model-studio/more-tools)、[Token Plan 多模态与语音合成](https://help.aliyun.com/zh/model-studio/token-plan-multimodal-gen)。

在当前交互中，按同一 Token Plan 域名尝试通用音色注册路径的只读查询：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py --service token-plan check
```

该路径映射属于验证尝试，不是已由 Token Plan 文档确认的音色注册能力。当前 voice-enrollment 实际返回 404 InvalidParameter、Model not exist；此结果只代表音色注册未跑通，不能据此认定 Key 不能合成语音。工具不会自动改用其他注册模型，不重复创建音色。

若本机代理绕过列表包含 IPv6 单主机 CIDR（如 ::1/128），工具会仅在当前进程内转换为等价单主机表示，兼容 HTTPX 0.28；不修改系统代理，保留代理和证书配置。

确认是新加坡 Key 时，可临时指定地域（模型也应按老师提供的信息修改）：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py --region ap-southeast-1 check
```

## 创建音色

当前 Token Plan 的音色注册仍未通过，以下 clone/status 流程为接口准备，不代表已可用；普通系统音色测试见下一节。

将声音样本放在本地，例如 D:\AI Pet Badge\voice\sample.wav。工具支持 WAV、MP3、M4A，通过 HTTPS 直接提交 Base64 音频，无需搭建公开文件服务器。文件不超过 10MB；WAV 本地检查 16-bit PCM、至少 16kHz、1～2 声道、5～60 秒。MP3/M4A 的时长与声音质量仍需人工或服务端检查。

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py clone 'D:\AI Pet Badge\voice\sample.wav'
```

clone 会向云端创建音色，不自动重试。创建成功立即将音色 ID、模型、地域、接口模式及端点保存到本地 artifacts/voice/created-*.json，然后查询状态。就绪后更新 .env 中的 TTS_VOICE_ID、TTS_MODEL、DASHSCOPE_REGION、DASHSCOPE_SERVICE，保留其他配置。请求超时不等于创建失败，先运行以下命令按项目默认前缀查询音色 ID（若创建时修改了前缀，查询也应指定相同 --prefix），再决定是否重建：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py list
```

若已有 ID 但仍在处理，可运行：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py status --voice-id '创建返回的音色ID'
```

## 合成试听

现有 Token Plan Key 已通过以下系统音色测试，不依赖复刻音色注册。该操作会提交一次短句合成，按套餐用量计量：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py --service token-plan --model qwen-audio-3.0-tts-plus system-test
```

本次实测系统音色 longanhuan_v3.6，试听保存在 artifacts/voice/qwen-system-preview.wav，2.64 秒、24kHz、单声道、16-bit PCM。它只证明语音合成可用，不是角色复刻结果。接口返回的 OSS 音频 URL 若为 HTTP，工具只对阿里云域名升级为 HTTPS 下载，不携带 API Key；流式 WAV 中的占位长度会按实际 PCM 数据重写，避免播放器显示错误时长。

具备已经就绪的复刻音色时，再运行以下命令；它会先验证音色绑定模型和状态：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py speak --text '你好呀，今天也要开开心心的。'
```

通用接口模式先查询音色绑定模型和就绪状态；Token Plan 模式使用已有音色 ID 直接提交合成，由合成接口验证音色资源和权限，不向当前不可用的注册接口查询。两者均下载为 artifacts/voice/preview.wav。合成按服务计费，工具不自动反复试听；如输出已存在，使用新的 --output 文件名。下载音频不携带 API Key。试听确认后再集成板端语音链路，不改变当前已验收的动画固件。

若同账号管理者已创建 qwen-audio-3.0-tts-plus 音色，可以只提供完整 voice_id，无需向用户提供另一个 Key。现有 Key 能否使用该音色仍需实测；工具支持如下入口，模型前缀不匹配时在本地拒绝，仅在合成与下载成功后保存新 ID，不会自动回退到系统音色：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py speak --voice-id '同账号创建的完整音色ID' --text '你好呀，今天也要开开心心的。' --output artifacts\voice\elysia-cloud-preview.wav
```

当前没有这样的音色 ID，以上为准备好的验证入口。完整排查与可转交管理者的问题见 [VOICE_CLOUD_STATUS_2026-10-22.md](VOICE_CLOUD_STATUS_2026-10-22.md)。

## 当前验证范围

2026-10-23 复核：现有 Key 的模型列表返回 200、29 个模型，语音相关新增 qwen-audio-3.0-asr-flash；voice-enrollment 列表查询仍返回 404、Model not exist。新增 multipart 文件直传示例实测返回 400、Model not exist，未创建音色；该示例使用另一套 Qwen3-TTS 模型，未替换正式工具的 JSON 注册流程。直接参考音频合成的 NLS 方案需要另一套项目 Appkey 与 NLS Token，现有 Token Plan Key 不能代替。当前仍无角色音色 ID，等待账号侧确认注册入口或提供同账号创建的对应模型音色 ID，详见上面的排查报告。

2026-10-22：用户提供现有 Key、北京 Token Plan 地址，以及本地 sy 目录中的 7 段 MP3。实测如下：

- Token Plan /compatible-mode/v1/models 返回 200，共 27 个模型；语音相关项包含 qwen-audio-3.0-tts-plus 与 qwen-audio-3.0-realtime-plus。
- qwen-audio-3.0-tts-plus 合成与音频下载均返回 200，系统音色试听成功；CosyVoice-v3-flash 在当前入口返回模型不存在。因此使用现有 Key 和匹配的千问模型继续验证，无须据前缀直接要求换 Key。
- voice-enrollment/list_voice 和另一套 qwen-voice-enrollment/list 查询均返回 404、Model not exist。将注册路径的 model 改成已列出的千问 TTS 模型只是诊断尝试；其列表查询、Base64 audio、旧 url Data URI 及官方公网示例音频 URL 均返回 400、url error。未返回音色 ID；不将这些诊断尝试记为复刻成功，不把替换 model 的方式固化为正式注册流程。Token Plan 域名上的临时上传凭证路径也返回 404。
- 用户选择只继续云端排查，未安装本地模型。进一步对照官方 SDK 和完整复刻示例，再实际提交 voice-enrollment/create_voice，明确指定 qwen-audio-3.0-tts-plus 与现有样本，仍返回 404、Model not exist。完整创建与列表查询分开验证，尚未得到角色音色 ID。
- 普通语音可用与注册失败分别记录。北京、新加坡通用接口上的 401 不证明该 Token Plan Key 失效。后续复刻仍需要当前套餐可访问的音色注册接口或平台端支持；当前没有可用的角色音色 ID。
- 7 个样本均可本地解码，每段约 7.7～9.6 秒。选取 1.0.mp3 和 1.1.mp3 合成 artifacts/voice/elysia-reference.wav，17.45 秒、24kHz、单声道、16-bit PCM；原始素材保留。音色相似度与实际复刻质量尚未验证。
- 完整密钥未回显或提交仓库。参考音频已按用户的复刻目标提交官方注册接口进行格式校验，但未获得可用音色 ID；原始样本、试听、参考音频与接口诊断均未提交仓库，本地文件保留在被忽略的 sy 和 artifacts 中。本地 Key 不变，仅更新已验证的服务、地域与目标模型配置。

参考音频可复现生成（输出存在时需换一个文件名）：

```powershell
.\.venv\Scripts\python.exe tools\prepare_voice_reference.py sy\1.0.mp3 sy\1.1.mp3 --output artifacts\voice\elysia-reference.wav
```

14 项离线测试通过，新增已有音色 ID 在 Token Plan 下不查询注册接口，以及错误模型的外部音色 ID 在发送前被拒绝。既有检查覆盖千问系统音色、HTTPS 下载、流式 WAV 时长、密钥保护、创建不重复提交与模型绑定等。真实生成的 WAV 已校验数据长度与时长；实际角色音色创建及已有角色音色的 Token Plan 合成仍未完成。

官方参考：[API Key 配置](https://help.aliyun.com/zh/model-studio/get-api-key)、[音色复刻指南](https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide)、[音色复刻 HTTP API](https://help.aliyun.com/zh/model-studio/voice-clone-design-http-api)、[HTTP 语音合成](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)。
