---
ticket: T008
title: Playwright sobre o fluxo login → 2F → editor → score → exportar
sprint: sprint-01
priority: P1
status: done
created: 2026-09-29
---

# T008 — Playwright sobre o fluxo login → 2F → editor → score → exportar

## Context

Todos os fluxos estavam testados por HTTP, com um cliente ASGI in-process
(`tests/asgi_client.py`). Isso prova que as rotas respondem. Não prova que os
botões estão ligados aos campos, que a folha de estilo é aplicada, que a
descarga de um ficheiro chega ao browser, nem que um segundo factor se
completa — coisas que um browser faz e um cliente in-process não.

O custo desta ignorance turns out ser seis bugs, um deles impeditivo.

## Acceptance Criteria

- [x] Servidor uvicorn real, com base de dados e media em temporário
- [x] Login completo com segundo factor por email, o código lido do stdout do
      servidor e não semeado na base de dados
- [x] Editor, score e exportação `.html` verificados com clique e formulário
- [x] `blob:` no `frame-src`, a preview renderiza, e `blob:` não aparece noutra
      directive
- [x] A barra de score reflecte o score no primeiro load, no editor **e** no
      analisador
- [x] `make e2e` corre dentro de `make check` e falha alto se faltarem browsers
- [x] Nenhum `style=""` em template ou em HTML construído por JavaScript
- [x] Testes de regressão em `tests/` para cada bug, com mutação provada

## Bugs encontrados

Nenhum destes era visível a um teste de código de estado.

| # | Bug | Gravidade | Teste que o apanha |
|---|-----|-----------|-------------------|
| 1 | Campos `type="url"` exigiam esquema; o produto manda escrever o domínio sem esquema. `checkValidity()` falso, o browser **recusava submeter o formulário** e o botão Guardar não fazia nada, sem erro visível. | P0 | `e2e::test_fluxo_completo_login_editor_score_exportar` |
| 2 | O middleware definia `request.state.theme` **depois** de `call_next`; os templates lêem-no durante o render. O tema nunca se aplicava. | P1 | `test_browser_regressions::test_login_com_cookie_claro_da_html_claro` |
| 3 | `/assinatura` passava `tema` no contexto — o tema **da assinatura** — que ganhava por cima do `tema` da aplicação. Colisão de nomes. | P1 | `test_browser_regressions::test_editor_nao_sobrescreve_o_tema_da_aplicacao` |
| 4 | `style-src 'self'` descartava os 43 atributos `style=`. A barra de score renderizava a 100% com score 0 — e em `/analisar`, que o JS nunca corrige, ficava assim para sempre. | P0 | `e2e::test_a_barra_de_score_diz_a_verdade_no_primeiro_load`, `..._no_analisador` |
| 5 | O preview usava uma `blob:` URL e `default-src 'self'` bloqueava-a. O painel de pré-visualização nunca renderizou nada. | P1 | `e2e::test_o_preview_da_assinatura_renderiza` |
| 6 | `id="conteudo"` duplicado (`<main>` e `<textarea>`). `getElementById` devolvia o `<main>`, e o botão "colar um exemplo" focava a página em vez do campo. | P2 | validação de HTML; sem teste dedicado |

## Decisões

**Uma dependência nova.** `playwright>=1.40` entra em
`[project.optional-dependencies].dev` e em mais lado nenhum. O `CLAUDE.md`
proíbe dependências novas; este é o desvio, declarado e justificado, e o dono
aprovou-o. Declarada em vez de "usada se estiver instalada" pelo mesmo motivo
que o `format-check` deixou de engolar falhas: uma dependência implícita torna
o gate dependente da máquina.

**Sem `pytest-playwright`.** Traria uma dependência a mais e a sua própria
fixture de `page`, e o que este suite precisa de controlar é o servidor, não o
browser. `playwright.sync_api` dentro de pytest dá o mesmo com mais controlo.

**`e2e/` fora de `tests/`.** `testpaths = ["tests"]` mantém `make test` sem
browser, e `e2e/` tem `__init__.py` para não sombrear o `conftest.py` da outra
suite.

**A CSP não foi afrouxada.** `'unsafe-inline'` em `style-src` resolveria o
bug 4 numa linha, e abriria um sink de injecção de CSS no ficheiro onde o
utilizador escreve o texto da assinatura. As 43 declarações foram para
`app.css`; a largura da barra é uma regra por valor de score, indexada por
`data-score`, com um teste que garante que as 101 existem.

## Scope

**Dentro:** `e2e/`, `scripts/check-playwright-browsers.py`, `make e2e` /
`e2e-check`, o bug 4 (todos), o bug 5 (a excepção `frame-src`), os bugs 2, 3 e
6, `tests/test_browser_regressions.py`, e a documentação.

**Fora, de propósito:** `<progress>` em vez de `div` (perde a transição de
largura), mover os 41 estilos estáticos para nomes de componente em vez de
utilitário, e qualquer teste E2E fora do caminho login → 2F → editor → score →
exportar. T005-T007, T009-T012.

## Rollback

Reverter o commit. Nada de migrations, nada de dados, nada de esquema.

## Known Risks

- **A barra depende de o score continuar inteiro.** `data-score="42.5"` não
  casa com nenhuma das 101 regras e a barra esvazia. O teste da completude
  falha se isso acontecer, mas só depois de alguém mudar `spam.py`.
- **O `stdout` do servidor é o contrato do `console` backend.** Se o `print` do
  `mailer` mudar de sítio, o `wait_for_code` falha — de propósito, e com uma
  mensagem que diz o que aconteceu.
- **Correr em contentor sem browser é agora um `make check` vermelho.** Com a
  mensagem de reparação. É o custo de um gate que não mente.

## Notas

Cada teste novo foi provado por mutação: o bug foi reintroduzido e o teste
confirmado vermelho. Um teste que nunca foi visto falhar não é evidência.
