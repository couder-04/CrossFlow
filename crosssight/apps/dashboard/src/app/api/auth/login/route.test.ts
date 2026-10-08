import assert from "node:assert/strict";
import { mock, test } from "node:test";
import { POST } from "./route";

function loginResponse() {
  return new Response(
    JSON.stringify({ access_token: "tok", username: "admin", role: "admin" }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function setNodeEnv(value: string | undefined) {
  (process.env as Record<string, string | undefined>).NODE_ENV = value;
}

async function setCookie(nodeEnv: string) {
  const previous = process.env.NODE_ENV;
  setNodeEnv(nodeEnv);
  mock.method(globalThis, "fetch", async () => loginResponse());
  try {
    const response = await POST(
      new Request("http://localhost/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: "admin", password: "secret" }),
      }),
    );
    return response.headers.getSetCookie();
  } finally {
    setNodeEnv(previous);
    mock.restoreAll();
  }
}

test("production login cookie is Secure, HttpOnly, and SameSite=Lax", async () => {
  const cookies = await setCookie("production");
  const token = cookies.find((value) => value.startsWith("anpr_token="));
  assert.ok(token);
  assert.match(token, /HttpOnly/i);
  assert.match(token, /Secure/i);
  assert.match(token, /SameSite=Lax/i);
});

test("a dead API is reported as unreachable", async () => {
  mock.method(globalThis, "fetch", async () => {
    throw new TypeError("fetch failed");
  });
  try {
    const response = await POST(
      new Request("http://localhost/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: "admin", password: "secret" }),
      }),
    );
    assert.equal(response.status, 502);
    const body = (await response.json()) as { error?: string };
    assert.equal(body.error, "Control room API is unreachable");
  } finally {
    mock.restoreAll();
  }
});

test("development login cookie is HttpOnly without Secure", async () => {
  const cookies = await setCookie("development");
  const token = cookies.find((value) => value.startsWith("anpr_token="));
  assert.ok(token);
  assert.match(token, /HttpOnly/i);
  assert.doesNotMatch(token, /Secure/i);
  assert.match(token, /SameSite=Lax/i);
});
