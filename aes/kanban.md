---
project: mailutils
created: 2026-09-29
current_sprint: sprint-01
current_ticket: "T008"
---

# Kanban — mailutils

## Estado

Sprint 01 concluída. O produto tem o primeiro caminho completo utilizável:
entrar, carregar o logótipo, escrever os campos, ver o score de spam, exportar
para o Thunderbird. E esse caminho está agora verificado num browser real — o
T008 encontrou seis bugs que os 493 testes por HTTP davam por bons, um deles
impeditivo: o botão de guardar a assinatura não submeteva nada.

## Como verificar

```bash
make setup
make check          # o gate: docs, código, testes com cobertura, lint
make test
make run            # http://127.0.0.1:8000
```

## Sprints

| Sprint | Objectivo | Estado |
|--------|-----------|--------|
| sprint-01 | Gerador de assinaturas + 2F por email + tema | done |

## Tickets

| ID | Título | Prioridade | Estado |
|----|--------|-----------|--------|
| T001 | Gerador de assinaturas sem padrões de spam, com 2F por email | P0 | done |
| T008 | Playwright sobre o fluxo login → 2F → editor → score → exportar | P1 | review: 2 rondas |

## Backlog

Ver `docs/ROADMAP.md`. Regra: um item só entra depois de existir uma Persona
que o precise ou um requisito em `docs/REQUIREMENTS.md`.

## Dívida conhecida

| Item | Onde | Impacto |
|------|------|---------|
| Logótipo não é redimensionado (sem Pillow) | `images.py` | Imagem grande num `.html` grande. O utilizador é avisado no upload; o score penaliza acima de 30 KiB. |
| Impressão digital de dispositivo é User-Agent + Accept-Language | `security.py` | Saltável por quem conheça o browser da vítima. Aceito: o 2F protege o acesso, não a phishing. |
| `rodolfomatos/pdftools` não auditado (privado) | `docs/DESIGN.md` | O layout segue a convenção AES. Declarado como divergência. |
| Testes E2E não cobrem o caminho de convite nem o de administração | `e2e/` | O suite cobre login, 2F, editor, score, exportação, tema e analisador. A gestão de utilizadores (convites, revogação) continua só por HTTP. |
| O preview recarrega o `iframe` a cada alteração | `app.js:setPreview` | Visível como um piscar leve a 350 ms de distância. Escolha do dono entre rota sem estado (mais simples) e cache no servidor (sem piscar). Registado, não resolvido. |
| A revisão de peer ainda não foi executada por um humano | `aes/peer-reviews/T008/` | O veredicto é REJECT até `human-validation.sh` ser corrido por alguém que não seja o autor do candidato. |
| Contraste da `--text-soft` em light mode por confirmar | `app.css` | `#555555` sobre `#f5f5f5` dá 6.8:1 — passa. Em `--surface-elevated` (`#ffffff`) dá 7.4:1. OK, mas rever se a superfície mudar. |

## Notas de decisões

Duas decisões foram tomadas pelo dono antes da implementação, porque o custo
de errar era alto e não era inferível do repositório:

1. **Logótipo servido pela aplicação**, não embebido em base64. `data:` URI é
   o sinal de spam mais severo numa assinatura.
2. **Convites pelo administrador**, não signup público. Fecha o topo do registo.
3. **A CSP não se afrouxa para resolver um problema de estilo.** O T008
   encontrou 43 atributos `style=` que o browser descartava em silêncio — a
   barra de score renderizava a 100% com score 0. A resposta foi mover tudo
   para `app.css`, com a largura da barra indexada por `data-score`. O
   dono escolheu isto em vez de `'unsafe-inline'`, e a excepção que ficou é
   `frame-src 'self' blob:` para o preview.
