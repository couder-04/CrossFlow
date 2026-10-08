/* auto-generated from PlateRead.json — do not edit */

export type Direction = "N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW";

export type PlateFormat = "standard" | "bh" | "nonstandard";

export type VehicleClass = "car" | "motorcycle" | "bus" | "truck" | "auto" | "other";

export interface PlateRead {
  event_id?: string;
  camera_id: string;
  ts: string;
  plate_raw: string;
  plate_norm: string;
  plate_valid: boolean;
  plate_format: PlateFormat;
  confidence: number;
  char_conf?: Array<number>;
  alternates?: Array<string>;
  lane?: number | null;
  direction?: Direction | null;
  vehicle_class?: VehicleClass;
  color?: string | null;
  make?: string | null;
  speed_kmh?: number | null;
  crop_key?: string | null;
  source?: "ocr" | "simulator";
}

