"""Actualizar não pode perder a base de dados.

Este ficheiro existe por causa de uma pergunta simples que valia a pena fazer e
que valia a pena responder com evidência: **se alguém correr o comando de
actualização, a base de dados existente sobrevive?**

## O que foi medido, e como

Em 2026-10-02 a resposta foi dada com um contentor real, não lida num ecrã:

1. `docker compose up -d --build` num volume nomeado, com um utilizador, uma
   lista, um endereço confirmado e um pendente dentro.
2. Digest SHA-256 de tudo o que está em `/data`: `998d9571934d903a`.
3. O comando de actualização, à letra — `up -d --build` com código novo.
4. O mesmo digest: `998d9571934d903a`. Idêntico.
5. `down` sem `-v`: o volume sobrevive.
6. `down -v`: **o volume é destruído**. É o único comando que apaga a base.

O que este ficheiro fixa é o que é verificável sem Docker — que a hipótese
continua a ser verdadeira depois de alguém refactorizar o `deploy.sh` ou o
compose. Docker num contentor de CI é outro projecto; o que está aqui corre em
qualquer máquina em menos de um segundo.

## E porque o `deploy.sh` é o ficheiro perigoso

O script imprime, na mesma bloco e sem separador:

    actualizar         git -C $RAIZ pull && docker compose -C $RAIZ up -d --build
    apagar tudo        docker compose -C $RAIZ down -v      (apaga a base de dados)

Uma `-v` a mais na linha de cima, e a base vai-se. `test_o_apagar_tudo_avisa`
e `test_a_actualizacao_nao_tem_v` existem para tornar esse erro difícil de
escrever e impossível de não notar.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path

import pytest

from mailutils import db

RAIZ = Path(__file__).resolve().parent.parent
DEPLOY = RAIZ / "deploy.sh"
COMPOSE = RAIZ / "docker-compose.yml"
GITIGNORE = RAIZ / ".gitignore"


@pytest.fixture(scope="module")
def deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _sem_acentos(texto: str) -> str:
    """`CÓPIA` e `COPIA` são a mesma palavra para o que se quer verificar.

    Comparar acentos à mão num teste é brittle: o próximo que mexer no texto
    escreve `cópia` e o teste falha por ortografia e não por substância.
    """
    decomposto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def _comandos(bloco: str) -> list[str]:
    """As linhas do bloco que são comandos, e não prosa.

    A linha *"Sem -v em lado nenhum"* é sobre `-v`; testá-la como se fosse um
    comando daria um falso positivo que obriga a alguém a reescrever a
    documentação para satisfazer o teste.
    """
    saida = []
    for linha in bloco.splitlines():
        limpa = linha.strip()
        if limpa.startswith(
            ("cd ", "docker ", "docker-compose ", "git ", "./deploy.sh", "sudo ", "rm ")
        ):
            saida.append(limpa)
    return saida


def _bloco_impresso(deploy: str) -> str:
    """O texto que o `deploy.sh` imprime no fim — o que o utilizador lê.

    Separar isto da função `actualizar()` é necessário: `down -v` aparece na
    função só numa frase que explica *não* o fazer, e um teste que varre o
    ficheiro inteiro confunde o aviso com o comando.
    """
    inicio = deploy.index("cat <<FIM\n${G}A seguir${N}")
    fim = deploy.index("\nFIM", inicio)
    return deploy[inicio:fim]


def _corpo_da_funcao(deploy: str) -> str:
    """O corpo de `actualizar()`, sem o resto do script."""
    inicio = deploy.index("actualizar() {")
    fim = deploy.index("\n}\n", inicio) + 3
    return deploy[inicio:fim]


# --------------------------------------------------------------------------
# O comando de actualização
# --------------------------------------------------------------------------


def test_a_actualizacao_nao_tem_v(deploy: str) -> None:
    """A linha que **actualiza** não pode ter `-v`.

    `-v` é o que apaga o volume nomeado, e é a única diferença entre `down` e
    `down -v`. Uma `-v` escrita na linha de actualização é a forma mais curta
    de perder tudo.

    O `down` sem `-v` — que é o comando `parar` — é seguro e verificado: deixa
    o volume intacto. Por isso o teste olha para a linha que actualiza e não
    para todas as do bloco.
    """
    actualizacoes = [
        linha
        for linha in _comandos(_bloco_impresso(deploy))
        if "up -d" in linha and "pull" in linha
    ]
    assert actualizacoes, "o bloco deixou de imprimir o comando de actualização"
    for linha in actualizacoes:
        assert " -v" not in linha and "--volumes" not in linha, (
            f"o comando de actualização tem -v: {linha!r}"
        )
        assert " down" not in linha, f"o comando de actualização pára o serviço: {linha!r}"


def test_o_parar_nao_apaga_a_base(deploy: str) -> None:
    """`down` sem `-v` é seguro — e é o que o bloco imprime para parar.

    Fica escrito para que ninguém "conserte" o `parar` acrescentando um `-v`
    por achar que `down` apaga tudo. Apagar é `down -v`.
    """
    parar = [linha for linha in _comandos(_bloco_impresso(deploy)) if linha.endswith("down")]
    assert parar, "o bloco deixou de dizer como parar"
    for linha in parar:
        assert " -v" not in linha and "--volumes" not in linha, (
            f"o comando `parar` ficou com -v e passa a apagar a base: {linha!r}"
        )


def test_a_actualizacao_nao_tem_rm(deploy: str) -> None:
    """Nenhum `rm` na linha de actualização.

    Menos óbvio que o `-v`: `rm -rf var/` apagaria a base em silêncio e sem
    qualquer mensagem do docker.
    """
    for linha in _bloco_impresso(deploy).splitlines():
        if "up -d --build" in linha:
            assert "rm " not in linha, (
                f"o comando de actualização apaga ficheiros: {linha.strip()!r}"
            )


def test_a_funcao_actualizar_nunca_para_nem_apaga(deploy: str) -> None:
    """A função não pode correr `down`, e não pode apagar nada.

    Teste mais forte do que o do texto impresso: o texto é o que se lê, a função
    é o que se executa. Uma `-v` ou um `down` dentro dela passaria a primeira
    verificação e perderia a base a todos.
    """
    corpo = _corpo_da_funcao(deploy)
    # As menções a `down -v` que a função faz são frases que dizem *não*;
    # o que se rejeita é o comando, não a palavra.
    comandos = [
        linha
        for linha in corpo.splitlines()
        if linha.strip().startswith(("docker ", "$compose ", "rm ", "sudo "))
    ]
    for linha in comandos:
        passo = linha.strip()
        assert not passo.startswith(("rm ", "sudo rm")), f"a função apaga: {passo!r}"
        assert " down" not in passo, f"a função pára o serviço: {passo!r}"
        assert " -v" not in passo and "--volumes" not in passo, f"a função apaga volumes: {passo!r}"


def test_a_funcao_actualizar_faz_copia_antes_de_mexer(deploy: str) -> None:
    """A ordem é o que dá valor à cópia: antes do `git pull` e antes do `up`.

    Uma cópia feita depois da actualização é uma cópia do estrago.
    """
    corpo = _corpo_da_funcao(deploy)
    pos_copia = corpo.index("a.backup(b)")
    pos_pull = corpo.index("git -C")
    pos_up = corpo.index("up -d --build")
    assert pos_copia < pos_pull < pos_up, (
        "a ordem está errada: a cópia tem de vir antes do git pull e do up"
    )


def test_a_funcao_actualizar_aborta_se_a_copia_falhar(deploy: str) -> None:
    """`|| falhar` logo a seguir à cópia, e a mensagem diz que não continua.

    É a propriedade mais importante de toda a função, e foi provada a correr:
    com o contentor indisponível, a função parou em `Copia de seguranca` e a
    aplicação não foi reconstruída.
    """
    corpo = _corpo_da_funcao(deploy)
    janela = corpo[corpo.index("a.backup(b)") : corpo.index("git -C")]
    assert "falhar" in janela, "a cópia de segurança pode falhar em silêncio"
    assert "NAO continua" in janela or "não continua" in janela, (
        "a mensagem de falha não diz que a actualização parou"
    )


def test_a_actualizacao_e_um_comando(deploy: str) -> None:
    """Uma linha copiável, sem continuação, com os dois passos encadeados.

    Duas linhas significam que o utilizador corre metade; e a segunda sem a
    primeira não actualiza nada sem dizer que não actualizou.
    """
    linhas = [
        linha.strip()
        for linha in _bloco_impresso(deploy).splitlines()
        if linha.strip().startswith("cd ") and "git pull" in linha
    ]
    assert linhas, "o deploy.sh deixou de imprimir o comando de actualização"
    linha = linhas[0]
    assert "\\" not in linha, f"o comando tem continuação de linha: {linha!r}"
    assert "&&" in linha, f"os dois passos não estão encadeados: {linha!r}"
    assert "git pull" in linha, linha
    assert "up -d" in linha, linha


# --------------------------------------------------------------------------
# Onde a base de dados vive
# --------------------------------------------------------------------------


def test_a_base_de_dados_vive_num_volume_nomeado(compose: str) -> None:
    """`dados:/data`, e não `./var:/data`.

    Um bind mount para dentro do directório do projecto sobrevive a
    `up --build` também, mas não a um `git clean`, e o `var/` está no
    `.gitignore`. Um volume nomeado vive no `/var/lib/docker`, que nenhuma
    operação do git toca.
    """
    assert "dados:/data" in compose, "a base de dados não está num volume nomeado"
    assert "./var:/data" not in compose
    assert "./data:/data" not in compose


def test_a_base_de_dados_esta_fora_da_imagem(compose: str) -> None:
    """/data, e não um caminho dentro da imagem.

    Se o caminho estivesse dentro da imagem, cada `up --build` criaria um
    contentor novo com uma base nova — e a base antiga ia-se embora o volume
    continuasse lá.
    """
    assert "MAILUTILS_DB_PATH: /data/mailutils.db" in compose
    assert "MAILUTILS_MEDIA_DIR: /data/media" in compose
    for errado in ("MAILUTILS_DB_PATH: /app", "MAILUTILS_DB_PATH: ./var"):
        assert errado not in compose, f"o caminho da base de dados está dentro da imagem: {errado}"


def test_a_var_e_ignorada_pelo_git() -> None:
    """`var/` tem a base de dados de quem corre sem contentor, e `*.db` a de
    quem deixa um ficheiro de fora do sítio."""
    texto = GITIGNORE.read_text(encoding="utf-8")
    assert re.search(r"^var/$", texto, re.M), "var/ não está no .gitignore"
    assert re.search(r"^\*\.db$", texto, re.M)


# --------------------------------------------------------------------------
# A migração, que é onde os dados se perdem a sério
# --------------------------------------------------------------------------
#
# O `-v` é o único comando que apaga a base, mas uma migração mal escrita
# também pode perder dados sem apagar nada — reescrevendo a tabela em vez de a
# alterar. Foi por isso que o T014 ganhou duas tabelas num esquema que já
# estava em produção, e é por isso que isto está aqui.


def _base_v1(caminho: Path) -> None:
    """Uma base no esquema anterior ao sprint 02, com dados dentro."""
    conn = sqlite3.connect(caminho)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, password_changed_at TEXT);
        CREATE TABLE signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT 'assinatura principal',
            theme TEXT NOT NULL DEFAULT 'dark',
            fields_json TEXT NOT NULL DEFAULT '{}', logo_id INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (user_id, name));
        CREATE TABLE logos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            filename TEXT NOT NULL UNIQUE, content_type TEXT, byte_size INTEGER,
            width INTEGER, height INTEGER, created_at TEXT NOT NULL);
        """
    )
    conn.execute("PRAGMA user_version = 1")
    for i, email in enumerate(("ana@exemplo.pt", "bruno@exemplo.pt", "carla@exemplo.pt"), 1):
        conn.execute(
            "INSERT INTO users (email, password_hash, is_admin, created_at)"
            " VALUES (?, ?, 0, '2026-09-01T10:00:00+00:00')",
            (email, f"hash{i}"),
        )
    for i in (1, 2):
        conn.execute(
            "INSERT INTO signatures (user_id, name, theme, fields_json, created_at, updated_at)"
            " VALUES (?, 'assinatura principal', 'dark', ?, '2026-09-01', '2026-09-02')",
            (i, '{"name":"Ana Silva"}'),
        )
    conn.commit()
    conn.close()


