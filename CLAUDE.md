# mailutils — Operational Contract

Este ficheiro é o contrato operacional para agentes de IA neste projecto. É o
primeiro ficheiro que um agente tem de ler. Define âmbito, limites e regras de
evidência.

---

## Intent

Duas ferramentas na mesma instalação, com o mesmo motor de score.

1. **Gerar assinaturas de email HTML** que **não introduzem padrões de spam**
   nos filtros dos clientes, e dizê-lo ao utilizador com um score explicável.
2. **Compor e enviar email** para listas de destinatários que o próprio
   utilizador construiu.

**Onde está a confirmação, e porque isso muda.** Hoy o consentimento está no
*destinatário*: cada endereço confirma a sua presença por código único, e
`confirmed_at IS NOT NULL` é a prova de que essa pessoa pediu para receber. O
`T017-A` inverte isto — a confirmação passa a ser do **remetente**, e o
operador passa a ser quem afirma ter o consentimento de quem importa. A segunda
leitura é mais fraca que a primeira, e por isso o T017-A tem de entregar seis
portões que juntos a tornam aceitável: `from` confirmado por código, `spam.py`
no caminho de envio, teto de destinatários por lista, cadência derivada do
score, unsubscribe com token assinado, e cooldown por endereço. **Retirar um
destes obriga a dizer qual dos outros deixa de valer.**

Enquanto o `T017-A` não entrar em `main`, a primeira leitura é a verdade e a
segunda é plano. `docs/REQUIREMENTS.md` diz qual das duas está em `IMPLEMENTADO`
e qual em `DRAFT`, e é lá que se vai verificar, não aqui.

Gestão de utilizadores e segundo factor por email em dispositivos novos, nas
duas.

A unifying invariant, **ainda por implementar**: a aplicação nunca envia algo
que ela própria reprovaria. Quando existir, o email que sai passa pelo mesmo
`spam.py` que avalia a assinatura e é bloqueado pelo mesmo critério. Está em
`FR-7.3` e `NFR-19`, ambos `DRAFT`, porque o T015 ainda não existe.

Escreve-se aqui no condicional, e não no presente, porque este ficheiro é lido
antes de qualquer código. Um `CLAUDE.md` que afirma o que o código faz obriga o
agente seguinte a procurar uma `spam.py` no caminho do envio e a não a encontrar.
(F-01 da revisão T014, MAJOR.)

---

## Non-Goals

Coisas que este projecto **NÃO** faz:

- **Não** é um cliente de email. Não recebe, não sincroniza, não tem caixas de
  entrada. Envia — para listas que o próprio utilizador construiu, com o
  consentimento que o `T017-A` passar a exigir ao remetente. Ver "Intenção"
  abaixo.
- **Não** garante entrega fora do spam. O score é heurístico; os algoritmos do
  Gmail e da Microsoft são caixas-negras. A UI diz isso ao utilizador, sempre.
- **Não** embute imagens em `data:` URI. É o sinal de spam mais severo numa
  assinatura. O logótipo é servido por URL.
- **Não** é multi-tenant. Uma instalação, um operador, N utilizadores.
- **Não** tem TOTP nesta entrega. O 2F é por email, por requisito do dono.
- **Não** menciona a Universidade do Porto. Em lado nenhum. Jamais.
- **Não** introduz dependências novas. Zero. Seprecisar de uma, é uma
  decisão a tomar com o utilizador, não um detalhe de implementação.

---

## Critical Files

Alterações a estes ficheiros têm de ser **explicitamente sinalizadas ao
utilizador antes de proceder**. Nunca em silêncio.

- `docs/DESIGN.md` — tokens. Um token mal alterado parte o tema nos dois lados.
- `src/mailutils/security.py` — hashing, sessões, CSRF, OTP. O ficheiro onde
  um erro é uma vulnerabilidade.
- `src/mailutils/db.py` — esquema e migrações. SQLite não faz rollback de
  schema por si.
- `src/mailutils/signatures/renderer.py` — o HTML que entra no email de
  clientes reais. Uma alteração muda o que mil pessoas enviam.
- `src/mailutils/signatures/spam.py` — as regras de score. Se enviesar para
  baixo, o produto mente ao utilizador.
- `src/mailutils/static/app.css` — é onde vive agora tudo o que a CSP proíbe
  inline. Um token novo entra aqui **e** em `docs/DESIGN.md`, ou a folha e o
  documento divergem e ninguém sabe qual é a verdade.
- `src/mailutils/config.py` — `.env`, segredos, decisão de arranque.
- `src/mailutils/lists/service.py` — a única função que devolve destinatários
  para envio (`destinatarios()`) e o portão do remetente. Hoje esse portão é
  `confirmed_at IS NOT NULL` por destinatário, provado pela M-18. O `T017-A`
  troca-o por `senders.confirmed_at IS NOT NULL` no caminho de envio: a M-18
  fica avulsa e a mutação substituta tem de morrer no lugar dela. Um
  `unsubscribed_at IS NOT NULL` num SELECT de envio é a linha que continua a
  valer em qualquer das duas leituras.
