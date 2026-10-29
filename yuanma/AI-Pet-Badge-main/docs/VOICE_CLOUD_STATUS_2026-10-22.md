# 云端音色注册排查 · 2026-10-22

最近复核：2026-10-23。文件名保留，避免既有链接失效。

## 当前约束与结论

继续使用老师提供的现有 Token Plan API Key，只走云端。用户已选择不切换本地模型；没有安装本地推理环境或下载模型。

当前 Key 的千问系统音色合成已经成功。音色注册还没有成功，也没有得到角色音色 ID。查询失败和完整创建请求失败已分别验证，不能只把列表查询失败当作创建失败的证明。

## 已有证据

| 操作 | 实际结果 |
| --- | --- |
| Token Plan 模型列表 | HTTP 200，27 个模型；含 qwen-audio-3.0-tts-plus 与 qwen-audio-3.0-realtime-plus |
| qwen-audio-3.0-tts-plus 系统音色合成及下载 | HTTP 200；有效 WAV，24kHz、单声道、16-bit，2.64 秒 |
| voice-enrollment/list_voice | HTTP 404，InvalidParameter，Model not exist |
| 官方格式的完整 voice-enrollment/create_voice | HTTP 404，InvalidParameter，Model not exist；目标模型 qwen-audio-3.0-tts-plus，本地参考样本以 Data URI 提交，未返回音色 ID |
| qwen-voice-enrollment/list | HTTP 404，Model not exist |
| 把注册请求 model 改为可用的千问 TTS 模型 | HTTP 400，url error；不能当作正确的注册调用方式，未固化到工具 |

对照阿里云官方 Python SDK 与示例：音色注册使用 voice-enrollment，目标语音模型放在 input.target_model；不是把注册请求的 model 改成 TTS 模型。官方示例与当前请求的核心字段一致。Token Plan 文档给出了语音合成接口，但没有据此确认当前凭证可以调用音色注册服务。

因此，目前可以定位到当前 Token Plan 接入下音色注册模型不可访问；是套餐支持范围、路由配置还是账号权限问题，需要平台或账号管理者确认。不能仅据 404 保证“开通某个权限”一定能解决，也不能据此前缀或错误认定整个 Key 无效。

## 2026-10-23 补充实测

本轮没有重复发送先前失败的 JSON 创建请求。新增验证文件直传示例，并复核模型列表与注册查询：

| 操作 | 实际结果 |
| --- | --- |
| 当前 Key 的模型列表 | HTTP 200，29 个模型；语音模型新增 qwen-audio-3.0-asr-flash，原有 TTS Plus 与 Realtime Plus 仍在 |
| 官方 JSON 格式 voice-enrollment/list_voice | HTTP 404，InvalidParameter，Model not exist |
| QianWen-AI 示例中的 multipart 文件直传创建 | HTTP 400，InvalidParameter，Model not exist；未返回 output 或音色 ID |

文件直传严格沿用该示例的 qwen-voice-enrollment/create、qwen3-tts-vc-2026-01-22 和 preferred_name，上传现有 1.0.mp3；仅将域名切为现有凭证对应的 Token Plan 原生入口，不重试、不跟随重定向。该示例属于另一套 Qwen3-TTS 模型，其目标模型并未列在当前 Key 的模型列表中；这项对照失败不能用来替代 Qwen-Audio 正式注册请求的验证，也不能把该参数组推广到当前模型。正式工具继续使用官方 HTTP 文档要求的 voice-enrollment 与 JSON 格式。

另外核查了不先注册、直接带参考音频合成的路线。当前百炼 Qwen-Audio-TTS HTTP 文档要求 voice，复刻音色由注册接口创建；Realtime 文档同样要求先创建对应目标模型的音色 ID。阿里云智能语音交互另有 prompt_wav_url 直接合成接口，但属于 NLS 产品，需要项目 Appkey 和 NLS Token，不能把现有 Token Plan API Key 当作这两项凭证。没有找到仅凭现有 Key 可验证的替代复刻入口，没有将系统音色试听冒充复刻结果。

