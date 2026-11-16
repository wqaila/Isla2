/** 数据加载 hook：统一 loading / error / 重试。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError } from "../api/client";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * @param fn 加载函数。必须用 useCallback 包裹或声明为稳定引用，
 *           否则每次渲染都会重新请求。
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  // 用 ref 保存最新 fn，避免把它放进依赖导致死循环
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fnRef
      .current()
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((e: unknown) => {
        if (alive) setError(describeError(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, loading, error, reload };
}
