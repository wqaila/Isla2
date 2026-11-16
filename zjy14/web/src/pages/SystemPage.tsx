/** 系统设置：配置只读展示 + 路径信息。 */

import { useCallback } from "react";
import { systemApi } from "../api/client";
import { Alert, Badge, Card, ErrorBox, Loading, PageHead } from "../components/ui";
import { useAsync } from "../hooks/useAsync";

export default function SystemPage() {
  const status = useAsync(useCallback(() => systemApi.status(), []));
  const s = status.data;

  return (
    <>
      <PageHead
        title="系统设置"
        desc="当前配置与路径。修改请编辑 configs/gpu.yaml 或用 ZJY14_* 环境变量，改完需重启。"
      />

      {status.error && <ErrorBox message={status.error} />}

      {status.loading ? (
        <Loading />
      ) : !s ? (
        <ErrorBox message="无法读取系统状态" />
      ) : (
        <>
          <Card title="引擎配置">
            <table>
              <tbody>
                <tr>
                  <td className="dim" style={{ width: 220 }}>
                    引擎类型
                  </td>
                  <td className="mono">{s.config.engine}</td>
                </tr>
                <tr>
                  <td className="dim">推理精度</td>
                  <td className="mono">
                    {s.config.precision}{" "}
                    {s.config.precision === "fp32" && (
                      <Badge kind="ok">实测最优</Badge>
                    )}
                  </td>
                </tr>
                <tr>
                  <td className="dim">文本前端</td>
                  <td>
                    <span className="mono">{s.config.text_frontend}</span>{" "}
                    {s.config.text_frontend === "wetext" ? (
                      <Badge kind="ok">已启用</Badge>
                    ) : (
                      <Badge kind="dim">未启用</Badge>
                    )}
                  </td>
                </tr>
                <tr>
                  <td className="dim">默认音色</td>
                  <td className="mono">{s.config.default_voice_id}</td>
                </tr>
                <tr>
                  <td className="dim">最小文本长度比</td>
                  <td className="mono">
                    {s.config.min_text_ratio}{" "}
                    <span className="hint">（低于此值音色会漂移）</span>
                  </td>
                </tr>
                <tr>
                  <td className="dim">token_hop_len 重置</td>
                  <td>
                    {s.config.token_hop_len_reset ? (
                      <Badge kind="ok">已开启</Badge>
                    ) : (
                      <Badge kind="warn">已关闭</Badge>
                    )}
                  </td>
                </tr>
                <tr>
                  <td className="dim">模型目录</td>
                  <td className="mono" style={{ fontSize: 11 }}>
                    {s.config.model_dir}
                  </td>
                </tr>
              </tbody>
            </table>
          </Card>

          <Card title="服务配置">
            <table>
              <tbody>
                <tr>
                  <td className="dim" style={{ width: 220 }}>
                    监听地址
                  </td>
                  <td className="mono">
                    {s.config.host}:{s.config.port}{" "}
                    {s.config.is_loopback ? (
                      <Badge kind="ok">仅本机</Badge>
                    ) : (
                      <Badge kind="warn">对外开放</Badge>
                    )}
                  </td>
                </tr>
                <tr>
                  <td className="dim">鉴权令牌</td>
                  <td>
                    {s.config.auth_token_set ? (
                      <Badge kind="ok">已设置</Badge>
                    ) : (
                      <Badge kind="dim">未设置</Badge>
                    )}
                  </td>
                </tr>
                <tr>
                  <td className="dim">最大并发</td>
                  <td className="mono">{s.config.max_concurrent_requests}</td>
                </tr>
                <tr>
                  <td className="dim">请求超时</td>
                  <td className="mono">{s.config.request_timeout_s}s</td>
                </tr>
                <tr>
                  <td className="dim">音色缓存</td>
                  <td>
                    {s.config.speaker_cache_enabled ? (
                      <>
                        <Badge kind="ok">已启用</Badge>{" "}
                        <span className="hint">上限 {s.config.speaker_cache_max_items} 条</span>
                      </>
                    ) : (
                      <Badge kind="dim">未启用</Badge>
                    )}
                  </td>
                </tr>
                <tr>
                  <td className="dim">显存安全余量</td>
                  <td className="mono">{s.config.memory_safety_margin_mb} MB</td>
                </tr>
              </tbody>
            </table>
          </Card>

          <Card title="路径">
            <table>
              <tbody>
                {Object.entries(s.paths).map(([k, v]) => (
                  <tr key={k}>
                    <td className="dim" style={{ width: 220 }}>
                      {k}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {v}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <Card title="配置来源">
            <div className="hint">
              配置文件：<span className="mono">{s.config_sources.config_path}</span>
              {s.config_sources.config_exists ? (
                <Badge kind="ok">存在</Badge>
              ) : (
                <Badge kind="err">缺失</Badge>
              )}
              <br />
              已登记字段：{s.config_sources.consumed_field_count} 个
              <br />
              环境变量覆盖：
              {s.config_sources.env_overrides.length === 0 ? (
                <span className="dim">（无）</span>
              ) : (
                <span className="mono">{s.config_sources.env_overrides.join(", ")}</span>
              )}
            </div>
            <div className="hint" style={{ marginTop: 10 }}>
              所有配置字段都必须在 schema.py 的 CONSUMED_FIELDS 中登记消费方，
              否则启动会报错。这是为了杜绝「配置看似生效但实际未使用」。
            </div>
          </Card>

          <Card title="数据库">
            <div className="hint">
              表：<span className="mono">{s.db.tables.join(", ")}</span>
              <br />
              日志模式：{s.db.journal_mode} · 外键约束：
              {s.db.foreign_keys ? (
                <Badge kind="ok">已开启</Badge>
              ) : (
                <Badge kind="err">未开启</Badge>
              )}
            </div>
          </Card>

          <Alert kind="warn">
            本页为只读。修改配置请编辑 <span className="mono">configs/gpu.yaml</span>
            ，或设置 <span className="mono">ZJY14_&lt;大写字段名&gt;</span> 环境变量，改完需重启服务。
          </Alert>
        </>
      )}
    </>
  );
}
