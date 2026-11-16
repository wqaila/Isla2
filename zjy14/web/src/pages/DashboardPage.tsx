/** 仪表盘：GPU、引擎、服务状态总览。 */

import { useCallback } from "react";
import { systemApi } from "../api/client";
import { Alert, Badge, Card, Empty, ErrorBox, Loading, Metric, PageHead } from "../components/ui";
import { useAsync } from "../hooks/useAsync";

export default function DashboardPage() {
  const health = useAsync(useCallback(() => systemApi.health(), []));
  const gpu = useAsync(useCallback(() => systemApi.gpu(), []));
  const status = useAsync(useCallback(() => systemApi.status(), []));

  const reloadAll = () => {
    health.reload();
    gpu.reload();
    status.reload();
  };

  const h = health.data;
  const g = gpu.data;
  const s = status.data;
  const vramPct =
    g?.ok && g.total_mb ? Math.round(((g.used_mb ?? 0) / g.total_mb) * 100) : 0;

  return (
    <>
      <PageHead
        title="仪表盘"
        desc="服务状态、GPU 与引擎信息。数据来自 /health 与 /api/v1/system/*。"
      />

      <div style={{ marginBottom: 16 }}>
        <button className="ghost sm" onClick={reloadAll}>
          刷新
        </button>
      </div>

      {health.error && <ErrorBox message={`健康检查失败：${health.error}`} />}

      {health.loading ? (
        <Loading />
      ) : (
        <>
          <div className="grid grid-4" style={{ marginBottom: 16 }}>
            <Metric
              label="服务状态"
              value={
                h?.ok ? <Badge kind="ok">正常</Badge> : <Badge kind="err">异常</Badge>
              }
            />
            <Metric label="引擎" value={h?.engine ?? "—"} />
            <Metric label="采样率" value={h?.sample_rate ?? "—"} unit="Hz" />
            <Metric label="可用音色" value={h?.voices?.length ?? 0} unit="个" />
          </div>

          <div className="grid grid-2">
            <Card title="GPU">
              {gpu.loading ? (
                <Loading />
              ) : g?.ok ? (
                <>
                  <div className="grid grid-2" style={{ marginBottom: 12 }}>
                    <Metric label="设备" value={g.device_name ?? "—"} />
                    <Metric
                      label="显存占用"
                      value={`${g.used_mb ?? 0} / ${g.total_mb ?? 0}`}
                      unit="MB"
                    />
                  </div>
                  <div className="hint">
                    空闲 {g.free_mb ?? 0} MB · 占用率 {vramPct}%
                    <br />
                    torch {g.torch} · CUDA {g.cuda_runtime}
                    <br />
                    计算能力 sm_{g.capability?.join("")} · {g.multi_processor_count} SM
                  </div>
                </>
              ) : (
                <Alert kind="warn">{g?.reason ?? "无法读取 GPU 信息"}</Alert>
              )}
            </Card>

            <Card title="引擎诊断">
              {h?.diagnostics ? (
                <>
                  <table>
                    <tbody>
                      <tr>
                        <td className="dim">文本前端</td>
                        <td>
                          {h.diagnostics.text_frontend ? (
                            <Badge kind="ok">{h.diagnostics.text_frontend}</Badge>
                          ) : (
                            <Badge kind="dim">未启用</Badge>
                          )}
                        </td>
                      </tr>
                      <tr>
                        <td className="dim">精度</td>
                        <td>{h.diagnostics.precision}</td>
                      </tr>
                      <tr>
                        <td className="dim">模型加载</td>
                        <td>{h.load_s}s</td>
                      </tr>
                      <tr>
                        <td className="dim">预热</td>
                        <td>{h.warmup_s}s</td>
                      </tr>
                      <tr>
                        <td className="dim">hop_len 重置</td>
                        <td>{h.diagnostics.hop_len_resets} 次</td>
                      </tr>
                      <tr>
                        <td className="dim">文本过短警告</td>
                        <td>{h.diagnostics.ratio_warnings} 次</td>
                      </tr>
                      <tr>
                        <td className="dim">峰值显存</td>
                        <td>{h.peak_vram_mb ?? 0} MB</td>
                      </tr>
                    </tbody>
                  </table>
                  <div className="hint" style={{ marginTop: 10 }}>
                    模型目录：<span className="mono">{h.diagnostics.model_dir}</span>
                  </div>
                </>
              ) : (
                <Empty>无诊断信息</Empty>
              )}
            </Card>
          </div>

          <Card title="运行状态">
            {status.loading ? (
              <Loading />
            ) : s ? (
              <div className="grid grid-4">
                <Metric label="版本" value={s.version} />
                <Metric label="运行时长" value={Math.round(s.uptime_s)} unit="秒" />
                <Metric label="数据库表" value={s.db.tables.length} unit="张" />
                <Metric
                  label="监听"
                  value={`${s.config.host}:${s.config.port}`}
                />
              </div>
            ) : (
              <Empty>无法读取状态</Empty>
            )}
          </Card>

          <Card title="能力说明">
            <Alert kind="info">
              当前为<strong>路线乙</strong>：只控制语速 / 音量 / 停顿。
              情绪标签仅作元数据，不传给 TTS 模型 ——
              实测 CosyVoice3 的情绪指令无效（见 docs/13_D1_EMOTION_VERDICT.md）。
            </Alert>
          </Card>
        </>
      )}
    </>
  );
}
