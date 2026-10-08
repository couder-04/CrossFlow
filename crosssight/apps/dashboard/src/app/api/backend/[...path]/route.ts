import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE } from "@/lib/auth";
import { upstreamFetch } from "@/lib/upstream";

export const maxDuration = 60;
export const dynamic = "force-dynamic";

function apiBase(): string {
  return process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

const SLOW_PREFIXES = new Set(["exports", "imports", "uploads"]);

async function proxy(request: NextRequest, segments: string[]) {
  const jar = await cookies();
  const token = jar.get(AUTH_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const path = `/${segments.join("/")}`;
  const url = new URL(request.url);
  const target = `${apiBase()}${path}${url.search}`;

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  headers.set("Authorization", `Bearer ${token}`);

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: "no-store",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  const timeoutMs = SLOW_PREFIXES.has(segments[0] ?? "") ? 45_000 : 12_000;
  let res: Response;
  try {
    res = await upstreamFetch(target, init, timeoutMs);
  } catch {
    return NextResponse.json({ error: "Control room API is unreachable" }, { status: 502 });
  }
  const body = await res.arrayBuffer();
  const outHeaders = new Headers();
  outHeaders.set("Content-Type", res.headers.get("Content-Type") ?? "application/json");
  outHeaders.set("Content-Length", String(body.byteLength));
  outHeaders.set("Cache-Control", "no-store");
  const disposition = res.headers.get("Content-Disposition");
  if (disposition) outHeaders.set("Content-Disposition", disposition);

  return new NextResponse(body, {
    status: res.status,
    headers: outHeaders,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxy(request, path);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxy(request, path);
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxy(request, path);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  return proxy(request, path);
}
