#!/usr/bin/env bash
# xkmailutils — instalação de um comando, sem vhost.
#
#     curl -fsSL https://raw.githubusercontent.com/linuxkafe/xkmailutils/main/deploy.sh | sudo bash
#
# A porta não é 8080 e não é fixa: procura a primeira livre a partir de 8642.
#
# Ou, com opções:
#
#     curl -fsSL https://raw.githubusercontent.com/linuxkafe/xkmailutils/main/deploy.sh \
#       | sudo bash -s -- --porta 9000 --dominio mail.exemplo.pt
#
# O que este script faz, e só:
#
#   1. Descarga o repositório.
#   2. Verifica o que falta (docker, git) antes de fazer qualquer coisa.
#   3. Gera um `.env` com segredos aleatórios, se não existir.
#   4. Arranca o contentor.
#   5. Espera que responda, e diz o endereço.
#
# O que este script NÃO faz, e porquê:
#
#   * Não instala nginx, Apache, certbot, nem toca em `/etc/`. A aplicação
#     serve-se a si mesma numa porta. Um script que edita configuração do sistema
#     tem de ser revisto linha a linha antes de correr com `sudo`, e isso
#     anula a vantagem de ser um comando só.
#   * Não define uma palavra-passe para o administrador. Seria escolhida por
#     esta máquina, não por quem vai usar a aplicação, e quem a usasse ficaria
#     sem saber o que era. A aplicação arranca sem administrador e diz isso; o
#     primeiro arranque faz-se com um comando que está no README.
#   * Não abre portas de firewall. Numa máquina com firewall, `ufw`/`firewalld`
#     não é do contentor para gerir, e desligar um firewall sem perguntar é o
#     tipo de coisa que um script de instalação não faz.

set -euo pipefail

REPO="linuxkafe/xkmailutils"
BRANCH="${XKMAILUTILS_BRANCH:-main}"
RAIZ="${XKMAILUTILS_DIR:-/opt/xkmailutils}"
#: Porta em branco = escolher uma livre. Ver `escolher_porta`.
PORTA="${XKMAILUTILS_PORTA:-}"
#: Candidatas, por ordem de preferência. Nada de 8080: é a porta que meia
#: dúzia de projectos auto-hospedados usa, e o objectivo de servir a aplicação
#: numa porta alta era exactamente o de não chocar com nada. A primeira livre
#: ganha; se nenhuma estiver livre, e a falha diz quais foram tentadas, para a pessoa escolher com --porta.
PORTAS_CANDIDATAS="8642 8643 8644 8645 8646 8647 8648 8649 8650 8651 8652 8653"
DOMINIO=""
COM_TLS="nao"
PREFIXO="/xkmailutils"
EMAIL_ADMIN=""

# ----------------------------------------------------------------- output --
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    V=$(printf '\033[32m'); A=$(printf '\033[33m'); E=$(printf '\033[31m')
    N=$(printf '\033[0m'); G=$(printf '\033[1m')
else
    V=""; A=""; E=""; N=""; G=""
fi

passo()  { printf "%s==>%s %s%s%s\n" "$G" "$N" "$G" "$1" "$N"; }
aviso() { printf "%saviso:%s %s\n" "$A" "$N" "$1"; }
erro()  { printf "%serro:%s %s\n" "$E" "$N" "$1" >&2; }
falhar(){ erro "$1"; printf "%sO deploy parou. Nada foi instalado a meio.%s\n" "$E" "$N" >&2; exit 1; }

uso() {
    cat <<'FIM'
uso: deploy.sh [opções]

  --porta N        porta no host. Sem esta opção, escolhe a primeira livre
  --dominio N      activa o TLS com Caddy; exige portas 80 e 443 livres
  --dir CAMINHO    onde instalar (predefinição /opt/xkmailutils)
  --prefixo CAMINHO  prefixo de path (predefinição /xkmailutils)
  --email-admin E  email do primeiro administrador
  --ramo N         ramo a instalar (predefinição main)
  --ajuda          este texto

Sem opções, escolhe uma porta livre e instala em
    http://<este-servidor>:<porta><prefixo>
FIM
}

# ------------------------------------------------------------------ porta --