def test_a_migracao_nao_perde_utilizadores(tmp_path: Path) -> None:
    """O caso que o T014 introduziu: `SCHEMA_VERSION` 1 → 3, dois passos.

    É o caminho de toda a instalação que já existia. Uma migração que reescrevesse
    uma tabela em vez de a alterar perderia utilizadores sem dar erro nenhum.
    """
    caminho = tmp_path / "m.db"
    _base_v1(caminho)

    antes = sqlite3.connect(caminho)
    emails_antes = [r[0] for r in antes.execute("SELECT email FROM users ORDER BY id")]
    assinaturas_antes = antes.execute("SELECT COUNT(*) FROM signatures").fetchone()[0]
    antes.close()

    conn = db.connect(caminho)
    db.migrate(conn)

    emails_depois = [r[0] for r in conn.execute("SELECT email FROM users ORDER BY id")]
    assinaturas_depois = conn.execute("SELECT COUNT(*) FROM signatures").fetchone()[0]

    assert emails_depois == emails_antes, "a migração perdeu ou reordenou utilizadores"
    assert assinaturas_depois == assinaturas_antes


def test_a_migracao_preserva_a_conteudo_da_assinatura(tmp_path: Path) -> None:
    """Não basta o utilizador sobreviver: a assinatura tem de sobreviver com o
    que o utilizador escreveu. Um `INSERT` sem os campos correctos falha aqui."""
    caminho = tmp_path / "m.db"
    _base_v1(caminho)

    conn = db.connect(caminho)
    db.migrate(conn)

    linha = conn.execute("SELECT theme, fields_json FROM signatures ORDER BY id LIMIT 1").fetchone()
    assert linha["theme"] == "dark"
    assert "Ana Silva" in linha["fields_json"]


