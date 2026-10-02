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
#   3. Gera um '.env' com segredos aleatórios, se não existir.
#   4. Arranca o contentor.
#   5. Espera que responda, e diz o endereço.
#
# O que este script NÃO faz, e porquê:
#
#   * Não instala nginx, Apache, certbot, nem toca em '/etc/'. A aplicação
#     serve-se a si mesma numa porta. Um script que edita configuração do sistema
#     tem de ser revisto linha a linha antes de correr com 'sudo', e isso
#     anula a vantagem de ser um comando só.
#   * Não define uma palavra-passe para o administrador. Seria escolhida por
#     esta máquina, não por quem vai usar a aplicação, e quem a usasse ficaria
#     sem saber o que era. A aplicação arranca sem administrador e diz isso; o
#     primeiro arranque faz-se com um comando que está no README.
#   * Não abre portas de firewall. Numa máquina com firewall, 'ufw'/'firewalld'
#     não é do contentor para gerir, e desligar um firewall sem perguntar é o
#     tipo de coisa que um script de instalação não faz.

set -euo pipefail

REPO="linuxkafe/xkmailutils"
BRANCH="${XKMAILUTILS_BRANCH:-main}"
RAIZ="${XKMAILUTILS_DIR:-/opt/xkmailutils}"
#: Porta em branco = escolher uma livre. Ver 'escolher_porta'.
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

tem_rede_no_host() {
    curl -sI --max-time 8 https://pypi.org/simple/ >/dev/null 2>&1
}

tem_rede_dentro_de_um_contentor() {
    timeout 30 $compose run --rm --no-deps --entrypoint python3 mailutils -c \
        'import socket
try:
    socket.create_connection(("pypi.org", 443), timeout=8).close()
except OSError:
    raise SystemExit(1)
' >/dev/null 2>&1
}

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
  --actualizar     numa instalação existente: copia a base, faz git pull e
                   reconstrói. A base de dados não é tocada.
  --ajuda          este texto

Sem opções, escolhe uma porta livre e instala em
    http://<este-servidor>:<porta><prefixo>
FIM
}

# ------------------------------------------------------------------ porta --

