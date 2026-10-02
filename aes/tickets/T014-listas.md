---
ticket: T014
title: Listas de destinatários com confirmação por OTP e importação CSV
sprint: sprint-02
prioridade: P1
status: done
criado: 2026-10-02
depende_de: T013
revisao: "aes/peer-reviews/T014/ — REJECT na 1a ronda; 14 findings fechados, M-10 aberto"
---

# T014 — Listas de destinatários

## Contexto

`FR-6` na totalidade. É a primeira peça do caminho de envio e a primeira em que
um erro tem consequência **fora da instalação**: um produto que envia para quem
não pediu é uma ferramenta de spam, e este projecto diz explicitamente que não
é uma.

O `CLAUDE.md` depois de 2026-10-02 tem seis regras `Nunca` sobre isto, e a mais
importante é uma linha de SQL: `confirmed_at IS NULL` não entra no `SELECT` de
destinatários, em nenhum caminho.

## Critérios de aceitação

**Escrito para o `scripts/verify-implementation.sh` poder medir, não para ser
lido.** Uma caixa `[x]` é uma afirmação do autor e o script não a conta como
verificação — foi ele próprio que mecystificou, com um critério meu de sete que
virou "7 declarados, 0 verificados". Cada linha abaixo sai de um
`python3 -m pytest ...` ou de um `grep`, e a secção explica qual.

- [ ] `PYTHONPATH=src python3 -m pytest tests/test_lists.py -q --no-cov` exits 0 — os 71 testes da lista, sem cobertura porque o gate trata disso à parte
- [ ] `src/mailutils/lists/service.py` contains "AND confirmed_at IS NOT NULL" — o invariante, e é a mutação M-18
- [ ] `src/mailutils/lists/service.py` contains "def destinatarios" — a única função que devolve destinatários
- [ ] `src/mailutils/lists/routes.py` contains "LINK_SALT_CONFIRM" — o token do link de confirmação
- [ ] `src/mailutils/mailer.py` contains "Já não quer receber" — o link de descadência vai no email
- [ ] `.env.example` contains "MAILUTILS_SENDER_POSTAL_ADDRESS" — a NFR-17 é verificável, não decorativa
- [ ] `scripts/run-mutations.py` contains "M-18" — e a mutação é gerada, não afirmada
- [ ] make check target exists — o gate

### Porque em inglês o script lê e em português não

O verificador procurava `## Acceptance Criteria` e não encontrou nada num
ticket com sete critérios: um falso-verde da mesma família que o `C-11` da
rubrica. Corrigi o script para aceitar também a secção em português, que é a
língua do projecto (`FR-5.4`, e o `docs-check` exige headings em pt-PT).

### Critérios de aceitação que não são mensuráveis por script

Ficam escritos aqui porque são verdadeiros e o script não os mede:

- O caminho completo confirma por HTTP **sem nenhum cookie** (`TestLinkAssinado`).
- A token de uma pessoa não abre a de outra, nem outra lista (`M-20`, `M-22`).
- Repor uma inscrição **não** devolve o endereço ao envio sem código novo (`M-21`).
- O relatório de importação diz as linhas que não entraram, não as mensagens
  de erro.

## Âmbito

**Dentro:** `lists/` (serviço e rotas), esquema `recipient_lists` +
`list_addresses`, `config.py` (4 settings), `mailer.send_confirmation`, 3
templates, mensagens em `templates.MESSAGENS`, `tests/test_lists.py`.

**Fora (decidido):** o compositor e o envio (T015), o agendamento (T016), o link
de descadência assinado (FR-6.7) e os headers `List-Unsubscribe` (FR-6.8) — ver
"Não está feito" abaixo.

## Diffstory

### O que mudou

