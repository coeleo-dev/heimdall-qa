import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SourcePane } from "@/components/SourcePane";
import type { SourceDocument } from "@/types";

/**
 * The editor pane, over a faked API.
 *
 * `JsonEditor` is mocked rather than run: Monaco needs a real DOM with layout, and what
 * these tests are about is the pane's own logic — the dirty flag, what a save does with
 * findings, and that the header and the list cannot disagree. A Monaco test would be a
 * test of Monaco.
 */
vi.mock("@/components/JsonEditor", () => ({
  JsonEditor: ({
    value,
    readOnly,
    onChange,
    ariaLabel,
  }: {
    value: string;
    readOnly: boolean;
    onChange?: (next: string) => void;
    ariaLabel?: string;
  }) => (
    <textarea
      aria-label={ariaLabel}
      readOnly={readOnly}
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}));

const fetchSource = vi.fn();
const saveSource = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchSource: (...args: unknown[]) => fetchSource(...args),
    saveSource: (...args: unknown[]) => saveSource(...args),
  };
});

function documentFor(overrides: Partial<SourceDocument> = {}): SourceDocument {
  return {
    path: "rounds/smoke.yaml",
    kind: "round",
    text: "id: smoke\n",
    editable: true,
    valid: true,
    findings: [],
    ...overrides,
  };
}

describe("SourcePane", () => {
  beforeEach(() => {
    fetchSource.mockReset();
    saveSource.mockReset();
  });

  it("opens with the file the server sent, unsaved changes marked", async () => {
    fetchSource.mockResolvedValue(documentFor());
    render(<SourcePane path="rounds/smoke.yaml" onClose={() => {}} onSaved={() => {}} />);

    const editor = await screen.findByLabelText("Editar rounds/smoke.yaml");
    expect(editor).toHaveValue("id: smoke\n");
    expect(screen.getByText("rounds/smoke.yaml")).toBeInTheDocument();
    expect(screen.getByText("round")).toBeInTheDocument();
    expect(screen.queryByText("não salvo")).not.toBeInTheDocument();
  });

  it("marks the file as unsaved once a key lands", async () => {
    fetchSource.mockResolvedValue(documentFor());
    render(<SourcePane path="rounds/smoke.yaml" onClose={() => {}} onSaved={() => {}} />);

    const editor = await screen.findByLabelText("Editar rounds/smoke.yaml");
    await userEvent.type(editor, "title: novo\n");

    expect(screen.getByText("não salvo")).toBeInTheDocument();
  });

  it("saves the text in the editor, not the text it opened with", async () => {
    fetchSource.mockResolvedValue(documentFor());
    saveSource.mockResolvedValue(documentFor({ text: "id: smoke\ntitle: novo\n" }));
    const onSaved = vi.fn();
    render(<SourcePane path="rounds/smoke.yaml" onClose={() => {}} onSaved={onSaved} />);

    const editor = await screen.findByLabelText("Editar rounds/smoke.yaml");
    await userEvent.type(editor, "title: novo\n");
    await userEvent.click(screen.getByRole("button", { name: /Salvar e validar/ }));

    await waitFor(() => expect(saveSource).toHaveBeenCalledWith("rounds/smoke.yaml", "id: smoke\ntitle: novo\n"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("keeps an invalid save and shows the findings under the file", async () => {
    // The server wrote the file and answered 200 with findings; the pane has to keep
    // the edit *and* the findings, which is the whole point of not refusing the save.
    fetchSource.mockResolvedValue(documentFor());
    saveSource.mockResolvedValue(
      documentFor({
        text: "cases: [\n",
        valid: false,
        findings: [
          {
            code: "ROUND_UNREADABLE",
            where: "rounds/smoke.yaml",
            message: "round YAML is invalid",
            fix: "fix the round file and run heimdall-qa validate",
            why: "the round file does not load, so nothing it names can be checked.",
          },
        ],
      }),
    );
    render(<SourcePane path="rounds/smoke.yaml" onClose={() => {}} onSaved={() => {}} />);

    const editor = await screen.findByLabelText("Editar rounds/smoke.yaml");
    // `fireEvent` and not `userEvent.type` for this one: the broken text contains `[`,
    // which `userEvent` reads as the start of a key descriptor. Monaco hands over whole
    // values anyway, so this is also the closer simulation of the real editor.
    fireEvent.change(editor, { target: { value: "cases: [\n" } });
    await userEvent.click(screen.getByRole("button", { name: /Salvar e validar/ }));

    expect(await screen.findByText("ROUND_UNREADABLE")).toBeInTheDocument();
    expect(screen.getByText(/fix the round file/)).toBeInTheDocument();
    expect(screen.getByText("1 achado")).toBeInTheDocument();
    // The text stays in the editor: a refusal that reverted it would lose the edit.
    expect(editor).toHaveValue("cases: [\n");
  });

  it("reverts the editor to what is on disk without saving", async () => {
    fetchSource.mockResolvedValue(documentFor());
    render(<SourcePane path="rounds/smoke.yaml" onClose={() => {}} onSaved={() => {}} />);

    const editor = await screen.findByLabelText("Editar rounds/smoke.yaml");
    await userEvent.type(editor, "lixo");
    await userEvent.click(screen.getByRole("button", { name: /Reverter/ }));

    expect(editor).toHaveValue("id: smoke\n");
    expect(saveSource).not.toHaveBeenCalled();
  });

  it("surfaces a refused save as an error rather than as a change", async () => {
    const { ApiRefusal } = await import("@/lib/api");
    fetchSource.mockResolvedValue(documentFor());
    saveSource.mockRejectedValue(
      new ApiRefusal(403, {
        error: {
          code: "SOURCE_OUTSIDE_CONTENT",
          message: "the path must stay inside the project's content: ../x.yaml",
          hint: "paths are relative to the content root, e.g. rounds/smoke.yaml",
          details: [],
          exit_code: 1,
        },
        comment_required: false,
      }),
    );
    render(<SourcePane path="rounds/smoke.yaml" onClose={() => {}} onSaved={() => {}} />);

    const editor = await screen.findByLabelText("Editar rounds/smoke.yaml");
    await userEvent.type(editor, "x");
    await userEvent.click(screen.getByRole("button", { name: /Salvar e validar/ }));

    expect(await screen.findByText(/must stay inside the project's content/)).toBeInTheDocument();
  });
});
