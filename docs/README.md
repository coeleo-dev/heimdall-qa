# Documentação do Nokr QA

Comece por **[nokr-qa.md](nokr-qa.md)** — a especificação do produto e o plano que o construiu.

| Parte | O que é |
|---|---|
| **A** | Especificação do produto: o que construir, packs, protocolo da matriz Trilho A, schemas, logs, UI, agente, conferência cruzada de valores (A.18). |
| **B** | Plano de implementação em 12 fases. Cada fase tem aceite, o que não fazer, e um gate. Fase 10 = loops + oráculo; não misturar com a Fase 4. |

Não implemente duas fases no mesmo PR. Não comece código sem o aceite da fase anterior (Parte B).

---

## Refatoração para Heimdall QA

O harness vai ser aberto como projeto genérico, com o Nokr como implementação de referência. **O escopo é REST** — a identidade vem primeiro (§ fase 1.1) e o navegador fica fora deste plano.

| Documento | O que é |
|---|---|
| **[plano-implementacao-heimdall.md](plano-implementacao-heimdall.md)** | **O plano de implementação da refatoração**: 12 fases em 2 etapas, no formato Objetivo / Arquivos / Não fazer / Aceite / Verificar / Gate, mais a tabela de PRs. **É a fila de execução.** |
| [estudo-heimdall/](estudo-heimdall/) | O estudo que o produziu: 9 frentes, 11 ADRs e 48 edge cases. Insumo, não entrega. Comece por [09-consolidacao.md](estudo-heimdall/09-consolidacao.md). |
| [estudo-harness-agnostico.md](estudo-harness-agnostico.md) | O estudo anterior (15/09) que originou a ideia. Vira a semente do `docs/architecture.md` em inglês. |

**O escopo é REST.** O teste de navegador **não** está neste plano. O código do E3 sai de `main` na fase 1.1 e fica preservado na tag `e3-freeze`; quando voltar, volta como **provider** (`heimdall-qa-browser`), nunca como código do núcleo. A UI de review (`src/nokr_qa/serve/`) **fica** — ela é a tela onde um humano lê um run, não um teste de frontend.

**Duas filas, não uma.** A Parte B de `nokr-qa.md` está **concluída** — foi o que construiu o harness até aqui, e o E3 fechou a última fase. A refatoração tem plano próprio, e a ordem interna dela é a que vale de agora em diante.

O `E3` está **completo e verde, congelado com registro** ([00-baseline.md](estudo-heimdall/00-baseline.md) §4); a [emenda 11](emenda-11-ui-browser.md) está pausada e retoma na Fase 3.3.

---

## Conteúdo do diretório

| Arquivo | Estado |
|---|---|
| `nokr-qa.md` | vigente — spec do provider Nokr (migra para o provider na Fase 1.3) |
| `plano-implementacao-heimdall.md` | vigente — a fila de execução |
| `emenda-11-ui-browser.md` | pausada até a Fase 3.3 |
| `estudo-harness-agnostico.md` | estudo |
| `estudo-heimdall/` | estudo |
| `examples/` | exemplos do descriptor |
| `notes-0109.md` | 2 linhas, sobra — apagar na Fase 1.5 |
