# F0 — Baseline congelado

> **Frente F0 do estudo Heimdall QA.** Documento de estudo, não spec. Ele não substitui
> [`docs/nokr-qa.md`](../nokr-qa.md) nem [`docs/emenda-11-ui-browser.md`](../emenda-11-ui-browser.md).
>
> Pergunta desta frente: *como provar que a refatoração não quebrou nada, se não existe
> histórico git?*
>
> Data do congelamento: 2026-09-22.

---

## 1. Veredito em uma linha

O baseline está congelado em `nokr-qa@616cf6e`. Os outros três repositórios do workspace
**já tinham histórico** — o achado "zero commits nos três repos" estava errado em 2 de 3.
E o E3 **está completo e verde**, não "em curso": o que falta nele é apenas a verificação
`slow` contra stack vivo, que não é gate de nada.

---

## 2. Achado que corrige o plano: são quatro repos, não três

A varredura do plano tratou `nokr-ui-lib` como um repositório sem commits. Na verdade
existem **dois** repos aninhados ali, e o que importa tem histórico:

| Caminho | `HEAD` | Commits | Remote | Working tree |
|---|---|---|---|---|
| `NokrAPI` | `8b24f72` | 53 | `gitlab.com:nokr-billing/nokr-api` | sujo (WIP do autor) |
| `nokr-qa` | `616cf6e` (novo) | 1 | — | limpo |
| `nokr-ui-lib` (raiz) | — | **0** | — | sujo (repo acidental) |
| `nokr-ui-lib/nokr-workspace` | `fe6544e` | 14 | `gitlab.com:nokr-billing/nokr-frontend` | **limpo** |

Consequências:

1. **O frontend real (`nokr-workspace`) nunca esteve em risco.** É um repo limpo, com 14
   commits e remote. O "gargalo do E1" continua sendo verdade (sem CI, sem âncora), mas
   *não* é verdade que ele não tem histórico.
2. **`nokr-ui-lib` raiz é um repo acidental.** Não tem commits, não tem remote, e contém
   `nokr-workspace` como repo embutido. `git add -A` ali produz um *gitlink* sem URL —
   ou seja, um clone do repo externo não traz o conteúdo do interno. Ele também tem
   `graphify-out/cache` com **390 arquivos** gerados e `nokr_audit_results/` com 2,4 MB
   de screenshots de maio.
3. **Por isso o `nokr-ui-lib` raiz não recebeu commit de baseline.** Congelá-lo como repo
   gravaria um gitlink quebrado no histórico, que é exatamente o tipo de coisa que depois
   não se remove sem reescrever história. O estado foi **registrado** (abaixo) e o destino
   do repo acidental é item de F8.

---

## 3. Folha de números inicial

Tudo medido em 2026-09-22, nesta máquina, no commit `616cf6e`.

### 3.1 Código e testes

| Medida | Valor |
|---|---|
| Linhas em `src/nokr_qa/` | 8.463 (37 arquivos `.py`) |
| Linhas em `tests/` | 8.491 (51 arquivos `.py`) |
| Suíte hermética | **375 passed, 10 skipped** em **7,03 s** (wall 8,36 s) |
| Testes `slow` (browser + stack vivo) | 7 coletados, **skipped** sem `NOKR_QA_SLOW=1` |

Quebra por módulo de `src/nokr_qa/` (só arquivos do próprio diretório):

| Módulo | Linhas |
|---|---|
| `nokr_qa` (raiz) | 6.589 |
| `packs/` | 752 |
| `serve/` | 403 |
| `schema/` | 370 |
| `oracle/` | 247 |
| `logs/` | 102 |

### 3.2 Conteúdo declarativo

| Diretório | Arquivos | Linhas |
|---|---|---|
| `cases/` | 539 | 6.713 |
| `rounds/` | 71 (64 `.yaml` na raiz) | 1.351 |
| `contracts/` | 48 | 1.425 |
| `baselines/` | 34 | 300 |
| `suites/` | 10 | 268 |
| `campaigns/` | 3 | 401 |

### 3.3 Runs

| Medida | Valor |
|---|---|
| Diretórios de run | 83 (79 com `summary.json`) |
| Arquivos totais | 6.238 |
| Tamanho em disco | 26 MB |
| Agregado dos 79 summaries | 788 pass / **41 fail** / 6 skip / **2 instrument** |

Os 41 `fail` e 2 `instrument` **são baseline, não dívida nova**: vêm dos pilotos do
Trilho B, dos rounds `live-*` (que dependem de ambiente) e de três execuções antigas de
`ui-overview` (16/09 e 17/09), anteriores ao fechamento do E3. Uma refatoração que
preserve o comportamento tem de reproduzir esses números, não zerá-los.