# `porta_ocupada` responde se algo está a escutar. Usa o `/dev/tcp` do bash em
# vez de `ss` ou `netstat` porque o bash já é um requisito do script e `ss` não
# está em todas as máquinas — sobretudo em contentores mínimos, que é onde
# este script vai correr mais vezes.
porta_ocupada() {
    (echo >"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

# `escolher_porta` devolve a primeira candidata livre. A ordem de tentativa é
# determinística: a mesma máquina dá a mesma porta, o que evita a surpresa de
# reinstalar e ver a aplicação noutro sítio.
escolher_porta() {
    local candidata
    for candidata in $PORTAS_CANDIDATAS; do
        if ! porta_ocupada "$candidata"; then
            printf "%s" "$candidata"
            return 0
        fi
    done
    return 1
}

# ------------------------------------------------------------------ args --
while [ $# -gt 0 ]; do
    case "$1" in
        --porta) PORTA="$2"; shift 2 ;;
        --dominio) DOMINIO="$2"; COM_TLS="sim"; shift 2 ;;
        --dir) RAIZ="$2"; shift 2 ;;
        --prefixo) PREFIXO="$2"; shift 2 ;;
        --email-admin) EMAIL_ADMIN="$2"; shift 2 ;;
        --ramo) BRANCH="$2"; shift 2 ;;
        --ajuda|-h) uso; exit 0 ;;
        *) uso; falhar "opção desconhecida: $1" ;;
    esac
done

# A porta é escolhida aqui, depois dos argumentos, para que `--porta` tenha
# precedência e a procura só corra quando ninguém pediu uma.
if [ -z "$PORTA" ]; then
    PORTA="$(escolher_porta)" || falhar \
        "nenhuma das portas $PORTAS_CANDIDATAS está livre. Escolhe outra com --porta N"
    passo "Porta escolhida: $PORTA"
fi
porta_ocupada "$PORTA" &&
    aviso "a porta $PORTA já tem algo a escutar. Se o contentor não arrancar, é por causa disto."

# ------------------------------------------------------------ pré-requisitos --
passo "A verificar o que falta"

command -v git >/dev/null 2>&1 || falhar "git não está instalado. Debian/Ubuntu: apt install git"
command -v curl >/dev/null 2>&1 || falhar "curl não está instalado. Debian/Ubuntu: apt install curl"

DOCKER_OK=nao
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        DOCKER_OK="sim"
    else
        aviso "o comando docker existe mas não responde. Talvez o utilizador não pertença ao grupo docker."
        falhar "não é possível falar com o daemon do docker"
    fi
fi

if [ "$DOCKER_OK" = "nao" ]; then
    cat <<'FIM' >&2

O Docker não está a falar connosco.

Este deploy usa contentores de propósito: a aplicação tem zero dependências
para instalar à mão, e o contentor é a forma de não as instalar.

Para instalar o Docker, o caminho oficial para a sua distribuição é:

    https://docs.docker.com/engine/install/

Depois,Volte a correr este comando. Se o erro for de permissões:

    sudo usermod -aG docker "$USER"   # e Volte a entrar na sessão

FIM
    exit 1
fi

docker compose version >/dev/null 2>&1 \
    || falhar "o plugin 'docker compose' não está disponível (precisa do Docker 20.10 ou mais)"
aviso "docker e git disponíveis"

# ------------------------------------------------------------------ clone --
passo "A descarregar $REPO ($BRANCH)"

if [ -d "$RAIZ/.git" ]; then
    aviso "$RAIZ já existe: a actualizar"
    git -C "$RAIZ" fetch --quiet origin "$BRANCH" \
        || falhar "não foi possível ir buscar as actualizações"
    git -C "$RAIZ" checkout --quiet "origin/$BRANCH"
else
    [ -e "$RAIZ" ] && falhar "$RAIZ existe e não é um repositório git. Mova-o ou apague-o."
    mkdir -p "$RAIZ" || falhar "não foi possível criar $RAIZ"
    git clone --quiet --depth 1 --branch "$BRANCH" \
        "https://github.com/$REPO.git" "$RAIZ" \
        || falhar "não foi possível clonar https://github.com/$REPO.git"
fi

for ficheiro in Dockerfile docker-compose.yml Caddyfile; do
    [ -f "$RAIZ/$ficheiro" ] || falhar "$ficheiro não existe no repositório. Clone incompleto?"
done

# --------------------------------------------------------------- segredos --
# `MAILUTILS_SECRET_KEY` é o que assina as sessões e os tokens de CSRF. Gerado
# aqui e enviado para lado nenhum: o valor vive só no `.env` do servidor, com
# permissões 600.
passo "A preparar a configuração"

gerar_segredo() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-64
    else
        head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

SECRETO_NOVO="$(gerar_segredo)"
[ "${#SECRETO_NOVO}" -ge 32 ] || falhar "não foi possível gerar um segredo com 32+ caracteres"

BASE_PUBLICA="http://$(hostname -f 2>/dev/null || hostname):$PORTA"
[ -n "$DOMINIO" ] && BASE_PUBLICA="https://$DOMINIO"

