package com.elysia.ai.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.util.Log

/**
 * 语音播放器：全局单例，同一时刻只播一条。
 *
 * 为什么用系统自带的 [MediaPlayer] 而不是 ExoPlayer/Media3：
 *   - 我们的需求只是"播一个 HTTP 上的 mp3/wav"，MediaPlayer 原生支持；
 *   - **零新增依赖**（APK 体积和构建时间都不受影响）；
 *   - Media3 只在需要 HLS/DASH/DRM、播放列表、后台服务时才值得引入。
 *
 * 为什么要做单例：**绝不能同时播两条语音**。用户在一条还在播时点了另一条，
 * 旧的那条必须立刻停掉，否则会变成两个声音叠在一起（实测很难听清）。
 */
object TtsPlayer {

    private const val TAG = "TtsPlayer"

    private var player: MediaPlayer? = null

    /** 当前正在播的"条目 id"（通常是消息 id）。UI 用它判断该显示 ▶ 还是 ⏸ */
    var currentId: String? = null
        private set

    /** 播放状态回调：null 表示已停止；否则是正在播的条目 id */
    var onStateChanged: ((String?) -> Unit)? = null

    /**
     * 播放指定 URL。
     *
     * @param id 条目标识（用消息 id）。同一个 id 再次调用 = **停止**（切换播放/暂停语义）。
     * @param url 音频地址。可以是相对路径（如 `/tts_cache/x.mp3?exp=..&sig=..`），
     *            由 [baseUrl] 补全；也可以是完整 http(s) 地址。
     * @param baseUrl 服务端地址，形如 `http://192.168.1.5:8080`。仅当 url 是相对路径时使用。
     * @param onError 出错回调（网络/解码失败）。注意 MediaPlayer 的错误回调
     *                在线程上不固定，调用方需要自己切回主线程更新 UI。
     */
    fun play(id: String, url: String, baseUrl: String, onError: ((String) -> Unit)? = null) {
        // 同一条：理解为"再点一次 = 停止"
        if (currentId == id && player != null) {
            stop()
            return
        }

        stop()

        val full = if (url.startsWith("http://") || url.startsWith("https://")) {
            url
        } else {
            baseUrl.trimEnd('/') + "/" + url.trimStart('/')
        }

        try {
            val mp = MediaPlayer()
            mp.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            mp.setDataSource(full)

            mp.setOnPreparedListener {
                currentId = id
                onStateChanged?.invoke(id)
                it.start()
            }
            mp.setOnCompletionListener {
                Log.d(TAG, "播放完成: $id")
                releaseInternal()
                onStateChanged?.invoke(null)
            }
            mp.setOnErrorListener { _, what, extra ->
                Log.e(TAG, "播放失败 what=$what extra=$extra url=$full")
                releaseInternal()
                onStateChanged?.invoke(null)
                onError?.invoke("语音播放失败（$what/$extra）")
                true
            }

            player = mp
            mp.prepareAsync()  // 网络源必须异步，同步会卡 UI 线程（ANR）
        } catch (e: Exception) {
            Log.e(TAG, "准备播放失败", e)
            releaseInternal()
            onStateChanged?.invoke(null)
            onError?.invoke("语音播放失败：${e.message}")
        }
    }

    /** 停止播放并释放。可重复调用。 */
    fun stop() {
        if (player == null && currentId == null) return
        releaseInternal()
        onStateChanged?.invoke(null)
    }

    private fun releaseInternal() {
        try {
            player?.reset()
            player?.release()
        } catch (e: Exception) {
            Log.w(TAG, "释放播放器时忽略异常", e)
        }
        player = null
        currentId = null
    }

    /** 供 ViewModel.onCleared 调用，避免持有已销毁 Activity 的播放器 */
    fun release() {
        onStateChanged = null
        releaseInternal()
    }

    /**
     * 用 [Context] 拿到的 baseUrl 拼完整地址（便捷重载）。
     * 目前没用到 Context 本身，保留是为了将来加"仅 Wi-Fi 播放"之类的策略。
     */
    @Suppress("UNUSED_PARAMETER")
    fun play(context: Context, id: String, url: String, baseUrl: String,
             onError: ((String) -> Unit)? = null) {
        play(id, url, baseUrl, onError)
    }
}
