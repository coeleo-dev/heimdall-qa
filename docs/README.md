# Documentação do Nokr QA

Comece por **[nokr-qa.md](nokr-qa.md)**.

| Parte | O que é |
|---|---|
| **A** | Especificação do produto: o que construir, packs, protocolo da matriz Trilho A, schemas, logs, UI, agente, conferência cruzada de valores (A.18). |
| **B** | Plano de implementação em 10 fases. Cada fase tem aceite, o que não fazer, e um gate. Fase 10 = loops + oráculo; não misturar com a Fase 4. |

Não implemente duas fases no mesmo PR. Não comece código sem o aceite da fase anterior (Parte B).

Este diretório, nesta entrega, contém só estes arquivos. `src/`, `pyproject.toml` e cases YAML nascem na Fase 1 em diante.
