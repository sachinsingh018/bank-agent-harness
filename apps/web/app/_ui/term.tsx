"use client";

// An inline definition for a finance word. The word is a button: tap, click, Enter or Space
// opens a short definition below it, Escape or moving focus away closes it, and hovering shows
// it too. Screen readers get the definition as the button's description even when closed.

import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import styles from "./term.module.css";

export function Term({ children, definition }: { children: ReactNode; definition: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(false);
  const [shift, setShift] = useState(0);
  const tip = useRef<HTMLSpanElement>(null);
  const shown = open || hover;

  // Keep the open definition inside the window on narrow screens.
  useLayoutEffect(() => {
    if (!shown || !tip.current) return;
    const rect = tip.current.getBoundingClientRect();
    const room = document.documentElement.clientWidth - 8;
    if (rect.right - shift > room) setShift(room - (rect.right - shift));
    else if (rect.left - shift < 8) setShift(8 - (rect.left - shift));
  }, [shown, shift]);

  return (
    <span
      className={styles.wrap}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <button
        type="button"
        className={styles.word}
        aria-expanded={open}
        aria-describedby={id}
        onClick={() => setOpen((o) => !o)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
      >
        {children}
      </button>
      <span
        ref={tip}
        id={id}
        role="tooltip"
        className={styles.tip}
        hidden={!shown}
        style={shift ? { transform: `translateX(${shift}px)` } : undefined}
      >
        {definition}
      </span>
    </span>
  );
}
