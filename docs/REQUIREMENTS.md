# REQUIREMENTS — mailutils

Convenção: `FR-*` funcional, `NFR-*` não funcional, `CON-*` restrição.
Estado: `DRAFT` | `IMPLEMENTADO` | `VERIFICADO`. Só `VERIFICADO` tem teste.

## Functional

### FR-1 Autenticação e gestão de utilizadores

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-1.1 | O primeiro utilizador (admin) é criado a partir de `MAILUTILS_ADMIN_EMAIL` + `MAILUTILS_ADMIN_PASSWORD` no `.env`, de forma **idempotente** no arranque. | Must | IMPLEMENTADO |
| FR-1.2 | O admin cria convites. Um convite tem email, expiração (horas) e estado (`pending`/`accepted`/`revoked`). | Must | IMPLEMENTADO |
| FR-1.3 | Um utilizador aceite o convite, **tem de definir a sua própria palavra-passe**. O admin nunca vê a palavra-passe do utilizador depois da criação. | Must | IMPLEMENTADO |
| FR-1.4 | O admin pode listar utilizadores, revogar convites e activar/desactivar contas. Não existe botão de "reenviar": criar um convite novo para um email que já tem um pendente **substitui-o** (o antigo é revogado), de modo que há no máximo um token vivo por email. | Must | IMPLEMENTADO |
| FR-1.5 | Word-passe mínimo 12 caracteres. Email tem de ser único e sintacticamente válido. | Must | IMPLEMENTADO |
| FR-1.6 | Palavras-passe nunca são guardadas em claro, logadas, nem devolvidas em qualquer resposta da API. | Must | IMPLEMENTADO |
| FR-1.7 | Todas as acções de escrita exigem sessão válida + CSRF. | Must | IMPLEMENTADO |

### FR-2 Segundo factor por email em dispositivos novos

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-2.1 | A palavra-passe correcta **num dispositivo conhecido** faz login imediato. | Must | IMPLEMENTADO |
| FR-2.2 | A palavra-passe correcta **num dispositivo novo** **não** faz login: envia um código de 6 dígitos por email e apresenta o formulário de verificação. | Must | IMPLEMENTADO |
| FR-2.3 | O código expira em 10 minutos e é de uso único (invalidado no primeiro uso, mesmo falhado). | Must | IMPLEMENTADO |
| FR-2.4 | Máx. 5 tentativas por código; depois é preciso pedir um novo. | Must | IMPLEMENTADO |
| FR-2.5 | Cooldown de 60 s entre pedidos de código para o mesmo email. | Must | IMPLEMENTADO |
| FR-2.6 | O dispositivo é identificado por hash do User-Agent + `Accept-Language`. Há no máximo 20 dispositivos por utilizador; o mais antigo é descartado. | Should | IMPLEMENTADO |
| FR-2.7 | O utilizador pode listar e revogar os seus dispositivos. | Should | IMPLEMENTADO |
| FR-2.8 | O código de email **nunca** é registado em log, e o email não é reflectido na resposta HTTP além do necessário. | Must | IMPLEMENTADO |
| FR-2.9 | `/health` **não** revela se um email existe (resposta constante). | Must | IMPLEMENTADO |

### FR-3 Gerador de assinaturas

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-3.1 | O utilizador carrega uma imagem de logótipo (PNG/JPEG/GIF/WebP) via upload. | Must | IMPLEMENTADO |
| FR-3.2 | O ficheiro é validado por **magic bytes**, não por extensão nem por `Content-Type` do cliente. | Must | IMPLEMENTADO |
| FR-3.3 | Tamanho máximo 2 MiB; acima disso, rejeição com mensagem explícita. | Must | IMPLEMENTADO |
| FR-3.4 | A imagem é validada estruturalmente (assinatura + tipo de conteúdo) e as suas dimensões são lidas. Se exceder `MAILUTILS_LOGO_MAX_PX`, a imagem **é aceite mas o utilizador é avisado** de que não foi redimensionada. O redimensionamento automático está fora do alcance: exige Pillow, que não está instalado e que este projecto não instala. | Must | IMPLEMENTADO |
| FR-3.5 | A imagem é servida em `/media/{id}.{ext}` com `Content-Type` correcto, `X-Content-Type-Options: nosniff` e `Cache-Control` longo. | Must | IMPLEMENTADO |
| FR-3.6 | Campos do formulário: nome, cargo, empresa, telefone, email,Morada, website, e uma lista de ligações (rótulo + URL). | Must | IMPLEMENTADO |
| FR-3.7 | Campo opcional "disclaimer/nota" em texto simples, com suporte a quebras de linha. | Should | IMPLEMENTADO |
| FR-3.8 | Preview mostra o **HTML exacto** que será copiado (não uma aproximação). | Must | IMPLEMENTADO |
| FR-3.9 | Exportação: (a) copiar para a área de transferência, (b) descarregar `.html`, (c) descarregar `.txt` (texto simples equivalente). | Must | IMPLEMENTADO |
| FR-3.10 | Instruções de configuração por cliente (Thunderbird, Outlook, Apple Mail) geradas a partir do output. | Should | IMPLEMENTADO |
| FR-3.11 | A assinatura é guardada por utilizador e recarregada ao voltar. | Must | IMPLEMENTADO |
| FR-3.12 | Temas: claro, escuro, e a paleta derivada da aplicação. Escolha por omissão = escuro. | Should | IMPLEMENTADO |

