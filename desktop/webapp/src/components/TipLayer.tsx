import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * One tooltip for the whole window, shared by every `[data-tip]`.
 *
 * The collection puts a status tooltip on every row, and there are projects with a
 * thousand rows. A tooltip library would mount a provider, a state hook and an effect
 * per row — the heaviest thing in the sidebar would be the thing that explains it. So
 * the markup carries only `data-tip` (an attribute, free) and this single layer reads
 * it on the way past, which also fixes the two things a per-row tooltip cannot: rows
 * clip their own overflow, and a fixed layer does not. The browser's own `title` would
 * have been free too, and is what the rest of the window still uses; it is a second of
 * nothing happening before it appears, which is not feedback.
 *
 * Reached by keyboard as well as by pointer: `focusin`/`focusout` cover tabbing through
 * rows, so the information is not mouse-only.
 */
const OFFSET = 8;

export function TipLayer() {
  const [tip, setTip] = useState<{ text: string; x: number; y: number; below: boolean } | null>(
    null,
  );
  const target = useRef<Element | null>(null);

  useEffect(() => {
    const elementFor = (event: Event): HTMLElement | null => {
      const node = event.target as HTMLElement | null;
      return node?.closest?.("[data-tip]") ?? null;
    };

    const show = (event: Event) => {
      const element = elementFor(event);
      if (!element || element === target.current) return;
      const text = element.getAttribute("data-tip");
      if (!text) return;
      target.current = element;
      const box = element.getBoundingClientRect();
      // Prefer below and, when there is no room, above: the bottom edge of the window
      // is where the status bar lives and a tooltip under it would be cut off.
      const below = box.bottom + 40 + OFFSET < window.innerHeight;
      setTip({
        text,
        x: box.left,
        y: below ? box.bottom + OFFSET : box.top - OFFSET,
        below,
      });
    };

    const hide = (event: Event) => {
      const element = elementFor(event);
      if (element && element !== target.current) return;
      target.current = null;
      setTip(null);
    };

    // `pointerover` bubbles and fires again for every child of a row, so the identity
    // check above is what keeps a moving pointer from re-positioning on each one.
    document.addEventListener("pointerover", show);
    document.addEventListener("pointerout", hide);
    document.addEventListener("focusin", show);
    document.addEventListener("focusout", hide);
    // A tooltip pinned to a viewport coordinate is wrong the moment the page moves.
    window.addEventListener("scroll", hide, true);
    return () => {
      document.removeEventListener("pointerover", show);
      document.removeEventListener("pointerout", hide);
      document.removeEventListener("focusin", show);
      document.removeEventListener("focusout", hide);
      window.removeEventListener("scroll", hide, true);
    };
  }, []);

  if (!tip) return null;

  return (
    <div
      role="tooltip"
      className={cn(
        "pointer-events-none fixed z-50 max-w-80 rounded-md border border-border bg-popover px-2 py-1 text-[11px] leading-snug text-popover-foreground shadow-float",
        tip.below ? "-translate-y-0" : "-translate-y-full",
      )}
      style={{ left: tip.x, top: tip.y }}
    >
      {tip.text}
    </div>
  );
}
