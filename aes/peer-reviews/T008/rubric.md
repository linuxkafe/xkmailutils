---
rubric-id: MAILUTILS-T008-v1
candidate: T008 — Playwright E2E + seis correcções de browser + gates do Makefile
candidate-commit: 919f38a71b42c0839afc0fa23738761da63a5bf2
branch: aes/t008-playwright-e2e
created: 2026-09-30
mode: multi-perspective (fallback, PEER_REVIEW.md §4 — modelo único)
dimensions:
  - correctness
  - security
  - coherence
  - debt
  - reproducibility
  - usability
---

# Review Rubric — MAILUTILS-T008-v1

Pré-registado antes de qualquer persona ver o candidato. Qualquer edição
posterior a este ficheiro invalida a revisão. Hash em `rubric-hash`.

O candidato é **um commit só** (`919f38a`), e não uma branch comparável: o
`src/mailutils/` estava por versionar, portanto não existe um estado anterior
em git contra o qual comparar. As correcções e o produto entraram juntos. Isto
é um facto sobre o que é revisto, não um critério.

Critérios D1–D6, cada um com um comando verificável. Adjectivos não são
critérios: se não há comando, não há critério.

## Correctness

| ID | Critério | Verificável |
|----|----------|-------------|
| C-01 | O gate que falhava passa a falhar | `cp src/mailutils/db.py /tmp/d.bak && printf '\n\ndef   x( ):\n  return  1\n' >> src/mailutils/db.py && make format-check; ec=$?; cp /tmp/d.bak src/mailutils/db.py; test $ec -ne 0` |
| C-02 | A excepção de CSP é exactamente uma e só está em `frame-src` | `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k csp` passa; e o teste `test_only_frames_may_use_blob` faz o trabalho |
| C-03 | A tabela de larguras cobre todos os scores possíveis | `python3 -m pytest tests/test_browser_regressions.py -q --no-cov -k tabela` passa |
| C-04 | O score é mesmo um inteiro de 0 a 100 | `python3 -c "from mailutils.signatures.spam import score_signature; s=score_signature('<p>Ana</p>'); assert isinstance(s['score'], int) and 0 <= s['score'] <= 100, s['score']; print('int ok', s['score'])"` |
| C-05 | Não há `style=""` em markup nem em HTML construído por JS | `python3 -m pytest tests/test_browser_regressions.py -q --no-cov -k inline` passa |
| C-06 | O E2E mede a barra renderizada, não o atributo | `grep -q "getBoundingClientRect" e2e/test_fluxo_completo.py` |
| C-07 | O segundo factor do E2E não é semeado na base de dados | `! grep -qE "hash_otp|otp_codes" e2e/*.py` |
| C-08 | A porta do servidor E2E não é fixa | `grep -q "_free_port" e2e/conftest.py` |
| C-09 | `pytest tests e2e` juntos funciona (a colisão de `conftest`) | `python3 -m pytest tests e2e -q --no-cov` sai 0 |
| C-10 | Os testes de tema provam o que dizem provar | `python3 -m pytest tests/test_browser_regressions.py -q --no-cov -k tema` passa |

## Security

| ID | Critério | Verificável |
|----|----------|-------------|
| S-01 | Nenhum segredo committado | `python3 scripts/check-no-secret-leak.py` passa; `! git ls-files \| grep -x ".env"`; `test -f .env.example` |
| S-02 | A CSP não relaxa estilos nem scripts | `! grep -E "unsafe-inline\|unsafe-eval\|unsafe-hashes" src/mailutils/main.py` |
| S-03 | `blob:` não aparece noutra directive, no header **servido** | `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k only_frames` passa. Contar no ficheiro não serve: `blob:` aparece três vezes em `main.py` e duas são comentários. |
| S-04 | A interpolação do utilizador não entra em CSS nem em HTML servido | `! grep -nE "innerHTML *= *[^;]*\b(escapeHtml|fields\[)" src/mailutils/static/app.js \| grep -v escapeHtml` — e `! grep -n "style=\"{{" src/mailutils/templates/*.html` |
| S-05 | O nonce é o único caminho que a CSP não tem, e não foi aberto | `! grep -rn "nonce" src/mailutils/main.py` (o candidato escolheu não usar nonce) |
| S-06 | O OTP nunca é impresso | `! grep -rn "print" src/mailutils/mailer.py \| grep -iE "otp\|code\|código"` sem ser o backend console, e `python3 -m pytest tests/test_security.py -q --no-cov` passa |

## Coherence