O `runs/` fica fora do git (§ `.gitignore`) e **não foi commitado**. A referência
verificável é o agregado acima, que qualquer `grep` no `summary.json` reconstrói.

### 3.4 Tempos de referência (gates de E0)

| Comando | Resultado | Wall |
|---|---|---|
| `nokr-qa campaign validate campaigns/trilho-a-http.yaml` | rc=0, sem saída | **1,35 s** |
| `nokr-qa campaign status campaigns/trilho-a-http.yaml` | rc=0, JSON completo | 0,62 s |
| `nokr-qa validate rounds/api-ingest-get.yaml` | rc=0 | 0,61 s |
| `pytest -q` (hermético) | 375 passed, 10 skipped | **8,36 s** |

O gate de E0 — "Trilho A continua verde e nenhum round muda de status" — está satisfeito
no baseline: `campaign validate` retorna 0 sem uma linha de saída.

---

## 4. Registro de congelamento do E3

Decisão registrada no plano: **E3 congelado com registro; emenda 11 pausada.** O registro
abaixo é o que torna o congelamento reavaliável na fase 3 em vez de indistinguível de
trabalho abandonado.

### 4.1 Veredito: E3 está **completo**, não "em curso"

O plano classificou o E3 como em curso. A verificação de hoje mostra que o **código está
escrito e testado**; o que não foi feito é a execução `slow` contra stack vivo — que o
próprio §7.6 lista como não obrigatória para o gate (o gate é *fixture plantada*, e as
fixtures existem e passam).

**108 testes herméticos verdes** em `test_ui_structural_packs.py`, `test_ui_browser.py`,
`test_ui_step_artifacts.py`, `test_ui_schema.py`, `test_ui_packs.py`.

### 4.2 Gate de §7.6, item por item

| Gate de E3 | Evidência | Estado |
|---|---|---|
| Erro de console → `ui.render` falha | `test_a_console_error_is_the_only_pack_ui_render_brings_down`; `test_a_page_error_recorded_as_pageerror_also_fails_ui_render` | **fechado** |
| ARIA divergente → `ui.structure` falha | `test_a_diverged_aria_tree_is_the_only_pack_ui_structure_brings_down`; `test_the_structure_detail_carries_the_playwright_diff` | **fechado** |
| Violação WCAG → `ui.a11y` falha | `test_a_wcag_violation_is_the_only_pack_ui_a11y_brings_down`; `test_the_a11y_detail_names_the_offending_rule_and_node` | **fechado** |
| `ui.visual` waivável sem P-GAP | `test_ui_visual_can_be_waived_without_a_p_gap` | **fechado** |
| `ui.value` **não** waivável | `test_ui_value_is_not_waivable_and_the_refusal_is_the_failure` | **fechado** |

### 4.3 Inventário do que o E3 deixou no disco

Código e política:

| Artefato | Onde | Estado |
|---|---|---|
| `PackContext` estendido com campos de UI | `src/nokr_qa/packs/__init__.py` | completo |
| `run_ui` + dispatch por `case_kind == "ui"` | `src/nokr_qa/packs/__init__.py` | completo |
| Packs `ui.render`, `ui.structure`, `ui.a11y`, `ui.visual` | `src/nokr_qa/packs/__init__.py` | completo |
| `_UI_CONSOLE_FAIL = {"error", "pageerror"}` | `src/nokr_qa/packs/__init__.py` | completo |
| `NON_WAIVABLE_PACKS = {"ui.value"}` | `src/nokr_qa/packs/__init__.py:41` | completo |
| `UiStep.baseline` + `UiStep.waive` + validador de escape do root | `src/nokr_qa/schema/models.py` | completo |
| `_structure`, `_a11y`, `_inject_axe` no driver | `src/nokr_qa/browser.py` | completo |
| `UI_BASELINE_MISSING`, `A11Y_SCRIPT_MISSING`, `A11Y_RUN_FAILED` | `src/nokr_qa/ui_step.py` | completo |
| `a11y.json` no run store | `src/nokr_qa/run_store.py` | completo |
| `UiConfig.a11y` + `config.yaml` | `src/nokr_qa/config.py` | completo |
| Dependência `axe-playwright-python>=0.1.8` | `pyproject.toml` | completo (axe.min.js vendorizado, sem rede) |

Conteúdo declarativo e testes:

| Artefato | Onde | Estado |
|---|---|---|
| Baseline ARIA commitado | `baselines/ui/overview.aria.yml` | existe (1.619 B) |
| Decoração do `login` com `baseline` | `suites/ui-smoke.yaml` | existe |
| Notas de aceite E2/E3 | `rounds/ui-overview.yaml` | existe |
| Testes do gate | `tests/test_ui_structural_packs.py` (11 KB) | verde |
| Testes de driver | `tests/test_ui_browser.py` (21 KB) | verde |
| Testes de artefato | `tests/test_ui_step_artifacts.py` (11 KB) | verde |
| Testes de schema | `tests/test_ui_schema.py` | verde |
| Fakes de sessão | `tests/support_ui.py` | completo |
| Verificação `slow` | `tests/test_ui_slow.py` (7 testes) | **nunca executada nesta máquina** |

