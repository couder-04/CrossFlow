import assert from "node:assert/strict";
import { test } from "node:test";
import { SignJWT } from "jose";
import { NextRequest } from "next/server";
import { AUTH_COOKIE } from "./lib/auth";
import { middleware } from "./middleware";

const SECRET = "middleware-test-secret";

async function token(role: string, expiresIn: string | number) {
  return new SignJWT({ role, sub: "user" })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime(expiresIn)
    .sign(new TextEncoder().encode(SECRET));
}

function request(path: string, value?: string) {
  const headers = new Headers();
  if (value) headers.set("cookie", `${AUTH_COOKIE}=${value}`);
  return new NextRequest(new URL(path, "http://localhost:3000"), { headers });
}

test("expired token redirects to login", async () => {
  process.env.JWT_SECRET = SECRET;
  const expired = await token("admin", Math.floor(Date.now() / 1000) - 60);
  const response = await middleware(request("/live", expired));
  assert.equal(response.status, 302);
  assert.equal(new URL(response.headers.get("location") ?? "").pathname, "/login");
});

test("tampered signature redirects to login", async () => {
  process.env.JWT_SECRET = SECRET;
  const signed = await token("admin", "1h");
  const tampered = `${signed.slice(0, -4)}xxxx`;
  const response = await middleware(request("/live", tampered));
  assert.equal(response.status, 302);
  assert.equal(new URL(response.headers.get("location") ?? "").pathname, "/login");
});

test("non-admin role is redirected away from /admin", async () => {
  process.env.JWT_SECRET = SECRET;
  const analyst = await token("analyst", "1h");
  const response = await middleware(request("/admin", analyst));
  assert.equal(response.status, 302);
  assert.equal(new URL(response.headers.get("location") ?? "").pathname, "/live");
});
