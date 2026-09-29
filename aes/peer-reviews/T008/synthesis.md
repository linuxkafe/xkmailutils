# Síntese — revisão T008 (multi-perspectiva, fallback)

- **Candidate:** T008, commit `919f38a`, branch `aes/t008-playwright-e2e`
- **Rubric:** `MAILUTILS-T008-v1`, hash `042865ef…` (ver `rubric-hash`)
- **Modo:** multi-perspective (PEER_REVIEW.md §4 — modelo único, sem revisores
  independentes de outra família)
- **Rubric pré-registado e commitado** em `a3ec20a`, antes de qualquer persona
  ver o candidato
- **Data:** 2026-09-30

## Finder por persona

| Persona | BLOCKER | MAJOR | MINOR |
|---------|---------|-------|-------|
| CÍNICO | 1 | 2 | 0 |
| PURISTA | 0 | 4 | 1 |
| PRAGMÁTICO | 1 | 2 | 2 |
| UTILIZADOR | 1 | 3 | 0 |

## Findings fundidos

| # | Tipo | Personas | Critério |
|---|------|----------|----------|
| F-01 | BLOCKER | Cínico (B), Utilizador (M), Pragmático (M), Purista (m) | U-02 |
| F-02 | BLOCKER | Utilizador | U-04 |
| F-03 | BLOCKER | Pragmático | R-02 |
| F-04 | MAJOR | Utilizador | C-02 |
| F-05 | MAJOR | Purista | C-01 |
| F-06 | MAJOR | Purista | C-01 |
| F-07 | MAJOR | Purista | C-01 |
| F-08 | MAJOR | Utilizador | C-02 |
| F-09 | MAJOR | Pragmático, Purista (notado) | R-01 |
| F-10 | MAJOR | Purista | R-01 |
| F-11 | MAJOR | Cínico | D-03 |
| F-12 | MAJOR | Utilizador | U-04 |
| F-13 | MINOR | Pragmático | U-01 |
| F-14 | MINOR | Pragmático | E-01 |

**Totais: 3 BLOCKER, 9 MAJOR, 2 MINOR.**

## Divergências preservadas

**F-01 (tema inacessível).** O Cínico classificou BLOCKER; o Pragmático e o
Utilizador MAJOR; o Purista MINOR (via FR-5.3). A divergência é real e não se
resolve: para o utilizador é BLOQUEANTE (uma feature inteira que ele não
consegue acender), e o Purista tem razão numa coisa — o mecanismo está
correcto e o que falta é interface, o que é trabalho pequeno. Mantido BLOCKER
porque o candidato **pré-registou U-02 como vermelho e escreveu que a revisão
não pode ser ACCEPT enquanto durar** (rubric, nota sobre U-02). O autor fixou
o próprio criterion; a review não pode baixá-lo.

**F-11 (selectores CSS).** O Cínico assume que os 4 utilitários órfãos são
deste commit com base na forma da família, e diz explicitamente que **não
consegue prová-lo** porque D-05 declara que não há baseline. Aceito como MAJOR
mas com essa incerteza registada. Os 6 selectores BEM (`preview--light`,
`code-display`, `link-rows`, `no-js-only`, …) podem ser anteriores.

**F-04 e F-08 sob C-02.** O Utilizador mapeou ambos para C-02, e noto que
**o critério está mal escolhido**: C-02 diz "a excepção de CSP é uma só e está
em `frame-src`", e isso é verdade — o problema é que a excepção está no sítio
certo e o ecrã continua errado. Isto é uma limitação do rubric, não das
personas. O mesmo se passa com C-05. O rubric mede a excepção, não o
resultado em ecrã. **Fica registado como limitação do rubric**, e é a razão
de o D5 (reproducibilidade) e o U-04 (o score nunca mente) serem os critérios
que de facto apanharam F-02.

## Verificação independente do moderador

Regra: nenhum finding é aceite por autoridade. O moderador reproduziu:

| Finding | Verificado | Como |
|---------|-----------|------|
| F-01 | sim | `grep mailutils_theme src/mailutils/templates/` vazio; confirmado |
| F-02 | sim | `renderer.py:56` define `background` e o grep mostra que nunca é lido; fragmento exportado tem `color:#f0f0f0` e zero `background` — medido |
| F-03 | sim | `.github/workflows/ci.yml:16,25` — `make setup` depois `make check`; nenhum `playwright install` em `.github/`, `Makefile`, `Dockerfile` |
| F-04 | sim | medido num browser: `Times New Roman`, `rgb(0,0,0)`, body transparente, 11 violações `style-src`, e `TÃ©cnica de TI` no preview com o exportado correcto |
| F-06 | não | aceito da persona (mutação não reproduzida por mim) |
| F-08 | sim | `routes.py:326` — `default-src 'none'; img-src https: http:`, sem `style-src` |
| F-09 | não | aceite da persona |
| F-12 | sim | medido: `0 / 100`, `data-nivel="SEGURO"`, `Créditos: COMPACT (-4), PLAIN_FALLBACK (-3)` com o formulário vazio |

## Veredicto

Pela tabela de `PEER_REVIEW.md` §6:

- **3 BLOCKER** → no máximo **MAJOR-REVISIONS**
- **Human validation script não executado** → **REJECT**

O veredicto formal desta ronda é **REJECT**, e recuso-me a degradá-lo: as duas
condições da tabela aplicam-se. Isto é o resultado correcto, e é mais severo do
que o resumo que fiz ao dono antes desta revisão.

Registado com honestidade: **o resumo que entreguei antes dizia que o preview
passou a renderizar e que a barra de score estava corrigida.** A segunda
afirmação é verdadeira. A primeira é verdadeira só no sentido mais fraco que
permite: o iframe carrega, e o meu próprio teste passa porque afirma apenas
texto não-vazio. O preview mostra a assinatura sem estilos e com os acentos
partidos. `docs/DESIGN.md` diz que o cartão do preview mostra o "HTML exacto".
Não mostra.

## O que a revisão não apanhou

Isto é o que a revisão não viu, e vale mais do que os findings:

1. **Nenhuma das 4 personas notou que `Theme.background` está definido e nunca
   é lido** — e o Utilizador, que foi o único a abrir a aplicação de facto,
   foi o único a apanhar. A moral é que **corri código em vez de correr a
   aplicação**, e que quatro revisores LLM não substituem uma pessoa a olhar
   para o ecrã.
2. **Duas personas descobriram, por mutação, que três dos meus testes não
   detectam as mutações que alegam detectar** (F-05, F-06, F-07). A minha
   "prova por mutação" foi feita à mão, uma vez, e não deixou rasto. A
   afirmação era verdadeira no momento em que a fiz e é falsa como prova
   auditável.
3. **F-09 é o mesmo bug que eu corrigi no `format-check`.** Um gate que
   imprime verde sem verificar. Eu procurei um e não procurei o outro.

## Shadow docs

Por `PEER_REVIEW.md` §8, cada finding entra na camada de memória. Este
projecto **não tem** `aes/shadow/`, `aes/index.db` nem `scripts/`. Registado
como dívida do regime, não como finding sobre o candidato.

## Próximo passo

1. Os 3 BLOCKER primeiro. F-03 é o mais barato (uma linha em `ci.yml`).
2. F-02 é o mais grave para quem usa: uma assinatura invisível com selo SEGURO.
3. `human-validation.sh` neste directório tem de ser executado por alguém que
   não seja eu nem o dono do candidato. Sem isso, nenhuma ronda pode ACCEPT.