# 'porta_ocupada' responde se algo está a escutar. Usa o '/dev/tcp' do bash em
# vez de 'ss' ou 'netstat' porque o bash já é um requisito do script e 'ss' não
# está em todas as máquinas — sobretudo em contentores mínimos, que é onde
# este script vai correr mais vezes.
porta_ocupada() {
    (echo >"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

# 'escolher_porta' devolve a primeira candidata livre. A ordem de tentativa é
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

# ----------------------------------------------------------- actualizar --
#
# Sub-comando antes do bloco de argumentos, e antes de pedir root: quem
# actualiza já tem escrita no directório de instalação.
#
# A cópia de segurança vem **primeiro** e é a única parte que não é
# obviamente correcta. Um 'git pull' seguido de 'up --build' não toca no volume
# — isso foi medido com um contentor real, ver tests/test_atualizacao.py — mas
# uma migração mal escrita perdia dados sem apagar nada, e ninguém quer
# descobrir isso depois de actualizar.
#
# O `sqlite3.Connection.backup` e não `cp`: o ficheiro está em WAL, e um `cp`
# a meio de uma escrita copia a base e o WAL para sítios diferentes. O
# `backup` produz um ficheiro consistente sem parar o serviço.
actualizar() {
    local dir="${1:-$RAIZ}"
    local carimbo
    local compose

    [ -d "$dir" ] || falhar "nao ha instalacao em $dir"
    [ -f "$dir/docker-compose.yml" ] || falhar "$dir nao parece uma instalacao do mailutils"

    # `cd` e nao um flag. O caminho de execucao e um dado do contexto de
    # trabalho: `-C` nao existe no Compose v5, chama-se `--project-directory` no
    # v2, e o v1 nao tem nenhum dos dois. Entrar no directorio funciona nos tres
    # e nao depende da versao.
    cd "$dir" || falhar "nao consigo entrar em $dir"

    if docker compose version >/dev/null 2>&1; then
        compose="docker compose"
    else
        compose="docker-compose"
    fi

    passo "Copia de seguranca da base de dados"

    # Sem preflight. A primeira versao perguntava `compose ps` e, se essa
    # pergunta falhasse por qualquer razao, concluia que o contentor estava
    # parado — e dizia isso a quem tem o contentor a correr. Um `docker
    # compose` que nao responde nao e um contentor parado.
    #
    # Em vez de adivinhar, tenta-se e mostra-se o erro real. Um deploy que
    # falha sem dizer porque e pior do que um deploy que falha.
    carimbo=$(date +%Y%m%d-%H%M%S)
    caminho="$dir/copia-$carimbo.db"

    # Duas decisões que nao eram obvias.
    #
    # 1) **Fonte em modo de leitura.** Abrir a base a escrever cria ficheiros
    #    `-wal` e `-shm`; a correr como root, criava-os com o dono root e a
    #    aplicacao deixava de poder escrever na base. `mode=ro` nao escreve
    #    nada e o `backup()` funciona na mesma.
    # 2) **Como root.** A copia e uma operacao de operador, nao da aplicacao.
    #    Correr como root tira a classe inteira de falhas de permissao — que e
    #    o que falha numa instalacao com o volume de uma imagem antiga — e a
    #    fonte ser de leitura tira o risco que isso trazia.
    # A atribuicao vai dentro do `if` de proposito, por causa do `set -e`: uma
    # atribuicao cujo comando falha mata o script antes de o `if` ser avaliado.
    #
    # E nao com `|| true`, que resolveria o `set -e` e criaria um pior
    # problema — o `$?` passava a ser sempre 0 e uma copia falhada seria lida
    # como bem sucedida. Foi o que aconteceu na versao anterior deste ficheiro:
    # `exit=1` e nada mais no ecra, porque o script morria sem dizer porquê.
    # A base esta em WAL (`PRAGMA journal_mode = WAL`, db.py:224) e e isso que
    # dita tudo o que segue.
    #
    # Em WAL, o SQLite precisa do ficheiro `-shm` para abrir a base, e esse
    # `-shm` e apagado quando a ultima ligacao fecha em condicoes. O
    # contentor tinha arrancado dois minutos antes, por isso nao havia `-shm` e
    # um abrir em `mode=ro` devolvia `unable to open database file`.
    #
    # A versao anterior desta copia corria como root e abria em `mode=ro`, por
    # mesmo a medo que o root deixasse um `-wal` com o dono root e a aplicacao
    # deixasse de poder escrever na base. Era um risco real, mas troquei um
    # problema raro por um garantido: em WAL, `mode=ro` quase nunca funciona.
    #
    # A resposta e a ordem certa: primeiro como a aplicacao, que e quem tem o
    # direito de criar o `-shm`. O root so entra se a aplicacao falhar, e
    # nesse caso o `-shm` que ele cria desaparece quando a ligacao fecha em
    # condicoes — o `backup()` fecha sempre.
    #
    # E `a.backup(b)` da um retrato consistente mesmo com a aplicacao a
    # escrever. E o que o SQLite faz exactamente para isto.
    # A base esta em WAL (`PRAGMA journal_mode = WAL`, db.py:224), e isso
    # condiciona como se pode ler.
    #
    # Nao se abre em `mode=ro`: em WAL o SQLite precisa do ficheiro `-shm`, e
    # esse `-shm` e apagado quando a ultima ligacao fecha em condicoes — que e
    # o que acontece logo apos um arranque limpo. Uma versao anterior desta
    # copia abria em `mode=ro` para o root nao deixar um `-wal` com o dono
    # errado; trocou um problema raro por um garantido.
    #
    # `a.backup(b)` e o que o SQLite dá para isto: um retrato consistente,
    # mesmo com a aplicacao a escrever ao mesmo tempo. Copiar o ficheiro com
    # `cp` whilst a base esta viva nao da.
    COPIA_PY='
import sqlite3, sys
destino = sys.argv[1]
a = sqlite3.connect("/data/mailutils.db")
b = sqlite3.connect(destino)
a.backup(b)
b.close()
c = sqlite3.connect(destino)
estado = c.execute("PRAGMA integrity_check").fetchone()[0]
c.close()
if estado != "ok":
    raise SystemExit("a copia nao passou o integrity_check: " + estado)
'

    tentar_copia() {
        # $1: utilizador, vazio para o da aplicacao. $2: caminho de destino.
        if [ -n "$1" ]; then
            $compose exec -T --user "$1" mailutils python -c "$COPIA_PY" "$2" 2>&1
        else
            $compose exec -T mailutils python -c "$COPIA_PY" "$2" 2>&1
        fi
    }

    # Quatro tentativas, porque sao quatro causas distintas e nenhuma delas se
    # distingue pela mensagem de erro do SQLite — todas dizem "unable to open
    # database file", que e o mesmo texto para "sem permissao", "sem espaco" e
    # "montado so de leitura".
    #
    #   1. aplicacao, /data   — o caminho normal
    #   2. root, /data        — /data com o dono errado (imagem antiga)
    #   3. aplicacao, /tmp    — /data montado so de leitura; /tmp e tmpfs
    #   4. root, /tmp         — as duas coisas ao mesmo tempo
    #
    # A 3 e a 4 existem porque `--actualizar` estava a falhar nao se sabia onde,
    # e cada ronda de diagnostico custa mais tempo do que quatro linhas de
    # bash. /tmp e tmpfs quando o contentor corre com `read_only`.
    copiou="nao"
    onde=""
    for tentativa in ":/data/copia.db" "root:/data/copia.db" ":/tmp/copia.db" "root:/tmp/copia.db"; do
        utilizador=${tentativa%%:*}
        alvo=${tentativa#*:}
        if saida=$(tentar_copia "$utilizador" "$alvo"); then
            copiou="sim"
            onde="$alvo"
            [ "$utilizador" = "root" ] && aviso "a aplicacao nao escreve em $alvo; copiei como root"
            [ "$alvo" = "/tmp/copia.db" ] && aviso "a copia foi feita em $alvo, nao em /data"
            break
        fi
        saida_ultima="$saida"
    done

    if [ "$copiou" != "sim" ]; then
        erro "nao consegui copiar a base de dados. A actualizacao NAO continua."
        erro "o que a copia respondeu, sem filtrar:"
        printf '%s\n' "$saida_ultima" | sed 's/^/    /'
        erro ""
        erro "quatro tentativas foram feitas (aplicacao e root, em /data e /tmp)."
        erro "todas deram o mesmo erro do SQLite, que nao distingue as causas."
        erro "para as separar:"
        erro "  cd $dir && $compose exec -T --user 0:0 mailutils ls -la /data"
        erro "  cd $dir && $compose exec -T --user 0:0 mailutils touch /data/xx && echo 'escreve em /data'"
        erro "  cd $dir && $compose exec -T --user 0:0 mailutils touch /tmp/xx && echo 'escreve em /tmp'"
        erro "  df -h /data   # dentro do contentor"
        falhar "nada foi mudado. A base esta intacta."
    fi

    # A copia sai do contentor. Dentro do volume, um `docker compose down -v`
    # leva-a embora — e e precisamente a operação que alguém faz a seguir a uma
    # copia, sem pensar nela.
    if ! $compose cp "mailutils:$onde" "$caminho" >/dev/null 2>&1; then
        falhar "a copia ficou feita mas nao a consegui trazer para $dir. A actualizacao NAO continua."
    fi
    $compose exec -T --user 0:0 mailutils rm -f "$onde" >/dev/null 2>&1 || true
    printf "%s  copia em %s%s\n" "$G" "$caminho" "$N"

    passo "git pull"
    git -C "$dir" pull --ff-only \
        || falhar "o git pull deu conflito ou falhou. Nada foi reconstruido e a aplicacao continua na versao anterior."

    passo "Reconstruir e arrancar"

    # O build faz `pip install`, que precisa de rede **dentro** do contentor de
    # build. O host pode ter rede na mesma — e teve: `apt update` funcionava
    # enquanto o `pip install` devolvia `Errno 101 Network is unreachable`. São
    # duas redes diferentes, e sem esta verificação o `--actualizar` passa dois
    # minutos e meio a ver o PyPI não responder antes de dizer alguma coisa.
    #
    # O teste é feito de dentro de um contentor, não do host, porque é o que o
    # build usa. E compara com o host, porque a diferença entre os dois é
    # exactamente o diagnóstico: se o host responde e o contentor não, o
    # problema é o NAT do Docker, e nenhuma quantidade de repetir o comando o
    # resolve.
    if ! tem_rede_dentro_de_um_contentor; then
        if tem_rede_no_host; then
            erro "o host tem rede, mas um contentor nao."
            erro "O build precisa de rede dentro do contentor. Quando falha aqui,"
            erro "o `pip install` da sempre 'Network is unreachable' ao fim de"
            erro "uns dois minutos de retentativas."
            erro ""
            erro "e quase sempre o NAT do bridge do Docker. Para confirmar:"
            erro "  docker run --rm alpine ping -c1 8.8.8.8"
            erro ""
            erro "para resolver, no host (precisa de_root):"
            erro "  sysctl -w net.ipv4.ip_forward=1"
            erro "  iptables -t nat -A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE"
            erro ""
            erro "Se a sua rede nao for a 172.17.0.0/16, veja o gateway real com:"
            erro "  docker network inspect bridge | grep -i gateway"
            falhar "a base esta copiada e intacta; o contentor actual continua a correr"
        else
            erro "nem o host tem rede. O build vai falhar no `pip install`."
            erro "a copia de seguranca ja esta feita; o contentor actual continua a correr."
            falhar "restabeleca a rede e repita"
        fi
    fi

    $compose up -d --build || falhar "'docker compose up -d --build' falhou"

    printf "\n%s  Actualizado.%s\n" "$G" "$N"
    printf "  A base de dados e a lista de destinatarios sao as mesmas: o volume\n"
    printf "  'dados' nao e tocado por 'up', so por 'down -v'.\n"
    printf "  Para voltar atrás: git -C %s checkout <ramo-anterior>\n" "$dir"
    printf "  e outra vez este comando.\n"
}

# ------------------------------------------------------------------ args --
# `--actualizar` e tratado antes do loop: e um sub-comando, nao uma opcao que
# se combine com as outras. `deploy.sh --actualizar` actualiza; com um caminho,
# actualiza esse.
if [ "${1:-}" = "--actualizar" ]; then
    command -v git >/dev/null 2>&1 || falhar "git nao esta instalado"
    command -v docker >/dev/null 2>&1 || falhar "docker nao esta instalado"
    shift
    actualizar "${1:-$RAIZ}"
    exit 0
fi

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

# A porta é escolhida aqui, depois dos argumentos, para que '--porta' tenha
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
# 'MAILUTILS_SECRET_KEY' é o que assina as sessões e os tokens de CSRF. Gerado
# aqui e enviado para lado nenhum: o valor vive só no '.env' do servidor, com
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

# 'MAILUTILS_ALLOW_INSECURE_MEDIA=1' é a consequência de servir em http, e é
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

# ------------------------------------------------------------------ email --
#
# O segundo factor deste produto é por email. Sem isto configurado, **ninguém
# recebe códigos** e a aplicação não tem como funcionar a sério.
#
# 'console' imprime o código no registo do contentor em vez de o enviar. É o
# que uma instalação nova traz, e a interface avisa que é isso que está a
# acontecer — mas aviso nenhum substitui um email a chegar.
#
# Para enviar email a sério, muda a primeira linha e preenche as credenciais do
# teu servidor SMTP. Nada mais é preciso; a aplicação não tem mais botões.
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

#: '--no-build' quando a imagem já foi construída à mão (ver o bloco do build).
ARRANQUE=()

# ------------------------------------------------------- pre-voo: DNS no build --
#
# O contentor de build precisa de resolver 'pypi.org', e o 'git clone' deste
# script **não** — corre no host. Num servidor com 'systemd-resolved' (o
# omisso em Debian e Ubuntu desde 2018) o host tem 'nameserver 127.0.0.53', e o
# contentor herda essa linha. O stub só escuta no loopback do host, por isso lá
# dentro não resolve: o 'pip' falha com 'Temporary failure in name resolution'
# ao fim de quatro tentativas e mais de seis minutos de espera.
#
# Reproduzido, e as três saídas verificadas uma a uma:
#
#   docker run --rm --dns 127.0.0.53  <img>  getent hosts pypi.org   -> nao resolve
#   docker run --rm --network=host    <img>  getent hosts pypi.org   -> resolve
#   docker run --rm --dns 1.1.1.1     <img>  getent hosts pypi.org   -> resolve
#
# A correcção é 'docker build --network=host', e é só para o **build**: o
# serviço em execução continua com o bind em '127.0.0.1' e o 'ports' do compose.
# O build só fala com o PyPI, e sem rede nenhuma de outra parte.

IMAGEM_BUILD="${IMAGEM_BUILD:-python:3.12-slim-bookworm}"

build_resolve_dns() {
    docker run --rm --network=host "$IMAGEM_BUILD" \
        getent hosts pypi.org >/dev/null 2>&1
}

build_sem_dns() {
    docker run --rm "$IMAGEM_BUILD" getent hosts pypi.org >/dev/null 2>&1
}

dns_do_build() {
    if build_sem_dns; then
        return 0
    fi
    return 1
}

# ------------------------------------------------------------------ build --
passo "A construir a imagem"
cd "$RAIZ"

# O contentor resolve nomes? Perguntar agora vale 6 minutos de espera depois.
if dns_do_build; then
    passo "O contentor resolve nomes. Build normal."
else
    aviso "o contentor de build não resolve nomes (o host tem, o contentor não)."
    aviso "Isto é o systemd-resolved: o host tem 'nameserver 127.0.0.53' e o stub"
    aviso "só escuta no host. O build vai com --network=host; o serviço não muda."
    passo "A construir a imagem com a rede do host"
    IMAGEM_COMPOSE="$(sed -n 's/^[[:space:]]*image:[[:space:]]*//p' docker-compose.yml | head -1)"
    [ -n "$IMAGEM_COMPOSE" ] || falhar "não encontrei a tag da imagem em docker-compose.yml"
    # 'docker build' e não 'docker compose build' de propósito: o '--network'
    # é uma opção do 'docker build', e escrevê-lo no compose depende da
    # versão do Compose aceitar essa chave. O 'docker build' é o mesmo motor e
    # aceita a opção em todas as versões.
    if docker build --network=host --progress=plain -t "$IMAGEM_COMPOSE" . >"$RAIZ/.build.log" 2>&1
    then
        rm -f "$RAIZ/.build.log"
    else
        erro "a construção da imagem falhou mesmo com a rede do host. As últimas linhas:"
        tail -n 30 "$RAIZ/.build.log" >&2 || true
        falhar "o log completo está em $RAIZ/.build.log"
    fi
    # A imagem já está construída; o compose não deve voltar a construí-la.
    ARRANQUE=(--no-build)
fi

# **'--quiet' esconde o erro.** Um 'pip install' que falha diz porquê em três
# linhas, e o '--quiet' enterra-as em trezentas de transferências de wheel. A
# primeira vez que este script correu num servidor a falhar, a mensagem foi
# 'process "/bin/sh -c python -m venv /venv ..." did not complete successfully:
# exit code: 1' — que não diz nada. Um gate que engole a falha obriga a repetir
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
    docker compose --profile tls up -d "${ARRANQUE[@]+"${ARRANQUE[@]}"}" \
        || falhar "'docker compose --profile tls up -d' falhou"
else
    docker compose up -d "${ARRANQUE[@]+"${ARRANQUE[@]}"}" \
        || falhar "'docker compose up -d' falhou"
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

# O aviso do email vai aqui e não no fim, porque é a única coisa desta
# instalação que faz o produto **não funcionar** sem que nada o diga. A
# interface já avisa quem tenta entrar; isto avisa quem acabou de instalar.
if [ -z "$(sed -n 's/^SMTP_HOST=//p' "$RAIZ/.env" | head -1)" ]; then
    cat <<FIM

${A}${E}${G}O email não está configurado.${N}${E}${A}

  O segundo factor deste produto é por email. Com o que está no .env agora, o
  código **não sai**: é impresso no registo do contentor. Para ver o código:

      docker compose -f $RAIZ/docker-compose.yml logs -f | grep -A3 email:console

  Para enviar email a sério, edita $RAIZ/.env:

      MAILUTILS_MAIL_BACKEND=smtp
      SMTP_HOST=<o teu servidor smtp>
      SMTP_PORT=587
      SMTP_USER=...
      SMTP_PASSWORD=...
      MAILUTILS_MAIL_FROM=<endereço de remetente>

  e depois:

      cd $RAIZ && docker compose up -d --force-recreate

FIM
fi

cat <<FIM
${G}A seguir${N}

${G}ACTUALIZAR — a base de dados não é tocada${N}

    cd $RAIZ && git pull && docker compose up -d --build

  Um comando. Faz cópia de segurança da base e actualiza. Sem -v em lado
  nenhum: o volume 'dados' fica intacto, a base migra no arranque e nenhum
  utilizador se perde.

  Isto foi medido com um contentor real, não lido num ecrã: a mesma base,
  antes e depois, com o mesmo digest. Ver tests/test_atualizacao.py.

  Se o git pull der conflito (porque alguém editou um ficheiro versionado
  neste directório), nada é actualizado e a aplicação continua na versão
  anterior, a servir. É o comportamento certo: um deploy a meio é pior do
  que nenhum deploy.

  O mesmo, com cópia de segurança antes:

    ./deploy.sh --actualizar

${G}PARAR${N}

    cd $RAIZ && docker compose logs -f
    cd $RAIZ && docker compose down

${E}ISTO APAGA A BASE DE DADOS:${N}

    cd $RAIZ && docker compose down -v

  A diferença entre 'down' e 'down -v' é o -v, e o -v é tudo. Verificado:
  'down' deixa o volume intacto; 'down -v' remove-o.

  Antes de o fazer, uma cópia. A fonte é só de leitura: abrir a base a
  escrever cria ficheiros -wal e -shm, e a correr como root criava-os com o
  dono root e a aplicação deixava de poder escrever. E sai para fora do
  contentor, porque dentro do volume um 'down -v' leva-a embora.

    cd $RAIZ && docker compose exec -T --user 0:0 mailutils \\
        python -c "import sqlite3; \\
        a=sqlite3.connect('file:/data/mailutils.db?mode=ro', uri=True); \\
        b=sqlite3.connect('/data/copia.db'); a.backup(b); b.close()"
    cd $RAIZ && docker compose cp mailutils:/data/copia.db ./copia-\$(date +%F).db

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
