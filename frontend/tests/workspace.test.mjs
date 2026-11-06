import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AppearanceChoices, Workspace } from "../.test-build/App.js";
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

test("workspace starts with chat space and no empty work panel or fixed navigation", () => {
  const html = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "anonymous" }, refresh: () => {} }));
  assert.match(html, /aria-label="Conversation"/);
  assert.doesNotMatch(html, /aria-label="Work area"|role="separator"|Workspace guide|work-view-controls/);
});

test("workspace exposes System, Light and Dark choices without fake finance or provider controls", () => {
  const html = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "anonymous" }, refresh: () => {} }));
  // The account menu is a closed Fancy popover until opened; its trigger is always present.
  assert.match(html, /aria-label="Account and appearance"/);
  assert.doesNotMatch(html, /\$[\d,]+|<textarea|Send message|Connected to (Claude|Codex)/);
  const chosen = [];
  const choices = renderToStaticMarkup(React.createElement(AppearanceChoices, { preference: "dark", disabled: false, choose: value => chosen.push(value) }));
  for (const name of ["System", "Light", "Dark"]) assert.match(choices, new RegExp(`>${name}<`));
  assert.match(choices, /aria-label="Appearance"/);
  assert.equal((choices.match(/aria-pressed="true"/g) || []).length, 1);
  assert.match(choices, /id="maipa-theme-dark"[^>]*aria-pressed="true"|aria-pressed="true"[^>]*id="maipa-theme-dark"/);
  assert.doesNotMatch(choices, /\$[\d,]+|<textarea|Connected to (Claude|Codex)/);
});

test("failed session checks show recovery without leaking response details or showing data", () => {
  const html = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "unavailable" }, refresh: () => {} }));
  assert.match(html, /Workspace unavailable/);
  assert.match(html, /Try again/);
  assert.doesNotMatch(html, /private-debug-text|synthetic-token/);
});
