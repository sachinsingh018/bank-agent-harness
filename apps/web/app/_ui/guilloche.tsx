// A guilloche hairline for section breaks: a few interleaved waves, like the fine line work
// on a banknote. Pure decoration.

import styles from "./guilloche.module.css";

const WIDTH = 480;
const HEIGHT = 14;

function wave(phase: number, period: number): string {
  const points: string[] = [];
  for (let x = 0; x <= WIDTH; x += 4) {
    const y = HEIGHT / 2 + (HEIGHT / 2 - 1.5) * Math.sin((x / period) * Math.PI * 2 + phase);
    points.push(`${x === 0 ? "M" : "L"}${x} ${y.toFixed(2)}`);
  }
  return points.join(" ");
}

const PATHS = [wave(0, 48), wave(Math.PI, 48), wave(Math.PI / 2, 32)];

export function Guilloche() {
  return (
    <svg
      className={styles.line}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS.map((d, i) => (
        <path key={i} d={d} className={i === 2 ? styles.fine : styles.wave} />
      ))}
    </svg>
  );
}
