---
ticket: T013
title: Temas de assinatura adicionais e 3 estruturas
sprint: sprint-02
prioridade: P1
status: done
criado: 2026-10-02
depende_de: "decisão do dono de 2026-10-02 (inverter o Non-Goal de CLAUDE.md)"
---

# T013 — Temas e estruturas da assinatura

## Contexto

O pedido foi *"mais temas para o utilizador poder ter assinaturas com estilos
diferentes"*. Duas leituras eram possíveis e o trabalho é o dobro:

- **só paletas** — alarga `FR-3.12`, que só tem claro/escuro;
- **paletas + estruturas** — "estilos diferentes" também pode ser *forma*, e
  isso toca `renderer.py`, que é *critical file*: o HTML entra em emails de
  clientes reais.

O dono escolheu as duas. Este ticket faz as duas.

## Critérios de aceitação

Escrito na forma que o `scripts/verify-implementation.sh` mede, e não na forma
que se lê. O T014 corrigiu o script para ler a secção em português; este
ticket é o primeiro a usar essa forma desde a correcção.

- [ ] `PYTHONPATH=src python3 -m pytest tests/test_renderer.py -q --no-cov` exits 0 — os testes do renderer, com as 28 combinações
- [ ] `src/mailutils/signatures/renderer.py` contains "LAYOUTS" — as quatro estruturas
- [ ] `src/mailutils/signatures/renderer.py` contains "graphite" — a primeira das cinco paletas novas
- [ ] `src/mailutils/db.py` contains "layout" — a coluna que faz o layout persistir
- [ ] `docs/DESIGN.md` contains "border-radius:8px" — o token novo vive na folha **e** no documento
- [ ] `scripts/run-mutations.py` contains "M-16" — a mutação que prova que a estrutura chega ao HTML
- [ ] make check target exists — o gate

### Critérios que o script não mede

- As 28 combinações abrem num Thunderbird e num Outlook. Nenhum script deste
  projecto abre um email num cliente real; é o passo [4] do
  `human-validation.sh`.
- `stack` produz byte a byte o HTML de antes. **Verdadeiro e não provado** —
  ver M-10, que continua aberto. O teste mais próximo compara fragmentos, e a
  mutação `display:inline-block` → `display:inline` passa com a suite verde.

## Âmbito

**Dentro:** `renderer.py`, `db.py` (coluna `layout`), `signatures/routes.py`,
`templates/editor.html`, `static/app.js`, `docs/DESIGN.md`, testes.

**Fora (decidido, não é backlog):** gradientes, imagens por contacto,
`prefers-color-scheme` no email (ver ROADMAP T011),e temas por utilizador
personalizados com um editor de cores livre.

## Diffstory

### O que mudou

| Ficheiro | O quê |
|---|---|
| `signatures/renderer.py` | +5 paletas, `LAYOUTS`, `SignatureData.layout`, classe `_Blocos` com as peças partilhadas, quatro renderizadores, `render_html` despacha |
| `db.py` | `SCHEMA_VERSION` 1 → 2, `_add_column` idempotente, coluna `signatures.layout` |
| `signatures/routes.py` | `layout` atravessa o pipeline único `_build`, o `INSERT`/`UPDATE` e o contexto do editor |
| `templates/editor.html` | campo escondido `campo-layout`, selector `.botao-estrutura`, descrição da estrutura escolhida |
| `static/app.js` | `layoutInput`, enviado no preview e no `preview-documento`, selector com `aria-checked` |
| `docs/DESIGN.md` | secção nova: as 7 paletas e as 4 estruturas, com o porquê do contraste |
| `scripts/run-mutations.py` | alvos de M-01/M-02 reapontados; M-16 e M-17 novas |
| `tests/test_renderer.py` | `TestLayouts` e `TestCorreccoesAosTestesDeLayout` |
| `tests/test_editor_flows.py` | `TestEstruturaDaAssinatura`, `_save` com `layout` |

### Porque mudou

O utilizador pediu variedade visual. O que interessou foi fazê-lo **sem** que
isso virasse uma superfície de suporte que ninguém consiga depurar — daí a regra de
que `stack` não muda um byte e de que as invariantes são parametrizadas sobre o
produto cartesiano.

### O que ficou intacto, deliberadamente

- **`analyzer/`, `spam.py`, `security.py`, `mailer.py`, `db.py` além da coluna
  nova.** Nada do caminho de envio existe ainda; é o T014–T016.
- **`renderer._normalise_url`, o escaping, `_precisa_de_fundo`.** Reimplementados
  uma vez, mantidos. `_Blocos` chama-os; não os substitui.
