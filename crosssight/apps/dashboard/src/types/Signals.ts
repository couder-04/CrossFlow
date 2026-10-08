/** Responses of the /signals API (signal-control results and camera-measured demand). */

export interface SignalHeadline {
  scenario: string;
  best_baseline: string;
  pct_fuel_reduction_vs_best: number;
  ci_lo: number | null;
  ci_hi: number | null;
  sanity_flag?: string;
}

export interface SignalControllerRow {
  scenario: string;
  controller: string;
  label: string;
  n_seeds: number;
  fuel_per_vehicle_L?: number;
  mean_waiting_s?: number;
  mean_queue_veh?: number;
  stops_per_vehicle?: number;
}

export interface SignalSummary {
  available: boolean;
  reason?: string;
  n_runs?: number;
  config_locked?: boolean;
  label?: string;
  note?: string;
  controllers: SignalControllerRow[];
  headlines: SignalHeadline[];
  latest_run: { created?: string; mode?: string; verdict?: string } | null;
}

export interface PipelineVsBaseline {
  pct_fuel_reduction: number;
  ci_lo: number | null;
  ci_hi: number | null;
  n: number;
}

export interface PipelineReport {
  created: string;
  source: { kind: string; camera: string; hour: number; demand_streams: number };
  demand: {
    observed_veh_h: Record<string, number>;
    scale_vs_xtraflow_balanced: Record<string, number>;
    geh_observed_vs_simulated: Record<string, number>;
    geh_target: number;
    vehicle_mix_source: string;
  };
  run: { mode: string; seeds: number[]; controllers: string[] };
  per_controller: Record<
    string,
    { fuel_per_vehicle_L: number; mean_waiting_s: number; mean_queue_veh: number; stops_per_vehicle: number }
  >;
  comparison: {
    best_baseline?: string;
    verdict: string;
    vs_baseline?: Record<string, PipelineVsBaseline>;
    dropped_seeds: number[];
  };
  caveats: string[];
}

export type LatestRunResponse =
  | { available: false; reason: string }
  | { available: true; report: PipelineReport };

export interface MeasuredDemand {
  from: string;
  to: string;
  period_hours: number;
  cameras: Record<string, string>;
  silent_cameras: string[];
  approach_veh_h: Record<string, number>;
  scale_vs_xtraflow_balanced: Record<string, number>;
  vehicle_mix: Record<string, number> | null;
}