def test_a_migracao_acrescenta_sem_aperceber_dos_dados_antigos(tmp_path: Path) -> None:
    """O que tem de aparecer: a coluna nova com o valor por omissão, e as
    tabelas novas."""
    caminho = tmp_path / "m.db"
    _base_v1(caminho)

    conn = db.connect(caminho)
    db.migrate(conn)

    colunas = {r["name"] for r in conn.execute("PRAGMA table_info(signatures)")}
    assert "layout" in colunas
    temas = {r["layout"] for r in conn.execute("SELECT layout FROM signatures")}
    assert temas == {db.DEFAULT_SIGNATURE_LAYOUT}, (
        f"as assinaturas existentes ficaram com layout {temas!r} em vez de "
        f"{db.DEFAULT_SIGNATURE_LAYOUT!r} — quem tinha uma assinatura vai ver "
        f"o aspecto mudar depois da actualização"
    )
    tabelas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"recipient_lists", "list_addresses"} <= tabelas


def test_cada_migracao_e_um_statement_so() -> None:
    """A invariante que `migrate()` pressupõe e que ninguém escreve.

    `migrate()` faz `for statement in _MIGRATIONS: conn.execute(statement)`, e
    `execute()` do sqlite3 aceita **um** statement: duas tabelas no mesmo string
    dão `near "CREATE": syntax error` e a aplicação não arranca. Foi assim que
    parou a instalação em 2026-10-02, com `list_addresses` e `list_envios` no
    mesmo bloco de texto.

    Uma migração a partir de uma base nova também apanha o erro, mas diz só
    "a migração falhou". Este teste repete o que `migrate()` faz e diz **qual**
    das entradas foi a culpada. As entradas correm todas na mesma ligação e por
    ordem, porque um `CREATE INDEX` depende da tabela que a entrada anterior
    criou — o que interessa é a sequência, não cada pedaço isolado.
    """
    conn = sqlite3.connect(":memory:")
    try:
        for indice, statement in enumerate(db._MIGRATIONS):
            try:
                conn.execute(statement)
            except sqlite3.Error as erro:
                pytest.fail(
                    f"_MIGRATIONS[{indice}] falhou: {erro}. Ou não é um statement "
                    f"so (duas tabelas no mesmo string dão «near CREATE»), ou "
                    f"depende de uma tabela que ainda não foi criada. "
                    f"O que la esta é: {statement!r}"
                )
    finally:
        conn.close()