- **`signatures.theme` e o selector de tema.** Continua igual; o layout é um
  campo à parte e os dois são ortogonais.
- **A regra de `data:` URI, `http://` e o marcador.** Inalteradas, e a mutação
  M-02 continua a morrer.

## Risco

### Risco assumido

**28 combinações.** Sete paletas × quatro estruturas. O próprio
`renderer.py:51` avisava que *"cada tema é uma superfície de suporte a sério"*.
Isto é 28 superfícies. A mitigação é a parametrização: as invariantes
estruturais correm sobre as 28, não sobre uma.

### Risco que não foi eliminado

**Nenhum browser real renderizou as 28.** Os testes E2E do T008 verificam o
caminho do editor, mas não abrem um email no Thunderbird nem no Outlook. Um
`border-radius` ignorado, um `padding` dobrado num Word engine antigo, ou uma
coluna a colapsar a 320 px de largura **não são detectáveis aqui**. O que posso dizer
é que o HTML é table-based, tem estilos inline, e passa 4.5:1 — não que
seja bonito no Outlook.

## Verificação

```bash
./scripts/verify-implementation.sh T013   # 7 passed, 0 failed, 0 declarados
make check
```

Resultado, texto integral do gate:

| Sub-gate | Resultado |
|---|---|
| `docs-check` | verde |
| `code-check` | verde |
| `test-check` | **697 passed**, cobertura **90.19%** (mínimo 80%) |
| `lint-check` | `All checks passed!` |
| `format-check` | 41 ficheiros já formatados |
| `e2e-check` | 11 passed em Chromium |
| `mutation-check` | **17/17 mutações detectadas** |

### Mutações

| ID | O que morre | Alvo |
|---|---|---|
| M-16 | `_RENDERERS.get(data.layout, ...)` passa a ler sempre o default. A interface continua a marcar a estrutura escolhida, o formulário continua a enviar, a coluna continua a gravar — e o email sai vertical. | T013 |
| M-17 | `muted` do tema `navy` de 9.69:1 para 1.6:1. Prova que os temas novos entram no gate de contraste **sem** teste novo, porque a classe está parametrizada sobre `sorted(THEMES)`. | T013 |
| M-01, M-02 | O `background` e o `bgcolor` do tema escuro. Reapontados para dentro de `_Blocos`. | F-02 |

**M-01 e M-02 pararam de morrer quando este ticket começou.** O harness de
mutação substitui strings literais do código-fonte, e o refactor moveu as duas
linhas para dentro de `_Blocos.superficie()` e `_Blocos.tabela()`. A mutação
virou um no-op em silêncio — e o gate ficou vermelho, que é exactamente para que
existe. O `NÃO APLICADA` é distinto de "o teste não prova": o harness tem os
dois casos separados, e é por isso que o diagnóstico não engana.

### Requisitos

| ID | Estado | O que o prova |
|---|---|---|
| FR-3.12 | `IMPLEMENTADO` — alargada | `test_renderer.py::TestLayouts` (todas as combinações), `TestAssinaturaLegivelNoClienteDeEmail` |
| FR-3.11 | `IMPLEMENTADO` — agora inclui o layout | `TestEstruturaDaAssinatura::test_a_estrutura_e_guarded` |
| FR-3.8 | `IMPLEMENTADO` | `test_preview_respeita_a_estrutura` |
| FR-4.1 | `IMPLEMENTADO` — 28 HTMLs | `test_toda_a_combinacao_e_table_based` |
| NFR-13 | `IMPLEMENTADO` — contraste das paletas | `test_todo_o_texto_do_tema_passa_4_5`, morto por M-17 |

Nenhum requisito passou a `VERIFICADO`: `VERIFICADO` exige
`scripts/verify-implementation.sh` a correr contra este ticket, e isso é o
primeiro item a fazer quando o dono decidir continuar.

## Dívida que este ticket abre

| Item | Onde | Impacto |
|---|---|---|
| As 28 combinações nunca foram abertas num cliente de email | `renderer.py` | Só um teste manual com Thunderbird e Outlook detecta. Registado, não resolvido. |
| `scripts/run-mutations.py` continua a apontar para linhas literais | `run-mutations.py` | Qualquer refactor futuro volta a partir mutações. O gate apanha, mas obriga a caçar. Um alvo por caminho+nome seria mais robusto. |
| `CLAUDE.md` ficou com 223 linhas, acima do alvo de 200 | `CLAUDE.md` | As 6 regras novas do `Never Do` são verdadeiras na maioria das sessões, que é o critério de entrada do ficheiro. A alternativa seria `Never Do` em `.claude/rules/`. Decisão do dono. |