### FR-4 Compatibilidade com filtros de spam

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-4.1 | O HTML gerado é **table-based** com estilos **inline**. Sem `<style>`, `<script>`, `<iframe>`, `<form>`, `<object>`, `<embed>`, sem CSS externo. | Must | IMPLEMENTADO |
| FR-4.2 | A imagem é referenciada por `https://{PUBLIC_BASE_URL}/media/...` — **nunca** por `data:` URI. | Must | IMPLEMENTADO |
| FR-4.3 | Sem tracking pixels, sem imagens de 1×1, sem `display:none` com texto. | Must | IMPLEMENTADO |
| FR-4.4 | Limite de 6 ligações visíveis na assinatura; o excedente é truncado com aviso. | Should | IMPLEMENTADO |
| FR-4.5 | O bloco termina com `<!-- mailutils-signature -->` e a versão em texto simples termina com `-- `, para reduzir falsos positivos de Bayes. | Should | IMPLEMENTADO |
| FR-4.6 | Score de risco 0–100 calculado sobre o HTML final, com **regras nomeadas e explicadas** em pt-PT. | Must | IMPLEMENTADO |
| FR-4.7 | Categorias: `SEGURO` (0–19), `ATENÇÃO` (20–44), `ELEVADO` (45–69), `CRÍTICO` (≥70). | Must | IMPLEMENTADO |
| FR-4.8 | O score **nunca** é apresentado como garantia de entrega. A UI diz explicitamente que é heurístico. | Must | IMPLEMENTADO |
| FR-4.9 | As versões `.html` e `.txt` são validadas na exportação. A exportação é bloqueada se o score for `CRÍTICO` **ou** se *qualquer uma* das regras for de gravidade `crítica`. O score mede risco agregado; o bloqueio é política sobre o pior sinal individual. Decidir só pelo total dava ao utilizador forma de contornar o bloqueio com mais texto. | Must | IMPLEMENTADO |

### FR-5 Interface

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-5.1 | **Dark mode: o fundo do cabeçalho e o do rodapé são idênticos** (mesmo token, sem gradiente). Requisito explícito do dono do projecto. | Must | IMPLEMENTADO |
| FR-5.2 | Paleta derivada do LINUXKAFÉ (`#F8B400` primário, `#2d2d2d` superfície escura, `#212121` texto, `#0056b3` ligação). **Sem qualquer referência à Universidade do Porto.** | Must | IMPLEMENTADO |
| FR-5.3 | Alternância claro/escuro com persistência em cookie, sem flash de tema incorrecto no primeiro paint. | Must | IMPLEMENTADO |
| FR-5.4 | Interface em português (pt-PT). | Must | IMPLEMENTADO |
| FR-5.5 | Layout responde a 375 px, 768 px, 1440 px. | Should | IMPLEMENTADO |
| FR-5.6 | Navegação por teclado e `aria-label` nos controlos interactivos. | Should | IMPLEMENTADO |

## Non-Functional

