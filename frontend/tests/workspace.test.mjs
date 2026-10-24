import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Workspace } from "../.test-build/App.js";
import { readSession, themeStorageKey } from "../.test-build/api.js";

test("session reads only the same-origin authenticated session with no-store", async () => {
  let observed;
  const result = await readSession(async (url, options) => {
    observed = { url, options };
    return new Response(JSON.stringify({ user: { id: "42" }, csrf_token: "synthetic-token" }), { status: 200 });
  });
  assert.equal(observed.url, "/api/session/");
  assert.equal(observed.options.credentials, "same-origin");
  assert.equal(observed.options.cache, "no-store");
  assert.deepEqual(result, { state: "authenticated", userId: "42" });
  assert.equal(JSON.stringify(result).includes("synthetic-token"), false);
});

test("anonymous sessions stay signed out", async () => {
  assert.deepEqual(await readSession(async () => new Response("", { status: 401 })), { state: "anonymous" });
});

test("malformed identity, server failures and network errors never claim authentication", async () => {
  for (const body of [{ user: { id: 42 } }, { user: { id: "" } }, { user: { id: "../../other" } }, {}]) {
    assert.deepEqual(await readSession(async () => new Response(JSON.stringify(body))), { state: "unavailable" });
  }
  assert.deepEqual(await readSession(async () => new Response("private-debug-text", { status: 500 })), { state: "unavailable" });
  assert.deepEqual(await readSession(async () => { throw new Error("private URL/key"); }), { state: "unavailable" });
});

test("theme storage is separate for each authenticated user and the signed-out workspace", () => {
  assert.notEqual(themeStorageKey({ state: "authenticated", userId: "1" }), themeStorageKey({ state: "authenticated", userId: "2" }));
  assert.notEqual(themeStorageKey({ state: "anonymous" }), themeStorageKey({ state: "authenticated", userId: "1" }));
});

test("workspace keeps two named panels and the published accessible splitter", () => {
  const html = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "anonymous" }, refresh: () => {} }));
  assert.match(html, /aria-label="Conversation"/);
  assert.match(html, /aria-label="Work area"/);
  assert.match(html, /role="separator"/);
  assert.match(html, /aria-label="Resize conversation and work area"/);
  assert.match(html, /aria-valuenow="28"/);
});

test("workspace exposes System, Light and Dark choices without fake finance or provider controls", () => {
  const html = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "anonymous" }, refresh: () => {} }));
  for (const name of ["System", "Light", "Dark"]) assert.match(html, new RegExp(`>${name}<`));
  assert.match(html, /Sign in to begin/);
  assert.doesNotMatch(html, /\$[\d,]+|<textarea|Send message|Connected to (Claude|Codex)/);
});

test("failed session checks show recovery without leaking response details or showing data", () => {
  const html = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "unavailable" }, refresh: () => {} }));
  assert.match(html, /Workspace unavailable/);
  assert.match(html, /Try again/);
  assert.doesNotMatch(html, /private-debug-text|synthetic-token/);
});