# `MAILUTILS_ALLOW_INSECURE_MEDIA=1` é a consequência de servir em http, e é
# explícita. A aplicação recusa arrancar em produção sem https; ligar isto é
# dizer "eu sei o que estou a fazer e aceito a penalização". Está no compose,
# na documentação e no arranque, e a interface avisa o utilizador.
INSECURE=1
[ -n "$DOMINIO" ] && INSECURE=0

if [ -f "$RAIZ/.env" ]; then
    aviso ".env já existe: não é tocado. A palavra-passe e o segredo ficam."
    if [ -n "$DOMINIO" ]; then
        aviso "actualizando só o domínio e a porta em .env"
        sed -i.bak "s|^MAILUTILS_PUBLIC_BASE_URL=.*|MAILUTILS_PUBLIC_BASE_URL=$BASE_PUBLICA|" "$RAIZ/.env"
        sed -i.bak "s|^XKMAILUTILS_DOMINIO=.*|XKMAILUTILS_DOMINIO=$DOMINIO|" "$RAIZ/.env" 2>/dev/null || true
        sed -i.bak "s|^XKMAILUTILS_PORTA=.*|XKMAILUTILS_PORTA=$PORTA|" "$RAIZ/.env" 2>/dev/null || true
        rm -f "$RAIZ/.env.bak"
    fi
else
    aviso "a escrever .env novo, com permissões 600"
    umask 077
    cat > "$RAIZ/.env" <<FIM
# Gerado por deploy.sh em $(date -Is)
#
# Ficheiro de SEGREDOS. Não va para o controlo de versões e não se partilha.
# As permissões 600 vêm do umask do script que o escreveu.

# URL pública. É com este valor que as imagens do logótipo vão entrar nos
# emails: {BASE_PUBLICA}{PREFIXO}/media/ficheiro.png
MAILUTILS_PUBLIC_BASE_URL=$BASE_PUBLICA
MAILUTILS_PATH_PREFIX=$PREFIXO
XKMAILUTILS_PORTA=$PORTA

# O primeiro administrador. Password em branco de propósito: quem executa isto
# não sabe qual deve ser. Ver README, "Criar o administrador".
MAILUTILS_ADMIN_EMAIL=$EMAIL_ADMIN
MAILUTILS_ADMIN_PASSWORD=

# Assina sessões e CSRF. Se isto mudar, toda a gente sai. Nunca é publicado.
MAILUTILS_SECRET_KEY=$SECRETO_NOVO

# Served em http. A aplicação recusa arrancar em produção sem https, a não ser
# que isto esteja ligado. A consequência: as imagens do logótipo entram nos
# emails por http://, e o score de spam penaliza a assinatura. Leia README.
MAILUTILS_ALLOW_INSECURE_MEDIA=$INSECURE

# Email: onde vão os códigos de acesso e os convites.
# 'console' escreve-os no terminal do contentor — é para experimentar.
MAILUTILS_MAIL_BACKEND=console
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_STARTTLS=1
MAILUTILS_MAIL_FROM=
MAILUTILS_MAIL_FROM_NAME=mailutils

FIM
    chmod 600 "$RAIZ/.env"
fi

# ------------------------------------------------------------------ build --
passo "A construir a imagem"
cd "$RAIZ"

# **`--quiet` esconde o erro.** Um `pip install` que falha diz porquê em três
# linhas, e o `--quiet` enterra-as em trezentas de transferências de wheel. A
# primeira vez que este script correu num servidor a falhar, a mensagem foi
# `process "/bin/sh -c python -m venv /venv ..." did not complete successfully:
# exit code: 1` — que não diz nada. Um gate que engole a falha obriga a repetir
# o comando à mão para saber o que se passou, e repetir é o que se tenta
# evitar. O log vai inteiro para um ficheiro e as últimas linhas entram no
# ecrã. (F-15)
LOG_BUILD="$RAIZ/.build.log"
if docker compose build --progress=plain >"$LOG_BUILD" 2>&1; then
    rm -f "$LOG_BUILD"
else
    erro "a construção da imagem falhou. As últimas linhas do build:"
    tail -n 30 "$LOG_BUILD" >&2 || true
    echo "" >&2
    erro "as três causas mais prováveis, por ordem:"
    echo "  1. Sem acesso ao PyPI a partir desta máquina. A imagem faz" >&2
    echo "     'pip install' na fase de build, e isso precisa de rede:" >&2
    echo "       curl -sI https://pypi.org/simple/ | head -1" >&2
    echo "  2. Memória insuficiente. 'pip install' do fastapi e do pydantic" >&2
    echo "     leva mais do que um contentor pequeno aguenta:" >&2
    echo "       free -m" >&2
    echo "  3. Disco cheio. A imagem ocupa centenas de MB:" >&2
    echo "       df -h $RAIZ" >&2
    echo "" >&2
    falhar "o log completo está em $LOG_BUILD"
