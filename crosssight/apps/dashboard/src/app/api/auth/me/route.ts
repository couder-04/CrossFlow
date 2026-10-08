import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { parseUserCookie, USER_COOKIE } from "@/lib/auth";

export async function GET() {
  const jar = await cookies();
  const user = parseUserCookie(jar.get(USER_COOKIE)?.value);
  if (!user) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }
  return NextResponse.json(user);
}
