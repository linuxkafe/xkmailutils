---
ticket: T001
title: Gerador de assinaturas sem padrões de spam, com 2F por email
sprint: sprint-01
priority: high
status: done
created: 2026-09-29
---

# T001 — Gerador de assinaturas sem padrões de spam, com 2F por email

## Context

O projecto nasce do zero. O dono pediu uma aplicação self-hosted que gere
assinaturas de email HTML compatíveis com filtros de spam, com gestão de
utilizadores e segundo factor por email em dispositivos novos. O primeiro
utilizador vem do `.env`.

Duas decisões foram pedidas ao dono antes de escrever código, porque o custo de
errar era alto e não era inferível do repositório:

1. **Logótipo**: servido por URL pela aplicação, não embebido em base64.
   Escolhido: upload servido pela app.
2. **Utilizadores**: convites pelo administrador, não signup público.
   Escolhido: convite.

## Acceptance Criteria

- [x] AC-1: O primeiro utilizador é criado a partir de `MAILUTILS_ADMIN_EMAIL` e `MAILUTILS_ADMIN_PASSWORD` no `.env`, de forma idempotente, e sem sobrescrever uma palavra-passe já alterada depois.
- [x] AC-2: `MAILUTILS_ENV=production` faz a aplicação recusar arrancar sem `MAILUTILS_SECRET_KEY` de 32+ caracteres, e sem `MAILUTILS_PUBLIC_BASE_URL` em https.
- [x] AC-3: Palavras-passe nunca são guardadas em claro, logadas, nem devolvidas em qualquer resposta HTTP.
- [x] AC-4: Login com palavra-passe correcta num dispositivo **conhecido** dá sessão directa.
- [x] AC-5: Login com palavra-passe correcta num dispositivo **novo** envia um código de 6 dígitos por email e não dá sessão.
- [x] AC-6: O código expira em 10 minutos, é de uso único, tem no máximo 5 tentativas e cooldown de 60 s por email.
- [x] AC-7: O código OTP nunca aparece no corpo de nenhuma resposta HTTP nem em log.
- [x] AC-8: Um utilizador que aceite um convite define a sua própria palavra-passe; o admin nunca a vê.
- [x] AC-9: Desactivar uma conta corta as sessões em curso, não apenas o login seguinte.
- [x] AC-10: O upload de logótipo é validado por magic bytes; SVG, ficheiros despistados e ficheiros acima de 2 MiB são recusados com mensagem em pt-PT.
- [x] AC-11: O nome do ficheiro gravado é gerado pela aplicação, nunca o enviado pelo cliente.
- [x] AC-12: `/media` recusa travessia de directório e só serve `logo-<int>.<ext>` com `nosniff`.
- [x] AC-13: O HTML da assinatura é table-based com estilos inline, e não contém `<style>`, `<script>`, `<iframe>`, `<form>`, `display:none` nem `data:` URI.
- [x] AC-14: `javascript:`, `data:`, `vbscript:` e `file:` são descartados das ligações escritas pelo utilizador.
- [x] AC-15: A imagem do logótipo entra no email por URL https absoluta, nunca em base64.
- [x] AC-16: O score de spam (0–100) é calculado sobre o HTML final, com regras nomeadas e explicadas em pt-PT, e o score limpo é ≤ 10.
- [x] AC-17: A exportação é bloqueada com score CRÍTICO **ou** com qualquer regra de gravidade crítica, e a razão é explicada ao utilizador.
- [x] AC-18: O preview e a exportação produzem o mesmo HTML, byte a byte.
- [x] AC-19: Em darkmode, o fundo do cabeçalho e o do rodapé são o mesmo token `--chrome`, sem gradiente nem regra própria.
- [x] AC-20: As cores funcionais usadas como texto passam 4.5:1 sobre o fundo dos dois temas.
- [x] AC-21: A interface é pt-PT e não contém qualquer menção institucional.
- [x] AC-22: Zero dependências novas; `ruff check` limpo; `make check` verde; cobertura ≥ 80%.

## Scope

**In scope**
- App web FastAPI + Jinja2, SQLite via stdlib.
- Auth com 2F por email em dispositivos novos; sessões; CSRF; limitação de tentativas.
- Convites, lista de utilizadores, activação/desactivação.
- Editor de assinaturas com logótipo, score explicável, preview e exportação.
- Tema claro/escuro, paleta LINUXKAFÉ, invariante cabeçalho == rodapé.
- Documentação completa e suite de testes.

