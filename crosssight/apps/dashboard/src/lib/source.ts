export type SourceMode = "sim" | "video";

export function sourceMode(): SourceMode {
  if (typeof document === "undefined") return "sim";
  const match = document.cookie.match(/(?:^|;\s*)anpr_source=(sim|video)(?:;|$)/);
  return match?.[1] === "video" ? "video" : "sim";
}

export function cameraInSource(cameraId: string, mode: SourceMode = sourceMode()): boolean {
  const video = cameraId.startsWith("vid-");
  return mode === "video" ? video : !video;
}

export function alertInSource(cameraIds: string[] | undefined, mode: SourceMode = sourceMode()): boolean {
  const video = (cameraIds ?? []).some((id) => id.startsWith("vid-"));
  return mode === "video" ? video : !video;
}
