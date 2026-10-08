"use client";

import { MapboxOverlay } from "@deck.gl/mapbox";
import type { Layer } from "@deck.gl/core";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import { DEFAULT_CENTER, MAP_STYLE } from "@/lib/utils";

interface DeckMapProps {
  layers: Layer[];
  className?: string;
  initialViewState?: {
    longitude: number;
    latitude: number;
    zoom: number;
    pitch?: number;
    bearing?: number;
  };
  onMapReady?: (map: maplibregl.Map) => void;
  getTooltip?: (info: { object?: Record<string, unknown> }) => { html: string } | null;
}

export function DeckMap({
  layers,
  className,
  initialViewState,
  onMapReady,
  getTooltip,
}: DeckMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: [initialViewState?.longitude ?? DEFAULT_CENTER[0], initialViewState?.latitude ?? DEFAULT_CENTER[1]],
      zoom: initialViewState?.zoom ?? 11,
      pitch: initialViewState?.pitch ?? 0,
      bearing: initialViewState?.bearing ?? 0,
      attributionControl: { compact: true },
    });

    const overlay = new MapboxOverlay({
      interleaved: true,
      layers,
      getTooltip,
    });

    map.addControl(overlay as unknown as maplibregl.IControl);
    mapRef.current = map;
    overlayRef.current = overlay;
    onMapReady?.(map);

    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    overlayRef.current?.setProps({ layers, getTooltip });
  }, [layers, getTooltip]);

  return <div ref={containerRef} className={className ?? "w-full h-full"} />;
}

export function flyTo(map: maplibregl.Map, lng: number, lat: number, zoom = 14) {
  map.flyTo({ center: [lng, lat], zoom, essential: true });
}