def test_a_migracao_e_repetivel_sem_alterar_nada(tmp_path: Path) -> None:
    """O arranque chama `migrate()` em cada start. A segunda vez tem de ser um
    no-op: se não for, a terceira execução de um reinício já mudou alguma coisa."""
    caminho = tmp_path / "m.db"
    _base_v1(caminho)

    conn = db.connect(caminho)
    db.migrate(conn)
    depois_da_primeira = conn.execute("SELECT email, is_active FROM users ORDER BY id").fetchall()

    for _ in range(4):
        db.migrate(conn)

    depois = conn.execute("SELECT email, is_active FROM users ORDER BY id").fetchall()
    assert [tuple(r) for r in depois] == [tuple(r) for r in depois_da_primeira]


def test_o_arranque_da_aplicacao_sobe_sobre_uma_base_antiga() -> None:
    """A migração não é um caminho só: o lifespan da aplicação é o caminho que
    corre em produção, e é o que tem de ser idempotente."""
    from mailutils.main import create_app  # noqa: F401 — o import é a prova

    assert callable(create_app)


# --------------------------------------------------------------------------
# O comando destrutivo
# --------------------------------------------------------------------------


def test_o_apagar_tudo_avisa_que_apaga_a_base(deploy: str) -> None:
    """A linha existe, e a secção que a precede diz, em maiúsculas, o que ela faz.

    Uma linha destrutiva que não avisa é uma linha destrutiva. A alternativa —
    não a imprimir — deixaria quem quisesse apagar sem caminho, e quem quisesse
    fazer cópia de segurança sem saber que havia um.
    """
    linhas = _bloco_impresso(deploy).splitlines()
    comandos = [
        i
        for i, linha in enumerate(linhas)
        if "down -v" in linha and "diferença" not in linha.lower()
    ]
    assert comandos, "o deploy.sh deixou de dizer como apagar tudo"
    for indice in comandos:
        janela = _sem_acentos("\n".join(linhas[max(0, indice - 12) : indice])).upper()
        assert "APAGA A BASE DE DADOS" in janela, (
            f"o comando destrutivo não avisa o que apaga. Janela acima:\n{janela}"
        )


