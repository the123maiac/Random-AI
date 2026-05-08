const $ = (sel, root = document) => root.querySelector(sel);
const h = (tag, attrs = {}, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
};
const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
const fmtDate = (s) => s ? new Date(s.replace(" ", "T") + (s.includes("Z") || s.includes("+") ? "" : "Z")).toLocaleString() : "—";

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { state.user = null; render(); throw new Error("unauthenticated"); }
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `HTTP ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

const state = {
  user: null,
  view: "chat",            // chat | feed | topics | usage
  keys: [], serviceKeys: [],
  chats: [], messages: [], activeChatId: null,
  topics: [], feed: [], usage: null, runs: [],
  showSettings: false,
  sending: false,
  agentic: false, useKnowledge: true,
  sidebarOpen: false,
};

async function loadChat() {
  state.chats = await api("/api/chats");
  if (state.activeChatId) {
    state.messages = await api(`/api/chats/${state.activeChatId}/messages`);
  }
}
async function loadTopics() { state.topics = await api("/api/topics"); }
async function loadFeed() { state.feed = await api("/api/feed?limit=80"); }
async function loadUsage() { state.usage = await api("/api/usage"); state.runs = await api("/api/runs?limit=20"); }
async function loadKeys() {
  state.keys = await api("/api/keys");
  state.serviceKeys = await api("/api/service-keys");
}

async function bootstrap() {
  try {
    state.user = await api("/api/me");
    await loadKeys();
    await loadChat();
  } catch { state.user = null; }
  render();
}

// -------- Login --------
function loginView() {
  let code = "", err = "", busy = false;
  const submit = async () => {
    if (busy || !code.trim()) return;
    busy = true; err = ""; render();
    try {
      const res = await api("/api/auth/login", { method: "POST", body: { code: code.trim() } });
      state.user = { display_name: res.display_name };
      await loadKeys(); await loadChat();
    } catch { err = "Invalid invite code"; }
    finally { busy = false; render(); }
  };
  return h("div", { class: "login" },
    h("div", { class: "card" },
      h("div", { class: "brand-bar" }),
      h("h1", {}, "agentic-chat"),
      h("p", { class: "muted" }, "Enter your invite code"),
      h("div", { class: "row" },
        h("label", {}, "Invite code"),
        h("input", { type: "text", autocomplete: "off", autocapitalize: "characters",
          spellcheck: "false", placeholder: "12 characters",
          oninput: e => { code = e.target.value; },
          onkeydown: e => { if (e.key === "Enter") submit(); } })
      ),
      h("button", { onclick: submit, disabled: busy }, busy ? "Signing in…" : "Sign in"),
      err && h("div", { class: "err" }, err),
    )
  );
}

// -------- App shell --------
function appView() {
  return h("div", { class: "shell" },
    sidebarView(),
    mainView(),
    state.showSettings && settingsModal(),
  );
}

function sidebarView() {
  const tab = (id, label) => h("button", {
    class: state.view === id ? "active" : "",
    onclick: () => switchView(id),
  }, label);
  return h("aside", { class: "sidebar" + (state.sidebarOpen ? " open" : "") },
    h("header", {},
      h("div", { class: "logo" }, "agentic", h("span", { class: "dot" }, "."), "chat"),
    ),
    h("div", { class: "tabs" },
      tab("chat", "💬  Chat"),
      tab("feed", "📚  Feed"),
      tab("topics", "🎯  Topics"),
      tab("usage", "⚡  Usage"),
    ),
    state.view === "chat" ? chatListPane() : null,
    h("footer", {},
      h("span", {}, state.user?.display_name || ""),
      h("button", { class: "ghost tiny", onclick: () => { state.showSettings = true; render(); } }, "Settings"),
    ),
  );
}

function chatListPane() {
  return h("div", { class: "chats" },
    h("div", { class: "new-chat" },
      h("button", { onclick: newChat, disabled: state.keys.length === 0 }, "+ New chat"),
    ),
    ...state.chats.map(c =>
      h("div", {
        class: "item" + (c.id === state.activeChatId ? " active" : ""),
        onclick: () => { state.sidebarOpen = false; loadMessages(c.id); },
        title: c.title,
      }, c.title)
    ),
    state.chats.length === 0 && state.keys.length > 0
      ? h("div", { class: "item muted", style: "cursor:default" }, "No chats yet")
      : null,
  );
}

async function switchView(view) {
  state.view = view;
  state.sidebarOpen = false;
  if (view === "feed") await loadFeed();
  if (view === "topics") { await loadTopics(); await loadKeys(); }
  if (view === "usage") await loadUsage();
  render();
}

function mainView() {
  if (state.view === "chat") return chatPane();
  if (state.view === "feed") return feedPane();
  if (state.view === "topics") return topicsPane();
  if (state.view === "usage") return usagePane();
  return h("section", { class: "main" });
}

// -------- Chat pane --------
function chatPane() {
  const chat = state.chats.find(c => c.id === state.activeChatId);
  return h("section", { class: "main" },
    h("header", { class: "bar" },
      h("button", { class: "menu-btn ghost", onclick: () => { state.sidebarOpen = !state.sidebarOpen; render(); } }, "☰"),
      h("div", { class: "title" }, chat ? chat.title : "agentic-chat"),
      chat && h("span", { class: "muted", style: "font-size:0.85rem" }, chat.model || ""),
      chat && h("button", { class: "danger tiny", onclick: () => deleteChat(chat.id) }, "Delete"),
      h("button", { class: "ghost tiny", onclick: logout }, "Sign out"),
    ),
    chat ? h("div", { class: "content" },
      h("div", { class: "toolbar" },
        h("label", {},
          h("input", { type: "checkbox", checked: state.agentic ? true : false,
            onchange: e => { state.agentic = e.target.checked; render(); } }),
          "Agentic (tool use)"
        ),
        h("label", {},
          h("input", { type: "checkbox", checked: state.useKnowledge ? true : false,
            onchange: e => { state.useKnowledge = e.target.checked; render(); } }),
          "Use knowledge base"
        ),
      ),
      messagesView(),
    ) : emptyChatView(),
    chat && composerView(),
  );
}

function emptyChatView() {
  if (state.keys.length === 0) {
    return h("div", { class: "empty" },
      h("div", {},
        h("p", {}, "No provider keys yet."),
        h("p", { class: "muted" }, "Open ", h("span", { style: "color:var(--accent)" }, "Settings"), " to add one."),
      )
    );
  }
  return h("div", { class: "empty" },
    h("div", {}, h("p", {}, "Start a new chat from the sidebar."))
  );
}

function messagesView() {
  return h("div", { class: "messages", id: "messages" },
    ...state.messages.map(m =>
      h("div", { class: "msg " + m.role },
        h("div", { class: "who" }, m.role === "user" ? "Y" : "AI"),
        h("div", { class: "body" }, m.content),
      )
    ),
    state.sending && h("div", { class: "msg assistant" },
      h("div", { class: "who" }, "AI"),
      h("div", { class: "body muted" }, state.agentic ? "thinking, may use tools…" : "thinking…"),
    ),
  );
}

function composerView() {
  let val = "";
  const send = async () => {
    const text = val.trim();
    if (!text || state.sending) return;
    state.sending = true;
    state.messages.push({ id: Date.now(), role: "user", content: text });
    const ta = $("#composer-ta"); if (ta) ta.value = "";
    val = ""; render();
    try {
      const reply = await api(`/api/chats/${state.activeChatId}/messages`, {
        method: "POST",
        body: { content: text, agentic: state.agentic, use_knowledge: state.useKnowledge },
      });
      state.messages.push(reply);
    } catch (e) {
      state.messages.push({ id: Date.now() + 1, role: "assistant", content: "⚠ " + e.message });
    } finally { state.sending = false; render(); }
  };
  return h("div", { class: "composer" },
    h("textarea", {
      id: "composer-ta", placeholder: "Message…", rows: "1",
      oninput: e => { val = e.target.value; e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px"; },
      onkeydown: e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } },
    }),
    h("button", { onclick: send, disabled: state.sending }, state.sending ? "…" : "Send"),
  );
}

// -------- Feed pane --------
function feedPane() {
  return h("section", { class: "main" },
    h("header", { class: "bar" },
      h("div", { class: "title" }, "Feed — Knowledge base"),
      h("span", { class: "muted", style: "font-size:0.85rem" }, state.feed.length + " entries"),
      h("button", { class: "ghost tiny", onclick: () => loadFeed().then(render) }, "Refresh"),
    ),
    h("div", { class: "content padded" },
      state.feed.length === 0
        ? h("div", { class: "muted" }, "No knowledge yet. Add topics + a Brave key, and the research loop will populate this.")
        : h("div", { class: "list" }, ...state.feed.map(feedEntry)),
    ),
  );
}

function feedEntry(e) {
  return h("div", { class: "entry" },
    h("div", { class: "top" },
      e.source_url
        ? h("a", { href: e.source_url, target: "_blank", rel: "noopener" }, e.title || e.source_url)
        : h("strong", {}, e.title || "(no title)"),
      h("div", {},
        e.topic_name && h("span", { class: "pill" }, e.topic_name),
        " ",
        h("button", { class: "danger tiny", onclick: async () => { await api(`/api/feed/${e.id}`, { method: "DELETE" }); await loadFeed(); render(); } }, "×"),
      ),
    ),
    h("div", { class: "meta" }, fmtDate(e.created_at)),
    h("p", {}, e.summary),
  );
}

// -------- Topics pane --------
function topicsPane() {
  let form = { name: "", query: "", interval_hours: 4, provider_key_id: "", model: "" };
  let busy = false, err = "";
  const create = async () => {
    if (busy) return;
    if (!form.name || !form.query || !form.provider_key_id) { err = "Name, query, and provider key are required."; render(); return; }
    busy = true; err = ""; render();
    try {
      await api("/api/topics", { method: "POST", body: {
        name: form.name, query: form.query,
        interval_hours: parseInt(form.interval_hours, 10) || 4,
        provider_key_id: parseInt(form.provider_key_id, 10),
        model: form.model || null, enabled: true,
      } });
      form = { name: "", query: "", interval_hours: 4, provider_key_id: form.provider_key_id, model: "" };
      await loadTopics();
    } catch (e) { err = e.message; }
    finally { busy = false; render(); }
  };
  const runNow = async (id) => { try { await api(`/api/topics/${id}/run-now`, { method: "POST" }); alert("Started — check the Feed in a minute."); } catch (e) { alert(e.message); } };
  const toggle = async (t) => {
    await api(`/api/topics/${t.id}`, { method: "PATCH", body: {
      name: t.name, query: t.query, interval_hours: t.interval_hours,
      provider_key_id: t.provider_key_id, model: t.model, enabled: !t.enabled,
    } });
    await loadTopics(); render();
  };
  const del = async (id) => {
    if (!confirm("Delete this topic? Knowledge entries are preserved.")) return;
    await api(`/api/topics/${id}`, { method: "DELETE" });
    await loadTopics(); render();
  };

  return h("section", { class: "main" },
    h("header", { class: "bar" }, h("div", { class: "title" }, "Research topics")),
    h("div", { class: "content padded" },
      h("div", { class: "card" },
        h("h3", { style: "margin-top:0" }, "Add topic"),
        h("div", { class: "row" }, h("label", {}, "Name"), h("input", { value: form.name, oninput: e => { form.name = e.target.value; } })),
        h("div", { class: "row" }, h("label", {}, "Search query"), h("input", { value: form.query, placeholder: "e.g. FastAPI production patterns", oninput: e => { form.query = e.target.value; } })),
        h("div", { class: "row" }, h("label", {}, "Interval (hours, min 1)"), h("input", { type: "number", min: "1", value: form.interval_hours, oninput: e => { form.interval_hours = e.target.value; } })),
        h("div", { class: "row" },
          h("label", {}, "Provider key (must be openai_compat)"),
          h("select", { onchange: e => { form.provider_key_id = e.target.value; } },
            h("option", { value: "" }, "— select —"),
            ...state.keys.filter(k => k.provider === "openai_compat").map(k =>
              h("option", { value: String(k.id), selected: String(k.id) === String(form.provider_key_id) ? "true" : false },
                `${k.label} · ${k.default_model}`)
            ),
          ),
        ),
        h("div", { class: "row" }, h("label", {}, "Model override (optional)"), h("input", { value: form.model, oninput: e => { form.model = e.target.value; } })),
        err && h("div", { class: "err" }, err),
        h("div", { class: "actions" }, h("button", { onclick: create, disabled: busy }, busy ? "Saving…" : "Add topic")),
      ),
      h("div", { class: "divider" }),
      state.topics.length === 0
        ? h("div", { class: "muted" }, "No topics yet.")
        : h("div", { class: "list" }, ...state.topics.map(t =>
            h("div", { class: "topic" },
              h("div", { class: "name" }, t.name, " ",
                h("span", { class: "pill " + (t.enabled ? "on" : "off") }, t.enabled ? "on" : "off")),
              h("div", { class: "controls" },
                h("button", { class: "ghost tiny", onclick: () => runNow(t.id) }, "Run now"),
                h("button", { class: "ghost tiny", onclick: () => toggle(t) }, t.enabled ? "Pause" : "Resume"),
                h("button", { class: "danger tiny", onclick: () => del(t.id) }, "Delete"),
              ),
              h("div", { class: "meta" },
                t.query, " · every ", String(t.interval_hours), "h · last: ",
                t.last_run_at ? fmtDate(t.last_run_at) : "never",
                " · next: ", t.next_run_at ? fmtDate(t.next_run_at) : "soon",
              ),
            ),
          )),
    ),
  );
}

// -------- Usage pane --------
function usagePane() {
  const u = state.usage;
  if (!u) return h("section", { class: "main" }, h("div", { class: "content padded muted" }, "Loading…"));
  const toggleKill = async () => { await api("/api/kill-switch", { method: "POST", body: { killed: !u.killed } }); await loadUsage(); render(); };
  return h("section", { class: "main" },
    h("header", { class: "bar" },
      h("div", { class: "title" }, "Usage today (UTC)"),
      h("span", { class: "pill " + (u.killed ? "off" : "on") }, u.killed ? "PAUSED" : "ACTIVE"),
      h("button", { class: u.killed ? "" : "danger", onclick: toggleKill },
        u.killed ? "Resume background work" : "🛑 Kill switch (pause all)"),
      h("button", { class: "ghost tiny", onclick: () => loadUsage().then(render) }, "Refresh"),
    ),
    h("div", { class: "content padded" },
      h("div", { class: "usage-grid" },
        ...u.buckets.map(b => {
          const pct = b.cap > 0 ? Math.min(100, Math.round(100 * b.used / b.cap)) : 0;
          return h("div", { class: "usage-item" },
            h("div", { class: "lbl" }, b.bucket),
            h("div", { class: "num" }, b.used, h("span", { class: "muted" }, " / " + b.cap)),
            h("div", { class: "bar-bg" }, h("div", { class: "bar-fg", style: `width:${pct}%` })),
          );
        }),
      ),
      h("h3", {}, "Recent runs"),
      state.runs.length === 0
        ? h("div", { class: "muted" }, "No runs yet.")
        : h("div", { class: "list" }, ...state.runs.map(r =>
            h("div", { class: "entry" },
              h("div", { class: "top" },
                h("div", {}, h("strong", {}, r.kind), " · ",
                  h("span", { class: "pill " + (r.status === "ok" ? "on" : r.status === "running" ? "" : "off") }, r.status),
                  " · ", r.steps, " steps"),
                h("div", { class: "meta" }, fmtDate(r.started_at)),
              ),
              r.output && h("div", { class: "meta", style: "white-space:pre-wrap" }, r.output.slice(0, 400)),
              r.error && h("div", { class: "err" }, r.error.slice(0, 400)),
            ),
          )),
    ),
  );
}

// -------- Settings modal (provider keys + service keys) --------
function settingsModal() {
  let form = { provider: "openai_compat", label: "", api_key: "", base_url: "", default_model: "" };
  let svcForm = { service: "brave_search", token: "" };
  let busy = false, err = "";
  const close = () => { state.showSettings = false; render(); };
  const refresh = async () => { await loadKeys(); render(); };
  const saveKey = async () => {
    if (busy) return; busy = true; err = ""; render();
    try {
      await api("/api/keys", { method: "POST", body: {
        provider: form.provider, label: form.label || form.provider,
        api_key: form.api_key, base_url: form.base_url || null, default_model: form.default_model,
      } });
      form = { provider: "openai_compat", label: "", api_key: "", base_url: "", default_model: "" };
      await refresh();
    } catch (e) { err = e.message; } finally { busy = false; render(); }
  };
  const delKey = async (id) => {
    if (!confirm("Delete this provider key?")) return;
    await api(`/api/keys/${id}`, { method: "DELETE" }); await refresh();
  };
  const saveSvc = async () => {
    if (!svcForm.token) return;
    await api("/api/service-keys", { method: "POST", body: svcForm });
    svcForm = { ...svcForm, token: "" };
    await refresh();
  };
  const delSvc = async (svc) => {
    if (!confirm(`Delete ${svc} key?`)) return;
    await api(`/api/service-keys/${svc}`, { method: "DELETE" }); await refresh();
  };

  return h("div", { class: "modal-bg", onclick: e => { if (e.target.classList.contains("modal-bg")) close(); } },
    h("div", { class: "card modal" },
      h("h2", {}, "Settings"),

      h("h3", {}, "Provider keys (LLM)"),
      h("div", { class: "key-list" },
        ...state.keys.map(k =>
          h("div", { class: "key-item" },
            h("div", {},
              h("div", {}, k.label, " ", h("span", { class: "meta" }, "(" + k.provider + " · " + k.default_model + ")")),
              k.base_url && h("div", { class: "meta" }, k.base_url),
            ),
            h("button", { class: "danger tiny", onclick: () => delKey(k.id) }, "Remove"),
          )
        ),
        state.keys.length === 0 && h("div", { class: "meta" }, "No provider keys yet."),
      ),
      h("div", { class: "row" }, h("label", {}, "Provider"),
        h("select", { onchange: e => { form.provider = e.target.value; } },
          h("option", { value: "openai_compat" }, "openai_compat (OpenAI / NIM / Groq / OpenRouter / Ollama)"),
          h("option", { value: "anthropic" }, "anthropic (Claude)"),
          h("option", { value: "gemini" }, "gemini (Google)"),
        )),
      h("div", { class: "row" }, h("label", {}, "Label"), h("input", { placeholder: "e.g. nvidia-nim", oninput: e => { form.label = e.target.value; } })),
      h("div", { class: "row" }, h("label", {}, "API key"), h("input", { type: "password", oninput: e => { form.api_key = e.target.value; } })),
      h("div", { class: "row" }, h("label", {}, "Base URL (openai_compat — e.g. https://integrate.api.nvidia.com/v1)"), h("input", { oninput: e => { form.base_url = e.target.value; } })),
      h("div", { class: "row" }, h("label", {}, "Default model"), h("input", { placeholder: "e.g. meta/llama-3.1-70b-instruct", oninput: e => { form.default_model = e.target.value; } })),
      err && h("div", { class: "err" }, err),
      h("div", { class: "actions" }, h("button", { onclick: saveKey, disabled: busy }, busy ? "Saving…" : "Add provider key")),

      h("div", { class: "divider" }),

      h("h3", {}, "Service keys (search / fetch / email / git)"),
      h("div", { class: "key-list" },
        ...state.serviceKeys.map(s =>
          h("div", { class: "key-item" },
            h("div", {}, s.service, " ", h("span", { class: "meta" }, fmtDate(s.created_at))),
            h("button", { class: "danger tiny", onclick: () => delSvc(s.service) }, "Remove"),
          )
        ),
        state.serviceKeys.length === 0 && h("div", { class: "meta" }, "No service keys yet."),
      ),
      h("div", { class: "row" }, h("label", {}, "Service"),
        h("select", { onchange: e => { svcForm.service = e.target.value; } },
          h("option", { value: "brave_search" }, "brave_search (web search)"),
          h("option", { value: "firecrawl" }, "firecrawl (JS-heavy fetch)"),
          h("option", { value: "github" }, "github (PAT, optional)"),
          h("option", { value: "resend" }, "resend (email)"),
        )),
      h("div", { class: "row" }, h("label", {}, "Token"), h("input", { type: "password", value: svcForm.token, oninput: e => { svcForm.token = e.target.value; } })),
      h("div", { class: "actions" },
        h("button", { class: "ghost", onclick: close }, "Close"),
        h("button", { onclick: saveSvc }, "Save service key"),
      ),
    )
  );
}

async function loadMessages(chatId) {
  state.activeChatId = chatId;
  state.messages = await api(`/api/chats/${chatId}/messages`);
  render();
}

async function newChat() {
  if (state.keys.length === 0) return;
  const key = state.keys[0];
  const res = await api("/api/chats", { method: "POST", body: { provider_key_id: key.id, model: key.default_model, title: "New chat" } });
  state.chats = await api("/api/chats");
  await loadMessages(res.id);
}

async function deleteChat(id) {
  if (!confirm("Delete this chat?")) return;
  await api(`/api/chats/${id}`, { method: "DELETE" });
  state.chats = await api("/api/chats");
  if (state.activeChatId === id) { state.activeChatId = null; state.messages = []; }
  render();
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  Object.assign(state, { user: null, keys: [], serviceKeys: [], chats: [], messages: [], activeChatId: null, topics: [], feed: [], usage: null, runs: [] });
  render();
}

function render() {
  const root = $("#app");
  clear(root);
  root.append(state.user ? appView() : loginView());
  const m = $("#messages");
  if (m) m.scrollTop = m.scrollHeight;
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

bootstrap();
