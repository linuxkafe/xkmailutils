---
rubric-id: MAILUTILS-T014-v1
candidate: T013 + T014 — temas e estruturas na assinatura, listas de destinatários
candidate-commit: 074a9fc
branch: aes/sprint-02-temas-listas
created: 2026-10-02
mode: multi-perspective (fallback, PEER_REVIEW.md §4 — modelo único)
dimensions:
  - correctness
  - security
  - coherence
  - debt
  - reproducibility
  - usability
  - epistemics
---

# Review Rubric — MAILUTILS-T014-v1

Pré-registado e committado **antes** de qualquer persona ver o candidato.
Qualquer edição posterior a este ficheiro invalida a revisão. Hash em
`rubric-hash`.

O candidato é a **inversão de produto**, não uma melhoria. Isto muda o que
"correcto" significa, e por isso a rubrica dá mais peso a duas perguntas que
nunca foram centrais no T008:

1. **A afirmação coincide com o código?** Este projecto passa o caminho
   inteiro a recusar claims não verificadas (ver `CLAUDE.md`, `Evidence
   Required`). Uma docstring que descreve um token assinado que não existe é um
   BLOCKER de epistemia, não um TODO.
2. **A superfície que se criou é maior do que a que se provou?** O T008
   encontrou 28 combinações de tema × estrutura num browser. Esta revisão tem de
   dizer se os testes cobrem o que a superfície permite.

## Correctness

| ID | Critério | Verificável |
|----|----------|-------------|
| C-01 | O invariante central é um único `SELECT` | `grep -c "confirmed_at IS NOT NULL" src/mailutils/lists/service.py` devolve 1, e a ocorrência está em `destinatarios()` |
| C-02 | Nenhuma outra função devolve destinatários | `! grep -nE "SELECT (email|\*).*FROM list_addresses" src/mailutils/lists/*.py \| grep -v destinatarios \| grep -v enderecos_da_lista \| grep -v contar_enderecos \| grep -v pendentes_por_utilizador` |
| C-03 | `stack` não mudou um byte | `python3 -m pytest tests/test_renderer.py -q --no-cov -k byte_identico` passa |
| C-04 | As 28 combinações passam as invariantes | `python3 -m pytest tests/test_renderer.py -q --no-cov -k "toda_a_combinacao"` passa (8 testes × 28) |
| C-05 | A estrutura chega ao HTML exportado | `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k Estrutura` passa |
| C-06 | Preview e exportação concordam sobre o layout | `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k preview_respeita` passa |
| C-07 | A migração é idempotente | `python3 -c "import sys;sys.path.insert(0,'src');from mailutils import db;c=db.connect(':memory:');[db.migrate(c) for _ in range(3)];print('ok')"` |
| C-08 | O tecto de pendentes é verificado onde o pendente nasce | `grep -q "ja_pendentes" src/mailutils/lists/service.py` e `! grep -q "pending.*>=.*max_pending" src/mailutils/lists/service.py` na função `pedir_confirmacao` |
| C-09 | Importar não confirma ninguém | `python3 -m pytest tests/test_lists.py -q --no-cov -k "test_ter_o_codigo"` passa |
| C-10 | A CSV de uma coluna sem cabeçalho não é lida como cabeçalho | `python3 -m pytest tests/test_lists.py -q --no-cov -k "uma_coluna_so"` passa |
| C-11 | O utilizador fica preso? | `python3 -m pytest tests/test_lists.py -q --no-cov -k "tecto_nao_prende"` passa |

## Security

| ID | Critério | Verificável |
|----|----------|-------------|
| S-01 | Nenhum segredo committado | `python3 scripts/check-no-secret-leak.py` passa; `! git ls-files \| grep -x ".env"` |
| S-02 | O código de confirmação nunca é devolvido | `python3 -m pytest tests/test_lists.py -q --no-cov -k nunca_volta` passa; e `! grep -rniE "print.*(codigo|código|code)" src/mailutils/lists/` |
| S-03 | Isolamento por utilizador em todas as rotas de lista | `python3 -m pytest tests/test_lists.py -q --no-cov -k Isolamento` passa |
| S-04 | CSRF em todas as escritas | `python3 -m pytest tests/test_lists.py -q --no-cov -k csrf` passa |
| S-05 | A escrita está dentro de transacção | `grep -c "with transaction" src/mailutils/lists/service.py` ≥ 6 |
| S-06 | O tecto anti-abuso não é removível sem quebrar testes | M-19 morre: `python3 scripts/run-mutations.py --verificar` reporta 19/19 |
| S-07 | O cooldown é por endereço entre listas | `python3 -m pytest tests/test_lists.py -q --no-cov -k cooldown` passa |
| S-08 | Sem SQL por concatenação | `! grep -nE '(execute|executemany)\(\s*f"' src/mailutils/lists/*.py` |
| S-09 | `descadenciar` exige token assinado | **VER PROVÁVEL.** `grep -q "descadenciar" src/mailutils/lists/routes.py` — hoje **não existe rota**. Um BLOCKER ou um ACCEPT depende disto |

## Coherence

