# ROADMAP

## Backlog

| ID | Item | Impacto | Esforço | Prioridade | Estado |
|----|------|---------|---------|-----------|--------|
| — | Scaffolding AES | alto | baixo | now | feito |
| T001 | Auth + 2FA por email em dispositivos novos + bootstrap `.env` | alto | alto | P0 | feito |
| T002 | Gerador de assinaturas com score de spam | alto | alto | P0 | feito |
| T003 | Tema escuro com cabeçalho == rodapé | médio | baixo | P0 | feito |
| T004 | Gestão de utilizadores: convites, listagem, revogação | alto | médio | P1 | feito |
| T005 | Exportar assinatura para Gmail/Outlook em HTML (não só Thunderbird) | médio | médio | P2 | planeado |
| T006 | Múltiplas assinaturas por utilizador com predefinições | médio | médio | P2 | planeado |
| T007 | TOTP (app autenticadora) como alternativa ao email | médio | médio | P2 | planeado |
| T008 | Testes E2E com Playwright sobre o fluxo login → assinatura | alto | médio | P1 | feito |
| T009 | Internacionalização (en) dos templates | baixo | médio | P3 | planeado |
| T010 | Relatório de telemetria local (histórico de scores) | baixo | baixo | P3 | planeado |
| T011 | Múltiplos logótipos / logótipo responsivo para ecrãs escuros | baixo | médio | P3 | planeado |
| T012 | Anti-replay de OTP por IP além do cooldown por email | médio | baixo | P3 | planeado |
| T013 | Temas de assinatura adicionais (paletas) **+ 3 variantes de layout** | médio | médio | P1 | em curso |
| T014 | Listas de destinatários com confirmação por OTP e importação CSV | alto | alto | P1 | feito |
| T015 | Compositor de email com score + envio | alto | alto | P1 | em curso |
| T016 | Agendamento de envios | alto | alto | P2 | planeado |

## Descrições

### T005 — Exportar para Gmail/Outlook em HTML
Gmail e Outlook não aceitam colar HTML. Requer gerar um `.eml` ou instruções de
upload para a definição de assinatura na web. Bloqueado por: nenhuma. Risco:
formato `.eml` muda por cliente.

### T007 — TOTP como alternativa ao email
Email como segundo factor é fraco (o email é o alvo). TOTP é mais forte mas exige
app no telemóvel. **Deve ser offered como opção, não substituir o email**, porque
o requisito do dono do projecto é email.

### T008 — Playwright sobre o fluxo login → assinatura
Feito. E o custo foi muito menor do que o previsto: o suite encontrou seis
bugs de browser em código que os 493 testes por HTTP davam por bom. O mais
grave era o formulário do editor, que **não submeteu nada** — os campos
`type="url"` exigiam esquema e o produto manda escrever o domínio sem
esquema, pelo que `checkValidity()` devolvia `false` e o browser recusava o
POST sem mostrar erro.

Depois passou por uma revisão `aes-peer-review` (multi-perspectiva, 4
personas) que encontrou mais 14: **3 BLOCKER**, entre eles a assinatura por
omissão a ser ilegível no cliente de email (contraste 1.14:1) e a CI não
instalar o browser. Todos corrigidos, cada um com mutação provada. Ver
`aes/tickets/T008-playwright-e2e.md` e `aes/peer-reviews/T008/`.

### T011 — Logótipo para ecrãs escuros
`prefers-color-scheme: dark` no email não é fiável. A maioria dos clientes
força modo escuro ou modo claro. Duas variantes (claro/escuro) teria de ser
escolhida na exportação. Risco: o cliente não honra a variante.

### T013 — Temas e estilos de assinatura

Alarga `FR-3.12` (que só tem claro/escuro) com paletas novas, e acrescenta **3
variantes de layout** porque o pedido foi "estilos diferentes" e isso é estrutura,
não só cor. Toca `renderer.py`, que é *critical file*: o HTML entra em emails de
clientes reais, e cada combinação nova é uma superfície de suporte.

