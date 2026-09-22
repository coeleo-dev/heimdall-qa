# Nokr QA — Constituição e Regras Invioláveis do Agente

Este arquivo define as restrições inegociáveis para qualquer agente (Antigravity ou Cursor) atuando no repositório `nokr-qa`.

<standards>

## Regras Absolutas

1. **Nunca commitar `secrets.local.yaml`:** Chaves de API reais, credenciais Asaas e senhas locais devem permanecer estritamente gitignored.
2. **Nunca entregar apenas o Happy Path:** Toda rota modelada em contrato deve cobrir os eixos da matriz (H, O, B, N, I, E, P).
3. **Fonte da Verdade = Record DTO Java:** O contrato YAML (`contracts/<path>.yaml`) deve ser derivado diretamente das anotações e tipos do código Java na `NokrAPI` (`@NotBlank`, `@Size`, `@Pattern`, `@DecimalMin`, compact constructors, throws de service), e NUNCA de exemplos legados da documentação ou playbooks antigos.
4. **Atualizar a Coleção Bruno:** Se um request novo for criado pelo harness, o arquivo correspondente `.bru` deve ser criado na coleção Bruno oficial (`/home/davi/Documentos/bruno/Nokr API - Dev Collection/`), conforme §41 da Constituição.
5. **Nunca inventar Waive:** Um waive só é válido com motivo justificado de no mínimo 40 caracteres ou vinculado formalmente a um `p_gap` registrado em `p-gaps.yaml`.
6. **O Agente NUNCA chama a porta 7878:** A interface visual web (`http://127.0.0.1:7878`) é de uso exclusivo do operador humano. A interface do agente é exclusivamente o CLI e os artefatos em disco (`runs/latest`).

## Formato de Execução

- Utilize o wrapper `./bin/nokr-qa` para execução de comandos CLI.
- Valide sempre antes de entregar: `./bin/nokr-qa validate <round.yaml>` ou `./bin/nokr-qa campaign validate <campaign.yaml>`.
- Para testes autônomos sem browser, utilize a flag headless: `./bin/nokr-qa run <round.yaml> --mode headless`.
- Leia `runs/latest/summary.json`, `verdict.json`, `packs.json` e logs correlacionados para redigir o `analysis.md`.

</standards>
