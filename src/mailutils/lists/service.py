"""Listas de destinatários: criação, importação, confirmação por código.

A regra deste módulo cabe numa linha e está repetida em três sítios de propósito:

    **Um endereço só entra num envio depois de `confirmed_at` estar preenchido.**

`destinatarios()` é a **única função do projecto que devolve endereços para
envio**, e o seu `SELECT` exige `confirmed_at IS NOT NULL`. Não há segunda
consulta, não há atalho para um envio agendado, não há caminho rápido. Um
endereço por confirmar é um endereço que alguém escreveu e que a pessoa do outro
lado não pediu para receber — e a diferença entre "lista de contactos" e "relay
de email bombing" está exactamente nessa coluna.

O predicado aparece também noutros sítios deste ficheiro, todos **contagens
para a interface** (`listas_do_utilizador`, `pendentes_por_utilizador`). A
afirmação certa não é "aparece uma vez", que o `grep` não sustenta; é que a
única função que devolve endereços para envio tem a condição. A revisão T014
achou a diferença.

Porquê as confirmações vivem em `list_addresses` e não em `otp_codes`: a tabela
`otp_codes` tem `user_id NOT NULL` e está ligada a um dispositivo, porque é o
segundo factor de *login*. Uma confirmação de lista não tem utilizador nem
dispositivo — quem pede é o dono da lista, quem confirma é o destinatário, e ele
não tem conta nenhuma. Reusar a tabela obrigaria a inventar um `user_id` falso
e a desligar a semântica de dispositivo.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any

from .. import mailer, security, web
from ..config import Settings
from ..db import transaction

#: Linhas de CSV lidas por importação. Existe porque o `max_upload_bytes` limita
#: o ficheiro a 2 MiB e 2 MiB de emails dão ~50 mil linhas — acima do que uma
#: lista de pessoa aceita, e o `max_list_size` pararia no meio. Cortar aqui
#: evita trinta segundos de parse para depois deitar tudo fora.
MAX_CSV_ROWS = 20000

#: Tentativas de confirmar um código antes de se pedir outro. Mesmo número que
#: o segundo factor de login (FR-2.4): seis dígitos são 10^6 combinações, e um
#: código de confirmação adivinhado é um endereço inscrito sem o dono da lista
#: saber que esse endereço é de outra pessoa.
MAX_CONFIRM_ATTEMPTS = 5

#: Nomes aceites na coluna de email de um `.csv`. Um ficheiro exportado de um
#: Excel pt-PT costuma trazer `endereço` com o `ç`, daí as duas variantes.
_COLUNAS_EMAIL = ("email", "e-mail", "mail", "endereco", "endereço")
_COLUNAS_NOME = ("nome", "name")


class ErroLista(Exception):
    """Falha de negócio com mensagem para o utilizador. Nunca regista segredo."""


class ErroConfirmacao(ErroLista):
    """Falha ao pedir ou ao confirmar. A mensagem é para o utilizador."""


# --------------------------------------------------------------------- listas


def criar_lista(conn: sqlite3.Connection, user_id: int, nome: str) -> int:
    """Cria uma lista. Devolve o `id`."""
    limpo = (nome or "").strip()
    if not limpo:
        raise ErroLista("A lista precisa de um nome.")
    if len(limpo) > 80:
        raise ErroLista("O nome da lista é demasiado longo (máximo 80 caracteres).")
    agora = security.iso(security.utcnow())
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO recipient_lists (user_id, name, created_at) VALUES (?, ?, ?)",
                (user_id, limpo, agora),
            )
    except sqlite3.IntegrityError:
        raise ErroLista("Já tem uma lista com esse nome.") from None
    if cur.lastrowid is None:
        raise ErroLista("A lista não foi criada.")
    return int(cur.lastrowid)


def lista_do_utilizador(conn: sqlite3.Connection, user_id: int, list_id: int) -> sqlite3.Row | None:
    """A lista, mas **só se for dele**.

    O `user_id` no `WHERE` não é redundante com a sessão nem com o CSRF: sem
    ele, trocar o número na URL dava a qualquer pessoa a leitura de qualquer
    lista da instalação. (FR-1.7)
    """
    return conn.execute(
        "SELECT * FROM recipient_lists WHERE id = ? AND user_id = ?",
        (list_id, user_id),
    ).fetchone()


def listas_do_utilizador(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """As listas do utilizador, com as contagens.

    As contagens vêm de subconsultas no mesmo `SELECT` e não de `COUNT(*)` por
    lista: são três listas, e três round-trips ao SQLite por página seria mais
    código para o mesmo número.
    """
    return list(
        conn.execute(
            "SELECT l.*,"
            " (SELECT COUNT(*) FROM list_addresses a"
            "  WHERE a.list_id = l.id) AS total,"
            " (SELECT COUNT(*) FROM list_addresses a"
            "  WHERE a.list_id = l.id AND a.confirmed_at IS NOT NULL) AS confirmados,"
            " (SELECT COUNT(*) FROM list_addresses a"
            "  WHERE a.list_id = l.id AND a.confirmed_at IS NULL) AS pendentes"
            " FROM recipient_lists l"
            " WHERE l.user_id = ?"
            " ORDER BY l.name",
            (user_id,),
        )
    )


def lista_publico(conn: sqlite3.Connection, list_id: int) -> dict[str, Any] | None:
    """Nome e contagem de uma lista, **sem**_validação de dono.

    Para as páginas públicas de confirmação e descadência. O que não sai daqui
    são os endereços: `SELECT name, (SELECT COUNT(*)…)` e nunca a lista de
    destinatários. Quem confirma vê o nome da lista em que se está a inscrever —
    é a única coisa de que precisa — e mais nada.
    """
    linha = conn.execute(
        "SELECT id, name, ("
        "  SELECT COUNT(*) FROM list_addresses a"
        "  WHERE a.list_id = recipient_lists.id AND a.confirmed_at IS NOT NULL"
        "   AND a.unsubscribed_at IS NULL"
        " ) AS confirmados"
        " FROM recipient_lists WHERE id = ?",
        (list_id,),
    ).fetchone()
    return dict(linha) if linha else None


def eliminar_lista(conn: sqlite3.Connection, user_id: int, list_id: int) -> bool:
    """Elimina a lista e os seus endereços; as `ON DELETE CASCADE` tratam do resto.

    Um envio que aponte para esta lista fica sem destinatários, que é
    preferível a apagar envios já feitos.
    """
    with transaction(conn):
        cur = conn.execute(
            "DELETE FROM recipient_lists WHERE id = ? AND user_id = ?",
            (list_id, user_id),
        )
    return cur.rowcount > 0


# ------------------------------------------------------------------ endereços


def pendentes_por_utilizador(conn: sqlite3.Connection, user_id: int) -> int:
    """Quantas confirmações estão pendentes, em todas as listas do utilizador.

    O limite é **por utilizador** e não por lista, de propósito: quem espalhe os
    mesmos endereços por trinta listas de trinta nomes contorna um limite por
    lista sem esforço nenhum.
    """
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM list_addresses a"
            " JOIN recipient_lists l ON l.id = a.list_id"
            " WHERE l.user_id = ? AND a.confirmed_at IS NULL",
            (user_id,),
        ).fetchone()["n"]
    )


def contar_enderecos(conn: sqlite3.Connection, list_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM list_addresses WHERE list_id = ?", (list_id,)
        ).fetchone()["n"]
    )


def endereco_da_lista(
    conn: sqlite3.Connection, list_id: int, address_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM list_addresses WHERE id = ? AND list_id = ?",
        (address_id, list_id),
    ).fetchone()


def enderecos_da_lista(conn: sqlite3.Connection, list_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM list_addresses WHERE list_id = ? ORDER BY email", (list_id,))
    )


def destinatarios(conn: sqlite3.Connection, list_id: int) -> list[sqlite3.Row]:
    """**A única função do projecto que devolve destinatários para envio.**

    Três exclusões, todas deliberadas:

    - `confirmed_at IS NOT NULL` — ninguém recebe sem ter confirmado. (FR-6.2)
    - `unsubscribed_at IS NULL` — descadência é irreversível pelo produto e tem
      de sobreviver a um erro no `FROM`. (FR-6.7)
    - `confirmed_at IS NOT NULL` implica `confirmation_hash IS NULL`, porque o
      hash é apagado ao confirmar: é isso que impede um código válido de voltar
      a confirmar depois de a subscrição ter sido anulada.

    Se algum dia aparecer uma segunda função que devolva destinatários, esta
    regra passa a ser um comentário. Por isso a mutação M-18 substitui a
    condição e tem de morrer: uma lista com 5000 pendentes a receber email é o
    produto a ser a ferramenta de spam que diz não ser.
    """
    return list(
        conn.execute(
            "SELECT email, name FROM list_addresses"
            " WHERE list_id = ?"
            "   AND confirmed_at IS NOT NULL"
            "   AND unsubscribed_at IS NULL"
            " ORDER BY email",
            (list_id,),
        )
    )


def remover_endereco(conn: sqlite3.Connection, list_id: int, address_id: int) -> bool:
    with transaction(conn):
        cur = conn.execute(
            "DELETE FROM list_addresses WHERE id = ? AND list_id = ?", (address_id, list_id)
        )
    return cur.rowcount > 0


# ------------------------------------------------------------------- importação


class ResultadoImportacao:
    """O que uma importação fez: contagens, e o que não entrou.

    Os endereços rejeitados voltam **para a interface**, não para um log: quem
    importou é quem tem de saber o que não entrou. O que não se faz é carregar
    metade do ficheiro e dizer que corrija os erros.
    """

    def __init__(self) -> None:
        self.importados: int = 0
        self.ja_existentes: int = 0
        self.invalidos: list[str] = []
        #: Linhas do ficheiro que não entraram na lista, por qualquer razão.
        #:
        #: É um contador à parte porque `len(invalidos)` **não** é o mesmo
        #: número: um `break` por tecto acrescenta uma string de causa e as
        #: linhas que nunca foram examinadas não acrescentam nenhuma. Com 300
        #: linhas e um tecto de 10, a interface dizia "1 rejeitado" e 290
        #: endereços tinham desaparecido sem que ninguém soubesse. (M-07)
        self.nao_importadas: int = 0

    @property
    def total_rejeitado(self) -> int:
        """O que a interface diz. Linhas perdidas, não mensagens de erro."""
        return self.nao_importadas


def _dividir(linha: str) -> list[str]:
    """Separa os campos de uma linha, adivinhando `,` ou `;`.

    O `csv.Sniffer` existe, mas erra com frequência em ficheiros de duas
    colunas e falhava o caso mais comum. Contar o separador predominante é
    suficiente para um `.csv` de contactos, que é delimitado e não quoted.
    """
    delimitador = ";" if linha.count(";") > linha.count(",") else ","
    return next(csv.reader(io.StringIO(linha), delimiter=delimitador), [])


def _parece_cabecalho(linha: str) -> bool:
    """A primeira linha é cabeçalho?

     Duas condições, e as duas são necessárias. A primeira sozinho não serve:
     `nome@exemplo.pt` é um endereço válido cuja palavra local contém "nome", e
     um ficheiro de uma coluna assim seria tomado por cabeçalho — com a
    columna de email a ser o nome de alguém. A segunda sozinha também não: um
     CSV sem cabeçalho com um email na primeira célula seria lido como
     cabeçalho e todas as linhas seriam descartadas.
    """
    celulas = [c.strip().lower() for c in _dividir(linha)]
    tem_rotulo = any(c in _COLUNAS_EMAIL or c in _COLUNAS_NOME for c in celulas)
    tem_email = any(security.is_valid_email(c) for c in celulas)
    return tem_rotulo and not tem_email


def _indice_unico(celulas: list[str]) -> int | None:
    """Índice a usar quando a coluna do email não existe nesta linha.

    O caso é real e não é exótico: uma folha de cálculo exportada como
    `nome,email` com a coluna `nome` vazia sai do Excel com uma coluna só, e o
    ficheiro tem um cabeçalho que promete duas. Sem isto o operador vê "0
    importados" e nenhuma razão que o esclareça — o que era exactamente o que a
    revisão do T014/aboutou no relatório de importação.

    Só quando a linha tem **uma** célula. Com mais, adivinhar qual é o email é
    frequentemente errado, e é melhor recusar.
    """
    if len(celulas) == 1:
        return 0
    return None


def _indice_da_coluna(cabecalho: list[str], nomes: tuple[str, ...]) -> int | None:
    for nome in nomes:
        if nome in cabecalho:
            return cabecalho.index(nome)
    return None


def importar_csv(
    conn: sqlite3.Connection,
    user_id: int,
    list_id: int,
    conteudo: bytes,
    settings: Settings,
) -> ResultadoImportacao:
    """Lê um `.csv` de endereços. **Importar não confirma ninguém** (FR-6.5).

    Os endereços entram como pendentes e o utilizador dispara a confirmação.
    Importar 5000 endereços que confirmaram por BCC já é spam, e o produto não é
    o que faz essa parte — a distinção é o que separa esta função de um `mail
    merge`.

    Uma linha inválida é contada e listada e não aborta a importação; o
    ficheiro inteiro inválido **é** erro, porque aí não há nada a importar.
    """
    if lista_do_utilizador(conn, user_id, list_id) is None:
        raise ErroLista("Lista inexistente.")
    if not conteudo:
        raise ErroLista("O ficheiro está vazio.")

    linhas = conteudo.decode("utf-8-sig", errors="replace").splitlines()
    if not linhas:
        raise ErroLista("O ficheiro está vazio.")
    if len(linhas) > MAX_CSV_ROWS:
        raise ErroLista(f"O ficheiro tem mais de {MAX_CSV_ROWS} linhas.")

    if _parece_cabecalho(linhas[0]):
        cabecalho = [c.strip().lower() for c in _dividir(linhas[0])]
        col_email = _indice_da_coluna(cabecalho, _COLUNAS_EMAIL)
        col_nome = _indice_da_coluna(cabecalho, _COLUNAS_NOME)
        corpo = linhas[1:]
    else:
        col_email, col_nome = 0, 1
        corpo = linhas

    if col_email is None:
        raise ErroLista(
            "O ficheiro tem de ter uma coluna de email. A primeira linha tem de "
            "ser o cabeçalho, por exemplo: nome,email"
        )

    resultado = ResultadoImportacao()
    agora = security.iso(security.utcnow())
    ja_presentes = {
        row["email"]
        for row in conn.execute("SELECT email FROM list_addresses WHERE list_id = ?", (list_id,))
    }
    a_inserir: list[tuple[int, str, str, str]] = []
    ja_na_lista = contar_enderecos(conn, list_id)

    # O tecto de pendentes vive **aqui** e não em `pedir_confirmacao`.
    #
    # Verificá-lo onde o código é enviado era tarde e inútil: pedir um código a
    # um endereço que já está pendente não cria um pendente novo, pelo que o
    # contador não subiu nunca e o tecto nunca era atingido — ou então era
    # atingido à partida por uma importação e o utilizador ficava sem caminho
    # para pedir a confirmação de Addresses que ele próprio tinha acabado de
    # importar. Um tecto que bloqueia a única acção que faz o endereço ficar
    # utilizável não é um tecto, é um beco sem saída.
    ja_pendentes = pendentes_por_utilizador(conn, user_id)

    for linha in corpo:
        if not linha.strip():
            continue
        celulas = _dividir(linha)
        indice_email = col_email if col_email < len(celulas) else _indice_unico(celulas)
        if indice_email is None:
            resultado.invalidos.append(f"{linha[:60]} — sem coluna de email")
            resultado.nao_importadas += 1
            continue
        candidato = celulas[indice_email].strip()
        nome = ""
        if col_nome is not None and col_nome < len(celulas):
            nome = celulas[col_nome].strip()
        normalizado = security.normalise_email(candidato)
        if not security.is_valid_email(normalizado):
            resultado.invalidos.append(f"{candidato[:60]} — email inválido")
            resultado.nao_importadas += 1
            continue
        if normalizado in ja_presentes:
            resultado.ja_existentes += 1
            continue
        if ja_pendentes + len(a_inserir) >= settings.max_pending_confirmations:
            resultado.invalidos.append(
                f"interrompido — chegou ao teto de "
                f"{settings.max_pending_confirmations} confirmações por confirmar. "
                f"Peça os códigos aos que já lá estão e volte a importar."
            )
            break
        if ja_na_lista + len(a_inserir) >= settings.max_list_size:
            resultado.invalidos.append(
                f"interrompido — a lista chegou ao teto de {settings.max_list_size} endereços"
            )
            break
        a_inserir.append((list_id, normalizado, nome, agora))

    if a_inserir:
        with transaction(conn):
            conn.executemany(
                "INSERT OR IGNORE INTO list_addresses (list_id, email, name, created_at)"
                " VALUES (?, ?, ?, ?)",
                a_inserir,
            )
        resultado.importados = len(a_inserir)

    # As linhas nunca examinadas contam como perdidas. `ja_existentes` é uma
    # terceira categoria — a linha foi examinada e o endereço já lá estava — e
    # o operador vê isso em separado, porque "já estava na lista" não é
    # "rejeitado".
    # O total vem do ficheiro, e não de um contador de linhas lidas: o `break`
    # deixa de ler, e era exactamente por isso que a primeira versão dizia
    # "1 rejeitado" com 289 linhas que ninguém tinha lido e ninguém via.
    total_linhas = sum(1 for linha in corpo if linha.strip())
    # Cada linha lida cai numa de quatro categorias: entrou, já estava, era
    # inválida, ou nunca chegou a ser examinada porque o `break` parou antes.
    # A quinta é a única que este contador soma, e é a que a primeira versão
    # não contava — dizia "1 rejeitado" com 290 endereços em silêncio.
    nao_analisadas = (
        total_linhas - resultado.nao_importadas - resultado.ja_existentes - resultado.importados
    )
    if nao_analisadas > 0:
        resultado.nao_importadas += nao_analisadas
        resultado.invalidos.append(
            f"{nao_analisadas} linha(s) não analisadas — a importação parou por "
            f"limite. Nenhum dos endereços abaixo entrou na lista."
        )

    return resultado


# ---------------------------------------------------------------- confirmação


def _segundos_desde(iso_quando: str | None, agora) -> float | None:
    """Quantos segundos passaram. `None` se nunca, ou se o timestamp é lixo."""
    if not iso_quando:
        return None
    try:
        return (agora - security.parse_iso(iso_quando)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return None


def _nome_da_lista(conn: sqlite3.Connection, list_id: int) -> str:
    linha = conn.execute("SELECT name FROM recipient_lists WHERE id = ?", (list_id,)).fetchone()
    return linha["name"] if linha else "lista"


def pedir_confirmacao(
    conn: sqlite3.Connection,
    user_id: int,
    settings: Settings,
    list_id: int,
    address_ids: list[int],
) -> dict[str, int]:
    """Envia o código a cada endereço indicado, e só a pendentes.

    Três guardas, pela ordem em que são avaliadas:

    1. **cooldown por endereço** — procurado em *qualquer* lista do utilizador.
       Pedir cinco códigos para o mesmo endereço em cinco listas não contorna o
       cooldown, e é por isso que a procura é por email e não por `address_id`.
    2. **a lista tem de ser do utilizador** e o endereço tem de ser dela.

    O tecto de pendentes **não** é verificado aqui, e a razão está escrita em
    `importar_csv`: pedir um código não cria um pendente novo, por isso o
    contador não sobe e o tecto não era atingido — ou era atingido à partida e
    o utilizador ficava sem poder confirmar o que acabara de importar.

    O que volta são contagens. O código **nunca** entra na resposta
    (`CLAUDE.md`, `Never Do`), e o endereço só aparece no ecrã do dono da lista,
    que já o escreveu.
    """
    if lista_do_utilizador(conn, user_id, list_id) is None:
        raise ErroConfirmacao("Lista inexistente.")
    if not address_ids:
        raise ErroConfirmacao("Escolha pelo menos um endereço.")

    resultado = {"enviados": 0, "ja_confirmados": 0, "em_cooldown": 0, "excedidos": 0}
    agora = security.utcnow()
    nome_lista = _nome_da_lista(conn, list_id)

    for address_id in address_ids:
        endereco = endereco_da_lista(conn, list_id, address_id)
        if endereco is None:
            resultado["excedidos"] += 1
            continue
        if endereco["confirmed_at"] is not None:
            resultado["ja_confirmados"] += 1
            continue

        ultima = conn.execute(
            "SELECT MAX(a.confirmation_sent_at) AS ultima"
            " FROM list_addresses a"
            " JOIN recipient_lists l ON l.id = a.list_id"
            " WHERE l.user_id = ? AND a.email = ?",
            (user_id, endereco["email"]),
        ).fetchone()["ultima"]
        passado = _segundos_desde(ultima, agora)
        if passado is not None and passado < settings.confirm_cooldown_seconds:
            resultado["em_cooldown"] += 1
            continue

        codigo = security.new_otp()
        expira = security.iso(security.otp_expiry(now=agora, ttl_minutes=settings.otp_ttl_minutes))
        with transaction(conn):
            conn.execute(
                "UPDATE list_addresses"
                " SET confirmation_hash = ?, confirmation_expires_at = ?,"
                "     confirmation_sent_at = ?, confirmation_attempts = 0"
                " WHERE id = ?",
                (security.hash_otp(codigo), expira, security.iso(agora), address_id),
            )
        mailer.send_confirmation(
            settings,
            endereco["email"],
            codigo,
            nome_lista,
            link_confirmar(settings, list_id, address_id),
            link_descadenciar(settings, list_id, address_id),
        )
        resultado["enviados"] += 1

    return resultado


def link_confirmar(settings, list_id: int, address_id: int) -> str:
    """URL de confirmação da inscrição, com token assinado.

    O `address_id` **não** é lido do caminho pela rota que consome este link: vem
    de dentro do token. É essa a razão de o token existir — sem ele, `address_id`
    é um inteiro adivinhável e enumerar inteiros dá a lista inteira. (B-04)
    """
    token = web.assinar_link(
        settings, web.PURPOSE_CONFIRM, list_id, address_id, web.LINK_SALT_CONFIRM
    )
    return f"{settings.base_url()}/listas/{list_id}/confirmar/{address_id}?token={token}"


def link_descadenciar(settings, list_id: int, address_id: int) -> str:
    """URL de descadência. Mesmo esquema, propósito e prazo diferentes."""
    token = web.assinar_link(
        settings, web.PURPOSE_UNSUBSCRIBE, list_id, address_id, web.LINK_SALT_UNSUBSCRIBE
    )
    return f"{settings.base_url()}/listas/{list_id}/descadenciar/{address_id}?token={token}"


def confirmar(conn: sqlite3.Connection, list_id: int, address_id: int, codigo: str) -> sqlite3.Row:
    """Confirma um endereço com o código. Devolve a linha já confirmada.

    Um código errado **não** fica à espera: à quinta tentativa o hash é apagado e
    o endereço volta ao estado inicial. Um código que fica indefinidamente
    verificável é um código que se adivinha durante o tempo que a máquina
    estiver ligada.
    """
    endereco = endereco_da_lista(conn, list_id, address_id)
    if endereco is None:
        raise ErroConfirmacao("Endereço inexistente.")
    if endereco["confirmed_at"] is not None:
        return endereco
    if not endereco["confirmation_hash"]:
        raise ErroConfirmacao("Peça um código novo para este endereço.")

    tentativas = int(endereco["confirmation_attempts"]) + 1
    if tentativas > MAX_CONFIRM_ATTEMPTS:
        _invalidar(conn, address_id)
        raise ErroConfirmacao("Excedeu as tentativas deste código. Peça um novo.")

    if security.is_expired(endereco["confirmation_expires_at"]):
        _invalidar(conn, address_id)
        raise ErroConfirmacao("Código expirado. Peça um novo.")

    if not security.otp_matches(codigo, endereco["confirmation_hash"]):
        with transaction(conn):
            conn.execute(
                "UPDATE list_addresses SET confirmation_attempts = ? WHERE id = ?",
                (tentativas, address_id),
            )
        raise ErroConfirmacao("Código inválido.")

    with transaction(conn):
        # Apagar o hash e o prazo **é** o que torna a confirmação de uso único.
        # Sem isto o mesmo código confirmava outra vez e, pior, sobrevivia a uma
        # descadência.
        conn.execute(
            "UPDATE list_addresses"
            " SET confirmed_at = ?, confirmation_hash = NULL,"
            "     confirmation_expires_at = NULL, confirmation_attempts = 0"
            " WHERE id = ?",
            (security.iso(security.utcnow()), address_id),
        )
    confirmado = endereco_da_lista(conn, list_id, address_id)
    assert confirmado is not None  # acabou de ser actualizado
    return confirmado


def _invalidar(conn: sqlite3.Connection, address_id: int) -> None:
    """Deixa o endereço como estava antes do pedido, para um novo pedido."""
    with transaction(conn):
        conn.execute(
            "UPDATE list_addresses"
            " SET confirmation_hash = NULL, confirmation_expires_at = NULL,"
            "     confirmation_sent_at = NULL, confirmation_attempts = 0"
            " WHERE id = ?",
            (address_id,),
        )


def repor_inscricao(
    conn: sqlite3.Connection, settings: Settings, user_id: int, list_id: int, address_id: int
) -> bool:
    """Repõe uma inscrição cancelada — e **paga por isso** com um novo código.

    A primeira versão fazia `unsubscribed_at = NULL` e mais nada, e devolvia o
    endereço ao envio sem ninguém confirmar. Isto é o produto a contornar a si
    próprio: o dono da lista decide por quem se cancelou, e a decisão de voltar
    a receber é de quem cancelou, não de quem tem a lista na base de dados.

    Por isso `confirmed_at` volta a `NULL` e `confirmation_hash` é apagado: a
    próxima pessoa a pedir confirmação tem de passar pelo email outra vez. O
    dono da lista pode enviar o pedido, mas não pode confirmar por outrem.

    Devolve `True` se a reposição foi accionada. (M-01 da revisão T014.)
    """
    if lista_do_utilizador(conn, user_id, list_id) is None:
        raise ErroLista("Lista inexistente.")
    endereco = endereco_da_lista(conn, list_id, address_id)
    if endereco is None or endereco["unsubscribed_at"] is None:
        return False

    codigo = security.new_otp()
    expira = security.iso(
        security.otp_expiry(now=security.utcnow(), ttl_minutes=settings.otp_ttl_minutes)
    )
    agora = security.iso(security.utcnow())
    with transaction(conn):
        # `confirmed_at = NULL` é a linha que fecha o bypass: sem ela o
        # endereço voltava a `destinatarios()` sem ninguém pedir nada.
        conn.execute(
            "UPDATE list_addresses"
            " SET unsubscribed_at = NULL, confirmed_at = NULL,"
            "     confirmation_hash = ?, confirmation_expires_at = ?,"
            "     confirmation_sent_at = ?, confirmation_attempts = 0"
            " WHERE id = ?",
            (security.hash_otp(codigo), expira, agora, address_id),
        )
    mailer.send_confirmation(
        settings,
        endereco["email"],
        codigo,
        _nome_da_lista(conn, list_id),
        link_confirmar(settings, list_id, address_id),
        link_descadenciar(settings, list_id, address_id),
    )
    return True


def descadenciar(conn: sqlite3.Connection, address_id: int) -> bool:
    """Descadência. Quem chama é a rota pública `/descadenciar/{address_id}`.

    A verificação de token **não** está aqui, e é deliberado: `service` recebe
    um `address_id` já autorizado e a decisão é de rota, porque a verificação
    precisa do `settings` e de um prazo que é política. Se esta função validasse
    o token, a próxima rota a chamar passava a validá-lo de duas maneiras
    diferentes.

    A posse continua a ser do link: `link_descadenciar` assina o `address_id` e
    a rota recusa quando o token não bate. Sem o link esta função é perigosa de
    chamar por engano, e por isso `__all__` e as docstrings dizem que o caminho
    é o link.
    """
    with transaction(conn):
        cur = conn.execute(
            "UPDATE list_addresses SET unsubscribed_at = ?, confirmation_hash = NULL"
            " WHERE id = ? AND unsubscribed_at IS NULL",
            (security.iso(security.utcnow()), address_id),
        )
    return cur.rowcount > 0


__all__ = [
    "MAX_CONFIRM_ATTEMPTS",
    "MAX_CSV_ROWS",
    "ErroConfirmacao",
    "ErroLista",
    "ResultadoImportacao",
    "confirmar",
    "contar_enderecos",
    "criar_lista",
    "descadenciar",
    "destinatarios",
    "eliminar_lista",
    "endereco_da_lista",
    "enderecos_da_lista",
    "importar_csv",
    "link_confirmar",
    "link_descadenciar",
    "lista_do_utilizador",
    "lista_publico",
    "listas_do_utilizador",
    "pedir_confirmacao",
    "pendentes_por_utilizador",
    "remover_endereco",
    "repor_inscricao",
]
