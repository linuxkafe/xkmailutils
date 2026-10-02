# Síntese da revisão — MAILUTILS-T014-v1

- **Candidato:** `074a9fc` — T013 (temas e estruturas) + T014 (listas com confirmação)
- **Rubrica:** `rubric.md`, hash `3da33066`, pré-registada em `8a353d8`
- **Modo:** multi-perspectiva (fallback, modelo único)
- **Personas:** Cínico, Purista, Pragmático, Utilizador — subagentes frescos, sem
  histórico do autor e sem partilha de findings
- **Data:** 2026-10-02

## VERDICT: REJECT

Não por um único erro grave. Por **três da mesma espécie**, e as três juntos
descrevem um candidato que afirma mais do que provou:

| | Afirma | Realidade |
|---|---|---|
| **B-01** | `FR-6.7` e `FR-6.8` `IMPLEMENTADO` | zero linhas de código |
| **B-02** | a funcionalidade central é confirmar por email | o email não tem link e a rota exige sessão |
| **B-03** | «o token vai no caminho» | não há token |

E o meu **próprio ticket** dizia `DRAFT` aos dois primeiros, ao lado do
`REQUIREMENTS.md` que dizia `IMPLEMENTADO`. Foi um `replace` em bloco meu, num
projecto cuja `Evidence Required` diz que uma afirmação sem prova não existe. O
defeito que a rubrica foi escrita para apanhar foi cometido por mim.

## Contagem

**30 findings**: 4 BLOCKER, 10 MAJOR, 16 MINOR. **Veredictos: REJECT × 4.**

As quatro personas convergentam em 5 findings sem terem falado entre si:

| Finding | Cínico | Purista | Pragmático | Utilizador |
|---|---|---|---|---|
| B-02 destinatária não consegue confirmar | F-04 | — | F-01 | F-01 |
| B-03 token que não existe | F-03 | F-02 | — | — |
| M-03 critical files inexistentes | F-05 | F-03 | — | F-09 |
| M-04 `.env.example` incompleto | F-06 | F-13 | F-08 | F-08 |
| M-05 `sender_postal_address` inerte | F-11 | F-05 | F-09 | F-09 |

Uma divergência que **não** resolvi e registo como tal: o Purista classificou
M-10 («stack byte a byte» sem teste) como BLOCKER e o Cínico como BLOCKER, mas
ambos concordam que `stack` **é** byte-idêntico — verificaram por
`pytest` e por diff, e é verdade. O defeito é a **afirmação não provada**, não o
comportamento. Mantive como MAJOR porque o comportamento está certo; se o dono
discordar, o argumentsómo é o mesmo que o do B-01.

## O que as personas acharam umas das outras

Cada uma encontrou coisas que as outras não viram, e vale a pena que fiquem
registadas porque são a prova de que o modo funcionou:

- **Só a Utilizador** viu `M-08` (o aviso diz «0 endereço(s)» porque `n` nunca
  chega ao contexto) e `M-16` (a descrição da estrutura não é anunciada).
- **Só o Pragmático** viu `m-04` (SMTP a meio do lote dá 500) e `m-05` (não há
  «marcar todos» com 150 destinatários).
- **Só o Purista** viu `M-02` (o `CLAUDE.md` afirma a invariante do `spam.py`),
  `m-03` (`CONFIRM_COOLDOWN_SECONDS=0` desliga o cooldown), `m-11` e `m-14`.
- **Só o Cínico** viu `m-08` (os números do meu diffstory não batem com os
  ficheiros) e `m-09` (helpers duplicados no meu ficheiro de testes).

E o mais importante: **a Utilizador encontrou um erro no meu instrumento de
revisão, não no candidato** (`m-15`). A rubrica `MAILUTILS-T014-v1` tem o
critério `C-11` escrito como `-k "tecto_nao_prende"`, e o teste chama-se
`test_o_teto_nao_prende_o_utilizador`. O critério corre **zero testes** e sai com
código 5. Uma rubrica não verificada é um instrumento que não mede.

## O que o candidato faz bem

Registo isto porque uma revisão que só sabe dizer mal não é uma revisão.

Verificado por execução, não por leitura:

