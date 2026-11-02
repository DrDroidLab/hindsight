import { escape as e, markdown } from "./safe";
type Obj = { [key: string]: any };
type RecordDoc = Obj & {
  id: string;
  title: string;
  revision: string;
  body: string;
  meta: Obj;
  scope: string;
};
const app = document.querySelector<HTMLDivElement>("#app")!;
let identity: Obj | null = null,
  csrf = "",
  setup = false,
  counts: Obj = {},
  sync: Obj = {},
  items: RecordDoc[] = [],
  selected: RecordDoc | null = null;
let status = "proposed",
  scope = "",
  search = "",
  source = "",
  service = "",
  cursor = "",
  nextCursor = "",
  message = "",
  failure = "",
  busy = false,
  mode = "detail";
let services: RecordDoc[] = [],
  runbooks: RecordDoc[] = [],
  incidents: RecordDoc[] = [],
  preview: Obj | null = null,
  operationId = "",
  enrollmentKey = "",
  editDraft: Obj | null = null,
  promotionDraft: Obj | null = null;
const meta = (r: RecordDoc, key: string, fallback: any = "") =>
  r[key] ?? r.meta?.[key] ?? fallback;
const sourceOf = (r: RecordDoc) => meta(r, "source", {});
const serviceNames = (r: RecordDoc) =>
  meta(r, "service_ids", [])
    .map((id: string) => services.find((s) => s.id === id)?.title || id)
    .join(", ");
const date = (v: any) =>
  v && Number.isFinite(new Date(v).getTime())
    ? new Date(v).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "Not recorded";
const state = (r: RecordDoc) => meta(r, "status", "proposed");
const badge = (s: string) =>
  `<span class="pill ${e(s)}">${e(s[0]?.toUpperCase() + s.slice(1))}</span>`;
function button(text: string, action: string, cls = "", disabled = false) {
  return `<button type="button" class="${cls}" data-action="${action}" ${disabled || busy ? "disabled" : ""}>${text}</button>`;
}
class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
async function api(path: string, method = "GET", body?: Obj): Promise<any> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response
    .json()
    .catch(() => ({ detail: "The server returned an unreadable response." }));
  if (!response.ok)
    throw new ApiError(response.status, data.detail || "Request failed.");
  if (data.sync_state) sync = data.sync_state;
  return data;
}
async function act(work: () => Promise<void>) {
  busy = true;
  failure = "";
  render();
  try {
    await work();
  } catch (err) {
    const error = err as ApiError;
    failure =
      error.status === 409
        ? "Revision conflict: another reviewer or git sync changed this record. Your change was not saved. Copy any edits, then reload the latest revision."
        : error.message;
    if (error.status === 401) {
      identity = null;
      csrf = "";
    }
  } finally {
    busy = false;
    render();
  }
}
async function loadQueue(append = false) {
  const query = new URLSearchParams({
    status,
    ...(scope ? { scope } : {}),
    ...(service ? { service } : {}),
    ...(append && cursor ? { cursor } : {}),
  });
  const data = await api(`/api/review?${query}`);
  items = append ? [...items, ...data.items] : data.items;
  counts = data.counts || {};
  nextCursor = data.next_cursor || "";
  if (selected && !items.some((r) => r.id === selected!.id)) selected = null;
}
async function loadSelected(id: string) {
  selected = await api(`/api/clues/${encodeURIComponent(id)}`);
  mode = "detail";
  preview = null;
}
const brand = '<div class="brand"><span class="mark">h</span>Hindsight</div>';
function auth() {
  if (enrollmentKey) {
    app.innerHTML = `<main class="auth">${brand}<section class="panel pad"><h1>Save your personal key</h1><p>This is shown once. Store it in a password manager. It is not saved in browser storage.</p><pre class="excerpt">${e(enrollmentKey)}</pre><p>Continue to sign in after saving your key.</p>${button("I saved my key · Sign in", "continue")}</section></main>`;
    return;
  }
  app.innerHTML = `<main class="auth">${brand}<p class="muted">Self-hosted operational memory</p><section class="panel pad"><h1>${setup ? "Set up your instance" : "Sign in to review clues"}</h1><p class="muted">${setup ? "Use the single-use enrollment token from your operator terminal." : "Your personal API key determines your identity and accessible teams."}</p>${failure ? `<p role="alert" class="notice error">${e(failure)}</p>` : ""}<form id="auth-form">${setup ? `<label>Enrollment token<input name="token" type="password" autocomplete="off" required></label><label>Your name<input name="name" autocomplete="name" required></label><label>Email<input name="email" type="email" autocomplete="email" required></label><label>Initial team<input name="team" pattern="[a-z0-9][a-z0-9\\-]{0,62}" placeholder="platform" required></label>` : '<label>API key<input name="key" type="password" autocomplete="off" required></label>'}<button class="primary" ${busy ? "disabled" : ""}>${setup ? "Create identity" : "Sign in"}</button></form><h3>Need ${setup ? "a token" : "a key"}?</h3><p>Ask the person running this instance. ${setup ? "Run hindsight admin bootstrap in the server container." : "Use a key assigned to your team."}</p><p class="small muted">Keys never enter URLs or browser storage. Sign out ends the browser session.</p></section><p class="small muted">Git-backed knowledge · No telemetry · No model provider required</p></main>`;
}
const clueMutationsLocked = () =>
  identity?.read_only === true || identity?.review_mode === "git";
