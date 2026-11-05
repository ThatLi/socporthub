const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(`${__dirname}/../webapp/js/pages/proposal-detail.js`, "utf8");
const renderSource = source.slice(source.indexOf("async function renderCommentsSection("), source.indexOf("function renderEditForm("));
const original = { id: 1, author_id: 10, author_name: "Admin", author_role: "admin", body: "Original text", created_at: "2026-11-05T12:00:00Z" };

// Exercise rendering and handlers without adding a DOM dependency to this vanilla-JS app.
async function render(comments, user = { id: 10, role: "admin" }) {
  const body = { textContent: "Original text", classList: { add() {} } };
  const ownContext = { removed: false, remove() { this.removed = true; } };
  const quote = { textContent: "Replying to Admin: Original text" };
  const article = { querySelector: (selector) => ({ ".comment-body": body, ".comment-reply-context": ownContext })[selector] };
  const button = { dataset: { deleteComment: "1" }, disabled: false, removed: false, closest: () => article, remove() { this.removed = true; }, addEventListener(_, handler) { this.click = handler; } };
  const textarea = { value: "Unfinished draft" };
  const error = { textContent: "", innerHTML: "" };
  const controls = { "#comment-form": { addEventListener() {} }, "#new-comment": textarea, "#reply-to-comment": {}, ".comment-replying": {}, "#comment-action-error": error };
  const slot = {
    innerHTML: "",
    querySelector: (selector) => controls[selector],
    querySelectorAll: (selector) => selector === "[data-delete-comment]" ? [button] : selector.startsWith("[data-reply-context-for=") ? [quote] : [],
  };
  const calls = [];
  const api = { get: async (path) => path.endsWith("/comments") ? comments : [], delete: async (path) => { calls.push(path); } };
  const window = { confirm: () => true };
  const context = vm.createContext({ api, window, escapeHtml: (value) => String(value ?? "").replaceAll("<", "&lt;").replaceAll(">", "&gt;") });
  vm.runInContext(renderSource, context);
  await context.renderCommentsSection(slot, user, { id: 42, status: "needs_action" });
  return { slot, button, body, ownContext, quote, textarea, error, api, calls, window };
}

test("only the current admin's active comments offer Delete", async () => {
  const comments = [original, { ...original, id: 2, author_id: 11 }, { ...original, id: 3, deleted_at: "2026-11-05T13:00:00Z" }];
  const admin = await render(comments);
  assert.deepEqual([...admin.slot.innerHTML.matchAll(/data-delete-comment="(\d+)"/g)].map((match) => match[1]), ["1"]);
  const submitter = await render(comments, { id: 10, role: "user" });
  assert.doesNotMatch(submitter.slot.innerHTML, /data-delete-comment=/);
  assert.doesNotMatch(submitter.slot.innerHTML, /data-reply-to="3"/);
});

test("deleted comments and parent previews hide original text and their own reply context", async () => {
  const result = await render([
    { ...original, deleted_at: "2026-11-05T13:00:00Z", reply_to_comment_id: 3 },
    { ...original, id: 2, body: "Existing reply", reply_to_comment_id: 1 },
    { ...original, id: 3, body: "Earlier comment" },
  ]);
  assert.doesNotMatch(result.slot.innerHTML, /Original text|data-reply-context-for="3"/);
  assert.match(result.slot.innerHTML, /Replying to Admin: “This comment was deleted\.”/);
  assert.match(result.slot.innerHTML, /Existing reply/);
});

test("cancel leaves the comment intact; success updates it without losing the draft", async () => {
  const result = await render([original]);
  result.window.confirm = () => false;
  await result.button.click();
  assert.equal(result.calls.length, 0);
  result.window.confirm = () => true;
  let complete;
  result.api.delete = (path) => { result.calls.push(path); return new Promise((resolve) => { complete = resolve; }); };
  const pending = result.button.click();
  assert.equal(result.button.disabled, true);
  complete();
  await pending;
  assert.deepEqual(result.calls, ["/api/proposals/42/comments/1"]);
  assert.equal(result.body.textContent, "This comment was deleted.");
  assert.equal(result.quote.textContent, "Replying to Admin: “This comment was deleted.”");
  assert.equal(result.ownContext.removed, true);
  assert.equal(result.button.removed, true);
  assert.equal(result.textarea.value, "Unfinished draft");
});

test("failed deletion shows an escaped error and allows retry without losing the draft", async () => {
  const result = await render([original]);
  result.api.delete = async () => { throw new Error("<Access denied>"); };
  await result.button.click();
  assert.equal(result.button.disabled, false);
  assert.equal(result.button.removed, false);
  assert.equal(result.body.textContent, "Original text");
  assert.match(result.error.innerHTML, /&lt;Access denied&gt;/);
  assert.equal(result.textarea.value, "Unfinished draft");
});
