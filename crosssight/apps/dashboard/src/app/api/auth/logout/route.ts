import { NextResponse } from "next/server";
import { AUTH_COOKIE, USER_COOKIE, sessionCookieOptions } from "@/lib/auth";

export async function POST() {
  const response = NextResponse.json({ ok: true });
  const options = sessionCookieOptions(0);
  response.cookies.set(AUTH_COOKIE, "", options);
  response.cookies.set(USER_COOKIE, "", options);
  response.cookies.set("anpr_source", "", { ...options, httpOnly: false });
  return response;
}
