"use client";

import { useEffect, useRef, useState } from "react";
import { LiveSocket } from "@/lib/ws";

/** Reads per minute over a 60s rolling window of the `reads` channel. */
export function useReadsRate(): number {
  const [rate, setRate] = useState(0);
  const stamps = useRef<number[]>([]);

  useEffect(() => {
    const socket = new LiveSocket();
    socket.connect();
    socket.subscribe(["reads"]);
    const off = socket.onMessage((msg) => {
      if (msg.channel !== "reads") return;
      const now = Date.now();
      stamps.current.push(now);
      const cutoff = now - 60_000;
      stamps.current = stamps.current.filter((ts) => ts >= cutoff);
      setRate(stamps.current.length);
    });
    const timer = window.setInterval(() => {
      const cutoff = Date.now() - 60_000;
      stamps.current = stamps.current.filter((ts) => ts >= cutoff);
      setRate(stamps.current.length);
    }, 1000);
    return () => {
      off();
      window.clearInterval(timer);
      socket.close();
    };
  }, []);

  return rate;
}

export function formatReadsRate(count: number): string {
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k/min`;
  return `${count}/min`;
}