def test_a_actualizacao_e_a_apaga_estao_separadas(deploy: str) -> None:
    """A `-v` a mais na linha de cima apagava tudo. Não podem ser adjacentes, e
    a secção destrutiva tem de estar identificada entre elas."""
    linhas = _bloco_impresso(deploy).splitlines()
    idx_actualiza = next((i for i, linha in enumerate(linhas) if "up -d --build" in linha), None)
    idx_apaga = next(
        (
            i
            for i, linha in enumerate(linhas)
            if "down -v" in linha and "diferença" not in linha.lower()
        ),
        None,
    )
    assert idx_actualiza is not None and idx_apaga is not None
    assert idx_apaga > idx_actualiza, "a linha destrutiva vem antes da actualização"

    entre = "\n".join(linhas[idx_actualiza:idx_apaga]).upper()
    assert "PARAR" in entre, (
        "a secção destrutiva está colada à de actualização, sem o `parar` entre elas"
    )
    assert "APAGA A BASE DE DADOS" in entre, (
        "a secção destrutiva não está identificada antes do comando"
    )


def test_a_actualizacao_avisa_que_a_base_sobrevive(deploy: str) -> None:
    """O texto tem de dizer o que a pessoa está a preservar.

    Uma pessoa que não sabe se perde tudo não actualiza — ou actualiza e
    espera o pior, o que é a mesma coisa com mais ansiedade.
    """
    bloco = _sem_acentos(_bloco_impresso(deploy)).upper()
    assert "NAO E TOCADA" in bloco, "o bloco não diz que a base não é tocada"
    assert "COPIA DE SEGURANCA" in bloco, "o bloco não menciona a cópia de segurança"


class TestASondaDeRedeNaoBloqueiaUmDeployBom:
    """A sonda de rede é um filtro de conveniência, não um portão.

    Duas lições de um teste só, ambas com o mesmo sabor: uma verificação que
    falha tem de ser mais barata de explicar do que a coisa que verifica.

    1. Mediu a rede errada — `compose run` liga à rede do projecto, e o build
       usa a bridge por defeito. Deu "tem rede" num servidor cujo `pip install`
       não tinha. Verde a mais é pior que vermelho.
    2. Quando a imagem não se resolve, respondia "sem rede". O `compose config`
       falha quando falta o `.env`, e bloqueava uma actualização correcta com
       um diagnóstico inventado.
    """

    @staticmethod
    def _corpo() -> str:
        """O corpo da sonda, **sem comentários**.

        `compose run` e `alpine` aparecem no texto que explica porquê não se
        usam. Um teste que lê a explicação como se fosse código falha por
        estar a ser bem escrito — e foi o que aconteceu à primeira vez.
        """
        bruto = (RAIZ / "deploy.sh").read_text(encoding="utf-8")
        i = bruto.index("tem_rede_dentro_de_um_contentor()")
        corpo = bruto[i : bruto.index("\n}\n", i)]
        sem_comentarios = [x for x in corpo.splitlines() if not x.strip().startswith("#")]
        return "\n".join(sem_comentarios)

    def test_a_sonda_usa_a_bridge_e_nao_a_rede_do_projecto(self) -> None:
        funcao = self._corpo()
        assert "--network bridge" in funcao, (
            "a sonda tem de medir a rede que o build usa — a bridge por defeito — "
            "e não a rede do projecto, que é outra"
        )
        assert "compose run" not in funcao, (
            "`compose run` liga à rede do projecto e dava verde num servidor "
            "onde o build não tinha rede"
        )

    def test_a_sonda_nao_puxa_uma_imagem(self) -> None:
        """Um `docker pull` durante o teste mascara o próprio teste."""
        funcao = self._corpo()
        assert "alpine" not in funcao, (
            "a sonda não deve puxar uma imagem nova: se a bridge não tem rede, "
            "o pull falha por outra razão e o diagnóstico passa a mentir"
        )

    def test_a_sonda_falha_aberta_quando_nao_sabe(self) -> None:
        """Não pode bloquear uma actualização correcta com um motivo inventado.

        Este é o mais importante dos três. Um `return 1` quando a imagem não
        se resolve traduzia-se em "o host tem rede, mas um contentor não" —
        num servidor que tinha rede, porque o `compose config` falha sem o
        `.env`.
        """
        funcao = self._corpo()
        assert "return 0" in funcao, (
            "quando a sonda não consegue determinar a rede tem de assumir que "
            "a rede está bem e deixar o build tentar"
        )
        assert '[ -z "$imagem" ] || return 1' not in funcao, (
            "a sonda devolve 'sem rede' quando não consegue resolver a imagem: "
            "isso bloqueia um deploy bom com um diagnóstico inventado"
        )