function modeBanner() {
  if (identity?.read_only)
    return '<div class="notice warn mode-banner" role="status">Read-only instance: all knowledge mutations are disabled. You can still inspect the queue and evidence.</div>';
  if (identity?.review_mode === "git")
    return '<div class="notice mode-banner" role="status">Git review mode: clue editing, approval, rejection, revalidation, and promotion happen through your normal git review workflow, not this UI. Proposed clues remain unreviewed until reviewed facts are merged into the trusted branch.</div>';
  return "";
}
function shell(content: string) {
  const syncStatus = sync.status || "unknown";
  app.innerHTML = `<aside class="rail">${brand}<div class="nav">Review queue <span>${e(counts.proposed ?? "")}</span></div><p class="muted small">Knowledge is canonical in git</p><div class="railfoot"><p class="small muted">Self-hosted · No telemetry</p><hr><strong>${e(identity!.name)}</strong><p class="small muted">${e(identity!.email)}</p>${button("Sign out", "logout", "link")}</div></aside><div class="workspace"><header class="top"><strong>Review queue <span class="muted">/ ${e(scope || "All accessible scopes")}</span></strong><span class="small muted" role="status">Git: ${e(syncStatus)}${sync.pending ? " · Local commits pending sync" : ""}</span>${button("Sign out", "logout", "link phone-only")}</header>${modeBanner()}${message ? `<div class="notice" role="status">${e(message)}</div>` : ""}${failure ? `<div class="notice error" role="alert">${e(failure)} ${button("Reload latest", "reload", "link")}</div>` : ""}${content}</div>`;
}
function toolbar() {
  return `<section class="toolbar"><div class="flex between"><div><h1>Review queue</h1><p class="muted">Turn session learnings into trusted operational knowledge.</p></div><label class="sr-only" for="scope">Team scope</label><select id="scope"><option value="">All accessible scopes</option>${identity!.read_scopes.map((s: string) => `<option ${s === scope ? "selected" : ""}>${e(s)}</option>`).join("")}</select></div><nav class="tabs" aria-label="Clue status">${["proposed", "approved", "rejected", "stale"].map((s) => `<button data-status="${s}" class="${status === s ? "chosen" : ""}" aria-pressed="${status === s}">${s[0].toUpperCase() + s.slice(1)} ${e(counts[s] ?? "")}</button>`).join("")}</nav><div class="filters"><input id="search" aria-label="Search loaded clues" placeholder="Search clues, alerts, or incidents" value="${e(search)}"><select id="service" aria-label="Service filter"><option value="">All services</option>${services.map((s) => `<option value="${e(s.id)}" ${service === s.id ? "selected" : ""}>${e(s.title)}</option>`).join("")}</select><select id="source" aria-label="Source filter"><option value="">All sources</option>${["claude", "cursor", "codex", "gemini", "manual"].map((s) => `<option ${source === s ? "selected" : ""}>${s}</option>`).join("")}</select>${button("Refresh", "reload", "link")}</div></section>`;
}
function queue() {
  const visible = items.filter(
    (r) =>
      (!search ||
        `${r.title} ${r.body} ${meta(r, "alerts", []).join(" ")} ${meta(r, "incident_id")}`
          .toLowerCase()
          .includes(search.toLowerCase())) &&
      (!source || String(sourceOf(r).agent).toLowerCase().includes(source)),
  );
  const list = visible
    .map(
      (r) =>
        `<button class="row ${selected?.id === r.id ? "selected" : ""}" data-id="${e(r.id)}">${badge(state(r))}<h3>${e(r.title)}</h3><p>${e(serviceNames(r) || r.scope)} · ${e(meta(r, "alerts", []).join(", "))}</p><p>${e(sourceOf(r).agent || "Manual")} · ${e(meta(r, "created_by"))}</p><p>${e(date(meta(r, "created_at")))}</p></button>`,
    )
    .join("");
  const empty = `<section class="empty"><div class="empty-mark">✓</div><h2>No ${e(status)} clues${scope ? ` in ${e(scope)}` : ""}</h2><p class="muted">${search || service || source ? "No clues match these filters." : "New clues appear when a teammate submits a session or proposes a learned fact through MCP."}</p><div class="flex">${button("View approved clues", "approved")}${button("Clear filters", "clear", "link")}</div><div class="notice"><strong>No LLM required</strong><p>Capture works without a model. Review and edit redacted drafts manually.</p></div></section>`;
  return (
    toolbar() +
    (visible.length
      ? `<div class="grid ${selected ? "has-selection" : ""}"><section class="list" aria-label="Clue queue">${list}<p class="small muted">Showing ${visible.length} loaded clues${search ? " · Search applies to loaded records" : ""}</p>${nextCursor ? button("Load more", "more") : ""}</section><article class="detail">${selected ? detail() : `<div class="empty"><h2>Select a clue to review</h2><p class="muted">Inspect evidence before you approve operational knowledge.</p></div>`}</article></div>`
      : empty)
  );
}
function detail() {
  const r = selected!,
    s = state(r),
    src = sourceOf(r),
    review = meta(r, "review", {}),
    outcomes = r.outcomes || [];
  const incident = incidents.find((i) => i.id === meta(r, "incident_id"));
  const canWrite =
    identity!.write_scopes.includes(r.scope) && !clueMutationsLocked();
  return `<button class="link phone-back" data-action="back">← Queue</button><div class="flex between"><span class="eyebrow">CLUE · ${e(r.id.slice(0, 8))}</span>${badge(s)}</div><h2>${e(r.title)}</h2>${s === "stale" ? '<div class="notice warn">This previously approved fact is stale. Review the recorded evidence before revalidation.</div>' : ""}${s === "proposed" ? '<p class="small muted">Unreviewed · not yet trusted knowledge</p>' : ""}<dl class="meta"><div><dt>Service / alert</dt><dd>${e(serviceNames(r) || "Not linked")} / ${e(meta(r, "alerts", []).join(", ") || "Not linked")}</dd></div><div><dt>Confidence</dt><dd>${e(meta(r, "confidence", "Not recorded"))}</dd></div><div><dt>Source / author</dt><dd>${e(src.agent || "Manual")} · ${e(meta(r, "created_by"))}</dd></div><div><dt>Incident</dt><dd>${incident ? e(incident.title) : meta(r, "incident_id") ? "Linked incident" : "None"}${incident ? ` · ${e(meta(incident, "state"))}` : ""}</dd></div></dl><section class="fact"><h3>${e(s[0].toUpperCase() + s.slice(1))} fact</h3><div class="markdown">${markdown(r.body || "No learned fact yet. Edit this redacted draft before approval.")}</div></section><section class="section"><div class="flex between"><h3>Redacted session excerpt</h3><span class="small muted">${e(src.session_id || "No session")}</span></div><pre class="excerpt">${e(src.excerpt || "No source excerpt recorded.")}</pre><p class="small muted">Only redacted evidence is retained. Redaction is not a guarantee that every secret was detected.</p></section>${
    outcomes.length
      ? `<section class="section"><h3>Reported outcomes</h3><p class="small muted">${outcomes.filter((o: Obj) => (o.meta || o).result === "success").length} successful · ${outcomes.filter((o: Obj) => (o.meta || o).result === "failure").length} failed · ${outcomes.filter((o: Obj) => (o.meta || o).result === "partial").length} partial</p>${outcomes
          .map((o: Obj) => {
            const m = o.meta || o;
            return `<div class="history"><strong>${e(m.result)} · ${e(m.step_id)}</strong><p class="small muted">${e(m.actor || m.created_by)} · ${e(date(m.timestamp || m.created_at))}</p><p>${e(m.note || o.body || "")}</p><p class="small muted">Runbook revision: ${e(m.revision || m.runbook_revision || "Not recorded")}</p></div>`;
          })
          .join("")}</section>`
      : ""
  }${review.decision || review.reason ? `<section class="section notice ${s === "rejected" ? "warn" : ""}"><strong>Review history</strong><p>${e(review.decision || s)} · ${e(review.actor || review.reviewer || "Recorded reviewer")} · ${e(date(review.at || review.decided_at))}</p><p>${e(review.reason || "No reason recorded.")}</p></section>` : ""}${s === "rejected" ? '<p class="notice">Retained for audit and duplicate detection. Excluded from normal recall.</p>' : `<div class="actions"><div class="flex">${s === "proposed" ? button("Reject", "reject", "danger", !canWrite) : ""}${s !== "approved" ? button("Edit clue", "edit", "", !canWrite) : ""}</div>${s === "approved" ? button("Promote to runbook", "promotion", "primary", !canWrite) : button(s === "stale" ? "Revalidate clue" : "Approve clue", s === "stale" ? "revalidate" : "approve", "primary", !canWrite)}</div><p class="small muted">${s === "approved" ? "Promotion is a separate reviewed runbook commit." : s === "stale" ? "Revalidation does not create a successful outcome." : "Approve saves only the clue. It never edits a runbook."}</p>`}${!identity!.write_scopes.includes(r.scope) ? '<p class="notice warn">Read-only scope. Ask your operator for write access.</p>' : ""}`;
}
function edit() {
  const r = selected!,
    d = editDraft;
  return `<main class="content"><p class="crumb">Review queue / Proposed clue / Edit</p><h1>Write the learned fact</h1><p class="muted">Manual editing works without an extraction provider. Saving does not approve a clue.</p><form id="edit-form"><div class="split"><section class="panel pad"><label>Title<input name="title" value="${e(d?.title ?? r.title)}" required maxlength="240"></label><label>Learned fact (Markdown)<textarea name="body" rows="11" required>${e(d?.body ?? r.body)}</textarea></label><label>Service IDs (comma separated)<input name="service_ids" value="${e(d?.service_ids ?? meta(r, "service_ids", []).join(", "))}"></label><label>Alerts (comma separated)<input name="alerts" value="${e(d?.alerts ?? meta(r, "alerts", []).join(", "))}"></label><label>Confidence<select name="confidence">${[
    [0.3, "Low"],
    [0.6, "Medium"],
    [0.9, "High"],
  ]
    .map(
      ([v, c]) =>
        `<option value="${v}" ${Math.abs(Number(d?.confidence ?? meta(r, "confidence", 0.6)) - Number(v)) < 0.16 ? "selected" : ""}>${c}</option>`,
    )
    .join(
      "",
    )}</select></label><p class="small muted">Edits pass through server validation and redaction before saving.</p></section><aside class="panel pad"><h3>Session evidence</h3><pre class="excerpt">${e(sourceOf(r).excerpt || "No excerpt recorded.")}</pre><h3>What belongs in a clue?</h3><p>What fired, what it was, and what fixed it. Keep the fact specific enough to verify against evidence.</p></aside></div><div class="actions">${button("Cancel", "cancel")}<button class="primary" ${busy ? "disabled" : ""}>Save clue</button></div></form></main>`;
}
function promotion() {
  return `<main class="content"><p class="crumb">Review queue / Approved clue / Promote to runbook</p><h1>Promote a clue to a runbook step</h1><p class="muted">Inspect the exact change. Hindsight never runs these instructions.</p><form id="promotion-form"><div class="split"><section class="panel pad"><label>Target runbook<select name="runbook_id" id="runbook-choice" required><option value="">Choose a runbook</option>${runbooks
    .filter((r) => r.scope === selected!.scope)
    .map(
      (r) =>
        `<option value="${e(r.id)}" ${(promotionDraft?.runbook_id ?? preview?.runbook_id) === r.id ? "selected" : ""}>${e(r.title)}</option>`,
    )
    .join(
      "",
    )}</select></label><label>New stable step ID<input name="step_id" value="${e(promotionDraft?.step_id ?? preview?.step_id ?? "")}" placeholder="verify-secret-version"></label><label>Step heading<input name="heading" value="${e(promotionDraft?.heading ?? preview?.heading ?? selected!.title)}" required></label><label>Step content<textarea name="content" rows="7" required>${e(promotionDraft?.content ?? preview?.content ?? selected!.body)}</textarea></label><button ${busy ? "disabled" : ""}>Preview exact diff</button>${
    preview
      ? `<h3>Proposed change</h3><pre class="diff">${String(preview.diff)
          .split("\n")
          .map(
            (line) =>
              `<span class="${line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : ""}">${e(line)}\n</span>`,
          )
          .join(
            "",
          )}</pre><label class="check"><input type="checkbox" id="verified">I verified this change against the evidence.</label>${button("Commit runbook change", "commit-promotion", "primary")}`
      : ""
  }</section><aside class="panel pad"><h3>Source clue</h3><p>${e(selected!.title)}</p><hr><h3>Two independent changes</h3><p>1. Clue approval is already saved.</p><p>2. Runbook promotion creates a separate linked commit with your identity.</p><p class="small muted">A revision conflict never silently overwrites another reviewer's change.</p></aside></div></form><div class="actions">${button("Cancel", "cancel")}</div></main>`;
}
function render() {
  app.setAttribute("aria-busy", String(busy));
  if (!identity) {
    auth();
    return;
  }
  shell(
    mode === "edit" ? edit() : mode === "promotion" ? promotion() : queue(),
  );
}
function fields(form: HTMLFormElement) {
  return Object.fromEntries(new FormData(form).entries()) as {
    [key: string]: string;
  };
}
function split(v: string) {
  return v
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}
app.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target as HTMLFormElement;
  const data = fields(form);
  if (
    identity &&
    clueMutationsLocked() &&
    ["edit-form", "promotion-form"].includes(form.id)
  )
    return;
  if (form.id === "auth-form")
    void act(async () => {
      if (setup) {
        const result = await api("/api/setup", "POST", data);
        enrollmentKey = result.key;
        setup = false;
        identity = null;
      } else {
        const result = await api("/api/session", "POST", { key: data.key });
        csrf = result.csrf_token;
        identity = result.identity;
        await initializeIdentity();
      }
    });
  if (form.id === "edit-form") {
    editDraft = data;
    void act(async () => {
      selected = await api(`/api/clues/${selected!.id}`, "PATCH", {
        ...data,
        confidence: Number(data.confidence),
        service_ids: split(data.service_ids),
        alerts: split(data.alerts),
        expected_revision: selected!.revision,
      });
      mode = "detail";
      message =
        "Saved in a local git commit. Trust state was not approved by editing.";
      await loadQueue();
      editDraft = null;
    });
  }
  if (form.id === "promotion-form") {
    promotionDraft = data;
    void act(async () => {
      const rb = runbooks.find((r) => r.id === data.runbook_id);
      if (!rb) throw new Error("Choose a runbook.");
      preview = {
        ...data,
        ...(await api(`/api/clues/${selected!.id}/promotion-preview`, "POST", {
          ...data,
          expected_runbook_revision: rb.revision,
        })),
        runbook_id: rb.id,
      };
      operationId = crypto.randomUUID();
      promotionDraft = null;
    });
  }
});
app.addEventListener("click", (event) => {
  const el = (event.target as HTMLElement).closest<HTMLElement>("button");
  if (!el || el.hasAttribute("disabled")) return;
  if (el.dataset.id) {
    void act(() => loadSelected(el.dataset.id!));
    return;
  }
  if (el.dataset.status) {
    status = el.dataset.status;
    selected = null;
    void act(() => loadQueue());
    return;
  }
  const action = el.dataset.action;
  if (
    clueMutationsLocked() &&
    [
      "edit",
      "approve",
      "reject",
      "revalidate",
      "promotion",
      "commit-promotion",
    ].includes(action || "")
  )
    return;
  if (action === "continue") {
    enrollmentKey = "";
    failure = "";
    render();
    return;
  }
  if (action === "cancel" || action === "back") {
    mode = "detail";
    editDraft = null;
    promotionDraft = null;
    if (action === "back") selected = null;
    render();
    return;
  }
  if (action === "edit") {
    editDraft = null;
    mode = "edit";
    render();
    return;
  }
  if (action === "approved") {
    status = "approved";
    selected = null;
    void act(() => loadQueue());
    return;
  }
  if (action === "clear") {
    search = "";
    service = "";
    source = "";
    void act(() => loadQueue());
    return;
  }
  if (action === "reload") {
    editDraft = null;
    promotionDraft = null;
    void act(async () => {
      await loadQueue();
      if (selected) await loadSelected(selected.id);
    });
  }
  if (action === "more")
    void act(async () => {
      cursor = nextCursor;
      await loadQueue(true);
    });
  if (action === "logout")
    void act(async () => {
      await api("/api/session", "DELETE");
      identity = null;
      csrf = "";
      selected = null;
      message = "";
    });
  if (action === "promotion")
    void act(async () => {
      runbooks = (await api("/api/runbooks")).items;
      mode = "promotion";
      preview = null;
      promotionDraft = null;
    });
  if (["approve", "reject", "revalidate"].includes(action || "")) {
    const decision = action!;
    showReviewDialog(decision);
  }
  if (action === "commit-promotion") {
    if (!document.querySelector<HTMLInputElement>("#verified")?.checked) {
      failure = "Confirm that you verified the proposed step.";
      render();
      return;
    }
    const data = fields(
      document.querySelector<HTMLFormElement>("#promotion-form")!,
    );
    if (
      ["runbook_id", "step_id", "heading", "content"].some(
        (k) => data[k] !== String(preview?.[k] ?? ""),
      )
    ) {
      failure =
        "The proposal changed. Preview the exact diff again before committing.";
      render();
      return;
    }
    void act(async () => {
      const result = await api(`/api/clues/${selected!.id}/promote`, "POST", {
        ...data,
        expected_revision: selected!.revision,
        expected_runbook_revision: preview!.runbook_revision,
        operation_id: operationId,
      });
      selected = result.clue;
      mode = "detail";
      promotionDraft = null;
      message =
        "Approved clue retained. Runbook promotion saved as a separate local commit.";
      await loadQueue();
    });
  }
});
function showReviewDialog(decision: string) {
  const dialog = document.createElement("dialog");
  dialog.innerHTML = `<form method="dialog"><h2>${decision === "approve" ? "Approve this clue?" : decision === "reject" ? "Reject this clue?" : "Revalidate this stale clue?"}</h2><p>${decision === "approve" ? "Approval records trust in this fact, not a runbook change." : decision === "reject" ? "The clue and fingerprint are retained for audit and duplicate detection." : "Review the evidence. This does not manufacture a successful outcome."}</p><label>Review reason<textarea name="reason" rows="4" ${decision === "reject" ? "required" : ""}></textarea></label><div class="flex between"><button value="cancel">Cancel</button><button value="confirm" class="primary">Confirm ${e(decision)}</button></div></form>`;
  document.body.append(dialog);
  dialog.showModal();
  dialog.addEventListener("close", () => {
    const reason = (dialog.querySelector("textarea") as HTMLTextAreaElement)
      .value;
    const confirm = dialog.returnValue === "confirm";
    dialog.remove();
    if (confirm)
      void act(async () => {
        selected = await api(`/api/clues/${selected!.id}/review`, "POST", {
          expected_revision: selected!.revision,
          decision,
          reason,
        });
        status = state(selected!);
        message = `${decision === "reject" ? "Rejection" : decision === "revalidate" ? "Revalidation" : "Approval"} saved in a local git commit. ${decision === "approve" ? "Runbook unchanged." : ""}`;
        await loadQueue();
      });
  });
}
app.addEventListener("change", (event) => {
  const el = event.target as HTMLInputElement;
  if (el.id === "scope") {
    scope = el.value;
    selected = null;
    void act(() => loadQueue());
  }
  if (el.id === "service") {
    service = el.value;
    selected = null;
    void act(() => loadQueue());
  }
  if (el.id === "source") {
    source = el.value;
    render();
  }
});
app.addEventListener("input", (event) => {
  const el = event.target as HTMLInputElement;
  if (el.id === "search") {
    const pos = el.selectionStart;
    search = el.value;
    render();
    const input = document.querySelector<HTMLInputElement>("#search")!;
    input.focus();
    input.setSelectionRange(pos, pos);
  }
});
async function initializeIdentity() {
  const who = await api("/api/whoami");
  identity = who;
  csrf = who.csrf_token || csrf;
  services = (await api("/api/services")).items;
  await loadQueue();
  incidents = (await api("/api/incidents")).items;
}
async function start() {
  try {
    const info = await api("/api/status");
    setup = info.setup_required;
    if (!setup) {
      try {
        await initializeIdentity();
      } catch (err) {
        if ((err as ApiError).status !== 401) throw err;
        identity = null;
      }
    }
  } catch (err) {
    failure = (err as Error).message;
  }
  render();
}
void start();