**Out of scope (decidido, ver docs/ROADMAP.md)**
- Multi-tenancy, facturação, TOTP, apps móveis, integração com Google Workspace / Microsoft 365.
- Envio de newsletters. Isto gera assinaturas; não envia email.
- Redimensionamento de imagens com Pillow — não está instalado e o projecto
  não ganha dependências. A validação estrutural corre sempre; a ausência do
  redimensionamento é **dita ao utilizador**, nunca escondida.

## Dependencies

- Nenhuma externa. `fastapi`, `uvicorn`, `jinja2`, `itsdangerous`,
  `python-multipart` já estavam instaladas. `sqlalchemy`, `passlib`, `pillow`
  não estão e não foram instaladas.
- SMTP configurável. Em desenvolvimento, `MAILUTILS_MAIL_BACKEND=console`.

## Rollback

`git revert` do commit. A base de dados é um ficheiro SQLite em `var/`;
apagar `var/` repõe o estado. A migração é idempotente e só acrescenta.

## Known Risks

- **O score de spam é uma heurística**, não o algoritmo do Gmail. A UI diz isso
  em todo o lado onde o número aparece. Risco: um utilizador lê o score como
  garantia e manda um email com score 8 que vai parar ao spam na mesma.
  Mitigação: o texto de aviso é obrigatório e tem teste.
- **O logótipo não é redimensionado** (sem Pillow). Uma imagem de 3000×3000
  resulta num ficheiro `.html` grande, que alguns clientes truncam. O
  utilizador é avisado no upload e o score penaliza acima de 30 KiB.
- **A impressão digital de dispositivo é User-Agent + Accept-Language.** Um
  atacante que knows o User-Agent da vítima consegue saltá-la. O segundo factor
  protege o acesso; não protege contra um atacante que já tem as duas coisas.
  Aceito porque o requisito é "segundo factor em dispositivos novos", não
  "autenticação resistente a phishing".
- **`rodolfomatos/pdftools` é privado e não foi auditado.** O layout segue a
  convenção AES, não uma reprodução verificada. Está declarado em
  `docs/DESIGN.md`.
- **O backend de email em desenvolvimento escreve o OTP no stdout.** Por isso
  `MAILUTILS_MAIL_BACKEND=console` nunca pode ser usado em produção; está
  documentado em `.env.example`.

## Notes

Bugs reais encontrados pelos testes durante a implementação, e corrigidos:

1. `hashlib.scrypt` com `maxmem` mal calculado — nenhuma palavra-passe podia ser
   criada. O cálculo correcto é `128 * n * r`.
2. `security.is_expired(None)` levantava `AttributeError` em vez de falhar
   fechado. Um timestamp ilegível tem de contar como expirado.
3. `request.state.session` nunca era posto: a navegação e o token CSRF do
   formulário não apareciam em lado nenhum. O formulário de login estava
   **sem protecção CSRF** porque o campo não existia.
4. O exportador imprimia o slug percent-encoded (`?erro=invalidos`) em vez da
   mensagem em pt-PT. Agora as mensagens vivem em `templates.MESSAGENS`.
5. `db.connect` não fazia o que o próprio docstring prometia
   (`check_same_thread`). Com handlers síncronos do FastAPI num thread pool,
   isto rebentava em produção e não só em teste.
6. Guardar o formulário apagava o logótipo que o upload tinha associado
   (upsert com `logo_id` vazio), e carregar o logótipo antes de existir uma
   assinatura deixava-o órfão.
7. A regra de tracking pixel exigia `1x1` **e** uma palavra-chave, e deixava
   passar `/beacon.gif` — o caso mais comum.
8. O CSS usava `#22c55e` da marca como cor de texto, com contraste 2.28:1 em
   light mode. O próprio `docs/DESIGN.md` proibia isso. Agora há dois papéis:
   `--*-fill` para barras e `--*-text` para texto.
9. Um único sinal crítico (tracking pixel, 40 pontos) ficava abaixo do limiar
   de bloqueio de exportação. O bloqueio passou a ser política sobre o pior
   sinal individual, não só sobre o total.