诊断响应、请求编号、采样时间及公开参考资料快照保存在被忽略的 artifacts/voice 中；不含密钥的账号管理员排查说明另存本地，便于转交。最新模型列表只能确认相应模型的可见性，不能证明音色注册可用。

补充来源：[文件直传参考示例](https://github.com/QianWen-AI/qianwen-ai/blob/main/skills/audio/qianwen-audio-tts/references/api-guide.md)、[百炼 TTS HTTP 参数](https://help.aliyun.com/zh/model-studio/qwen-audio-tts-http-api)、[实时对话音色配置](https://help.aliyun.com/zh/model-studio/qwen-audio-realtime-user-guides)、[NLS 参考音频合成及凭证要求](https://help.aliyun.com/zh/isi/user-guide/http-cosyvoice-api)。

## 参考样本

- 原始样本：本地 sy 目录 7 段 MP3，保留原文件。
- 已整理文件：artifacts/voice/elysia-reference.wav；由 1.0.mp3 和 1.1.mp3 合成，17.4498 秒、24kHz、单声道、16-bit PCM。
- 输入记录与文件 SHA256：同目录 elysia-reference.json。
- 样本与试听均未提交公开仓库。参考音频已用于用户授权的官方注册请求；没有公开托管原始录音。

## 仍走云端的下一步

路径一：请套餐或账号管理者确认，在现有 Token Plan Key 下创建 qwen-audio-3.0-tts-plus 音色，应使用哪个注册模型和请求地址，或修复当前 voice-enrollment 路由。工具可以在服务可用后按官方格式再次创建。

路径二：若同一阿里云主账号下能通过控制台或账号管理者自己的可用接口创建音色，只需提供目标模型对应的 voice_id，不必把另一个 Key 提供给用户。拿到 ID 后，用现有 Token Plan Key 验证合成是否可以访问该音色。跨入口的音色可用性尚未实测，不能事先承诺；注册与合成均需满足模型、账号及资源范围要求。

工具已支持路径二的直接验证，不向当前不可用的注册服务查询，不自动换为系统音色。ID 与当前模型前缀不匹配时在本地拒绝；云端继续验证音色资源及调用权限。只有合成成功并下载到音频后才保存提供的 ID：

```powershell
.\.venv\Scripts\python.exe tools\bailian_voice.py speak --voice-id '同账号创建的完整音色ID' --text '你好呀，今天也要开开心心的。' --output artifacts\voice\elysia-cloud-preview.wav
```

上面的 ID 是占位说明，当前尚无可用 ID；不能把这条命令视为已通过实机调用。

## 可转交管理者的问题

> 现有 Token Plan Key 可以用 qwen-audio-3.0-tts-plus 合成系统音色，但对 /api/v1/services/audio/tts/customization 发送官方格式的 voice-enrollment/create_voice 请求返回 404 InvalidParameter、Model not exist。请确认该套餐的音色注册模型与接入地址；若当前入口没有注册能力，能否在同一主账号创建 qwen-audio-3.0-tts-plus 音色并提供 voice_id，以便用现有 Key 验证合成？声音参考文件已准备好。

## 验证范围

工具离线测试共 14 项通过，包含已有音色直接调用合成、不查询注册接口、模型不匹配时不发送请求，以及既有的密钥保护、入口匹配、下载与 WAV 长度检查。已有角色音色的云端合成尚待真实 ID，当前没有声称复刻完成。

官方来源：[注册 HTTP API](https://help.aliyun.com/zh/model-studio/voice-clone-design-http-api)、[Python SDK 源码](https://github.com/dashscope/dashscope-sdk-python/blob/main/dashscope/audio/tts_v2/enrollment.py)、[千问官方复刻示例](https://github.com/aliyun/alibabacloud-bailian-speech-demo/blob/master/samples/speech-synthesizer/synthesize_speech_from_text_with_qwen_audio_tts_by_cloned_voice/python/run.py)、[Token Plan 语音合成接入](https://help.aliyun.com/zh/model-studio/token-plan-multimodal-gen)。
