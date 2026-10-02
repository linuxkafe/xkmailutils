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
| FR-5.3 | Alternância claro/escuro por botão no cabeçalho, em todas as páginas, com persistência em cookie e sem flash de tema incorrecto no primeiro paint. | Must | IMPLEMENTADO |
| FR-5.4 | Interface em português (pt-PT). | Must | IMPLEMENTADO |
| FR-5.5 | Layout responde a 375 px, 768 px, 1440 px. | Should | IMPLEMENTADO |
| FR-5.6 | Navegação por teclado e `aria-label` nos controlos interactivos. | Should | IMPLEMENTADO |

### FR-6 Listas de destinatários

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-6.1 | O utilizador cria listas nomeadas. Uma lista pertence a um utilizador e é eliminada com ele. | Must | IMPLEMENTADO |
| FR-6.2 | **Duas leituras, e só uma está implementada.** *Hoje*: um endereço é adicionado sempre por confirmar, recebe um código de uso único, e só entra no `SELECT` de destinatários depois de `confirmed_at IS NOT NULL`. *Depois do `T017-A`*: não há confirmação por destinatário — o operador afirma ter o consentimento de quem importa, e o portão passa a ser o remetente (`FR-6.9`). | Must | IMPLEMENTADO |
| FR-6.3 | O código de confirmação reusa as primitivas de `security.py` (mesmo alfabeto, mesmo `scrypt`, mesmo cooldown) mas **não** a tabela `otp_codes`: essa é `user_id NOT NULL` e ligada a dispositivo. Fica na tabela do que confirma — hoje `list_addresses`, e a partir do `T017-A` em `senders`. | Must | IMPLEMENTADO |
| FR-6.4 | Importação de um `.csv` com colunas de endereço (e nome opcional). Uma linha inválida é contada e listada, não aborta a importação. O ficheiro inteiro inválido **é** erro. | Must | IMPLEMENTADO |
| FR-6.5 | **Inverte-se.** *Hoje*: a importação **não confirma** ninguém, e o utilizador dispara a confirmação. *Depois do `T017-A`*: a importação coloca os endereços activos, sem pedido de confirmação e sem email enviado, e a interface **diz ao operador, no momento da importação, que ele assume o consentimento**. A afirmação é parte do produto: um operador que não sabe que assumiu a responsabilidade não pode ter concordado com ela. | Must | IMPLEMENTADO |
| FR-6.6 | Teto de destinatários por lista (`MAILUTILS_MAX_LIST_SIZE`, por omissão 5000). Ao exceder, recusa com mensagem que diz qual limite. O teto de confirmações pendentes por utilizador (`MAILUTILS_MAX_PENDING_CONFIRMATIONS`) **deixou de existir** com o `T017-A`, porque não há pendentes. | Must | IMPLEMENTADO |
| FR-6.7 | Um endereço pode ser descadenciado pelo próprio destinatário, sem sessão e sem passar pelo utilizador, por um link com token assinado. Um `unsubscribed_at IS NOT NULL` **nunca** entra num `SELECT` de envio, e a reposição não pode ser feita pelo dono da lista. | Must | IMPLEMENTADO |
| FR-6.8 | Todos os emails enviados trazem `List-Unsubscribe` com um endereço `mailto:` e um URL com token assinado, mais `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. | Must | DRAFT |
| FR-6.9 | **Novo.** O `from` de uma lista é um remetente do utilizador, confirmado **uma vez por código** e reutilizável entre listas. Sem `senders.confirmed_at IS NOT NULL`, a lista não entra em nenhum caminho de envio — nem imediato, nem agendado, nem reexecução. | Must | IMPLEMENTADO |
| FR-6.10 | **Novo.** Pedir e confirmar o `from` exige sessão **e** verificação de dono em cada operação. O código nunca vai na resposta HTTP, a comparação é `hmac.compare_digest`, há expiração e há tecto de tentativas. Confirmar o `from` é confirmar que o endereço é do operador — nunca que os destinatários consentiram. | Must | IMPLEMENTADO |


### FR-7 Composição e envio

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-7.1 | O utilizador escreve assunto e corpo (texto simples). A composição é guardada por utilizador. | Must | DRAFT |
| FR-7.2 | O email composto é pontuado por `analyzer/scoring.py`, o mesmo motor que avalia qualquer email completo, e o resultado é mostrado da mesma forma (regras nomeadas, em pt-PT). `signatures/spam.py` pontua **a assinatura** e não o email: um número de pontos não transfere entre uma assinatura e um email marketing, e afinar um motor para o segundo caso rebenta o score do primeiro, que já está provado. **Dois motores, duas calibrações** — a escala de categorias e o formato de `Finding` são partilhados por importação, nunca por cópia. | Must | DRAFT |
| FR-7.3 | O envio é **bloqueado** se o score for `CRÍTICO` ou se qualquer regra for de gravidade `crítica` — a mesma política de FR-4.9, não uma variante mais tolerante. É a unifying invariant do `Intent`. | Must | DRAFT |
| FR-7.4 | A assinatura do utilizador é anexada ao email enviado, com a opção de não a anexar. A assinatura entra no score do email inteiro. | Should | DRAFT |
| FR-7.5 | O utilizador escolhe uma lista de destinatários. Não há campo de destinatário livre para BCC: um BCC escrito à mão é a forma mais rápida de um utilizador de boa-fé se tornar spammer, e o produto deve empurrá-lo para a lista. | Must | DRAFT |
| FR-7.6 | O email é enviado com `Date`, `Message-ID` e `MIME-Version` válidos, e sem `MIME-Version` nas partes MIME (o mesmo bug que o T008 encontrou no caminho de OTP). | Must | DRAFT |
| FR-7.7 | Falha de SMTP num destinatário não aborta o envio dos restantes. A falha é contada e a razão é da classe da excepção, nunca a sua mensagem (pode conter credenciais). | Must | DRAFT |
| FR-7.8 | Um envio reporta `enviado` / `falhado` / `omitido`, e o relatório diz **quantos** de cada, sem expor endereços completos ao log. | Must | DRAFT |

### FR-8 Agendamento

| ID | Requisito | Prioridade | Estado |
|----|-----------|-----------|--------|
| FR-8.1 | Um envio pode ser agendado para uma data/hora futura, ou imediato. O agendamento persiste: reiniciar a aplicação não o perde. | Must | DRAFT |
| FR-8.2 | O envio é reivindicado por **exatamente um** processo, por `UPDATE ... WHERE id = ? AND state = 'agendado'` dentro de `BEGIN IMMEDIATE`, com o resultado a ser o número de linhas afectadas. É isto que torna seguro correr `uvicorn --workers N` com N threads de agendamento. | Must | DRAFT |
| FR-8.3 | Um envio cujo `claimed_at` expirou passa a `falhado` com os contadores parciais. **Nunca** volta a `agendado`: re-enfileirar reenvia a quem já recebeu. | Must | DRAFT |
| FR-8.4 | O loop de agendamento é desactivável por `MAILUTILS_SCHEDULER=off`, e os testes correm com ele desligado — um teste que dependa de um thread em background não é determinístico. | Must | DRAFT |
| FR-8.5 | O envio é feito em blocos (`MAILUTILS_SEND_BATCH`, por omissão 200 destinatários por ciclo) para que uma lista grande não segure o loop nem impeça o encerramento. | Should | DRAFT |
| FR-8.6 | **Novo.** A cadência é **derivada do score** de spam do email inteiro (assinatura incluída, `FR-7.4`) por uma tabela versionada, com um override manual por lista (`cadence_seconds`). O override pode **aumentar** o intervalo e nunca baixar o mínimo calculado: pedir mais depressa não é um direito, é o que o tecto existe para impedir. | Must | DRAFT |
| FR-8.7 | **Novo, e os números ainda não estão aprovados.** A forma de `FR-8.6` está decidida; os valores da tabela de score→cadência **não** foram assinados por ninguém e não entram em `main` sem isso. Vivem numa constante nomeada como proposta, e trocar a proposta por números aprovados é editar essa constante — nunca o código que a consome. | Must | DRAFT |

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
| NFR-16 | Maintainability | Toda a correcção de um bug tem uma mutação associada que, se passar, deixa `make check` vermelho. O ficheiro `docs/MUTATIONS.md` é **gerado** por `scripts/run-mutations.py` a partir da saída real dos comandos — ninguém escreve lá um resultado à mão. | IMPLEMENTADO |
| NFR-17 | Conformidade | Enviar exige um remetente identificável: `MAILUTILS_MAIL_FROM` mais `MAILUTILS_SENDER_POSTAL_ADDRESS`. Sem o endereço postal, o envio é recusado no arranque de quem activa o envio. **Esta é a parte de que eu não posso garantir a suficiência jurídica** — os requisitos de descadência variam por jurisdição e o owner é quem assume essa responsabilidade. O que o produto garante é que o mecanismo existe e é obrigatório, não que satisfaz toda a lei. | DRAFT |
| NFR-18 | Segurança | Os limites anti-abuso são configuração (`MAILUTILS_MAX_LIST_SIZE`, `MAILUTILS_MAX_PENDING_CONFIRMATIONS`, `MAILUTILS_CONFIRM_COOLDOWN_SECONDS`), nunca constantes escondidas. Reduzi-los é legítimo; **aumentá-los por omissão** é uma decisão do dono, e o valor por omissão está escolhido para ser defensável sem revisão legal. | IMPLEMENTADO |
| NFR-19 | Maintainability | Tudo o que sai da aplicação passa por `signatures/spam.py` antes de sair. Não há caminho de envio que salte o score — nem imediato, nem agendado, nem por reexecução. Testado por uma mutação que remove a chamada e deixa `make check` vermelho. | DRAFT |

## Constraints

- **Language:** Python 3.12
- **Framework:** FastAPI + Jinja2 (já instalados; Flask não está disponível)
- **Persistência:** SQLite via `sqlite3` da stdlib (SQLAlchemy não está disponível)
- **Deployment:** self-hosted, `uvicorn`, atrás de reverse proxy TLS
- **Dependências novas:** 0
- **SMTP:** o servidor SMTP **já configurado** pelo operador é o transporte. Não há
  SMTP próprio, nem fila externa, nem broker. Zero dependências novas aplica-se
  integralmente ao caminho de envio.
- **Fora do âmbito (ver CLAUDE.md):** multi-tenancy, TOTP, faturação, apps móveis,
  caixas de entrada e sincronização de email

> **Alteração de contrato, 2026-10-02.** Até aqui, "SMTP próprio" e "envio de
> newsletters" estavam em fora-do-âmbito. O dono inverteu a decisão: a aplicação
> passa a compor e enviar. A decisão anterior está registada no histórico
> no `git log`. O que **não** mudou: zero dependências
> novas, e o SMTP continua a ser o que o operador já tinha.

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
