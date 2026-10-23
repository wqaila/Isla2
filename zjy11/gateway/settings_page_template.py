SETTINGS_HTML = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>zjy11 网关设置</title>
<style>
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#0f1216; --panel:#171c22; --panel-2:#1e252d;
  --line:#2a333d; --line-soft:#212a33;
  --text:#e8edf2; --muted:#8f9ba6; --muted-2:#6b7784;
  --accent:#e0849f; --accent-strong:#c96b8a;
  --ok:#5fca8a; --warn:#e8c06a; --err:#f08b8b;
  --radius:14px;
}
html,body{height:100%}
body{
  margin:0; background:
    radial-gradient(1100px 560px at 82% -12%, #2a1b24 0%, transparent 62%),
    radial-gradient(900px 520px at 6% 108%, #16222b 0%, transparent 58%),
    var(--bg);
  color:var(--text);
  font:15px/1.65 "Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{min-height:100%;display:grid;place-items:center;padding:40px 20px}
.card{
  width:min(660px,100%);
  background:linear-gradient(180deg,rgba(30,37,45,.92),rgba(23,28,34,.96));
  border:1px solid var(--line);
  border-radius:var(--radius);
  box-shadow:0 24px 70px -30px rgba(0,0,0,.9),0 2px 0 rgba(255,255,255,.02) inset;
  overflow:hidden;
  animation:rise .32s cubic-bezier(.2,.8,.2,1) both;
}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.card{animation:none}}

.brand{
  display:flex;align-items:center;gap:14px;
  padding:26px 30px 22px;border-bottom:1px solid var(--line-soft);
}
.mark{
  width:44px;height:44px;flex:0 0 44px;border-radius:13px;
  display:grid;place-items:center;font-size:20px;font-weight:600;
  background:linear-gradient(150deg,var(--accent),#8d5f8f);
  color:#fff;letter-spacing:.5px;
  box-shadow:0 8px 22px -10px rgba(224,132,159,.85);
}
.brand h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.2px}
.brand p{margin:2px 0 0;font-size:12.5px;color:var(--muted)}

.body{padding:26px 30px 30px}
section[hidden]{display:none}

.field{margin-bottom:18px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:7px;font-weight:500}
label .req{color:var(--accent);margin-left:3px}

input[type=text],input[type=password],select{
  width:100%;padding:12px 14px;font-size:14px;color:var(--text);
  background:#0c1014;border:1px solid var(--line);border-radius:10px;
  transition:border-color .15s,box-shadow .15s;
}
input::placeholder{color:var(--muted-2)}
input:hover,select:hover{border-color:#37424e}
input:focus,select:focus{outline:none;border-color:var(--accent-strong);box-shadow:0 0 0 3px rgba(224,132,159,.16)}
input:focus-visible,select:focus-visible,button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
input:disabled,select:disabled{opacity:.55;cursor:not-allowed}

.hint{font-size:12px;color:var(--muted-2);margin-top:7px;line-height:1.55}
.hint code,code{background:#0c1014;border:1px solid var(--line-soft);padding:1.5px 6px;border-radius:5px;font-size:12px}

.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:22px}
button{
  font:inherit;font-size:14px;font-weight:500;
  padding:11px 20px;border-radius:10px;cursor:pointer;
  background:var(--panel-2);color:var(--text);
  border:1px solid var(--line);
  transition:background .15s,border-color .15s,transform .08s;
}
button:hover:not(:disabled){background:#252e38;border-color:#3a4653}
button:active:not(:disabled){transform:translateY(1px)}
button:disabled{opacity:.5;cursor:not-allowed}
button.primary{
  background:linear-gradient(180deg,var(--accent),var(--accent-strong));
  border-color:transparent;color:#fff;
  box-shadow:0 10px 24px -14px rgba(224,132,159,.9);
}
button.primary:hover:not(:disabled){filter:brightness(1.06);background:linear-gradient(180deg,var(--accent),var(--accent-strong))}
button.ghost{background:transparent}

.note{
  display:flex;gap:10px;align-items:flex-start;
  background:rgba(232,192,106,.07);border:1px solid rgba(232,192,106,.28);
  border-radius:10px;padding:12px 14px;font-size:12.5px;color:#e7d3a6;
  margin-bottom:22px;line-height:1.6;
}
.note svg{flex:0 0 16px;margin-top:2px}

.status{font-size:13px;min-height:20px;margin-top:14px;display:flex;align-items:center;gap:7px}
.status:empty{display:none}
.status.ok{color:var(--ok)} .status.err{color:var(--err)} .status.busy{color:var(--muted)}
.dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex:0 0 7px}

.switch{display:flex;align-items:flex-start;gap:11px;margin-top:6px}
.switch input{width:17px;height:17px;margin:3px 0 0;accent-color:var(--accent-strong);cursor:pointer;flex:0 0 17px}
.switch label{margin:0;color:var(--text);font-size:14px;font-weight:400;cursor:pointer}
.switch .hint{margin-top:3px}

.kv{display:grid;grid-template-columns:88px 1fr;gap:9px 16px;font-size:13px;margin:0}
.kv dt{color:var(--muted)}
.kv dd{margin:0;color:var(--text);word-break:break-all}

.foot{
  padding:16px 30px;border-top:1px solid var(--line-soft);
  display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;
  font-size:12px;color:var(--muted-2);background:rgba(0,0,0,.16);
}
.foot a{color:var(--muted);text-decoration:none;border-bottom:1px dashed var(--line)}
.foot a:hover{color:var(--text)}

.badge{
  display:inline-flex;align-items:center;gap:6px;font-size:12px;
  padding:3px 9px;border-radius:999px;
  background:rgba(143,155,166,.12);border:1px solid var(--line);color:var(--muted);
}
.badge.on{background:rgba(95,202,138,.12);border-color:rgba(95,202,138,.35);color:var(--ok)}
.badge.off{background:rgba(240,139,139,.1);border-color:rgba(240,139,139,.3);color:var(--err)}

@media (max-width:520px){
  .brand{padding:22px 20px 18px}
  .body{padding:22px 20px 26px}
  .foot{padding:14px 20px}
}
</style>
</head>
<body><div class="wrap">
<div class="card">

  <div class="brand">
    <div class="mark" aria-hidden="true">爱</div>
    <div>
      <h1>zjy11 网关设置</h1>
      <p>朋友型爱莉希雅 · 云端模型配置</p>
    </div>
  </div>

  <div class="body">

    <section id="loginView">
      <div class="note">
        <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M8 1a3.5 3.5 0 0 1 3.5 3.5V6H12a1.5 1.5 0 0 1 1.5 1.5v5A1.5 1.5 0 0 1 12 14H4a1.5 1.5 0 0 1-1.5-1.5v-5A1.5 1.5 0 0 1 4 6h.5V4.5A3.5 3.5 0 0 1 8 1Zm0 1.2A2.3 2.3 0 0 0 5.7 4.5V6h4.6V4.5A2.3 2.3 0 0 0 8 2.2Z"/>
        </svg>
        <span>本页面只允许本机访问。先验证管理令牌，通过后才能修改云端配置。</span>
      </div>

      <div class="field">
        <label for="admin">管理令牌<span class="req">*</span></label>
        <input id="admin" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴管理令牌">
        <p class="hint">令牌位于本机 <code>.local/admin_token</code>，用记事本打开复制。它与设备令牌、模型 API Key 都不是同一个东西。</p>
      </div>

      <div class="row">
        <button id="login" class="primary">登录</button>
      </div>
      <div id="loginStatus" class="status" role="status" aria-live="polite"></div>
    </section>

    <section id="settingsView" hidden>
      <div class="note">
        <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm0 3a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8ZM7.2 7h1.6v5H7.2V7Z"/>
        </svg>
        <span>保存密钥意味着对话内容会发送给你选择的服务商，请确认费用与数据政策。密钥只写入本机 <code>.env</code>，页面只显示末尾 4 位。</span>
      </div>

      <div class="row" style="margin:0 0 20px">
        <span class="badge" id="stateBadge">读取中…</span>
        <span class="badge" id="enabledBadge">—</span>
      </div>

      <div class="field">
        <label for="provider">服务商预设</label>
        <select id="provider">
          <option value="https://api.deepseek.com/v1|deepseek-chat">DeepSeek（deepseek-chat）</option>
          <option value="https://dashscope.aliyuncs.com/compatible-mode/v1|qwen-plus">通义千问（qwen-plus）</option>
          <option value="">自定义</option>
        </select>
      </div>

      <div class="field">
        <label for="baseUrl">接口地址<span class="req">*</span></label>
        <input id="baseUrl" type="text" value="https://api.deepseek.com/v1" spellcheck="false">
        <p class="hint">必须是 https 地址，通常以 <code>/v1</code> 结尾。</p>
      </div>

      <div class="field">
        <label for="model">模型标识<span class="req">*</span></label>
        <input id="model" type="text" value="deepseek-chat" spellcheck="false">
      </div>

      <div class="field">
        <label for="apiKey">API Key</label>
        <input id="apiKey" type="password" autocomplete="off" spellcheck="false" placeholder="留空表示不修改已保存的密钥">
        <p class="hint" id="keyHint">尚未保存密钥</p>
      </div>

      <div class="switch">
        <input id="enabled" type="checkbox">
        <div>
          <label for="enabled">允许网关调用云端</label>
          <p class="hint">关闭时聊天会返回「未启用」，不会产生费用。</p>
        </div>
      </div>

      <div class="row">
        <button id="test">测试连接</button>
        <button id="save" class="primary">保存配置</button>
        <button id="logout" class="ghost">退出登录</button>
      </div>
      <div id="status" class="status" role="status" aria-live="polite"></div>
    </section>

  </div>

  <div class="foot">
    <span>配置保存后需重启网关生效</span>
    <a href="/health" target="_blank" rel="noopener">健康状态</a>
  </div>

</div>
</div>
<script>
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let loggedIn = false;

  function setStatus(el, kind, text) {
    el.className = "status" + (kind ? " " + kind : "");
    el.innerHTML = "";
    if (!text) return;
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.setAttribute("aria-hidden", "true");
    el.appendChild(dot);
    const span = document.createElement("span");
    span.textContent = text;
    el.appendChild(span);
  }

  function showLogin() {
    loggedIn = false;
    $("loginView").hidden = false;
    $("settingsView").hidden = true;
    $("admin").focus();
  }

  function showSettings() {
    loggedIn = true;
    $("loginView").hidden = true;
    $("settingsView").hidden = false;
    loadState();
  }

  async function loadState() {
    try {
      const r = await fetch("/settings/state", { credentials: "same-origin" });
      if (r.status === 401) { showLogin(); setStatus($("loginStatus"), "err", "登录已失效，请重新登录"); return; }
      if (!r.ok) return;
      const s = await r.json();
      if (s.base_url) $("baseUrl").value = s.base_url;
      if (s.model) $("model").value = s.model;
      $("enabled").checked = !!s.enabled;
      $("keyHint").textContent = s.api_key_set
        ? "已保存密钥（末尾 " + s.api_key_hint + "），留空表示不修改"
        : "尚未保存密钥";
      const badge = $("stateBadge");
      badge.textContent = s.api_key_set ? "已配置密钥" : "未配置密钥";
      badge.className = "badge " + (s.api_key_set ? "on" : "off");
      const eb = $("enabledBadge");
      eb.textContent = s.enabled ? "云端调用已开启" : "云端调用已关闭";
      eb.className = "badge " + (s.enabled ? "on" : "off");
    } catch (err) {
      setStatus($("status"), "err", "读取配置失败：" + err.message);
    }
  }

  $("login").addEventListener("click", async () => {
    const token = $("admin").value.trim();
    if (!token) { setStatus($("loginStatus"), "err", "请先填写管理令牌"); return; }
    const btn = $("login");
    btn.disabled = true;
    setStatus($("loginStatus"), "busy", "正在验证…");
    try {
      const r = await fetch("/settings/login", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (r.ok) {
        $("admin").value = "";
        setStatus($("loginStatus"), "ok", "");
        showSettings();
      } else {
        setStatus($("loginStatus"), "err", r.status === 401 ? "管理令牌不正确" : "登录失败（HTTP " + r.status + "）");
      }
    } catch (err) {
      setStatus($("loginStatus"), "err", "请求失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  });

  $("admin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("login").click();
  });

  $("provider").addEventListener("change", (e) => {
    const parts = e.target.value.split("|");
    if (parts[0]) {
      $("baseUrl").value = parts[0];
      $("model").value = parts[1] || "";
    }
  });

  function payload(includeKey) {
    const body = {
      model: $("model").value.trim(),
      base_url: $("baseUrl").value.trim(),
      enabled: $("enabled").checked,
    };
    const key = $("apiKey").value.trim();
    if (includeKey && key) body.api_key = key;
    return body;
  }

  $("test").addEventListener("click", async () => {
    const body = payload(true);
    if (!body.api_key) { setStatus($("status"), "err", "测试连接需要填写 API Key"); return; }
    const btn = $("test");
    btn.disabled = true;
    setStatus($("status"), "busy", "正在测试连接…");
    try {
      const r = await fetch("/settings/test", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (r.ok && data.ok) setStatus($("status"), "ok", "连接成功，" + (data.message || "模型可调用"));
      else setStatus($("status"), "err", data.message || ("测试失败（HTTP " + r.status + "）"));
    } catch (err) {
      setStatus($("status"), "err", "请求失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  });

  $("save").addEventListener("click", async () => {
    const body = payload(true);
    if (!body.model || !body.base_url) { setStatus($("status"), "err", "请填写接口地址与模型标识"); return; }
    const btn = $("save");
    btn.disabled = true;
    setStatus($("status"), "busy", "正在保存…");
    try {
      const r = await fetch("/settings/save", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      if (r.ok) {
        $("apiKey").value = "";
        setStatus($("status"), "ok", "已保存，重启网关后生效");
        await loadState();
      } else {
        setStatus($("status"), "err", data.detail || data.message || ("保存失败（HTTP " + r.status + "）"));
      }
    } catch (err) {
      setStatus($("status"), "err", "请求失败：" + err.message);
    } finally {
      btn.disabled = false;
    }
  });

  $("logout").addEventListener("click", async () => {
    await fetch("/settings/logout", { method: "POST", credentials: "same-origin" });
    showLogin();
    setStatus($("loginStatus"), "ok", "已退出登录");
  });

  loadState();
  if (!loggedIn) $("admin").focus();
})();
</script>
</body></html>"""