- **744 testes, 87.47% de cobertura, 19/19 mutações, 11 E2E** — todos reproduzidos
  pelas personas, executados por elas e não por knowledge do autor.
- **`destinatarios()` é de facto a única função que devolve destinatários para
  envio.** O predicado aparece 5 vezes (M-06), mas as outras 4 são contagens para
  a interface. A substância do invariante está certa; a frase é que não.
- **A parametrização dos temas sobre `sorted(THEMES)` é exemplar.** Um tema novo
  entra no gate de contraste sem teste novo, e foi isso que me deixou escolher as
  cores a calcular em vez de a olho. Purista chegou a levantar um finding sobre
  isto e **auticou-o** por não conseguir rebater — é exactamente o comportamento
  que a rubrica pede.
- **`_dividir`, `MAX_CSV_ROWS` e `ResultadoImportacao` são o mínimo, não
  gold-plating.** `FR-6.4` pede «contada e listada»; devolver a lista ao ecrã em
  vez de a mandar para um log é que está certo. O defeito é a contagem (M-07), não
  o desenho.
- **A tabela de «Não está feito» do ticket é real** — cobre a falta de rota de
  descadência, `List-Unsubscribe`, E2E e SMTP real. Ou seja: **eu sabia do B-01 e
  do B-03, escrevi isso no ticket, e na mesma sessão escrevi `IMPLEMENTADO` no
  `REQUIREMENTS.md`.** Não foi esquecimento. Foi o documento de requisitos a
  ganhar a métrica errada.
- **A fronteira `analyzer/` ⊥ `compose/` está honrada** — `analyzer/` continua
  stateless, e `grep -rn execute src/mailutils/analyzer/` não devolve nada.
- **A migração funciona** sobre uma base v1 real, e `migrate()` é idempotente por
  execuções.

## O que a rubrica não mediu, e devia

Três lacunas que a ronda expôs e que a próxima rubrica tem de fechar:

1. **Comportamento quando uma operação a meio falha.** Não há critério. Foi
   `m-04`, e nenhum dos outros 29 findings o pagaria.
2. **Que o destinatário consegue completar o fluxo.** Havia critérios para o dono
   da lista e nenhum para quem recebe o email — que é a pessoa para quem a Persona 4
   existe. O `C-11` devia ter dito «um cliente HTTP sem sessão confirma a
   inscrição».
3. **A rubrica precisa de um critério que verifique a rubrica.** `m-15` provou que
   um `-k` mal escrito produz um falso-verde.

## Plano de fecho

Ordenado por dependência, não por severidade. `B-02` antes de `B-01` porque sem
link no email não há fluxo para avaliar.

| # | Finding | O que é preciso |
|---|---|---|
| 1 | B-02 | Link assinado no email de confirmação + rota pública de confirmação |
| 2 | B-04 | `_exige_lista` antes de ler o endereço, e teste de isolamento para `/confirmar` |
| 3 | B-03 | Token passa a existir (fecho 1) ou as frases saem |
| 4 | M-01 | Reposição exige novo código, ou a rota deixa de repor |
| 5 | B-01 | FR-6.7 e FR-6.8 regressam a `DRAFT` até o código existir |
| 6 | M-02, M-03 | `CLAUDE.md` deixa de afirmar o que não existe |
| 7 | M-06 | Reescrever a afirmação do invariante para descrever o código |
| 8 | M-10 | HTML dourado do `stack`, e a mutação de 1 byte morre |
| 9 | M-05, M-04 | Consumir `sender_postal_address` ou tirá-la; `.env.example` |
| 10 | M-07, M-08, M-09 | Contagens e mensagens que digam a verdade |
| 11 | m-01…m-16 | README, ROADMAP, números, helpers, `user_version`, `telefone()`, rubrica |

## Validação humana

`human-validation.sh` nesta pasta. **Sem ele executado por alguém que não seja o
autor nem o agente que escreveu o candidato, o veredicto continua REJECT** — é a
mitigação real de `PEER_REVIEW.md` §10.1, e é a razão de o script pedir olhos
num browser em vez de repetir os 744 testes que os quatro de nós já corremos.