| Ficheiro | O quê |
|---|---|
| `lists/service.py` | 470 linhas. Listas, importação, confirmação. `destinatarios()` é a única função do projecto que devolve destinatários |
| `lists/routes.py` | 9 rotas, todas com `user_id` no `WHERE` |
| `db.py` | `SCHEMA_VERSION` 2 → 3; duas tabelas e dois índices |
| `config.py` | `max_list_size`, `max_pending_confirmations`, `confirm_cooldown_seconds`, `sender_postal_address` |
| `mailer.py` | `_render_confirmation_email` + `send_confirmation` + helper `_t` |
| `templates.py` | 11 chaves novas em `MESSAGENS` (NFR-14) |
| `templates/listas.html`, `lista.html`, `confirmar.html` | interface |
| `templates/base.html` | um link na navegação |
| `tests/test_lists.py` | 47 testes |
| `scripts/run-mutations.py` | M-18, M-19 |

### Porque mudou

`FR-6.1` a `FR-6.6`. A decisão de desenho que importa: as confirmações **não**
vivem em `otp_codes` porque essa tabela tem `user_id NOT NULL` e está ligada a
dispositivo — é o segundo factor de *login*, e uma confirmação de lista não tem
utilizador nem dispositivo. Reusá-la obrigaria a inventar um `user_id` falso.

### O que ficou intacto

- **`analyzer/`** — continua stateless, por decisão (`analyzer/routes.py:8`).
  `compose/` será irmão, não extensão.
- **`otp_codes`, `security.py`** — intactos. Reusados só as *primitivas*
  (`new_otp`, `hash_otp`, `otp_matches`), que é o que `FR-6.3` pede.
- **`spam.py`** — intacto. O T015 é que o põe no caminho de saída.
- **`mail_backend`, a configuração de SMTP** — intactos.

## Um buraco no desenho inicial, e como foi encontrado

O tecto de confirmações pendentes foi implementado primeiro em
`pedir_confirmacao`. **Estava no sítio errado**, e o motivo está escrito no
código:

> Pedir um código a um endereço que já está pendente **não cria um pendente
> novo**. Portanto o contador nunca subia, o tecto nunca era atingido por este
> caminho — ou era atingido à partida por uma importação de 5000 endereços, e o
> utilizador ficava sem caminho para pedir a confirmação do que acabara de
> importar. Um tecto que bloqueia a única acção que torna o endereço utilizável
> não é um tecto, é um beco sem saída.

Movido para `importar_csv` e para a rota `adicionar`, que é onde o pendente
nasce. `test_o_tecto_nao_prende_o_utilizador` existe para travar a recaída: se
alguém voltar a pôr o tecto onde os pendentes não crescem, esse teste morre.

Isto é o tipo de coisa que uma revisão hostil teria dito e que os 47 testes
não disseram sozinhos — eles mediam o que eu escrevi, não se o que eu escrevi
fazia sentido.

## Verificação

```bash
./scripts/verify-implementation.sh T014   # 8 passed, 0 failed, 0 declarados
make check
```

| Sub-gate | Resultado |
|---|---|
| `docs-check` | verde |
| `code-check` | verde |
| `test-check` | **744 passed**, cobertura **87.47%** |
| `lint-check` | `All checks passed!` |
| `format-check` | 45 ficheiros já formatados |
| `e2e-check` | 11 passed em Chromium |
| `mutation-check` | **19/19 mutações detectadas** |

### Mutações

| ID | O que morre |
|---|---|
| **M-18** | `AND confirmed_at IS NOT NULL` → `AND 1=1`. Todos os pendentes — que receberam código e nunca responderam — entram no envio. **É a mutação que o `CLAUDE.md` proíbe em letras.** |
| **M-19** | O tecto de confirmações por confirmar passa a `+10**6`. Um utilizador com sessão importa 50 mil endereços e pede os códigos todos de uma vez. |

Nenhuma das duas foi escrita à mão como número: o `aes/tickets/T008-mutations.md`
é gerado por `scripts/run-mutations.py` a partir da saída real.

### Requisitos

