import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as api from "../.test-build/api.js";
import { Workspace } from "../.test-build/App.js";

const token = "a".repeat(64);

test("sign-in sends bounded same-origin credentials with the server CSRF token and retains no secrets", async () => {
  const calls = [];
  const result = await api.signIn("synthetic-user", "synthetic-password", async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(calls.length === 1 ? { csrf_token: token } : { user: { id: "42" }, csrf_token: "b".repeat(64) }));
  });
  assert.equal(result, "signed_in");
  assert.deepEqual(calls.map(x => x.url), ["/api/session/csrf/", "/api/session/login/"]);
  assert.deepEqual(JSON.parse(calls[1].options.body), { username: "synthetic-user", password: "synthetic-password" });
  assert.equal(calls[1].options.headers["X-CSRFToken"], token);
  for (const { options } of calls) {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
    assert.equal(options.redirect, "error");
  }
  assert.doesNotMatch(JSON.stringify(result), /synthetic-password|aaaaa/);
});

test("sign-in errors never expose raw diagnostics and failed CSRF bootstrap sends no password", async () => {
  for (const [status, expected] of [[401, "invalid_credentials"], [429, "rate_limited"], [409, "already_signed_in"], [500, "unavailable"]]) {
    let calls = 0;
    const result = await api.signIn("synthetic-user", "synthetic-password", async () => {
      calls++;
      return calls === 1 ? new Response(JSON.stringify({ csrf_token: token })) : new Response("private-server-diagnostic", { status });
    });
    assert.equal(result, expected);
  }
  let calls = 0;
  assert.equal(await api.signIn("synthetic-user", "synthetic-password", async () => {
    calls++;
    return new Response(JSON.stringify({ csrf_token: "bad\nheader" }));
  }), "unavailable");
  assert.equal(calls, 1);
});

test("sign-out requires CSRF POST and a verified backend response", async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(calls.length === 1 ? { csrf_token: token } : { status: "signed_out" }));
  };
  assert.equal(await api.signOut(fetcher), true);
  assert.deepEqual(calls.map(x => x.url), ["/api/session/csrf/", "/api/session/logout/"]);
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers["X-CSRFToken"], token);
  assert.equal(await api.signOut(async () => { throw Error("private diagnostic"); }), false);
});

test("anonymous workspace has a labelled password form and authenticated workspace has sign-out", () => {
  const signedOut = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "anonymous" }, refresh() {} }));
  assert.match(signedOut, /id="maipa-username"/);
  assert.match(signedOut, /autoComplete="username"/);
  assert.match(signedOut, /type="password"/);
  assert.match(signedOut, /autoComplete="current-password"/);
  assert.match(signedOut, /aria-label="Sign in to Maipa"/);
  const signedIn = renderToStaticMarkup(React.createElement(Workspace, { session: { state: "authenticated", userId: "42" }, refresh() {} }));
  assert.match(signedIn, />Sign out</);
  assert.doesNotMatch(signedIn, /type="password"/);
});