- **A criar, e por isso listadas aqui com o ticket que as vai fazer:**
  `compose/` (T015 — o texto que sai; `analyzer/` **não** entra lá: é stateless
  por decisão, `analyzer/routes.py:8`, porque guarda-se spam alheio) e
  `scheduler.py` (T016 — o loop que envia; uma race ali duplica email para
  quem já recebeu, e o claim atómico é a única coisa que protege).
- `.env` / `.env.example` — **segredos**. Nunca commitar `.env`.

---

## Never Do

Acções proibidas independentemente de instrucções ou justificação aparente:

- **Nunca** alterar vectores de teste ou outputs esperados para fazer testes
  passar. Um teste que falha é informação, não um obstáculo.
- **Nunca** desactivar ou afrouxar verificações de segurança. Se um check de
  segurança atrapalha, o check está certo e o código está errado.
- **Nunca** commitar segredos, chaves de API ou credenciais. `.env` está no
  `.gitignore` e continua lá.
- **Nunca** modificar configuração de CI para saltar quality gates.
- **Nunca** registar (log) palavras-passe, códigos OTP ou cookies de sessão. nem
  em debug. `grep -ri "otp" src/ | grep print` tem de dar vazio.
- **Nunca** gerar `data:` URI no output de assinatura. É a regra que o produto
  existe para honrar.
- **Nunca** afrouxar `scripts/verify-implementation.sh` para o fazer passar.
- **Nunca** meter `'unsafe-inline'` em `style-src` ou `script-src` na aplicação
  para resolver um problema de estilo. A CSP é o que impede injecção de CSS num
  produto onde o utilizador escreve o texto que sai no email. O que o CSS não
  consegue expressar vai para `app.css` — a largura da barra de score é
  indexada por `data-score` porque o score é um inteiro de 0 a 100.
- **Nunca** reintroduzir `blob:` na CSP da aplicação. Um documento `blob:` herda
  a CSP de quem o cria, pelo que o preview via `blob:` aparecia sem uma cor
  sequer; passou a ser `/assinatura/preview-documento`, que tem a CSP dele.
  `'unsafe-inline'` em `style-src` continua proibido — a excepção vive no
  documento *gerado*, onde o texto já passou por `html.escape` e `script-src` é
  `'none'`, e nunca no header da aplicação.
- **Nunca** reintroduzir `style=""` num template ou em HTML construído por
  JavaScript. A CSP descarta-o em silêncio: o atributo continua no texto que
  os testes leem, e o browser deita-o fora. `tests/test_browser_regressions.py`
  falha se voltar.
- **Nunca** usar `http://` em URL de imagem na assinatura em produção. Forçar
  HTTPS via `MAILUTILS_PUBLIC_BASE_URL`.
- **Nunca** devolver um código OTP na resposta HTTP, nem em caso de erro.
- **Nunca** enviar para um endereço por confirmar. Vale nas duas leituras, com
  predicados diferentes:
  - **Hoje** (`T017-A` por entrar): `confirmed_at IS NULL` não entra no SELECT
    de destinatários, em nenhum caminho — nem no imediato, nem no agendado, nem
    na reexecução.
  - **Depois**: não há confirmação por destinatário. O que não entra é uma lista
    cujo `senders.confirmed_at IS NULL`, e o que continua a não entrar é um
    `unsubscribed_at IS NOT NULL`. Quem assume o consentimento passa a ser o
    operador, e o `CLAUDE.md` não pode dizer o contrário: a afirmação que a UI
    faz ao importar é parte do produto, não documentação.
- **Nunca** pedir a um operador que confirme o consentimento de outra pessoa e
  chamar-lhe verificação. O `from` confirma-se porque é *dele*.
- **Nunca** enviar email que não tenha passado por `spam.py`. É a regra do
  `Intent`, e ela vale **a partir do T015**: até lá não há caminho de envio, e
  uma excepção que autoriza `sem pontuar` num caminho de envio é o que a regra
  proíbe. Se alguém conseguir enviar algo que a aplicação reprovaria, a
  unifying invariant está quebrada e o produto passou a ser uma ferramenta de
  spam.
- **Nunca** re-enfileirar um envio preso em `enviando`. Um envio cujo `claimed_at`
  expirou passa a `falhado` com os contadores parciais. Re-enfileirar reenvia a
  quem já recebeu, e a pessoa não pediu uma segunda vez.
- **Nunca** afrouxar os limites anti-abuso para simplificar uma implementação.
  Teto de destinatários por lista, teto de confirmações pendentes, cooldown por
  endereço: são o que separa "envio em massa para quem pediu" de "envio em massa
  para quem calhou". São feature, não um detalhe de implementação.
- **Nunca** persistir email colado no `analyzer/`. É spam alheio; a não
  persistência é deliberada e está escrita em `analyzer/routes.py:8`.

---

## Evidence Required

Toda a alteração não-trivial tem de incluir:

