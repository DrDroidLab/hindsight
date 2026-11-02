import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

test("setup key survives rendering; cookie review preserves conflicts and separates promotion", async () => {
  const dom = new JSDOM('<div id="app"></div>', {
    url: "http://localhost:8080",
  });
  Object.assign(globalThis, {
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    FormData: dom.window.FormData,
  });
  const identity = {
    id: "reviewer",
    name: "Demo Reviewer",
    email: "demo@example.invalid",
    read_scopes: ["shared"],
    write_scopes: ["shared"],
  };
  let record: any = {
    id: "clue-one",
    title: "Compare mounted versions",
    kind: "clue",
    scope: "shared",
    revision: "initial",
    body: "Compare **versions**, not values.",
    meta: {},
    status: "proposed",
    service_ids: [],
    alerts: ["CrashLoopBackOff"],
    confidence: 0.6,
    source: {
      agent: "codex",
      excerpt: "[REDACTED:token] <script>bad()</script>",
    },
  };
  let signedIn = false,
    conflict = false,
    committed = false;
  const requests: { url: string; method: string; body: any }[] = [];
  globalThis.fetch = async (input: any, options: any = {}) => {
    const url = String(input),
      method = options.method || "GET",
      body = options.body ? JSON.parse(options.body) : {};
    requests.push({ url, method, body });
    assert.equal(options.credentials, "same-origin");
    let data: any = {},
      code = 200;
    if (method !== "GET" && !["/api/setup", "/api/session"].includes(url))
      assert.equal(options.headers["X-CSRF-Token"], "csrf-test");
    if (url === "/api/status")
      data = { setup_required: true, sync_state: { status: "synced" } };
    else if (url === "/api/setup")
      data = { identity, key: "one-time-synthetic-key" };
    else if (url === "/api/session" && method === "POST") {
      signedIn = true;
      data = { identity, csrf_token: "csrf-test" };
    } else if (url === "/api/session" && method === "DELETE") {
      signedIn = false;
    } else if (url === "/api/whoami") {
      assert.ok(signedIn);
      data = { ...identity, csrf_token: "csrf-test" };
    } else if (url.startsWith("/api/review?"))
      data = {
        items: [record],
        counts: {
          proposed: record.status === "proposed" ? 1 : 0,
          approved: record.status === "approved" ? 1 : 0,
        },
        sync_state: { status: "pending", pending: true },
      };
    else if (url === "/api/services") data = { items: [] };
    else if (url === "/api/incidents") data = { items: [] };
    else if (url === "/api/runbooks")
      data = {
        items: [
          {
            id: "runbook-one",
            title: "CrashLoopBackOff",
            scope: "shared",
            revision: "runbook-initial",
          },
        ],
      };
    else if (url === "/api/clues/clue-one" && method === "GET") data = record;
    else if (url === "/api/clues/clue-one" && method === "PATCH") {
      if (conflict) {
        code = 409;
        data = { detail: "Revision conflict" };
      } else {
        assert.equal(typeof body.confidence, "number");
        record = { ...record, ...body, revision: "edited" };
        data = record;
      }
    } else if (url.endsWith("/review")) {
      assert.equal(body.expected_revision, "edited");
      record = { ...record, status: "approved", revision: "approved" };
      data = record;
    } else if (url.endsWith("/promotion-preview"))
      data = {
        diff: "--- old\n+++ new\n+Compare versions <script>bad()</script>",
        runbook_revision: "runbook-initial",
        step_id: "check-version",
      };
    else if (url.endsWith("/promote")) {
      assert.equal(body.expected_revision, "approved");
      assert.equal(body.expected_runbook_revision, "runbook-initial");
      assert.ok(body.operation_id);
      committed = true;
      data = { clue: record };
    } else throw new Error(`Unexpected ${method} ${url}`);
    return new Response(JSON.stringify(data), {
      status: code,
      headers: { "Content-Type": "application/json" },
    });
  };
  const query = <T extends Element = HTMLElement>(s: string) =>
    document.querySelector<T>(s)!;
  const click = (s: string) => query<HTMLButtonElement>(s).click();
  const fill = (name: string, value: string) => {
    query<HTMLInputElement | HTMLTextAreaElement>(`[name="${name}"]`).value =
      value;
  };
  const submit = (s: string) =>
    query(s).dispatchEvent(
      new dom.window.Event("submit", { bubbles: true, cancelable: true }),
    );
  const until = async (check: () => boolean) => {
    for (let i = 0; i < 150; i++) {
      if (check()) return;
      await new Promise<void>((r) => setImmediate(r));
    }
    assert.fail(
      "UI did not reach expected state: " + document.body.textContent,
    );
  };
  // HTML dialog behavior is browser-native; emulate only the open/close boundary in JSDOM.
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  await import("./app");
  await until(() => !!query("#auth-form"));
  for (const [name, value] of Object.entries({
    token: "synthetic-token",
    name: "Demo Reviewer",
    email: "demo@example.invalid",
    team: "platform",
  }))
    fill(name, value);
  submit("#auth-form");
  await until(() =>
    document.body.textContent!.includes("one-time-synthetic-key"),
  );
  assert.ok(!dom.window.localStorage.length);
  click('[data-action="continue"]');
  assert.ok(!document.body.textContent!.includes("one-time-synthetic-key"));
  fill("key", "one-time-synthetic-key");
  submit("#auth-form");
  await until(
    () =>
      !!query('[data-id="clue-one"]') &&
      !query('[data-id="clue-one"]').hasAttribute("disabled"),
  );
  click('[data-id="clue-one"]');
  await until(
    () =>
      !!query('[data-action="edit"]') &&
      !query('[data-action="edit"]').hasAttribute("disabled"),
  );
  assert.equal(document.querySelectorAll("script").length, 0);
  assert.ok(document.body.textContent!.includes("[REDACTED:token]"));
  click('[data-action="edit"]');
  fill("body", "My unsaved operational fact.");
  conflict = true;
  submit("#edit-form");
  await until(() => document.body.textContent!.includes("Revision conflict:"));
  assert.equal(
    query<HTMLTextAreaElement>('[name="body"]').value,
    "My unsaved operational fact.",
  );
  conflict = false;
  submit("#edit-form");
  await until(
    () =>
      !!query('[data-action="approve"]') &&
      !query('[data-action="approve"]').hasAttribute("disabled"),
  );
  click('[data-action="approve"]');
  const dialog = query<HTMLDialogElement>("dialog");
  dialog.returnValue = "confirm";
  dialog.dispatchEvent(new dom.window.Event("close"));
  await until(
    () =>
      !!query('[data-action="promotion"]') &&
      !query('[data-action="promotion"]').hasAttribute("disabled"),
  );
  assert.ok(!requests.some((r) => r.url.endsWith("/promote")));
  assert.ok(document.body.textContent!.includes("Runbook unchanged."));
  click('[data-action="promotion"]');
  await until(() => !!query("#promotion-form"));
  fill("runbook_id", "runbook-one");
  fill("step_id", "check-version");
  submit("#promotion-form");
  await until(() => !!query(".diff"));
  assert.equal(document.querySelectorAll("script").length, 0);
  query<HTMLInputElement>("#verified").checked = true;
  click('[data-action="commit-promotion"]');
  await until(() => committed && !!query('[data-action="promotion"]'));
  assert.ok(document.body.textContent!.includes("separate local commit"));
  click('[data-action="logout"]');
  await until(() => !signedIn && !!query("#auth-form"));
  assert.ok(
    !dom.window.localStorage.length && !dom.window.sessionStorage.length,
  );
  // A writer's scope must not override instance policy. Exercise proposed, stale,
  // and approved records under both externally reviewed and fully read-only modes.
  for (const flags of [
    { review_mode: "git", read_only: false },
    { review_mode: "local", read_only: true },
  ]) {
    Object.assign(identity, flags);
    fill("key", "one-time-synthetic-key");
    submit("#auth-form");
    await until(
      () =>
        !!query(".mode-banner") &&
        !!query('[data-id="clue-one"]') &&
        !query('[data-id="clue-one"]').hasAttribute("disabled"),
    );
    assert.ok(
      document.body.textContent!.includes(
        flags.read_only ? "Read-only instance" : "Git review mode",
      ),
    );
    for (const trustState of ["proposed", "stale", "approved"]) {
      record = { ...record, status: trustState };
      click('[data-id="clue-one"]');
      await until(
        () =>
          !!query(".detail h2") &&
          query("#app").getAttribute("aria-busy") === "false",
      );
      const expected =
        trustState === "proposed"
          ? ["reject", "edit", "approve"]
          : trustState === "stale"
            ? ["edit", "revalidate"]
            : ["promotion"];
      const before = requests.filter((r) => r.method !== "GET").length;
      for (const action of expected) {
        assert.ok(
          query<HTMLButtonElement>(`[data-action="${action}"]`).disabled,
          `${action} must be disabled`,
        );
        click(`[data-action="${action}"]`);
      }
      assert.equal(requests.filter((r) => r.method !== "GET").length, before);
    }
    click('[data-action="logout"]');
    await until(() => !signedIn && !!query("#auth-form"));
  }
  dom.window.close();
});
