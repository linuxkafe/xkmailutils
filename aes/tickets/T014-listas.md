---
ticket: T014
title: Listas de destinatários com confirmação por OTP e importação CSV
sprint: sprint-02
prioridade: P1
status: done
criado: 2026-10-02
depende_de: T013
revisao: "aes/peer-reviews/T014/"
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

- [x] Listas por utilizador, com isolamento testado
- [x] Endereço adicionado **sempre** por confirmar
- [x] Código de 6 dígitos, `scrypt`, de uso único, com tentativas limitadas
- [x] `confirmed_at IS NOT NULL` no único `SELECT` que devolve destinatários
- [x] Importação CSV que **não** confirma ninguém
- [x] Tetos anti-abuso como configuração, não constantes
- [x] `make check` verde com 19/19 mutações

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
| **Link de descadência assinado** | FR-6.7 | `service.descadenciar(address_id)` **existe e funciona**, mas não há rota nem token assinado. A docstring da função fala num token no caminho que **não existe** — é a afirmação não verificada mais óbvia que ficou em pé |
| **`List-Unsubscribe` e one-click** | FR-6.8 | Não implementado. Só faz sentido com o T015 |
| **E2E do caminho de confirmação** | NFR-15 | O suite não toca em `/listas` |
| **Envio real por SMTP** | FR-7 | `send_confirmation` é interceptado em todos os testes |

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