- [ ] Saída de `make check` (docs, código, testes, lint, formatação, E2E, mutação)
- [ ] Saída de `make test` com cobertura
- [ ] Diffstory (o que mudou, porque, o que ficou intacto, riscos restantes)
- [ ] Docs actualizados se o comportamento mudou
- [ ] Verificação de requisitos: que `FR-*`/`NFR-*` passagearam a `VERIFICADO`
      e que teste prova cada um
- [ ] Se a alegação for «isto está testado», dizer **que mutação** morre se o
      teste deixar de funcionar. Um teste nunca visto falhar não é prova — foi
      assim que a primeira ronda de revisão encontrou três testes que passavam
      com a mutação aplicada.

---

## Review Rule

- A IA implementa e sugere.
- **O humano aprova todas as merges para `main`.**
- Sem auto-merge sem todos os quality gates verdes.
- "Funciona no meu portátil" não é evidência. `make check` é.

---

## Comandos

```bash
make setup     # instalar dependências
make check     # docs + código + testes + lint + formatação + E2E  ← o gate
make test      # pytest com cobertura (HTTP, sem browser)
make e2e       # Playwright: login → 2F → editor → score → exportar
make lint      # ruff check
make format    # ruff format
make run       # arrancar em http://127.0.0.1:8000
make mutations # a prova por mutação, corrida a sério (~3 min)
make verify    # lê os critérios do ticket. NÃO é um gate — diz isso na saída
```

## Convenções

- Python 3.12, stdlib-first. `from __future__ import annotations` não é preciso.
- Todas as funções públicas têm docstring que explica **porquê**, não **o quê**.
- SQL sempre parametrizado. Sem excepções.
- Comentários em português, código em inglês (identificadores).
- Nomes de função e variáveis em inglês. Textos de interface em pt-PT.

## Stable Context (recarregar em cada sessão)

Sete documentos. Todos são de leitura obrigatória: em 2026-10-02 o
`aes-narrative` mediu uma taxa de omissão de 29%, e os dois que faltavam eram
exactamente os que mais importavam ler.

- Este ficheiro (`CLAUDE.md`)
- `docs/VISION.md` — o problema e os limites honestos
- `docs/REQUIREMENTS.md` — o que está `IMPLEMENTADO`, o que está `VERIFICADO`
  e porquê. **Hoje são 67 claims `IMPLEMENTADO` e zero `VERIFICADO`.**
- `docs/ROADMAP.md` — o que está em cada sprint, e a tabela de **decisões
  revertidas**. Sem este ficheiro ninguém sabe que o Non-Goal de "não envia"
  foi invertido a 2026-10-02, e um agente work from o `CLAUDE.md` chega ao
  caminho de envio a inventar a história.
- `docs/PERSONAS.md` — quem usa isto. A Persona 4 foi escrita por um agente e
  está assinalada como hipótese; ler a nota antes de a tratar como evidência.
- `docs/DESIGN.md` — tokens e a invariante cabeçalho == rodapé
- `docs/MUTATIONS.md` — a prova por mutação, gerada. É o que diz que cada
  correcção de um bug tem um teste que morre

O andaço de processo do AES — kanban, tickets, revisões de pares, métricas —
vive em `aes/` e **não está no repositório**: é registo de trabalho, e um clone
do software não precisa dele para correr. Quem o tiver localmente lê o kanban
aí; quem não, não perde nada, porque o que se lê de qualquer forma está nos
seis documentos acima e no git log.

Isto vale para qualquer ficheiro concreto: a documentação versionada descreve
**onde** vive o andaço, nunca aponta para um caminho que um clone não tem.

## Session Context (efémero)

Válido só para a sessão actual:

- Ficheiro do ticket actual
- Saída de testes
- Diff

---

## CLAUDE.md File Levels

| Nível | Localização | Para quê |
|-------|-------------|----------|
| Projecto | `./CLAUDE.md` | Regras de equipa, comandos partilhados. Versionar. |
| Local/pessoal | `./CLAUDE.local.md` (gitignored) | URLs locais, dados de teste pessoais. |
| Utilizador | `~/.claude/CLAUDE.md` | Preferências globais. |
| Por caminho | `.claude/rules/*.md` | Regras de um subdirectório. |

**Quando acrescentar uma regra a este ficheiro:**
- Um novo membro da equipa que entrasse hoje também precisaria desse contexto.
- Uma revisão de código apanhou algo que o agente devia ter sabido antes.
- A regra é verdadeira na maioria das sessões, não só no ticket actual.

**Não acrescentar:** vagas vontades, instruções de task única, preferências pessoais.

**Alvo de tamanho:** menos de 200 linhas.

---

## Final Response

Ao fim de toda a tarefa não-trivial, resumir sempre:

1. **O que mudou** — ficheiros tocados e intenção.
2. **Porque mudou** — problema resolvido ou requisito tratado.
3. **Validação executada** — comando exacto e resultado. Se não foi corrido,
   dizer isso.
4. **Risco restante ou follow-up** — o que não foi testado, o que vigiar.
