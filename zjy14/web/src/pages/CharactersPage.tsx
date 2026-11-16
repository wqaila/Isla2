/** 角色管理：列表 / 创建 / 编辑 / 删除。 */

import { useCallback, useState } from "react";
import { characterApi, describeError, speakerApi } from "../api/client";
import { EMOTIONS, type Character } from "../api/types";
import {
  Alert,
  Badge,
  Card,
  Empty,
  ErrorBox,
  Loading,
  PageHead,
} from "../components/ui";
import { useAsync } from "../hooks/useAsync";

interface FormState {
  character_id: string;
  display_name: string;
  description: string;
  personality: string;
  speaking_habits: string;
  default_emotion: string;
  default_style: string;
  speaker_id: string;
  allowed_emotions: string[];
}

const EMPTY_FORM: FormState = {
  character_id: "",
  display_name: "",
  description: "",
  personality: "",
  speaking_habits: "",
  default_emotion: "neutral",
  default_style: "",
  speaker_id: "",
  allowed_emotions: ["neutral"],
};

export default function CharactersPage() {
  const chars = useAsync(useCallback(() => characterApi.list(), []));
  const speakers = useAsync(useCallback(() => speakerApi.list(), []));

  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setErr(null);
    setShowForm(true);
  };

  const openEdit = (c: Character) => {
    setEditing(c.character_id);
    setForm({
      character_id: c.character_id,
      display_name: c.display_name,
      description: c.description,
      personality: c.personality,
      speaking_habits: c.speaking_habits,
      default_emotion: c.default_emotion,
      default_style: c.default_style,
      speaker_id: c.speaker_id ?? "",
      allowed_emotions: c.allowed_emotions.length ? c.allowed_emotions : ["neutral"],
    });
    setErr(null);
    setShowForm(true);
  };

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const body: Partial<Character> = {
        character_id: form.character_id,
        display_name: form.display_name,
        description: form.description,
        personality: form.personality,
        speaking_habits: form.speaking_habits,
        default_emotion: form.default_emotion,
        default_style: form.default_style,
        speaker_id: form.speaker_id || null,
        allowed_emotions: form.allowed_emotions,
      };
      if (editing) {
        const { character_id: _ignored, ...patch } = body;
        await characterApi.update(editing, patch);
      } else {
        await characterApi.create(body);
      }
      setShowForm(false);
      chars.reload();
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (!confirm(`确定删除角色 ${id}？此操作不可撤销。`)) return;
    try {
      await characterApi.remove(id);
      chars.reload();
    } catch (e) {
      alert(describeError(e));
    }
  };

  const toggleEmotion = (v: string) => {
    setForm((f) => ({
      ...f,
      allowed_emotions: f.allowed_emotions.includes(v)
        ? f.allowed_emotions.filter((x) => x !== v)
        : [...f.allowed_emotions, v],
    }));
  };

  return (
    <>
      <PageHead
        title="角色管理"
        desc="角色定义人设与说话习惯，并绑定一个声音档案。情绪标签仅作元数据。"
      />

      <div style={{ marginBottom: 16, display: "flex", gap: 8 }}>
        <button onClick={openCreate}>新建角色</button>
        <button className="ghost sm" onClick={chars.reload}>
          刷新
        </button>
      </div>

      {chars.error && <ErrorBox message={chars.error} />}

      {showForm && (
        <Card
          title={editing ? `编辑角色：${editing}` : "新建角色"}
          actions={
            <button className="ghost sm" onClick={() => setShowForm(false)}>
              取消
            </button>
          }
        >
          {err && <ErrorBox message={err} />}
          <div className="row">
            <div className="field">
              <label>角色 ID（唯一，仅字母数字下划线连字符）</label>
              <input
                value={form.character_id}
                disabled={!!editing}
                onChange={(e) => setForm({ ...form, character_id: e.target.value })}
                placeholder="elysia"
              />
            </div>
            <div className="field">
              <label>显示名</label>
              <input
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="爱莉希雅"
              />
            </div>
            <div className="field">
              <label>绑定声音</label>
              <select
                value={form.speaker_id}
                onChange={(e) => setForm({ ...form, speaker_id: e.target.value })}
              >
                <option value="">（不绑定）</option>
                {(speakers.data ?? []).map((s) => (
                  <option key={s.speaker_id} value={s.speaker_id}>
                    {s.speaker_id}（{s.duration_s.toFixed(1)}s）
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field">
            <label>角色描述</label>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>

          <div className="row">
            <div className="field">
              <label>性格</label>
              <textarea
                value={form.personality}
                onChange={(e) => setForm({ ...form, personality: e.target.value })}
              />
            </div>
            <div className="field">
              <label>说话习惯</label>
              <textarea
                value={form.speaking_habits}
                onChange={(e) => setForm({ ...form, speaking_habits: e.target.value })}
              />
            </div>
          </div>

          <div className="row">
            <div className="field">
              <label>默认情绪</label>
              <select
                value={form.default_emotion}
                onChange={(e) => setForm({ ...form, default_emotion: e.target.value })}
              >
                {EMOTIONS.map((e) => (
                  <option key={e.value} value={e.value}>
                    {e.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>默认风格</label>
              <input
                value={form.default_style}
                onChange={(e) => setForm({ ...form, default_style: e.target.value })}
                placeholder="gentle"
              />
            </div>
          </div>

          <div className="field">
            <label>允许的情绪（仅元数据，不影响合成）</label>
            <div className="tag-row">
              {EMOTIONS.map((e) => (
                <label
                  key={e.value}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    marginBottom: 0,
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    style={{ width: "auto" }}
                    checked={form.allowed_emotions.includes(e.value)}
                    onChange={() => toggleEmotion(e.value)}
                  />
                  {e.label}
                </label>
              ))}
            </div>
          </div>

          <button onClick={submit} disabled={busy || !form.character_id || !form.display_name}>
            {busy ? "保存中…" : editing ? "保存修改" : "创建"}
          </button>
        </Card>
      )}

      <Card title={`角色列表（${chars.data?.length ?? 0}）`}>
        {chars.loading ? (
          <Loading />
        ) : !chars.data || chars.data.length === 0 ? (
          <Empty>还没有角色。点击「新建角色」开始。</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>显示名</th>
                <th>声音</th>
                <th>默认情绪</th>
                <th>允许情绪</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {chars.data.map((c) => (
                <tr key={c.character_id}>
                  <td className="mono">{c.character_id}</td>
                  <td>{c.display_name}</td>
                  <td>
                    {c.speaker_id ? (
                      <Badge kind="ok">{c.speaker_id}</Badge>
                    ) : (
                      <Badge kind="dim">未绑定</Badge>
                    )}
                  </td>
                  <td>{c.default_emotion}</td>
                  <td className="dim" style={{ fontSize: 12 }}>
                    {c.allowed_emotions.join(" / ")}
                  </td>
                  <td className="dim mono" style={{ fontSize: 11 }}>
                    {c.updated_at?.replace("T", " ").slice(0, 19)}
                  </td>
                  <td>
                    <button className="ghost sm" onClick={() => openEdit(c)}>
                      编辑
                    </button>{" "}
                    <button className="danger sm" onClick={() => remove(c.character_id)}>
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Alert kind="info">
        角色卡里的「情绪」只是给 LLM 侧的提示，TTS 不会因此改变语气。
        想让角色听起来更开心，应该在 LLM 输出文本里用「嘿嘿~」这类措辞。
      </Alert>
    </>
  );
}