| ID | Critério | Verificável |
|----|----------|-------------|
| E-01 | O `CLAUDE.md` descreve o gate que existe | `grep -q "make e2e" CLAUDE.md` e `grep -q "E2E" CLAUDE.md` |
| E-02 | A excepção `frame-src` está escrita no contrato | `grep -q "frame-src 'self' blob:" CLAUDE.md` |
| E-03 | O ROADMAP diz a verdade sobre o T008 | `grep "T008" docs/ROADMAP.md` mostra `feito` |
| E-04 | O ticket T008 existe e lista os bugs com teste | `test -f aes/tickets/T008-playwright-e2e.md` e `grep -c "| P0 \|| P1 \|| P2 |" aes/tickets/T008-playwright-e2e.md` ≥ 4 |
| E-05 | O kanban não diz mais "Sem E2E com browser" | `! grep -q "Sem E2E com browser" aes/kanban.md` |
| E-06 | O desvio de dependência está declarado e justificado | `grep -q "playwright" docs/REQUIREMENTS.md` e `grep -q "NFR-10" docs/REQUIREMENTS.md` |
| E-07 | As utilitários de CSS estão documentadas | `grep -q "score__denom" docs/DESIGN.md` |

## Debt

| ID | Critério | Verificável |
|----|----------|-------------|
| D-01 | A dívida nova está declarada, não escondida | `grep -q "101" aes/tickets/T008-playwright-e2e.md` (a tabela de scores é dívida de acoplamento) |
| D-02 | A dívida que o ticket diz herdar continua visível no kanban | `grep -q "convites" aes/kanban.md` |
| D-03 | Não há código morto introduzido | `! grep -rn "TODO:" src/ tests/ e2e/` |
| D-04 | As 101 regras não são duplicação que um lint devia apanhar | `python3 -c "import re,pathlib; c=pathlib.Path('src/mailutils/static/app.css').read_text(); n=[int(x) for x in re.findall(r'data-score=\"(\d+)\"', c)]; assert len(n)==len(set(n))==101; print('101 únicas')"` |
| D-05 | Os dois blocos novos de CSS estão delimitados e a tabela de scores vive com o score, não com os utilitários | `python3 -c "import pathlib,re; c=pathlib.Path('src/mailutils/static/app.css').read_text(); u=c[c.index('utilitarios'):c.index('light -- */')]; s=c[c.index('barra --'):c.index('.score__badge {')]; assert u.count('data-score')==0, 'utilitarios nao devem ter regras de score'; n=[int(x) for x in re.findall(r'data-score=.(\d+).', s)]; assert n==list(range(101)), 'a tabela tem de ser 0..100 por ordem'; print('ok: utilitarios limpos, tabela completa e ordenada')"`. **Limite declarado:** não há baseline — `app.css` entrou no git neste commit, portanto qualquer medição de «crescimento» mede a primeira inserção e não o trabalho do T008. O ficheiro tem 906 linhas. |

## Reproducibility

| ID | Critério | Verificável |
|----|----------|-------------|
| R-01 | `make check` verde no commit | `git stash list` vazio e `make check` sai 0 |
| R-02 | O gate E2E falha alto sem browser, com instrução de reparação | `grep -q "playwright install chromium" scripts/check-playwright-browsers.py` |
| R-03 | O E2E é determinístico, não depende de `/saude` estar livre | `grep -q "_free_port" e2e/conftest.py` e `grep -q "tmp_path_factory" e2e/conftest.py` |
| R-04 | Nenhum teste depende de rede | `! grep -rnE "https?://(?!127\.0\.0\.1|localhost)" e2e/*.py` — a excepção pretendida é o base URL local |
| R-05 | O `.gitignore` protege os artefactos do candidato | `git check-ignore -q .coverage coverage.xml var/mailutils.db` |

## Usability

| ID | Critério | Verificável |
|----|----------|-------------|
| U-01 | O caminho principal é verificável por um humano sem ler código | `make run` e seguir `/xkmailutils/entrar` produz 6 passos; a validação está no human script |
| U-02 | O tema é alcançável pelo utilizador, não só por cookie | `! grep -rn "mailutils_theme" src/mailutils/templates/` — se não há toggle na UI, o tema claro é inalcançável por clique. **Este é um teste propositadamente REDE.** |
| U-03 | Os erros visíveis dizem o que fazer | `grep -c "notice--erro" src/mailutils/templates/editor.html` > 0 e cada `erro_chave` tem texto em `src/mailutils/templates.py` |
| U-04 | O score nunca mente ao utilizador | `python3 -m pytest e2e -q --no-cov -k barra` passa |
| U-05 | O segundo factor é completável por um humano real | `grep -q "MAILUTILS_MAIL_BACKEND=console" .env.example` e o utilizador sabe onde está o código; ver human script |

---

**Nota sobre U-02.** Este critério está pré-registado a Expectar FALHAR. O
candidato corrigiu o mecanismo do tema mas não adicionou interface para o
usar. A revisão não pode ser ACCEPT enquanto um utilizador real não conseguir
mudar de tema com um clique.

pre-registered-hash: 042865efdd349ddc5c3f9da01260780312f7da6b1016cc38d59db0bef6ddf6dc
