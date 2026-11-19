// A row of dots for "k of n": one dot per alert, filled for the k the agent would close.
// Hand-written SVG; the words next to it carry the same numbers, so the dots are decoration
// for sighted readers and hidden from screen readers.

import styles from "./count-bar.module.css";

const PER_ROW = 24;
const GAP = 11;
const R = 4;

export function CountBar({
  part,
  total,
  tone,
}: {
  part: number;
  total: number;
  /** "good" for false alarms closed, "miss" for real cases closed. */
  tone: "good" | "miss";
}) {
  const cols = Math.min(Math.max(total, 1), PER_ROW);
  const rows = Math.max(1, Math.ceil(total / PER_ROW));
  const width = cols * GAP;
  const height = rows * GAP;
  const dots = Array.from({ length: total }, (_, i) => ({
    cx: (i % PER_ROW) * GAP + GAP / 2,
    cy: Math.floor(i / PER_ROW) * GAP + GAP / 2,
    filled: i < part,
  }));
  return (
    <svg
      className={`${styles.bar} ${tone === "miss" ? styles.miss : styles.good}`}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      aria-hidden="true"
      focusable="false"
    >
      {dots.map((d, i) => (
        <circle
          key={i}
          cx={d.cx}
          cy={d.cy}
          r={d.filled ? R : R - 0.75}
          className={d.filled ? styles.filled : styles.empty}
        />
      ))}
    </svg>
  );
}