| ID | Critério | Verificável |
|----|----------|-------------|
| E-01 | O `CLAUDE.md` descreve o que o código faz | `grep -q "compose/" CLAUDE.md` e `grep -q "scheduler.py" CLAUDE.md` — mas `src/mailutils/compose/` e `scheduler.py` **não existem**. As regras mencionam ficheiros por escrever |
| E-02 | Nenhum FR `IMPLEMENTADO` sem código | Para cada FR marcado `IMPLEMENTADO` em `docs/REQUIREMENTS.md`, existe o teste nomeado no ticket. `grep -n "IMPLEMENTADO" docs/REQUIREMENTS.md \| wc -l` vs Finding list |
| E-03 | O ROADMAP reflecte o que está feito | `grep -q "T014.*feito" docs/ROADMAP.md` e `grep -q "T013.*feito" docs/ROADMAP.md` |
| E-04 | As mensagens de interface vivem em `MESSAGENS` | `python3 -m pytest tests/test_lists.py -q --no-cov -k MESSAGENS` passa |
| E-05 | Nenhuma chave de `MESSAGENS` duplicada | `python3 -c "import ast,pathlib;t=ast.parse(pathlib.Path('src/mailutils/templates.py').read_text());..."` — ou `ruff` sem F601 |
| E-06 | A Persona 4 está assinalada como hipótese | `grep -q "honestidade epistémica" docs/PERSONAS.md` |

## Debt

| ID | Critério | Verificável |
|----|----------|-------------|
| D-01 | Nada é `VERIFICADO` sem `verify-implementation.sh` | `! grep -n "VERIFICADO" docs/REQUIREMENTS.md` (só `VERIFICADO` no cabeçalho da convenção) |
| D-02 | A dívida nova está registada | `grep -q "T014" aes/kanban.md` |
| D-03 | O `CLAUDE.md` está dentro do alvo de tamanho | `test $(grep -c "" CLAUDE.md) -lt 200` — **hoje dá 223**. Falha, e o próprio ficheiro diz que o alvo é < 200 |
| D-04 | O harness de mutação aponta para linhas literais | `grep -q "original.count(mutacao.antes)" scripts/run-mutations.py` — fragilidade aceite e registada |
| D-05 | A textarea do `CLAUDE.md` diz o tamanho certo | `grep -q "menos de 200 linhas" CLAUDE.md` |

## Reproducibility

| ID | Critério | Verificável |
|----|----------|-------------|
| P-01 | `make check` verde num clone limpo | `git clone . /tmp/c && cd /tmp/c && make check` |
| P-02 | A prova por mutação é gerada, não escrita à mão | `grep -q "gerado-por: scripts/run-mutations.py" aes/tickets/T008-mutations.md` |
| P-03 | A migração corre sobre uma base v1 existente | `python3 -c "..."` que cria `signatures` sem `layout` e corre `migrate()` |
| P-04 | `python3 -m pytest tests e2e` juntos funciona | `python3 -m pytest tests e2e -q --no-cov` sai 0 |

## Usability

| ID | Critério | Verificável |
|----|----------|-------------|
| U-01 | A regra "importar não confirma" está escrita onde o utilizador a encontra | `python3 -m pytest tests/test_lists.py -q --no-cov -k "diz_que_importar"` passa |
| U-02 | O botão diz o que vai acontecer | Existe confirmação para eliminar uma lista e para pedir códigos em massa? Verificar `lista.html` |
| U-03 | Importar mostra o que não entrou | `grep -q "Ver o que não entrou" src/mailutils/templates/lista.html` |
| U-04 | Navegação por teclado nos checkboxes | `grep -q "visually-hidden" src/mailutils/templates/lista.html` |

## Epistemics

**Dimensão nova nesta ronda.** Não é sobre o código: é sobre o que o candidato
**afirma** e se a afirmação é verificável.

| ID | Critério | Verificável |
|----|----------|-------------|
| X-01 | Nenhuma docstring descreve comportamento inexistente | Para cada frase "o token vai no caminho" / "assinado" em `src/`, existe o código. Hoje: `service.descadenciar` fala num token que não existe |
| X-02 | Nenhum ticket afirma `VERIFICADO` sem `verify-implementation.sh` | `! grep -n "VERIFICADO" aes/tickets/T013-*.md aes/tickets/T014-*.md \| grep -v "Nada passou"` |
| X-03 | A Persona 4 é declarada como hipótese, não como evidência | `grep -q "não veio de entrevista" docs/PERSONAS.md` |
| X-04 | As mutações novas são geradas, não afirmadas | `grep -q "M-18" scripts/run-mutations.py` e `grep -q "M-19" scripts/run-mutations.py` |
| X-05 | As decisões do dono estão registadas como reversing do que estava escrito | `grep -q "Decisões reverteridas" docs/ROADMAP.md` |
| X-06 | O README diz o que o produto é | `grep -qi "assinatura" README.md` e — se o README ainda diz "gerador de assinaturas" sem menção de envio, isso é um finding de coerência |
| X-07 | O `.env.example` documenta os tectos novos | `grep -q "MAILUTILS_MAX_LIST_SIZE" .env.example` — **hoje não existe.** Finding de coerência |

## Nota sobre o que esta rubrica **não** mede

- Não mede se as 28 combinações de tema × estrutura se apresentam bem no
  Thunderbird ou no Outlook. Isso exige olhos e um cliente real, e é o que o
  `human-validation.sh` tem de fazer.
- Não mede a conformidade legal. `NFR-17` diz explicitamente que a suficiência
  jurídica é do owner, e nenhuma revisão interna pode fechar isso.
- Não mede se o SMTP funciona. Nenhum teste do candidato fala a um servidor de
  correio real.