Risco: um layout novo pode ser ilegível no Outlook sem o `bgcolor` que o T008
descobriu. Mitigação: `test_renderer.py` fixa o HTML de cada variante, e a
`_precisa_de_fundo` por luminância protege os temas escuros.

### T014 — Listas de destinatários

Pertence a Persona 4 (Marta). Um `.csv` entra; **ninguém fica confirmado sem
código**. O `confirmed_at IS NULL` não entra no `SELECT` de destinatários em
nenhum caminho — essa é a mutação que tem de morrer.

Risco: um utilizador com sessão usa a confirmação como relay de email bombing.
Mitigação: cooldown por endereço, teto de pendentes por utilizador, teto de
destinatários por lista. São configuração, não constantes (NFR-18).

### T015 — Compositor e envio

`compose/` como **irmão** de `analyzer/`, não extensão. O `analyzer` é stateless
por decisão (`analyzer/routes.py:8`) porque um email colado é quase sempre spam
*recebido*; o compositor é texto do dono. Persistir um não implica persistir o
outro, e a separação está escrita nos dois lados para que um revisor veja logo.

Risco: um caminho de envio que não passe pelo score. Mitigação: o email enviado
é pontuado pelo mesmo `spam.py` e bloqueado pela mesma política de `FR-4.9`
(NFR-19).

### T016 — Agendamento

A peça mais perigosa, porque sem dependências (NFR-10) não há Celery nem
APScheduler. Thread in-process com claim atómico em SQLite
(`BEGIN IMMEDIATE` + `UPDATE ... WHERE state='agendado'`), que é o que torna
seguro correr vários workers. Envios presos em `enviando` passam a `falhado` com
os contadores parciais — **nunca** re-enfileirados.

Risco residual, registado: sem entrega por destinatário, um envio que parte a
meio reporta contadores e não diz *quem* ficou por receber. Aceito como troca
por uma tabela a menos; anotar se a Persona 4 reclamar disso.

## Fora do escopo (decidido, não é backlog)

- Multi-tenancy / contas de organização
- Facturação e planos
- Aplicação móvel
- Segmentação de campanhas, A/B testing de assunto, automações — isto não é uma
  ferramenta de marketing. (Tirado de fora do escopo em 2026-10-02; já não é
  "envio de newsletters" em geral, que passou a estar no backlog acima.)
- Integração com Google Workspace / Microsoft 365 por API
- Envio transaccional disparado por eventos da aplicação

## Decisões reverteridas

Registradas para que ninguém as trate como erro:

| Data | Era | Agora |
|------|-----|-------|
| 2026-10-02 | `ROADMAP.md`: "Envio de newsletters — isto é um gerador de assinaturas, não um serviço de email" (fora do escopo, decidido) | No backlog como T014–T016. Decisão do dono. |
| 2026-10-02 | `CLAUDE.md` Non-Goals: "Não é um cliente de email. Não envia." | Envia, para listas com confirmação por código único. |
| 2026-10-02 | `REQUIREMENTS.md`: "SMTP próprio" fora do âmbito | O SMTP já configurado é o transporte. Sem SMTP próprio, sem fila. |

O que **não** foi revertido: zero dependências novas, e a assinatura continua
sujeita às mesmas regras de score.

## Regra

Um item só entra em `planeado` depois de existir uma Persona a precisar dele
(docs/PERSONAS.md) ou um requisito em docs/REQUIREMENTS.md. Não há itens por
receita.

T013–T016 entram com Persona 4. **Essa persona foi escrita por um agente, não
por entrevista** — ver a nota em `PERSONAS.md`. Se a pessoa real for outra, as
FR-6/7/8 estão dimensionadas para o caso errado, e isso é dívida conhecida, não
um pressuposto confirmado.