| ID | Categoria | Requisito | Estado |
|----|-----------|-----------|--------|
| NFR-1 | Segurança | Word-passe com `hashlib.scrypt` (N=2^15, r=8, p=1, 32 bytes de salt, comparação com `hmac.compare_digest`). | IMPLEMENTADO |
| NFR-2 | Segurança | Sessão em cookie `HttpOnly` + `SameSite=Lax`, `Secure` quando `MAILUTILS_HTTPS=1`. | IMPLEMENTADO |
| NFR-3 | Segurança | CSRF por token de sessão em todos os `POST`. | IMPLEMENTADO |
| NFR-4 | Segurança | Rate limit de login: 5 tentativas / 15 min por IP+email. | IMPLEMENTADO |
| NFR-5 | Segurança | `SECRET_KEY` tem de ser definida em produção; a app **recusa arrancar** sem ela se `MAILUTILS_ENV=production`. | IMPLEMENTADO |
| NFR-6 | Segurança | Toda a SQL é parametrizada (`?`). Zero concatenação de input em SQL. | IMPLEMENTADO |
| NFR-7 | Segurança | Logótipos servidos de directório fora do web root estático, com nome de ficheiro gerado (nunca o nome do cliente). | IMPLEMENTADO |
| NFR-8 | Privacidade | Zero telemetria, zero pedidos a terceiros. O SMTP configurado é o único destino externo. | IMPLEMENTADO |
| NFR-9 | Performance | Resposta de página < 200 ms em condição local com SQLite. Upload até 2 MiB processado em < 500 ms. | IMPLEMENTADO |
| NFR-10 | Maintainability | Zero dependências novas em produção: apenas stdlib + FastAPI/Jinja2/uvicorn/itsdangerous já presentes. A única excepção é `playwright`, declarada só em `[project.optional-dependencies].dev` e usada pelo suite E2E (T008); não entra em `dependencies` e não vai para produção. | IMPLEMENTADO |
| NFR-11 | Maintainability | Cobertura ≥ 80% em `make check`. | IMPLEMENTADO |
| NFR-12 | Maintainability | `ruff check` limpo. Sem `TODO:` em `src/`. | IMPLEMENTADO |
| NFR-13 | Acessibilidade | As cores funcionais usadas **como texto** (`--ok-text`, `--warn-text`, `--high-text`, `--bad-text`, `--info-text`) passam 4.5:1 sobre `--bg`, `--bg-alt` e `--surface` nos dois temas. As variantes de preenchimento (`--*-fill`, barras e bordas) estão isentas. O contraste do texto corrente e o contraste *não* textual não estão verificados. | IMPLEMENTADO |
| NFR-14 | Internacionalização | Apenas pt-PT. Todas as mensagens de interface vivem em `templates.MESSAGENS`, indexadas por chave estável — é o que permite traduzir sem caçar strings em templates. | IMPLEMENTADO |
| NFR-15 | Maintainability | O caminho login → segundo factor → editor → score → exportação é verificado num browser real, com clique e formulário, e corre dentro de `make check`. Um E2E que passa sem browser conta como falhado, não como ignorado. | IMPLEMENTADO |

## Constraints

- **Language:** Python 3.12
- **Framework:** FastAPI + Jinja2 (já instalados; Flask não está disponível)
- **Persistência:** SQLite via `sqlite3` da stdlib (SQLAlchemy não está disponível)
- **Deployment:** self-hosted, `uvicorn`, atrás de reverse proxy TLS
- **Dependencies novas:** 0
- **Fora do âmbito (ver CLAUDE.md):** multi-tenancy, SMTP próprio, TOTP, faturação, apps móveis

## Requisitos que NÃO foram implementados

Registrados aqui para que ninguém os descubra a olhar para o código e
assuma que estão lá:

| Requisito considerado | Porquê não |
|---------------------|-----------|
| Redimensionar o logótipo para 300×300 px | Exige Pillow. Não está instalado e o projecto não ganha dependências. Mitigação: a dimensão é lida, o utilizador é avisado, e o score penaliza HTML acima de 30 KiB. |
| Suporte a `.svg` | Um SVG pode conter `<script>`. Assumir o risco por conveniência seria exactamente o que o produto existe para evitar. Mitigação: mensagem que aponta para PNG. |
| TOTP como segundo factor | Requisito do dono é email. Ver ROADMAP T007. |
| Exportação para Gmail / Outlook como HTML | Ver ROADMAP T005. |
| Testes E2E com browser | Ver ROADMAP T008. Os fluxos são testados por HTTP, mas não há clique real. |
| Contraste do texto corrente (`--text-soft`) e de elementos não textuais | Verificado por inspecção, não por teste. O texto corrente passa (`#555555` sobre `#f5f5f5` dá 6.8:1); a dívida está registada no kanban. |

## Regra de aceitação

Um requisito só passa a `VERIFICADO` quando existe um teste automatizado que falha
sem a implementação. `scripts/verify-implementation.sh` faz essa verificação.
