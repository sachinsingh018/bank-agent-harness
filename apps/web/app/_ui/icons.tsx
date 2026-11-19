// Small hand-written line icons in the engraving style: thin strokes in currentColor, square
// caps, no fills except the GitHub mark. All are decoration: the text next to them carries
// the meaning, so they are hidden from screen readers.

const LINE = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

/** Step 1: a rule flags a transfer pattern. Three arrows fan out from one account, with a flag. */
export function FlagIcon() {
  return (
    <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true" focusable="false">
      <g {...LINE}>
        <circle cx="9" cy="22" r="3.2" />
        <path d="M12.2 20.5 L24 13 M12.4 22 L24 22 M12.2 23.5 L24 31" />
        <circle cx="26.5" cy="12" r="2" />
        <circle cx="26.5" cy="22" r="2" />
        <circle cx="26.5" cy="32" r="2" />
        <path d="M33 30 V6 M33 6.5 h6 l-2 3 l2 3 h-6" />
      </g>
    </svg>
  );
}

/** Step 2: the agent reads with read-only tools. A magnifier over a ledger. */
export function LensIcon() {
  return (
    <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true" focusable="false">
      <g {...LINE}>
        <rect x="4" y="5" width="22" height="28" rx="1.5" />
        <path d="M8 11 h14 M8 16 h9 M8 21 h11 M8 26 h6" />
        <circle cx="26" cy="24" r="6.5" />
        <path d="M30.8 28.8 L36 34" strokeWidth="2.4" />
      </g>
    </svg>
  );
}

/** Step 3: code checks every cited transfer and figure. A sum with a tick. */
export function CheckIcon() {
  return (
    <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true" focusable="false">
      <g {...LINE}>
        <path d="M6 9 h14 M6 15 h14 M6 21 h14 M4 26 h18" />
        <path d="M4 30.5 h18" />
        <circle cx="29" cy="20" r="8" />
        <path d="M25.2 20.4 L28 23.2 L33 16.8" strokeWidth="2" />
      </g>
    </svg>
  );
}

/** Step 4: an analyst approves or rejects, and it is logged. A person beside a stamped ledger. */
export function ApproveIcon() {
  return (
    <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true" focusable="false">
      <g {...LINE}>
        <circle cx="11" cy="11" r="4.5" />
        <path d="M3 30 c0 -7 3.5 -11 8 -11 s8 4 8 11" />
        <rect x="22" y="8" width="15" height="24" rx="1.5" />
        <path d="M25 13 h9 M25 17 h9 M25 21 h6" />
        <path d="M25.5 27 h8" strokeWidth="2.2" />
      </g>
    </svg>
  );
}

/** The GitHub mark, simplified, at text size. */
export function GitHubIcon({ size = 20 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M12 2a10 10 0 0 0-3.2 19.5c.5.1.7-.2.7-.5v-1.8c-2.8.6-3.4-1.3-3.4-1.3-.5-1.2-1.1-1.5-1.1-1.5-.9-.6.1-.6.1-.6 1 .1 1.5 1 1.5 1 .9 1.5 2.4 1.1 2.9.8.1-.7.4-1.1.6-1.3-2.2-.3-4.6-1.1-4.6-5 0-1.1.4-2 1-2.7-.1-.3-.4-1.3.1-2.7 0 0 .8-.3 2.8 1a9.6 9.6 0 0 1 5 0c1.9-1.3 2.8-1 2.8-1 .5 1.4.2 2.4.1 2.7.6.7 1 1.6 1 2.7 0 3.9-2.3 4.7-4.6 5 .4.3.7.9.7 1.9v2.8c0 .3.2.6.7.5A10 10 0 0 0 12 2z"
      />
    </svg>
  );
}
