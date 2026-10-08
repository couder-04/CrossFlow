import assert from "node:assert/strict";
import { test } from "node:test";
import { wsUrlFromApi } from "./wsUrl.ts";

test("https API base becomes a wss live socket", () => {
  assert.equal(
    wsUrlFromApi("https://example.trycloudflare.com"),
    "wss://example.trycloudflare.com/ws/live",
  );
});

test("http API base stays ws and drops any query", () => {
  assert.equal(
    wsUrlFromApi("http://localhost:8002/ignored?x=1"),
    "ws://localhost:8002/ws/live",
  );
});

test("a bad base falls back to the local socket", () => {
  assert.equal(wsUrlFromApi("not a url"), "ws://localhost:8000/ws/live");
});
