/* Dashboard domain types — keep in sync with API responses.
 * Generated event schemas live alongside and are re-exported below. */

export type Role = "admin" | "operator" | "analyst";

export interface UserSession {
  username: string;
  role: Role;
}

export interface LoginResponse {
  access_token: string;
  token_type?: string;
  role: Role;
  username: string;
  last_login_at?: string | null;
  last_login_ip?: string | null;
}

export interface Camera {
  id: string;
  name: string;
  lat: number;
  lng: number;
  heading_deg: number;
  lanes: number;
  allowed_direction?: string | null;
  status: string;
  volume?: number;
  has_live_frame?: boolean;
}

export interface Zone {
  id: string;
  name: string;
  kind: "sensitive" | "restricted" | "ward";
  active_hours?: Record<string, unknown> | null;
  geojson?: { type: string; coordinates: unknown } | null;
}

export interface WatchlistEntry {
  plate_norm: string;
  reason: string;
  severity: string;
  added_by: string;
  expires_at?: string | null;
}

export interface AuditEntry {
  id?: number;
  user_id?: string | null;
  username?: string | null;
  action: string;
  plate_norm?: string | null;
  case_id?: string | null;
  params?: Record<string, unknown>;
  ts: string;
}

export interface HeatmapCell {
  h3: string | number;
  count: number;
}

export interface HeatmapResponse {
  cells: HeatmapCell[];
  window?: string;
  stale?: boolean;
  latest_ts?: string | null;
}

export interface HeatmapWsPayload {
  cells?: HeatmapCell[];
  h3?: string | number;
  h3_cell?: string | number;
  count?: number;
}

export interface SegmentCongestion {
  camera_a: string;
  camera_b: string;
  congestion_index: number;
  median_speed_kmh?: number | null;
  path?: [number, number][];
}

export interface ODCell {
  origin_h3: string | number;
  dest_h3: string | number;
  trip_count: number;
  origin_lat?: number;
  origin_lng?: number;
  dest_lat?: number;
  dest_lng?: number;
}

export interface Bottleneck {
  camera_a: string;
  camera_b: string;
  congestion_index: number;
  downstream_flow_ratio?: number;
  at?: string;
}

export interface VolumeAnomaly {
  camera_id: string;
  z_score: number;
  hour_of_week?: number;
  volume?: number;
  baseline?: number;
}

export interface TrajectorySummary {
  read_count?: number;
  camera_count?: number;
  impossible_hop_count?: number;
  data_status?: "narrow" | "widened_to_all_history";
}

export interface GeoJSONFeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: string; coordinates: unknown };
    properties: Record<string, unknown>;
  }>;
  summary?: TrajectorySummary;
}

export type WsChannel = "heatmap" | "alerts" | "flow" | "reads" | "frames";

export interface FrameTrack {
  track_id: number;
  plate_norm: string;
  vehicle_class: string;
  bbox: number[];
  confidence: number;
  lane?: number | null;
  direction?: string | null;
}

export interface FrameNotice {
  camera_id: string;
  ts: string;
  key: string;
  tracks: FrameTrack[];
}

export interface LiveRead {
  camera_id: string;
  ts: string;
  plate_norm: string;
  confidence: number;
  vehicle_class: string;
  track_id?: number | null;
  bbox?: number[];
  lane?: number | null;
  direction?: string | null;
}

export interface WsMessage {
  channel: WsChannel;
  data: unknown;
}

export type {
  Alert,
  AlertSeverity,
  AlertStatus,
  AlertType,
} from "./Alert";

export type { FlowWindow } from "./FlowWindow";
export type { PlateRead } from "./PlateRead";

export type {
  LatestRunResponse,
  MeasuredDemand,
  PipelineReport,
  PipelineVsBaseline,
  SignalControllerRow,
  SignalHeadline,
  SignalSummary,
} from "./Signals";
