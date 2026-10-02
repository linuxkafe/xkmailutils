# xkmailutils

Assinaturas de email HTML que **não introduzem padrões de spam** nos filtros
dos clientes — e um score que explica porquê.

Self-hosted. Sem telemetria. Sem dependências novas.

---

## Instalar com um comando

```bash
curl -fsSL https://raw.githubusercontent.com/linuxkafe/xkmailutils/main/deploy.sh | sudo bash
```

Isto é o que acontece, por ordem: verifica o que falta (git, curl, docker),
escolhe **uma porta livre** — começa por 8642 e salta as ocupadas, porque 8080
é a porta que meia dúzia de projectos auto-hospedados usa —, gera um `.env` com
um segredo aleatório, arranca o contentor e diz o endereço.

Depois disto, a aplicação está em `http://<servidor>:<porta>/xkmailutils`.

Com opções, quando não serve o que está por omissão:

```bash
curl -fsSL https://raw.githubusercontent.com/linuxkafe/xkmailutils/main/deploy.sh \
  | sudo bash -s -- --porta 8700 --email-admin eu@exemplo.pt
```

TLS sem tocar em `/etc`: `--dominio mail.exemplo.pt`, que liga um Caddy com
certificado automático.

**Antes de correr isto com `sudo`, lê o topo do `deploy.sh`.** Está lá escrito o
que ele **não** faz: não instala nginx nem certbot, não abre portas de firewall,
não define a palavra-passe do administrador. Um `curl | sudo bash` que faz
pouco é legível; um que faz muito, não.