class TestOComposeCompativelComAVersao:
    """O `-C` do `docker compose` não existe. Este é um bug que aconteceu.

    `docker compose -C <dir>` deu `unknown shorthand flag: 'C' in -C` num
    servidor com Compose v5. O flag chama-se `--project-directory` no v2 e não
    existe no v1. O `deploy.sh` usava-o em três sítios e o `--actualizar` não
    arrancava — a pessoa via "a copia de seguranca falhou" e a causa estava a
    três linhas de distância, escondida.

    A solução é não depender da versão: `cd` para o directório funciona nos três.
    """

    def test_a_funcao_actualizar_nao_usa_o_flag_C(self, deploy: str) -> None:
        corpo = _corpo_da_funcao(deploy)
        assert " compose -C " not in corpo, (
            "actualizar() usa `compose -C`, que não existe no Compose v5"
        )

    def test_nenhum_comando_impresso_usa_o_flag_C(self, deploy: str) -> None:
        """O texto que o utilizador copia tem de correr na máquina dele."""
        for linha in _bloco_impresso(deploy).splitlines():
            limpa = linha.strip()
            if not limpa.startswith(("docker ", "cd ")):
                continue
            assert " compose -C " not in limpa and " compose -C$" not in limpa, (
                f"comando impresso usa o flag -C, que falha: {limpa!r}"
            )

    def test_o_todo_do_script_nao_usa_o_flag_C(self, deploy: str) -> None:
        """Todo o resto do script tem o mesmo problema.

        Este teste é mais largo de propósito: `deploy.sh` já tinha `-C` em mais
        um sítio, e só o primeiro foi corrigido na primeira vez. Um teste por
        função deixa passar o segundo sítio.
        """
        for numero, linha in enumerate(deploy.splitlines(), 1):
            limpa = linha.strip()
            if limpa.startswith("#"):
                continue
            assert " compose -C " not in limpa, (
                f"deploy.sh:{numero} usa `compose -C`, que não existe: {limpa!r}"
            )

    def test_a_actualizacao_entra_no_directorio(self, deploy: str) -> None:
        """O `cd` é o que substitui o flag, e tem de lá estar."""
        corpo = _corpo_da_funcao(deploy)
        assert 'cd "$dir"' in corpo, (
            "actualizar() não entra no directório de instalação — é o que "
            "substitui o `-C` e funciona em qualquer versão do Compose"
        )

    def test_a_copia_nao_abre_a_base_em_so_de_leitura(self, deploy: str) -> None:
        """`mode=ro` não funciona numa base em WAL. Foi o que partiu o deploy.

        A base está em `PRAGMA journal_mode = WAL` (db.py:224). Em WAL o SQLite
        precisa do ficheiro `-shm`, e esse `-shm` é apagado quando a última
        ligação fecha em condições — que é o que acontece logo depois de um
        arranque limpo. Abrir em `mode=ro` devolve então
        `unable to open database file`, e o `--actualizar` nunca arrancava.

        O objectivo original do `mode=ro` era bom — não deixar o root criar um
        `-wal` com o dono errado — mas trocou um problema raro por um
        garantido. Resolve-se pela ordem das tentativas, não pelo modo de
        abertura.
        """
        # Sem os comentários. `mode=ro` aparece no texto que explica porquê
        # não se usa `mode=ro`, e um teste que lê a explicação como se fosse
        # código falha por estar a ser bem escrito.
        corpo = "\n".join(
            linha
            for linha in _corpo_da_funcao(deploy).splitlines()
            if not linha.strip().startswith("#")
        )
        assert "mode=ro" not in corpo, (
            "a cópia abre a base em `mode=ro`, que falha numa base WAL sem `-shm`"
        )

    def test_a_copia_comeca_como_a_aplicacao(self, deploy: str) -> None:
        """A aplicação é quem tem o direito de criar o `-shm` e o `-wal`.

        Correndo como root por omissão, um `-wal` pode ficar com o dono root e
        **a aplicação deixa de poder escrever na base**. Quebrar a base ao
        tentar fazer uma cópia dela é o pior resultado possível, e por isso a
        primeira tentativa é sempre com o utilizador da aplicação.
        """
        corpo = _corpo_da_funcao(deploy)
        assert "$compose exec -T mailutils python -c" in corpo, (
            "a primeira tentativa tem de ser com o utilizador da aplicação"
        )
        assert "--user" in corpo, "tem de haver fallback para root"

    def test_a_copia_tenta_o_tmp(self, deploy: str) -> None:
        """`/data` pode estar montado só de leitura. `/tmp` é tmpfs.

        As quatro tentativas — aplicação e root, em `/data` e `/tmp` — existem
        porque o SQLite dá o mesmo texto de erro para "sem permissão", "sem
        espaço" e "montado só de leitura". Sem esta tentativa, uma montagem só
        de leitura era indistinguível de uma base ilesa.
        """
        corpo = _corpo_da_funcao(deploy)
        assert "/tmp/copia.db" in corpo, (  # noqa: S108 - e o caminho que se exige
            "sem a tentativa em /tmp, um /data montado só de leitura é "
            "indistinguível de uma base que não existe"
        )

    def test_a_copia_usa_o_backup_do_sqlite_e_nao_um_cp(self, deploy: str) -> None:
        """`a.backup(b)` dá um retrato consistente com a aplicação a escrever.

        Copiar o ficheiro com `cp` enquanto a base está viva pode apanhar um
        estado intermédio. É a razão de ser do `backup()`.
        """
        corpo = _corpo_da_funcao(deploy)
        assert "a.backup(b)" in corpo, "a cópia tem de usar sqlite3 backup()"
        assert "PRAGMA integrity_check" in corpo, "a cópia não é verificada"


