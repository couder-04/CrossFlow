const RETRYABLE_STATUS = new Set([502, 503, 504, 520, 521, 522, 523, 524, 530]);

/** Node and browsers surface a dead socket as these exact messages. */
export function publicApiError(message: string): string {
  if (
    /^(typeerror:\s*)?(fetch failed|failed to fetch|networkerror when attempting to fetch resource|load failed)$/i.test(
      message.trim(),
    )
  ) {
    return "Control room API is unreachable";
  }
  return message;
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isConnectionError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  if (err.name === "TimeoutError" || err.name === "AbortError") return false;
  return /fetch failed|failed to fetch|networkerror|load failed/i.test(err.message);
}

function canReplay(init: RequestInit): boolean {
  const method = (init.method ?? "GET").toUpperCase();
  if (method === "GET" || method === "HEAD") return true;
  const body = init.body;
  return typeof body === "string" || body instanceof ArrayBuffer || body instanceof Uint8Array;
}

/**
 * Fetch the control-room API.
 * Quick tunnels drop sockets often; one replay covers a reset that never reached the API.
 * Timeouts are not replayed, because the API may already be working on the first attempt.
 */
export async function upstreamFetch(
  url: string,
  init: RequestInit = {},
  timeoutMs = 12_000,
): Promise<Response> {
  const replay = canReplay(init);
  const attempts = replay ? 2 : 1;
  let lastError: unknown;

  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const res = await fetch(url, {
        ...init,
        cache: "no-store",
        signal: AbortSignal.timeout(timeoutMs),
      });
      const method = (init.method ?? "GET").toUpperCase();
      const retryStatus = method === "GET" || method === "HEAD";
      if (retryStatus && attempt + 1 < attempts && RETRYABLE_STATUS.has(res.status)) {
        await delay(400);
        continue;
      }
      return res;
    } catch (err) {
      lastError = err;
      if (attempt + 1 < attempts && isConnectionError(err)) {
        await delay(400);
        continue;
      }
      break;
    }
  }

  throw new Error("Control room API is unreachable", { cause: lastError });
}
