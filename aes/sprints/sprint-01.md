---
sprint: sprint-01
period: 2026-09-29 → 2026-09-29
status: done
---

# Sprint 01 — Caminho completo utilizável

Objectivo: alguém entra na aplicação, carrega o logótipo, escreve os campos,
vê o score de spam explicado, e exporta HTML pronto a colar no Thunderbird.
Com gestão de utilizadores por convite e segundo factor por email.

## Tickets

| ID | Título | Estado |
|----|--------|--------|
| T001 | Gerador de assinaturas sem padrões de spam, com 2F por email | done |

## Entregas

- Arranque recusado em produção sem segredo forte ou sem https.
- Primeiro utilizador a partir do `.env`, idempotente.
- Login com 2F só em dispositivos novos; código de 6 dígitos por email.
- Convites pelo admin; o utilizador define a própria palavra-passe.
- Editor com logótipo validado por magic bytes.
- Score de spam 0–100 com regras nomeadas e remediação em pt-PT.
- Exportação HTML/TXT, bloqueada em score crítico.
- Tema claro/escuro com cabeçalho e rodapé do mesmo fundo.
- 310 testes, `ruff` limpo, zero dependências novas.

## Retrospective

### O que correu bem

- **Escrever os testes antes de achar que tudo estava certo pagou.** Nove bugs
  reais apareceram assim, incluindo três que teriam sido sérios:
  o formulário de login estava **sem token CSRF** (o `request.state.session`
  nunca era posto, portanto o campo não existia), o `maxmem` do scrypt estava
  errado ao ponto de nenhuma palavra-passe poder ser criada, e `db.connect`
  não fazia o que o próprio docstring prometia.
- **A invariante cabeçalho == rodapé tem teste próprio.** É a única regra do
  `DESIGN.md` que é uma igualdade entre dois elementos, e igualdades não se
  veem numa revisão visual.
- **A incompatibilidade `starlette`/`httpx` ficou registada em vez de
  "resolvida"** a rebaixar o `httpx` do sistema. O cliente ASGI próprio
  (80 linhas) fica no repositório e não altera nada fora do projecto.

### O que correu mal

- **Três dependências que o plano assumia não estavam instaladas**
  (`sqlalchemy`, `passlib`, `pillow`). Os testes de ambiente revelaram-no no
  início, o que forçou a decidir a stack antes de escrever código — mas
  devia ter sido um `pip list` antes do plano, não durante a implementação.
- **O `pdftools` de referência é privado.** Não foi possível auditar. Está
  declarado como divergência em `docs/DESIGN.md`, e o risco é real: o visual
  final pode não ser o esperado. **É a maior incógnita que fica.**
- **O teste de tema teve o parser CSS partido três vezes** (comentários com
  `{`, selector `:root,` agrupado, regra partilhada vs individual). Cada uma
  das versões "verificava" alguma coisa errada em silêncio — que é pior do que
  um teste partido.

### O que mudar no próximo sprint

- Quando o objectivo de um teste é "esta coisa não acontece", **verificar
  primeiro que o teste consegue detectá-la**. Três dos testes de tema davam
  verde sobre código errado. Um teste que não falha quando injectado é um
  teste que não testa.
- **Listar as dependências disponíveis antes do plano**, não durante.
- **Escrever a divergência de referência no `DESIGN.md` no momento em que se
  descobre** que o repositório de referência é inacessível, não no fim.