Para correr o código em vez do contentor, ver [Arrancar](#arrancar).

---

## O problema

Uma assinatura com logótipo é precisamente o tipo de fragmento HTML que os
filtros penalizam: `<table>`, estilos inline, imagens remotas, ligações. A
maioria das soluções encontradas online pioram o problema — embebem a imagem em
base64, usam `<style>`, metem tracking pixels. Nenhuma diz *qual* parte está a
ser punida.

O mailutils gera o HTML **por defeito** limpo, calcula um score de risco sobre
o output real, e nomeia cada regra violada com a respective remediação.

## Arrancar

Do código, sem contentor:

```bash
cp .env.example .env
$EDITOR .env          # pelo menos: MAILUTILS_ADMIN_EMAIL, MAILUTILS_ADMIN_PASSWORD
make setup
make run              # http://127.0.0.1:8000
```

O primeiro utilizador (admin) é criado no primeiro arranque, a partir do `.env`.
Depois disso, mudar o `.env` **não** muda a palavra-passe — use `/perfil`.

Em desenvolvimento, os emails (OTP, convites) aparecem no terminal:

```
MAILUTILS_MAIL_BACKEND=console
```

## Verificar

```bash
make check     # O GATE: docs + código + testes com cobertura + lint + formato + E2E
make test      # pytest
# `make check` inclui o suite E2E, que precisa do Chromium. `make setup`
# instala o pacote `playwright` mas não o browser:
#   python3 -m playwright install chromium
# `make doctor` diz o que falta. (F-13)

make verify TICKET=T001   # lê os critérios do ticket; NÃO é um gate
make doctor    # estado do ambiente
```

## Como se usa

1. **Entrar.** Num dispositivo conhecido, a palavra-passe basta. Num
   dispositivo novo, a aplicação envia um código de 6 dígitos para o email.
2. **Carregar o logótipo.** PNG, JPEG, GIF ou WebP até 2 MiB. A validação é
   feita pelo conteúdo do ficheiro, não pela extensão.
3. **Preencher os campos** e ver o score a actualizar enquanto escreve.
4. **Exportar.** `.html` para colar no Thunderbird, `.txt` para clientes que
   não aceitam HTML, com instruções por cliente.

E, à parte, **listas de destinatatórios**: criar a lista, acrescentar ou
importar endereços, e pedir o código de confirmação a cada um. **Importar não
confirma ninguém** — a confirmação é um acto separado, e é o que separa uma
lista de contactos de uma lista de spam. Quem não confirmar não recebe.

O **compositor e o envio** são o ticket seguinte. O que existe hoje é a lista e
a confirmação; enviar, ainda não.

## As regras que o produto existe para impor

- **Nada de `data:` URI.** É o sinal de spam mais severo numa assinatura. A
  imagem é servida por `https://{MAILUTILS_PUBLIC_BASE_URL}/media/...`.
- **Nada de `<script>`, `<iframe>`, `<form>`, `display:none`.** Nem
  tracking pixels.
- **Table-based com estilos inline.** O motor de renderização do Outlook não
  implementa CSS moderno, e o Gmail remove `<style>` em muitos clientes.
- **Exportação bloqueada** em score crítico, ou com qualquer regra de
  gravidade crítica. Um botão que deixasse gerar o HTML que o produto acabou
  de dizer que é lixo seria uma decisão de produto contrária ao objectivo.

E o inverso, que importa tanto: **o score nunca é apresentado como garantia de
entrega.** É uma heurística local baseada em padrões documentados. Não é o
algoritmo do Gmail, do Outlook, nem de qualquer outro fornecedor. A interface
diz isso em todo o lado onde o número aparece, e há um teste que garante que
esse aviso não desaparece.

## Porquê estas decisões

| Decisão | Porquê |
|---|---|
| FastAPI + Jinja2 | Já estavam instalados. Flask não estava. |
| `sqlite3` da stdlib, sem ORM | `sqlalchemy` não está instalado, e o projecto não ganha dependências. Quatro tabelas não justificam um ORM. |
| `hashlib.scrypt` | `passlib`/`argon2` não estão instalados. Os parâmetros de custo viajam no próprio hash, para poderem subir sem invalidar senhas. |
| Sem redimensionamento de imagens | Exigiria Pillow. A dimensão é lida, o utilizador é avisado, e o score penaliza HTML grande. |
| Convites pelo admin | Fecha o topo do registo público. O utilizador define a sua própria palavra-passe; o admin nunca a vê. |
| 2F só em dispositivos novos | Requisito do dono do projecto. Dispositivos listáveis e revogáveis em `/dispositivos`. |

## O que este projecto não é

- **Não** garante entrega fora do spam.
- **Não** é um cliente de email. Não recebe nem sincroniza. A aplicação **envia**
  — para listas de destinatários que o próprio utilizador constrói e cujos
  endereços confirmaram a inscrição por código único. Um endereço por confirmar
  não entra em nenhum envio, em nenhum caminho.
- **Não** é multi-tenant. Uma instalação, um operador, N utilizadores.
- **Não** é uma ferramenta de marketing. Não faz segmentos, campanhas nem A/B testing. O envio em massa é do T015 e ainda não existe; o que existe hoje é a lista e a confirmação por código.

Ver `CLAUDE.md` para o contrato operacional completo e `docs/ROADMAP.md` para o
que está planeado e o que está deliberadamente fora de âmbito.

## Estrutura

```
src/mailutils/
├── main.py              app factory, lifespan, /media, /saude, cabeçalhos de segurança
├── config.py            leitura do .env; recusa arrancar em produção mal configurada
├── db.py                esquema SQLite e migrações
├── security.py          scrypt, sessões, CSRF, OTP, impressão de dispositivo
├── web.py               sessão, CSRF, dependências de rota
├── templates.py         motor Jinja2 e mensagens de interface em pt-PT
├── mailer.py            email por SMTP / console / null
├── auth/                regras de negócio de autenticação e rotas
├── admin/               convites e gestão de utilizadores
├── signatures/
│   ├── renderer.py      o HTML que entra no email de clientes reais
│   ├── spam.py          as regras de score
│   ├── images.py        validação e guarda do logótipo
│   └── routes.py        editor, preview, exportação
├── templates/           base + login, verificação, convite, editor, admin
└── static/              app.css, app.js
```

## Nota de ambiente

`starlette` 0.31 usa `TestClient(app=...)`, parâmetro que o `httpx` 0.28
removeu. Em vez de rebaixar o `httpx` do sistema — um efeito colateral no
computador de outra pessoa por causa de um bug nosso — os testes usam
`tests/asgi_client.py`, um cliente ASGI de ~120 linhas sobre a
`ASGITransport` que já está instalada. **Não rebaixar o `httpx`.**
