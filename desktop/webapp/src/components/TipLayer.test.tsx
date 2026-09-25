import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TipLayer } from "@/components/TipLayer";

/**
 * The tooltip layer, which is one component for the whole window rather than one per
 * row.
 *
 * What is worth pinning is the contract that makes it cheap: the markup carries
 * `data-tip` and this layer reads it. The failure mode is not "no tooltip" — it is a
 * tooltip that keeps showing the previous row's text, or one that stays on screen after
 * the pointer has left, and both are invisible in a screenshot.
 */
function row(tip: string, label: string): HTMLElement {
  const element = document.createElement("button");
  element.setAttribute("data-tip", tip);
  element.textContent = label;
  document.body.append(element);
  return element;
}

/** What the browser does for a pointer crossing into an element. */
function enter(element: Element): void {
  // Inside `act`, because a document-level listener setting state is still a React
  // update: without it the assertion runs before the layer has re-rendered and every
  // test here would pass or fail for reasons unrelated to the tooltip.
  act(() => {
    element.dispatchEvent(new Event("pointerover", { bubbles: true }));
  });
}

function leave(element: Element): void {
  act(() => {
    element.dispatchEvent(new Event("pointerout", { bubbles: true }));
  });
}

describe("TipLayer", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("shows the tip of the element the pointer is over", () => {
    render(<TipLayer />);
    const first = row("Passou", "one");

    enter(first);

    expect(screen.getByRole("tooltip").textContent).toBe("Passou");
  });

  it("swaps the text when the pointer moves to another row", () => {
    render(<TipLayer />);
    const first = row("Passou", "one");
    const second = row("Falhou", "two");

    enter(first);
    enter(second);

    // One layer, so the second row replaces the first rather than stacking on it —
    // two tooltips on screen at once is how a hover gets stuck.
    expect(screen.getAllByRole("tooltip")).toHaveLength(1);
    expect(screen.getByRole("tooltip").textContent).toBe("Falhou");
  });

  it("takes the tip down when the pointer leaves", () => {
    render(<TipLayer />);
    const first = row("Passou", "one");

    enter(first);
    leave(first);

    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("ignores an element with no tip", () => {
    render(<TipLayer />);
    const plain = document.createElement("button");
    plain.textContent = "no tip";
    document.body.append(plain);

    enter(plain);

    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("reads a tip from a child of the element the tip is on", () => {
    render(<TipLayer />);
    const owner = document.createElement("div");
    owner.setAttribute("data-tip", "Não pronta — TODO no contrato");
    const glyph = document.createElement("span");
    owner.append(glyph);
    document.body.append(owner);

    // The pointer lands on the glyph inside the marked element, which is what actually
    // happens: `data-tip` sits on the icon's wrapper, and the svg is what is hovered.
    enter(glyph);

    expect(screen.getByRole("tooltip").textContent).toContain("TODO no contrato");
  });
});
