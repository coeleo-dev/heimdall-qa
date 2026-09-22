# AGENTS.md — Heimdall QA

Heimdall QA mora neste repo. Spec: [`docs/nokr-qa.md`](docs/nokr-qa.md).

Skill: [`.agents/skills/heimdall-qa-round/SKILL.md`](.agents/skills/heimdall-qa-round/SKILL.md) (Antigravity) e [`.cursor/skills/heimdall-qa-round/SKILL.md`](.cursor/skills/heimdall-qa-round/SKILL.md) (Cursor) — dispara em “crie uma rodada”, “cubra o Trilho A HTTP”, “analise a campanha”, “Heimdall QA”, “analise o último run”.

- Nunca commitir `secrets.local.yaml`.
- Nunca entregar só happy path.
- Fonte do contract = DTO Java (não o exemplo do playbook).
- Preferir `.bru` existente; se criar request novo, atualizar Bruno.
- Não inventar waive.

CLI (via `./bin/heimdall-qa` ou `.venv/bin/heimdall-qa`): `heimdall-qa validate`, `heimdall-qa scaffold-endpoint`, `heimdall-qa scaffold-round`, `heimdall-qa campaign validate`, `heimdall-qa campaign status`, `heimdall-qa run --mode headless`, `heimdall-qa last-run`, `heimdall-qa fixture`, `heimdall-qa serve` (UI humana da coleção em `http://127.0.0.1:7878`). Campanhas: [`campaigns/trilho-a-http.yaml`](campaigns/trilho-a-http.yaml) (A0–A4 sandbox), [`campaigns/trilho-a-live.yaml`](campaigns/trilho-a-live.yaml) (Go-Live / P-live).

E-mail, nome, morada, CPF e CNPJ: `heimdall-qa fixture` ou `generate:` no case. Não inventar `qa-trilho-a@nokr.dev` nem documento de cabeça. Duplicado no mesmo run usa `capture:` + `generate: captured.<name>`. Tokens da cadeia A0 (`jwt`, `refresh_token`, `api_key`) saem de `capture_response` no H01 (persistidos em `runs/shared-captures.json`).

O agente **não** chama a porta 7878. A pasta do run (`runs/latest`) é a API.
