/** Turn the API base the server is using into the browser websocket URL. */
export function wsUrlFromApi(apiBase: string, socketPath = "/ws/live"): string {
  try {
    const url = new URL(apiBase);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = socketPath;
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return "ws://localhost:8000/ws/live";
  }
}