fi

# ----------------------------------------------------------------- arranque --
passo "A arrancar"
if [ "$COM_TLS" = "sim" ]; then
    docker compose --profile tls up -d \
        || falhar "'docker compose --profile tls up -d' falhou"
else
    docker compose up -d || falhar "'docker compose up -d' falhou"
fi

# ------------------------------------------------------------------ espera --
passo "A esperar que responda"

ALVO="http://127.0.0.1:$PORTA$PREFIXO/saude"
ESPERADO=nao
for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "$ALVO" >/dev/null 2>&1; then
        ESPERADO="sim"
        break
    fi
    sleep 1
done

if [ "$ESPERADO" = "nao" ]; then
    echo >&2
    erro "a aplicação não respondeu em $ALVO ao fim de 30 segundos"
    echo >&2
    echo "O log do contentor:" >&2
    docker compose logs --tail 40 >&2 2>&1 || true
    echo >&2
    echo "Causas habituais:" >&2
    echo "  * MAILUTILS_SECRET_KEY com menos de 32 caracteres no .env" >&2
    echo "  * MAILUTILS_PUBLIC_BASE_URL em http com ALLOW_INSECURE_MEDIA desligado" >&2
    echo "  * a porta $PORTA já estar ocupada" >&2
    exit 1
fi

# ---------------------------------------------------------------- relatório --
USUARIOS="$(curl -fsS --max-time 2 "$ALVO" 2>/dev/null | awk '{print $2}' || echo '?')"

printf '\n%s%s%s\n' "$G" "════════════════════════════════════════════" "$N"
printf "%s%s está a funcionar.%s\n\n" "$G$V" "xkmailutils" "$N"
printf "  endereço     %s%s\n" "$BASE_PUBLICA" "$PREFIXO"
if [ "$COM_TLS" = "sim" ]; then
    printf "  com TLS      %s%s (certificado do Caddy, renovado sozinho)\n" "$BASE_PUBLICA" "$PREFIXO"
fi
printf "  utilizadores %s\n" "$USUARIOS"
printf "  directório   %s\n" "$RAIZ"
printf "  segredos     %s/.env (modo 600, não versionado)\n" "$RAIZ"
printf '\n'

if [ "$USUARIOS" = "0" ] || [ "$USUARIOS" = "?" ]; then
    cat <<FIM
${G}Ainda não há nenhum utilizador.${N}

Não foi criado um por si: a palavra-passe tem de ser escolhida por quem vai
usar, e o script não a podia inventar sem que ninguém soubesse o que era.

Para criar o administrador:

    cd $RAIZ
    \$EDITOR .env                       # preencher MAILUTILS_ADMIN_PASSWORD
    docker compose up -d --force-recreate

Ou, sem editar ficheiros:

    docker compose exec -e MAILUTILS_ADMIN_PASSWORD='a-sua-palavra' \\
        mailutils python -c "from mailutils.main import create_app; create_app()"

Depois, entre em $BASE_PUBLICA$PREFIXO
FIM
else
    printf "%sJá existe pelo menos um utilizador.%s Nada a fazer aqui.\n\n" "$G" "$N"
fi

cat <<FIM
${G}A seguir${N}

  ver o log          docker compose -f $RAIZ/docker-compose.yml logs -f
  actualizar         git -C $RAIZ pull && docker compose -C $RAIZ up -d --build
  parar              docker compose -C $RAIZ down
  apagar tudo        docker compose -C $RAIZ down -v      ${E}(apaga a base de dados)${N}

FIM

if [ "$INSECURE" = "1" ]; then
    cat <<FIM
${A}Nota sobre TLS${N}

A aplicação está a ser servida em http. Ela funciona toda, mas a imagem do
logótipo vai entrar nos emails dos destinatários por http:// — e o próprio
mailutils penaliza isso no score, porque vários clientes de email não
carregam imagens http.

Se quiser https sem configurar vhost nem certificados à mão:

    $EDITOR $RAIZ/.env            # XKMAILUTILS_DOMINIO=mail.exemplo.pt
    cd $RAIZ && docker compose --profile tls up -d

O Caddy obtém o certificado sozinho e renova-o. Precisa de portas 80 e 443
livres no host.

FIM
fi