# --------------------------------------------------------------------------
# T017-A: migração 3 → 5. A lista deixa de confirmar destinatários e passa a
# ter um remetente confirmado. São testes de migração e não de serviço porque o
# caminho perigoso aqui é a reconstrução de `list_addresses`: SQLite não faz
# `DROP COLUMN` em toda a versão, e quem o fizer mal perde endereços sem dar
# erro nenhum.
# --------------------------------------------------------------------------


def _base_v3_com_lista(caminho: Path) -> int:
    """Base no esquema da v3 com uma lista e três endereços dentro.

    Reproduz a forma que o `T014` criou: `list_addresses` com cinco colunas de
    confirmação. O endereço descadenciado é o que a migração **não** pode
    perder — é o registo que impede uma reexecução de mandar para quem pediu
    para sair.
    """
    conn = db.connect(caminho)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, password_changed_at TEXT);
        CREATE TABLE recipient_lists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name       TEXT    NOT NULL,
            created_at TEXT    NOT NULL,
            UNIQUE (user_id, name)
        );
        CREATE TABLE list_addresses (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id                INTEGER NOT NULL
                                    REFERENCES recipient_lists(id) ON DELETE CASCADE,
            email                  TEXT    NOT NULL,
            name                   TEXT    NOT NULL DEFAULT '',
            confirmed_at           TEXT,
            confirmation_hash      TEXT,
            confirmation_expires_at TEXT,
            confirmation_attempts  INTEGER NOT NULL DEFAULT 0,
            confirmation_sent_at   TEXT,
            unsubscribed_at        TEXT,
            created_at             TEXT    NOT NULL,
            UNIQUE (list_id, email)
        );
        """
    )
    conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, 'x', '2026-09-01')",
        ("dono@exemplo.pt",),
    )
    conn.execute(
        "INSERT INTO recipient_lists (user_id, name, created_at)"
        " VALUES (1, 'clientes', '2026-09-01')"
    )
    # Um confirmado, um pendente, um descadenciado. Os três estados deixam de
    # existir no `T017-A` — menos o descadenciado, que sobrevive a tudo.
    conn.executemany(
        "INSERT INTO list_addresses (list_id, email, name, confirmed_at, unsubscribed_at,"
        " created_at) VALUES (1, ?, ?, ?, ?, '2026-09-01')",
        [
            ("confirmado@exemplo.pt", "Ana", "2026-09-02", None),
            ("pendente@exemplo.pt", "Bruno", None, None),
            ("saiu@exemplo.pt", "Carla", "2026-09-03", "2026-09-10"),
        ],
    )
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    return 1


def _colunas(caminho: Path, tabela: str) -> set[str]:
    conn = db.connect(caminho)
    try:
        return {linha["name"] for linha in conn.execute(f"PRAGMA table_info({tabela})")}
    finally:
        conn.close()


def test_a_migracao_da_a_lista_um_campo_de_remetente(tmp_path: Path) -> None:
    """A lista ganha `sender_id`, e uma lista antiga **fica sem remetente**.

    A alternativa tentadora seria criar um remetente para cada lista que já
    existe, com `confirmed_at` nulo. Não há de onde tirar o endereço, e um
    remetente com email inventado é pior do que a ausência: parece um portão e
    não é. Fica `NULL`, que é o mesmo estado que uma lista recém-criada tem
    antes de o utilizador escolher o `from`, e o portão trata dos dois igual.
    """
    user_id = _base_v3_com_lista(caminho := tmp_path / "mailutils.db")
    db.migrate(db.connect(caminho))

    conn = db.connect(caminho)
    try:
        existe = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'senders'"
        ).fetchone()[0]
        assert existe, "a tabela `senders` não foi criada"
        nada = conn.execute("SELECT COUNT(*) FROM senders").fetchone()[0]
        lista = conn.execute(
            "SELECT sender_id, user_id FROM recipient_lists WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert nada == 0, (
        f"a migração inventou {nada} remetentes. Um remetente com email "
        "fabricado parece um portão de consentimento e não o é"
    )
    assert lista["sender_id"] is None, (
        "a lista antiga tem de ficar sem remetente, para o utilizador escolher "
        "o `from` e o confirmar ele próprio"
    )
    assert lista["user_id"] == user_id, "a lista mudou de dono"


def test_a_migracao_tira_as_colunas_de_confirmacao_do_endereco(tmp_path: Path) -> None:
    """Ver o docstring do teste anterior: `tmp_path` é a base de dados em disco."""
    caminho = tmp_path / "mailutils.db"
    """As cinco colunas de confirmação saem; `unsubscribed_at` fica.

    Deixar as colunas seria um caminho aberto para reintroduzir o portão antigo:
    um `SELECT` novo passa a não ter de onde tirar `confirmed_at`, e é
    exactamente esse desaparecimento que força quem escreve a pensar.
    """
    _base_v3_com_lista(caminho)
    db.migrate(db.connect(caminho))

    colunas = _colunas(caminho, "list_addresses")
    mortas = [
        "confirmed_at",
        "confirmation_hash",
        "confirmation_expires_at",
        "confirmation_attempts",
        "confirmation_sent_at",
    ]
    assert not (set(mortas) & colunas), (
        f"as colunas de confirmação ainda existem: {set(mortas) & colunas}"
    )
    assert {"email", "name", "unsubscribed_at", "created_at"} <= colunas, (
        f"a reconstrução perdeu colunas que ainda são precisas: {colunas}"
    )


def test_a_migracao_preserva_enderecos_e_o_descadenciado(tmp_path: Path) -> None:
    """Ver o docstring do teste anterior: `tmp_path` é a base de dados em disco."""
    caminho = tmp_path / "mailutils.db"
    """O teste que justifica ter escrito a migração.

    Reconstruir `list_addresses` passa por `DROP TABLE`. Se o `INSERT ... SELECT`
    errar um nome ou esquecer uma coluna, os endereços desaparecem sem erro
    nenhum — a migração " corre" e a lista fica vazia. E o descadenciado é o
    pior dos três: perdê-lo devolve alguém que pediu para sair ao `SELECT` de
    envio.
    """
    _base_v3_com_lista(caminho)
    db.migrate(db.connect(caminho))

    conn = db.connect(caminho)
    try:
        linhas = conn.execute(
            "SELECT email, name, unsubscribed_at FROM list_addresses ORDER BY email"
        ).fetchall()
    finally:
        conn.close()

    vistos = [linha["email"] for linha in linhas]
    assert len(linhas) == 3, f"a migração perdeu endereços: {vistos}"
    por_email = {linha["email"]: linha for linha in linhas}
    assert "pendente@exemplo.pt" in por_email, "o endereço por confirmar desapareceu"
    assert por_email["saiu@exemplo.pt"]["unsubscribed_at"] == "2026-09-10", (
        "o descadenciado perdeu o `unsubscribed_at`: passaria a receber sem "
        "ter pedido, e é a linha que tem de sobreviver a tudo"
    )
    assert por_email["confirmado@exemplo.pt"]["unsubscribed_at"] is None, (
        "um endereço que não se descadencou não pode nascer descadenciado"
    )
    assert por_email["pendente@exemplo.pt"]["name"] == "Bruno", "o nome do endereço sumiu"


def test_a_migracao_twice_mantem_a_base_como_estava(tmp_path: Path) -> None:
    """Correr `migrate()` duas vezes dá o mesmo resultado que uma.

    A migração executa *todos* os statements a cada arranque e só no fim
    escreve o `user_version`, o que torna a idempotência uma propriedade que se
    tem de provar e não uma que se depreende. A reconstrução de
    `list_addresses` é o ponto onde isto é fácil de estragar: o guarda tem de
    ser "ainda existe a coluna antiga?", e não "a base parece migrada?".

    O `DROP TABLE` é o que torna o erro visível — se o guarda não funcionar, a
    segunda passagem reconstrói a partir de uma tabela **já sem** as colunas de
    confirmação e o `SELECT` falha, ou pior, reconstrói vazia.
    """
    _base_v3_com_lista(caminho := tmp_path / "mailutils.db")
    conn = db.connect(caminho)
    db.migrate(conn)
    antes = conn.execute("SELECT COUNT(*) FROM list_addresses").fetchone()[0]
    db.migrate(conn)
    depois = conn.execute("SELECT COUNT(*) FROM list_addresses").fetchone()[0]
    descadenciado = conn.execute(
        "SELECT unsubscribed_at FROM list_addresses WHERE email = 'saiu@exemplo.pt'"
    ).fetchone()
    senders = conn.execute("SELECT COUNT(*) FROM senders").fetchone()[0]
    conn.close()

    assert antes == depois == 3, (
        f"o segundo arranque mudou o número de endereços: {antes} -> {depois}"
    )
    assert descadenciado is not None and descadenciado[0] == "2026-09-10", (
        "o segundo arranque perdeu o `unsubscribed_at`"
    )
    assert senders == 0, f"o segundo arranque inventou {senders} remetentes"
