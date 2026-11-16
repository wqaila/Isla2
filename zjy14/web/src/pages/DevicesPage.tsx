/** 设备管理：ESP32-S3 状态。
 *
 * Phase 6 阶段设备端尚未联调（Phase 5 才做），
 * 这里展示协议约定与占位状态，不伪造在线数据。
 */

import { Alert, Badge, Card, PageHead } from "../components/ui";

export default function DevicesPage() {
  return (
    <>
      <PageHead
        title="设备管理"
        desc="ESP32-S3 播放端状态。Phase 5 才联调，当前无在线设备。"
      />

      <Alert kind="info">
        设备端尚未联调（Phase 5）。本页展示协议约定，不展示伪造的在线数据。
      </Alert>

      <Card title="设备列表">
        <div className="empty">
          暂无在线设备
          <br />
          <span style={{ fontSize: 12 }}>
            板子通过 zjy13 的 WebSocket 服务接入，zjy14 通过 HTTP 对接该服务
          </span>
        </div>
      </Card>

      <Card title="音频传输约定">
        <table>
          <tbody>
            <tr>
              <td className="dim" style={{ width: 160 }}>
                格式
              </td>
              <td className="mono">PCM S16LE / 单声道 / 24kHz</td>
            </tr>
            <tr>
              <td className="dim">块大小</td>
              <td className="mono">40–100ms（约 1920–4800 bytes）</td>
            </tr>
            <tr>
              <td className="dim">传输</td>
              <td className="mono">WebSocket 二进制帧</td>
            </tr>
            <tr>
              <td className="dim">控制帧</td>
              <td className="mono">JSON（synthesis.start / audio.chunk / synthesis.end）</td>
            </tr>
            <tr>
              <td className="dim">固件约束</td>
              <td>
                录音 16kHz 与播放 24kHz 共用 I2S 总线，
                <Badge kind="warn">半双工</Badge>
                不能同时进行
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Card title="对接方式">
        <div className="hint">
          zjy14 的 HTTP 接口与 zjy13 的 TTSClient 完全兼容（决策 D2）。
          切换只需把 zjy13 的 <span className="mono">TTS_BASE_URL</span> 从{" "}
          <span className="mono">:50000</span> 改到{" "}
          <span className="mono">:50001</span>，固件与协议一行不改。
          <br />
          回滚：改回 50000 即恢复。
        </div>
      </Card>
    </>
  );
}
