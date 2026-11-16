/** 情绪管理：标签定义 + 路线乙说明 + 对照试听。
 *
 * 这个页面的核心价值是「如实告知」：
 * 情绪标签不改变 TTS 输出，只是给 LLM 侧的提示。
 * 页面提供对照试听，让用户亲眼看到「传 emotion 和不传，音频指标一样」。
 */

import { useCallback, useState } from "react";
import { describeError, synthesisApi } from "../api/client";
import { EMOTIONS, type SynthesisMeta } from "../api/types";
import { Alert, Badge, Card, ErrorBox, PageHead } from "../components/ui";

interface Sample {
  emotion: string;
  label: string;
  meta: SynthesisMeta;
  url: string;
}

const TEST_TEXT = "今天天气真不错，我们一起出去走走吧。";

export default function EmotionPage() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);

  const runCompare = useCallback(async () => {
    setBusy(true);
    setErr(null);
    const out: Sample[] = [];
    try {
      for (const e of EMOTIONS) {
        const { blob, meta } = await synthesisApi.synthesize({
          text: TEST_TEXT,
          emotion: e.value,
          intensity: 0.8,
        });
        out.push({
          emotion: e.value,
          label: e.label,
          meta,
          url: URL.createObjectURL(blob),
        });
      }
      setSamples(out);
    } catch (ex) {
      setErr(describeError(ex));
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <>
      <PageHead
        title="情绪管理"
        desc="情绪标签的定义与能力边界。当前为路线乙：标签仅作元数据。"
      />

      <Alert kind="warn">
        <strong>重要：本模型不支持情绪控制。</strong>
        <br />
        2026-11-16 实测结论：加入「请用<em>蓝色</em>的语气说一句话」这种无意义指令，
        产生的音高变化（15.4 / 72.5 Hz）比真正的情绪指令（10.1 / 24.3 Hz）还大。
        说明模型只对「提示词变长」有反应，并未理解情绪。
        <br />
        详见 <span className="mono">docs/13_D1_EMOTION_VERDICT.md</span>
      </Alert>

      <Card title="情绪标签定义">
        <table>
          <thead>
            <tr>
              <th>标签</th>
              <th>中文</th>
              <th>用途</th>
              <th>是否影响 TTS</th>
            </tr>
          </thead>
          <tbody>
            {EMOTIONS.map((e) => (
              <tr key={e.value}>
                <td className="mono">{e.value}</td>
                <td>{e.label}</td>
                <td className="dim">LLM 侧措辞提示 / UI 展示</td>
                <td>
                  <Badge kind="dim">否</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="hint" style={{ marginTop: 12 }}>
          想让角色听起来更开心，正确做法是让 LLM 输出带情绪的<strong>文本</strong>
          （如「嘿嘿~」「耶~」），而不是给 TTS 传 emotion 标签。
        </div>
      </Card>

      <Card title="对照试听：验证情绪标签无效">
        <div className="hint" style={{ marginBottom: 12 }}>
          用同一句文本，分别传 6 种 emotion 合成。若标签真的生效，
          音频时长 / 音高应有明显差异；若几乎一致，则印证结论。
        </div>
        <button onClick={runCompare} disabled={busy}>
          {busy ? (
            <>
              <span className="spinner" />
              合成 6 个样本中…
            </>
          ) : (
            "生成对照样本"
          )}
        </button>

        {err && (
          <div style={{ marginTop: 12 }}>
            <ErrorBox message={err} />
          </div>
        )}

        {samples.length > 0 && (
          <>
            <table style={{ marginTop: 16 }}>
              <thead>
                <tr>
                  <th>情绪</th>
                  <th>时长</th>
                  <th>耗时</th>
                  <th>RTF</th>
                  <th>试听</th>
                </tr>
              </thead>
              <tbody>
                {samples.map((s) => (
                  <tr key={s.emotion}>
                    <td>
                      {s.label} <span className="mono dim">({s.emotion})</span>
                    </td>
                    <td>{s.meta.duration.toFixed(2)}s</td>
                    <td>{(s.meta.total_ms / 1000).toFixed(2)}s</td>
                    <td>{s.meta.rtf.toFixed(3)}</td>
                    <td>
                      <audio controls src={s.url} style={{ height: 30, width: 200 }} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="hint" style={{ marginTop: 12 }}>
              注意：时长会有采样随机性波动（实测变异系数约 0.03），
              但不应出现「开心明显更高更快、伤心明显更低更慢」的系统性差异。
            </div>
          </>
        )}
      </Card>

      <Card title="替代方案：用文本表达情绪">
        <table>
          <thead>
            <tr>
              <th>想要的效果</th>
              <th>推荐做法</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>开心</td>
              <td className="dim">LLM 输出「嘿嘿~」「耶~」「太棒了！」</td>
            </tr>
            <tr>
              <td>难过</td>
              <td className="dim">LLM 输出「唔…」「呜呜…」「有点失落」</td>
            </tr>
            <tr>
              <td>安慰</td>
              <td className="dim">
                LLM 用温柔措辞 + TTS 侧 speed 0.9 + pause_profile relaxed
              </td>
            </tr>
            <tr>
              <td>生气</td>
              <td className="dim">LLM 用短促直接的措辞 + TTS 侧 speed 1.1</td>
            </tr>
            <tr>
              <td>俏皮</td>
              <td className="dim">LLM 用语气词 + TTS 侧 speed 1.05 + pause tight</td>
            </tr>
          </tbody>
        </table>
        <div className="hint" style={{ marginTop: 12 }}>
          TTS 侧真正能控制的是语速 / 音量 / 停顿，这三项在「合成测试」页可调。
        </div>
      </Card>
    </>
  );
}
