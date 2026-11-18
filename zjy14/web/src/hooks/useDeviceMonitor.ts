/** 设备状态轮询 hook。
 *
 * 设计要点：
 *   1. 页面不可见时暂停轮询（省资源，也避免切回时看到一堆过期数据）
 *   2. 切回前台立即刷新一次（不要等下一个间隔）
 *   3. 请求失败保留上一次数据，并标记「数据可能过期」
 *   4. 组件卸载时中止进行中的请求
 *   5. 轮询间隔可配置
 *
 * 为什么用轮询而不是 SSE：
 *   zjy13 的监控端点复用了 WebSocket 端口，`process_request` 只能返回
 *   一次性完整响应，无法流式推送。轮询 1 秒对本场景完全够用。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, deviceApi } from "../api/client";
import type { DeviceSnapshot } from "../api/types";

export interface DeviceMonitorState {
  snapshot: DeviceSnapshot | null;
  /** 最近一次成功获取的时间（本地时间字符串） */
  lastOkAt: string | null;
  /** 连续失败次数 */
  failCount: number;
  /** 错误信息（null 表示正常） */
  error: string | null;
  /** 是否正在请求 */
  loading: boolean;
  /** 是否因页面不可见而暂停 */
  paused: boolean;
  /** 立即刷新一次 */
  refresh: () => void;
}

export function useDeviceMonitor(
  intervalMs = 1000,
  baseUrl?: string,
): DeviceMonitorState {
  const [snapshot, setSnapshot] = useState<DeviceSnapshot | null>(null);
  const [lastOkAt, setLastOkAt] = useState<string | null>(null);
  const [failCount, setFailCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [paused, setPaused] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number | null>(null);
  const aliveRef = useRef(true);

  const fetchOnce = useCallback(async () => {
    // 取消上一次未完成的请求，避免堆积
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setLoading(true);
    try {
      const snap = await deviceApi.snapshot(baseUrl, ac.signal);
      if (!aliveRef.current) return;
      setSnapshot(snap);
      setLastOkAt(new Date().toLocaleTimeString());
      setFailCount(0);
      setError(null);
    } catch (e) {
      if (!aliveRef.current) return;
      // 主动 abort 不算失败
      if (e instanceof DOMException && e.name === "AbortError") return;
      setFailCount((n) => n + 1);
      setError(describeError(e));
      // 注意：不清空 snapshot —— 保留上一次数据，UI 会标记「可能过期」
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [baseUrl]);

  const refresh = useCallback(() => {
    void fetchOnce();
  }, [fetchOnce]);

  useEffect(() => {
    aliveRef.current = true;

    const tick = () => {
      if (document.visibilityState === "hidden") {
        setPaused(true);
        return;
      }
      setPaused(false);
      void fetchOnce();
    };

    // 立即拉一次
    tick();

    timerRef.current = window.setInterval(tick, intervalMs);

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        // 切回前台：立即刷新，不等下一个间隔
        setPaused(false);
        void fetchOnce();
      } else {
        setPaused(true);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      aliveRef.current = false;
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
      document.removeEventListener("visibilitychange", onVisibility);
      abortRef.current?.abort();
    };
  }, [fetchOnce, intervalMs]);

  return { snapshot, lastOkAt, failCount, error, loading, paused, refresh };
}
