# AGENTS.md — Nokr QA

Nokr QA mora neste repo. Spec: [`docs/nokr-qa.md`](docs/nokr-qa.md).

Skill: [`.agents/skills/nokr-qa-round/SKILL.md`](.agents/skills/nokr-qa-round/SKILL.md) (Antigravity) e [`.cursor/skills/nokr-qa-round/SKILL.md`](.cursor/skills/nokr-qa-round/SKILL.md) (Cursor) — dispara em “crie uma rodada”, “cubra o Trilho A HTTP”, “analise a campanha”, “Nokr QA”, “analise o último run”.

- Nunca commitir `secrets.local.yaml`.
- Nunca entregar só happy path.
- Fonte do contract = DTO Java (não o exemplo do playbook).
- Preferir `.bru` existente; se criar request novo, atualizar Bruno.
- Não inventar waive.

CLI (via `./bin/nokr-qa` ou `.venv/bin/nokr-qa`): `nokr-qa validate`, `nokr-qa scaffold-endpoint`, `nokr-qa scaffold-round`, `nokr-qa campaign validate`, `nokr-qa campaign status`, `nokr-qa run --mode headless`, `nokr-qa last-run`, `nokr-qa fixture`, `nokr-qa serve` (UI humana da coleção em `http://127.0.0.1:7878`). Campanhas: [`campaigns/trilho-a-http.yaml`](campaigns/trilho-a-http.yaml) (A0–A4 sandbox), [`campaigns/trilho-a-live.yaml`](campaigns/trilho-a-live.yaml) (Go-Live / P-live).

E-mail, nome, morada, CPF e CNPJ: `nokr-qa fixture` ou `generate:` no case. Não inventar `qa-trilho-a@nokr.dev` nem documento de cabeça. Duplicado no mesmo run usa `capture:` + `generate: captured.<name>`. Tokens da cadeia A0 (`jwt`, `refresh_token`, `api_key`) saem de `capture_response` no H01 (persistidos em `runs/shared-captures.json`).

O agente **não** chama a porta 7878. A pasta do run (`runs/latest`) é a API.
