"""The UI's Portuguese words for the states the harness speaks in English.

These live in Python rather than in the client because they are the words *the harness*
speaks, not the words of one renderer: `validate` prints a status, the CLI's machine
output carries one, and the review screen shows one. Three copies of nine dicts is how
two of them end up disagreeing, and the one that disagrees is always the screen.

They are shipped once per bootstrap as `labels` (`serve/contract.py`) and read from
`labels.status_label[status]` — never retyped in TypeScript, because a status the
harness adds would then reach the reader as a raw slug.

The copy is Portuguese because the people who read the review screen are; the code,
the CLI output and the documentation are English. That split is deliberate and is
stated in `contrib/architecture.md` §9.
"""

from __future__ import annotations

STATUS_LABEL: dict[str, str] = {
    "pending": "Pendente",
    "pass": "Passou",
    "fail": "Falhou",
    "skip": "Pulado",
    "missing": "Em falta",
    "not_ready": "Não pronto",
    "not_reviewed": "Não reviewado",
    "http_5xx": "HTTP 5xx",
    "warn": "Alerta",
}

STATUS_PILL: dict[str, str] = {
    "pending": "status-warning",
    "pass": "status-success",
    "fail": "status-danger",
    "skip": "status-muted",
    "missing": "status-warning",
    "not_ready": "status-warning",
    "not_reviewed": "status-muted",
    "http_5xx": "status-danger",
    "warn": "status-warning",
}

KIND_LABEL: dict[str, str] = {
    "project": "Projeto",
    "directory": "Pasta",
    "campaign": "Campanha",
    "folder": "Fluxo",
    "round": "Endpoint",
    "case": "Caso",
}

#: What a suite step is called in the tree, where a case's own kind would otherwise
#: say "Caso" for a loop. A step is not a case, and the tree is where that is first
#: seen: the label is also what tells a reviewer which of the two buttons to expect.
STEP_KIND_LABEL: dict[str, str] = {
    "loop": "Laço",
    "probe": "Conferência",
}

#: The phases the live strip can be in, in the reviewer's words.
PHASE_LABEL: dict[str, str] = {
    "idle": "Ocioso",
    "running": "Executando",
    "awaiting": "Aguardando veredito",
    "done": "Concluído",
    "cancelled": "Cancelado",
    "error": "Erro",
}

#: What a run scope is called on its button.
SCOPE_LABEL: dict[str, str] = {
    "campaign": "Rodar a campanha inteira",
    "directory": "Rodar tudo nesta pasta",
    "folder": "Rodar este fluxo",
    "round": "Rodar este endpoint",
    "case": "Rodar só este caso",
    "case_forward": "Rodar deste caso em diante",
}

#: How a plan's scope is named once it is running.
SCOPE_RUNNING: dict[str, str] = {
        "campaign": "campanha",
        "directory": "pasta",
        "folder": "fluxo",
        "round": "endpoint",
        "case": "caso",
        "case_forward": "caso em diante",
    }

#: Why a run is paused, for the step header. Keyed by the mode the run was started in.
REASON_LABEL: dict[str, str] = {
    "walk": "walk — pausa a cada caso",
    "review": "review — auto-avança quando os packs passam",
}

#: What a scope is called when the selected node is a step of a suite and not a case.
#: A step is an *etapa* — a loop or a probe — so "deste caso em diante" would name a
#: case that does not exist. Only the forward button differs; the round button means
#: the same thing for a case and for a step.
STEP_SCOPE_LABEL: dict[str, str] = {
    "case_forward": "Rodar desta etapa em diante",
}

#: Why a suite step's unit card says what it says. A step is not a case, and the
#: reason is different for each kind, so the copy is keyed by kind rather than
#: written twice — and it is here rather than in a template because the card and the
#: step pane both show it.
STEP_NOTE: dict[str, str] = {
    "loop": (
        "Etapa de suite: um laço é uma contagem, não um caso. Rodar daqui em diante "
        "começa neste laço e segue pela conferência, mantendo a fotografia que a "
        "conferência compara."
    ),
    "probe": (
        "Etapa de suite: uma conferência sozinha fotografaria o mundo e o leria de "
        "volta logo em seguida, então todo `unchanged` passaria sem nada ter "
        "acontecido. Ela roda dentro do endpoint."
    ),
}
