/* auto-generated from Alert.json — do not edit */

export type AlertSeverity = "low" | "medium" | "high" | "critical";

export type AlertStatus = "new" | "acknowledged" | "dispatched" | "closed" | "false_positive" | "reviewing" | "approved" | "dismissed";

export type AlertType = "watchlist" | "cloned_plate" | "convoy" | "loitering" | "geofence" | "wrong_way" | "plate_vehicle_mismatch" | "route_anomaly" | "stopped_vehicle" | "camera_health";

export interface Alert {
  id?: string;
  type: AlertType;
  severity: AlertSeverity;
  plate_norm: string;
  camera_ids?: Array<string>;
  evidence?: Record<string, unknown>;
  status?: AlertStatus;
  needs_verification?: boolean;
  ts?: string;
  ack_by?: string | null;
  dispatched_to?: string | null;
  closed_note?: string | null;
  created_at?: string;
  crop_url?: string | null;
}

