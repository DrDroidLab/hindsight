import { test } from "node:test";
import assert from "node:assert/strict";
import { escape, markdown } from "./safe";
test("escapes markup in metadata, evidence and diffs", () =>
  assert.equal(
    escape('<img src=x onerror="x">'),
    "&lt;img src=x onerror=&quot;x&quot;&gt;",
  ));
test("Markdown cannot execute raw HTML or script URLs", () => {
  assert.ok(!markdown("<script>alert(1)</script>").includes("<script>"));
  assert.ok(!markdown("[click](javascript:alert(1))").includes("href="));
  assert.ok(
    !markdown("[click](data:text/html;base64,PHNjcmlwdD4=)").includes("href="),
  );
  assert.ok(
    markdown("**Operational fact**").includes(
      "<strong>Operational fact</strong>",
    ),
  );
});
test("knowledge cannot fetch remote images and links suppress referrers", () => {
  assert.ok(
    !markdown("![tracking](https://example.invalid/pixel)").includes("<img"),
  );
  assert.ok(
    markdown("[ticket](https://example.invalid/ticket)").includes(
      'rel="noopener noreferrer"',
    ),
  );
});
