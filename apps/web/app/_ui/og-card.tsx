// The share card drawn by next/og for the site and for each recorded run: the paper ground,
// an intaglio frame and a guilloche line, like the pages. It uses the font that ships with
// next/og, so the build fetches nothing from the network.

import { ImageResponse } from "next/og";

export const OG_SIZE = { width: 1200, height: 630 };
export const OG_CONTENT_TYPE = "image/png";

const PAPER = "#f3f5f2";
const INK = "#1c2830";
const INTAGLIO = "#2d6a5c";
const STEEL = "#56656f";

function wave(phase: number, period: number, width: number, height: number): string {
  const points: string[] = [];
  for (let x = 0; x <= width; x += 6) {
    const y = height / 2 + (height / 2 - 2) * Math.sin((x / period) * Math.PI * 2 + phase);
    points.push(`${x === 0 ? "M" : "L"}${x} ${y.toFixed(1)}`);
  }
  return points.join(" ");
}

export function ogCard({
  eyebrow,
  title,
  detail,
}: {
  eyebrow: string;
  title: string;
  detail: string;
}): ImageResponse {
  const lineWidth = 1040;
  const lineHeight = 22;
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        background: PAPER,
        padding: 24,
      }}
    >
      <div style={{ flex: 1, display: "flex", border: `1px solid ${INTAGLIO}`, padding: 6 }}>
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            border: `3px solid ${INTAGLIO}`,
            padding: "44px 52px",
            color: INK,
          }}
        >
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ fontSize: 30, color: INTAGLIO, fontWeight: 600 }}>{eyebrow}</div>
            <div
              style={{
                fontSize: 64,
                fontWeight: 700,
                lineHeight: 1.08,
                marginTop: 18,
                letterSpacing: -1,
              }}
            >
              {title}
            </div>
            <div style={{ fontSize: 30, color: STEEL, marginTop: 22, lineHeight: 1.3 }}>
              {detail}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <svg width={lineWidth} height={lineHeight} viewBox={`0 0 ${lineWidth} ${lineHeight}`}>
              <path
                d={wave(0, 64, lineWidth, lineHeight)}
                stroke={INTAGLIO}
                fill="none"
                strokeWidth={1.5}
              />
              <path
                d={wave(Math.PI, 64, lineWidth, lineHeight)}
                stroke={INTAGLIO}
                fill="none"
                strokeWidth={1.5}
              />
              <path
                d={wave(Math.PI / 2, 40, lineWidth, lineHeight)}
                stroke={STEEL}
                fill="none"
                strokeWidth={1}
              />
            </svg>
            <div style={{ display: "flex", fontSize: 26, color: STEEL, marginTop: 14 }}>
              AML Alert Desk. Recorded runs on synthetic data. An analyst approves every decision.
            </div>
          </div>
        </div>
      </div>
    </div>,
    OG_SIZE,
  );
}
