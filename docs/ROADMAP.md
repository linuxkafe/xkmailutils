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
POST sem mostrar erro. Ver `aes/tickets/T008-playwright-e2e.md`.

### T011 — Logótipo para ecrãs escuros
`prefers-color-scheme: dark` no email não é fiável. A maioria dos clientes
força modo escuro ou modo claro. Duas variantes (claro/escuro) teria de ser
escolhida na exportação. Risco: o cliente não honra a variante.

## Fora do escopo (decidido, não é backlog)

- Multi-tenancy / contas de organização
- Facturação e planos
- Aplicação móvel
- Envio de newsletters (isto é um gerador de assinaturas, não um serviço de email)
- Integração com Google Workspace / Microsoft 365 por API

## Regra

Um item só entra em `planeado` depois de existir uma Persona a precisar dele
(docs/PERSONAS.md) ou um requisito em docs/REQUIREMENTS.md. Não há itens por
receita.
