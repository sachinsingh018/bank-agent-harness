"use client";

// Dots of money moving along the money-flow links in time order, one per transfer, with the
// Web Animations API. Nothing moves under prefers-reduced-motion: the diagram stays static.

import { useEffect, useRef } from "react";

import styles from "./flow.module.css";

export interface MoneyTrip {
  id: string;
  /** Points along the link in diagram units, from payer to payee. */
  points: [number, number][];
  /** Start delay in ms, so transfers move in the order they happened. */
  delay: number;
}

/** One trip lasts this long (the slowest step of the motion scale). */
const TRIP_MS = 960;

export function FlowMotion({ trips }: { trips: MoneyTrip[] }) {
  const ref = useRef<SVGGElement>(null);
  // A string key, so a re-render with the same trips does not restart the motion.
  const spec = JSON.stringify(trips);

  useEffect(() => {
    const group = ref.current;
    if (!group || typeof group.animate !== "function") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const list = JSON.parse(spec) as MoneyTrip[];
    const dots = Array.from(group.querySelectorAll<SVGCircleElement>("circle"));
    const animations = dots.flatMap((dot, i) => {
      const trip = list[i];
      if (!trip) return [];
      const last = trip.points.length - 1;
      const frames = trip.points.map(([x, y], j) => ({
        transform: `translate(${x}px, ${y}px)`,
        opacity: j === 0 || j === last ? 0 : 1,
        offset: j / last,
      }));
      return [
        dot.animate(frames, {
          duration: TRIP_MS,
          delay: trip.delay,
          easing: "ease-in-out",
          fill: "both",
        }),
      ];
    });
    return () => animations.forEach((a) => a.cancel());
  }, [spec]);

  return (
    <g ref={ref} className={styles.money} aria-hidden="true">
      {trips.map((t) => (
        <circle key={t.id} r={1.7} />
      ))}
    </g>
  );
}