### 4.4 O que o E3 **não** entregou (e não deveria)

| Item | Etapa dona | Confirmação de ausência |
|---|---|---|
| Bloco `SurfaceSpec.from: ui` | E4 | não há `from` em `suite_run.py:_get_surface` |
| `capture_ui` | E4 | não há ocorrência em `src/` |
| Oráculo `ui.value` comparando tela × API × `book.json` | E4 | só existe a constante de não-waivabilidade, sem produtor do pack |
| Cadeia de ouro, `trilho-c-ui.yaml`, `A5`/`A6` | E5 | não existem |
| Painel do passo `ui` no `serve` | E6 | não existe partial de `ui/` |

Ou seja: o E3 fechou o **veredito estrutural**. O E4 — que é o que justifica a emenda —
não começou. Congelar aqui é congelar num ponto coerente.

### 4.5 Consequência para a fase 3 do Heimdall QA

Quando a fase 3 for ativada, o trabalho é **portar** (não reescrever) estes cinco pontos:
dispatch de UI em `packs/`, `PackContext` de UI, `_structure`/`_a11y` em `browser.py`,
códigos de instrumento em `ui_step.py` e o par `baseline`/`waive` de `UiStep`.
Os 7 testes `slow` são a prova de não-quebra dessa porta e **não podem ser apagados**
mesmo não sendo gate das fases 1–2.

---

## 5. Estado do `nokr-ui-lib` raiz (repo acidental)

Não commitado de propósito. Registro do que existe, para F8 decidir:

| Item | Tamanho / contagem | Natureza |
|---|---|---|
| `nokr-workspace/` | repo embutido, 407 arquivos fonte, 60.176 linhas (`apps/`+`libs/`) | **real, preservar** |
| `graphify-out/cache/` | 390 arquivos | gerado, não deveria ser versionado |
| `nokr_audit_results/` | 14 arquivos, 2,4 MB | gerado (screenshots de maio) |
| `a` | 4.456 B | prompt de LLM salvo por acidente (junho) |
| `blabla` | 0 B | arquivo vazio |
| `main.py`, `nokr_qa_audit.py`, `walkthrough.md` | 89 B / 36 KB / 16 KB | scripts e análise avulsos |
| `docs/`, `specs/`, `.agents/`, `AGENTS.md` | — | conteúdo com valor (docs de front, specs) |
| `.venv`, `uv.lock`, `pyproject.toml`, `.python-version` | — | ambiente Python da época |

O conteúdo com valor (`docs/`, `specs/`, `.agents/`, `AGENTS.md`) precisa de destino
antes de o repo acidental ser desfeito. Isso é decisão de F8, não de F0.

---

## 6. O que este congelamento destrava

1. **`nokr-qa` tem baseline.** `616cf6e` é o "antes". Toda fase do Heimdall QA compara
   contra ele, e a suíte hermética tem um alvo reproduzível: **375 passed, 10 skipped,
   7,03 s**.
2. **O gate de E0 está provado no baseline.** `campaign validate` verde, sem alteração de
   status em nenhum round, com o Trilho A intocado.
3. **O E3 está num ponto coerente para congelar** — gate fechado por fixture, E4 não
   iniciado. A fase 3 sabe exatamente o que portar.
4. **Dois riscos do plano caíram.** O frontend real tem histórico e remote (não estava
   desprotegido), e o NokrAPI tem 53 commits (o provider Nokr é restaurável).
5. **Dois riscos subiram.** O repo acidental do `nokr-ui-lib` precisa de destino, e o WIP
   do `NokrAPI` (9 arquivos modificados, 13 não rastreados) continua fora do histórico —
   uma refatoração que dependa do comportamento atual do provider tem de congelar esse
   WIP também, ou avisar que não o faz.

---

## 7. O que ficou fora, deliberadamente

- **`runs/` não foi commitado.** É gitignored, são 26 MB e 6.238 arquivos. A referência é
  o agregado da §3.3, reconstruível por `grep`.
- **O WIP do `NokrAPI` não foi commitado.** É trabalho do autor, não meu, e misturá-lo num
  commit de baseline seria irreversível na prática. Registro: `feature/ubb_engine_update`,
  HEAD `8b24f72`, 9 arquivos modificados (35 inserções / 35 remoções), 13 não rastreados.
- **O `nokr-ui-lib` raiz não foi commitado.** Ver §2 e §5.
- **`secrets.local.yaml` confirmado gitignored** e ausente do commit de baseline.