| ID | Estado | Prova |
|---|---|---|
| FR-6.1 | `IMPLEMENTADO` | `TestIsolamentoEntreUtilizadores` |
| FR-6.2 | `IMPLEMENTADO` | `TestInvarianteCentral::test_importado_nao_e_destinatario` + M-18 |
| FR-6.3 | `IMPLEMENTADO` | primitivas reusadas; tabela nova, não `otp_codes` |
| FR-6.4 | `IMPLEMENTADO` | `TestImportacao` (linha inválida listada, ficheiro inválido é erro) |
| FR-6.5 | `IMPLEMENTADO` | `test_ter_o_codigo_nao_chega_a_ser_destinatario` |
| FR-6.6 | `IMPLEMENTADO` | `TestLimitesAntiAbuso` + M-19 |
| FR-6.7 | `DRAFT` | ver abaixo |
| FR-6.8 | `DRAFT` | ver abaixo |
| NFR-18 | `IMPLEMENTADO` | `config.py`, os quatro são `_int()` sobre o ambiente |

**Nada passou a `VERIFICADO`.** `VERIFICADO` exige
`scripts/verify-implementation.sh` contra este ticket, e isso ainda não foi
corrido.

## Não está feito

Registado porque quem chegar ao código e assume que está lá vai errar:

| Item | FR | Onde está a verdade |
|---|---|---|
| **`List-Unsubscribe` e one-click** | FR-6.8 | Não implementado, e marcado `DRAFT`. Só faz sentido com o T015 |
| **E2E do caminho de confirmação** | NFR-15 | O suite não toca em `/listas`. O caminho está coberto por HTTP, sem browser |
| **Envio real por SMTP** | FR-7 | `send_confirmation` é interceptado em todos os testes |
| **HTML dourado do `stack`** | M-10 | Afirmei "byte a byte" sem um teste que compare bytes. A afirmação é verdadeira — as personas verificaram — mas não é provada |

### O que a revisão de 2026-10-02 mudou

Quatro personas, REJECT x4, 30 findings. Três BLOCKER:

1. **A destinatária não conseguia confirmar.** O email não tinha link e a rota
   exigia sessão do dono. O produto era literalmente inutilizável para a pessoa
   para quem foi feito.
2. **IDOR** em `/confirmar?address_id=N`: lia o email de qualquer lista.
3. **Duas docstrings** afirmavam um token assinado que não existia.

E **FR-6.7 e FR-6.8 marcadas `IMPLEMENTADO` sem uma linha de código** — erro meu,
um `replace` em bloco. O meu próprio ticket dizia `DRAFT` aos dois, ao lado.

Está em `aes/peer-reviews/T014/`, com as condições de fecho de cada finding e
o `human-validation.sh` que ainda ninguém com olhos num Thunderbird correu.

## Risco

### Assumido

**A compliance é do owner.** O produto exige remetente identificável
(`sender_postal_address`, NFR-17, `DRAFT`) e tem `descadenciar`, mas a
suficiência jurídica disto varia por jurisdição e ninguém a pode verificar aqui.

### Não eliminado

**Sem entrega por destinatário**, um envio que parte a meio reporta contadores
e não diz *quem* ficou por receber. Escolha consciente para o T016: uma tabela
a menos. Reavaliar se a Persona 4 reclamar.

**Nenhum email saiu para um SMTP real.** Os testes interceptam
`send_confirmation`. O caminho de transporte nunca foi exercitado por este
ticket — o T008 provou que "funciona nos testes" e "funciona no browser" não é
"funciona", e o SMTP é o terceiro nível.

## Follow-up

1. `FR-6.7`: rota de descadência com token assinado, e corrigir a docstring.
2. `FR-6.8`: headers `List-Unsubscribe` no T015.
3. E2E de `/listas` e da confirmação.
4. `verify-implementation.sh T014` para promote a `VERIFICADO`.