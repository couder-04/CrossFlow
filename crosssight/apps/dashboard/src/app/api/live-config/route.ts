import { NextResponse } from "next/server";
import { wsUrlFromApi } from "@/lib/wsUrl";

export const dynamic = "force-dynamic";

/** Websocket address for this deployment. Read at request time so it matches the API the proxy uses. */
export function GET() {
  const api =
    process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return NextResponse.json({ wsUrl: wsUrlFromApi(api) });
}
