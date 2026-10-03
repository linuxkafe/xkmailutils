"""Listas de destinatários: criação, importação e remetente confirmado.

Este módulo mudou de regra no `T017-A`, e a regra nova é mais fraca que a
antiga. Antes:

    **Um endereço só entra num envio depois de `confirmed_at` estar preenchido.**

`confirmed_at IS NOT NULL` era uma prova: alguém tinha typed um código num
email que recebeu. Essa prova **não existe mais**. O `T017-A` tirou as cinco
colunas de confirmação de `list_addresses` e a garantia com elas.

Agora a regra cabe assim:

    **Uma lista só entra num envio depois de o `from` estar confirmado.**

É mais fraca, e é importante não a descrever como se fosse a mesma coisa. Um
remetente confirmado prova que o endereço é do operador. Não prova que os
destinatários consentiram — prova isso é uma declaração do operador, e uma
declaração. `CLAUDE.md` diz isto no `Intent`, e `REQUIREMENTS.md` FR-6.2 diz as
duas leituras lado a lado para que ninguém as troque.

`destinatarios()` continua a ser a **única função do projecto que devolve
endereços para envio**, e agora recusa-se a responder a uma lista sem `from`
confirmado em vez de devolver os endereços e confiar em quem chamou. O que
sobreviveu da regra antiga é a descadência: um `unsubscribed_at IS NOT NULL`
nunca entra num `SELECT` de envio, em nenhuma das duas leituras, e essa linha é
a que não pode ser esquecida quando o resto foi deliberadamente invertido.

Porquê a confirmação do `from` vive em `senders` e não em `otp_codes`: a tabela
`otp_codes` tem `user_id NOT NULL` e está ligada a um dispositivo, porque é o
segundo factor de *login*. Isto confirma que o endereço é do operador, que tem
utilizador — mas não tem dispositivo, e reusar a tabela obrigaria a inventar
uma association a um dispositivo que não tem. `senders` é a tabela do que
confirma *o dono*, e é a mesma forma de `list_addresses` antes do `T017-A`:
hash, expiração, tentativas, e o hash apagado ao confirmar.
"""

from __future__ import annotations

import csv
import hmac
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
    """As listas do utilizador, com as contagens e o estado do `from`.

    As contagens vêm de subconsultas no mesmo `SELECT` e não de `COUNT(*)` por
    lista: são três listas, e três round-trips ao SQLite por página seria mais
    código para o mesmo número.

    **As contagens mudaram de subjecto no `T017-A`.** `total`, `confirmados` e
    `pendentes` contavam endereços por estado de confirmação. `total` continua
    a contar endereços, e os outros dois passaram a contar algo que decide se
    a lista envia: `senders.confirmed_at`. Um `NULL` em `sender_id` conta como
    por confirmar, que é o mesmo estado do ponto de vista de quem tenta enviar.

    Os nomes `confirmados` e `pendentes` ficaram porque a interface e os testes
    os leem, e porque `pendentes` já não quer dizer "endereços sem código" — o
    `CLAUDE.md` diz qual é o estado novo.
    """
    return list(
        conn.execute(
            "SELECT l.*,"
            " (SELECT COUNT(*) FROM list_addresses a"
            "  WHERE a.list_id = l.id) AS total,"
            " (SELECT COUNT(*) FROM list_addresses a"
            "  WHERE a.list_id = l.id AND a.unsubscribed_at IS NULL) AS confirmados,"
            " (SELECT s.confirmed_at FROM senders s WHERE s.id = l.sender_id)"
            "  AS from_confirmado,"
            " l.sender_id IS NULL AS sem_from"
            " FROM recipient_lists l"
            " WHERE l.user_id = ?"
            " ORDER BY l.name",
            (user_id,),
        )
    )


def lista_publico(conn: sqlite3.Connection, list_id: int) -> dict[str, Any] | None:
    """Nome e contagem de uma lista, **sem** validação de dono.

    Para a página pública de descadência — a de confirmação saiu com o
    `T017-A`. O que não sai daqui são os endereços: `SELECT name, (SELECT
    COUNT(*)…)` e nunca a lista de destinatários. Quem se descadencia vê o nome
    da lista de onde saiu — é a única coisa de que precisa — e mais nada.
    """
    linha = conn.execute(
        "SELECT id, name, ("
        "  SELECT COUNT(*) FROM list_addresses a"
        "  WHERE a.list_id = recipient_lists.id"
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


def contar_pendentes_de_envio(conn: sqlite3.Connection, user_id: int) -> int:
    """Quantas listas do utilizador estão **impedidas de enviar**.

    O `T017-A` trocou "pendentes" por outra coisa: já não há endereços à espera
    de código, mas há listas sem `from` escolhido e listas com um `from` por
    confirmar. Um `sender_id` nulo conta como bloqueada, e conta como tal
    porque é o mesmo estado do ponto de vista de quem tenta enviar — mesmo que
    a lista tenha cinco mil endereços válidos.
    """
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM recipient_lists l"
            " LEFT JOIN senders s ON s.id = l.sender_id"
            " WHERE l.user_id = ? AND (l.sender_id IS NULL OR s.confirmed_at IS NULL)",
            (user_id,),
        ).fetchone()["n"]
    )


def lista_pode_enviar(conn: sqlite3.Connection, list_id: int) -> bool:
    """A lista tem um `from` escolhido **e** confirmado.

    É o portão que substitui a confirmação por destinatário, e vale para todos
    os caminhos de envio: imediato, agendado, e reexecução. Um `LEFT JOIN` e
    `IS NULL` em vez de `JOIN` é o que faz a ausência de `sender` contar como
    "não pode" em vez de não contar.
    """
    achado = conn.execute(
        "SELECT s.confirmed_at AS confirmado FROM recipient_lists l"
        " LEFT JOIN senders s ON s.id = l.sender_id"
        " WHERE l.id = ?",
        (list_id,),
    ).fetchone()
    return achado is not None and achado["confirmado"] is not None


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


def destinatarios(conn: sqlite3.Connection, list_id: int) -> dict[str, Any]:
    """**A única função do projecto que devolve destinatários para envio.**

    Chama-se `destinatarios` e não `para_enviar` porque `para_enviar` seria um
    nome que a alternativa tentadora é prometer. Este `SELECT` só é seguro
    **quando** `lista_pode_enviar` é verdadeira, e nada impede um chamador de o
    usar sem essa verificação. Por isso esta função recusa-se a responder a uma
    lista sem `from` confirmado, em vez de devolver os endereços e confiar em
    quem chamou.

    Duas exclusões, todas deliberadas:

    - `unsubscribed_at IS NULL` — descadência é irreversível pelo produto e tem
      de sobreviver a um erro no `FROM`. (FR-6.7) **Esta linha é a que
      continua a valer em qualquer das duas leituras** do `CLAUDE.md`, e a que
      não pode ser esquecida quando o outro filtro desapareceu.
    - o portão do `from`, acima.

    A exclusão que o `T017-A` removeu era `confirmed_at IS NOT NULL` (FR-6.2),
    e com ela a garantia de que ninguém recebe sem ter pedido. Isso é uma
    perda real e o `CLAUDE.md` diz qual é a troca: quem passa a afirmar o
    consentimento é o operador, e a prova verificada deixa de existir.

    Se algum dia aparecer uma segunda função que devolva destinatários, esta
    regra passa a ser um comentário. Por isso a mutação substituta da M-18
    substitui estas duas condições e tem de morrer.
    """
    if not lista_pode_enviar(conn, list_id):
        return {"enviavel": False, "destinatarios": [], "motivo": "sem from confirmado"}
    return {
        "enviavel": True,
        "destinatarios": list(
            conn.execute(
                # O `id` vem no mesmo SELECT porque o compositor precisa dele
                # para assinar o link de descadência, e um segundo query por
                # destinatário num envio de 5000 pessoas são 5000 round-trips
                # para descobrir um inteiro que já estava à mão.
                "SELECT id, email, name FROM list_addresses"
                " WHERE list_id = ?"
                "   AND unsubscribed_at IS NULL"
                " ORDER BY email",
                (list_id,),
            )
        ),
        "motivo": None,
    }


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
        #: O que a interface tem de dizer ao operador depois de importar.
        #:
        #: Com o `T017-A` não há código de confirmação para o destinatário
        #: preencher, e portanto **ninguém** disse que quer receber. Isto não é
        #: um rodapé: a afirmação de que o operador tem o consentimento de quem
        #: importa é o que sustenta a lista inteira, e um operador que não a
        #: viu não pode ter concordado com ela. (FR-6.5)
        self.aviso_consentimento: str = ""

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
    """Lê um `.csv` de endereços, e os coloca activos.

    **Isto é uma inversão, e o `T017-A` é o que a fez.** Antes os endereços
    entravam pendentes e cada um confirmava por código, pelo que a prova de
    consentimento era `confirmed_at IS NOT NULL`. Agora entram activos sem
    ninguém dizer nada, e quem afirma ter o consentimento é o operador
    (FR-6.5).

    A consequência é que a função deixou de ser a coisa mais prudente do
    projecto e passou a ser a coisa de que o `CLAUDE.md` fala mais a sério: quem
    importa está a declarar que tem autorização. Por isso `ResultadoImportacao`
    traz `aviso_consentimento` preenchido e a interface **tem de** o mostrar. Um
    caminho de importação que não o mostra seria o produto a assumir o
    consentimento em silêncio, que é o resultado que o `T017-A` veio evitar.

    O que sobreviveu do modelo antigo é o tecto de tamanho da lista: continua a
    haver um limite para o número de endereços que uma lista pode ter. O tecto de
    confirmações pendentes **saiu**, porque já não há pendentes (FR-6.6).

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

    # O tecto de pendentes que vivia aqui saiu com o `T017-A`: sem confirmação
    # por destinatário não há pendentes, e um tecto sobre um estado que não
    # existe é código que ninguém sabe porque está ali.
    #
    # O tecto de tamanho da lista fica, e é o único. Ele é o que impede a
    # ferramenta de ser usada para despejar um ficheiro de 200 mil linhas numa
    # lista, e não tem nada a ver com consentimento.

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

    resultado.aviso_consentimento = (
        f"Importaste {resultado.importados} endereço(s) activos para esta lista. "
        "Nenhum deles disse que queria receber, e o produto não vai pedir: "
        "assume que já tens a autorização de quem importaste. Quem se "
        "descadenciar deixa de receber, e isso não se desfaz pela interface."
    )
    return resultado


# ------------------------------------------------------------------ remetente
#
# Tudo o que segue confirma que **o endereço é do operador**. Nada aqui diz que
# os destinatários consentiram, e nenhuma função deste bloco pode ser lida como
# se dissesse. Essa distinção é a troca que o `T017-A` fez, e é a razão de
# `CLAUDE.md` escrever "remetente" e não "consentimento" onde quer que falte.


def _segundos_desde(iso_quando: str | None, agora) -> float | None:
    """Quantos segundos passaram. `None` se nunca, ou se o timestamp é lixo."""
    if not iso_quando:
        return None
    try:
        return (agora - security.parse_iso(iso_quando)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return None


def remetentes_do_utilizador(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """Os `from` do utilizador, confirmados ou não.

    Não há `ON DELETE CASCADE` a apagar um remetente em uso: `sender_id` é
    `ON DELETE RESTRICT`, e é `definir_from_da_lista` que tem de recusar. Um
    `CASCADE` aqui apagaria a lista inteira quando alguém apagasse um `from`, o
    que seria destruir trabalho de outra pessoa por causa de um clique.
    """
    return list(conn.execute("SELECT * FROM senders WHERE user_id = ? ORDER BY email", (user_id,)))


def remetente_do_utilizador(
    conn: sqlite3.Connection, user_id: int, sender_id: int
) -> sqlite3.Row | None:
    """Um `from` **deste** utilizador.

    O `user_id` no `WHERE` e não só o `id` é o teste de dono. Sem ele, um
    utilizador confirma o `from` de outro por tentativa-e-erro, e a interface
    deixa de distinguir "não existe" de "não é seu" — que é a mesma resposta, e
    por isso não serve de nada.
    """
    return conn.execute(
        "SELECT * FROM senders WHERE id = ? AND user_id = ?", (sender_id, user_id)
    ).fetchone()


def registar_remetente(
    conn: sqlite3.Connection, user_id: int, email: str, agora: str | None = None
) -> sqlite3.Row:
    """Cria o `from` por confirmar, ou devolve o que já existia.

    Reutilizável entre listas, confirmado **uma vez** (FR-6.9). Reutilizar é o
    que torna o código útil em vez de um custo por lista: um operador com três
    listas do mesmo endereço escreve a palavra e recebe três códigos.

    Se o `from` já existe e está confirmado, fica como está — pedir um código
    novo para um endereço já provado seria um email inútil e um caminho para
    alguém tentar re-confirmar o que já confirmou.
    """
    normalizado = security.normalise_email(email)
    if not security.is_valid_email(normalizado):
        raise ErroLista("Email inválido.")
    momento = agora or security.iso(security.utcnow())
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO senders (user_id, email, created_at) VALUES (?, ?, ?)",
            (user_id, normalizado, momento),
        )
    achado = conn.execute(
        "SELECT * FROM senders WHERE user_id = ? AND email = ?", (user_id, normalizado)
    ).fetchone()
    if achado is None:
        raise ErroLista("Não foi possível registar o remetente.")
    return achado


def definir_from_da_lista(
    conn: sqlite3.Connection, user_id: int, list_id: int, sender_id: int | None
) -> bool:
    """Liga a lista a um `from`, ou solta-a (`sender_id` a `None`).

    Recusa um `sender_id` que não é do utilizador, e isso é o teste de dono de
    `FR-6.10` a funcionar antes de qualquer código sair. Aceitar o `id` e
    confiar no `SELECT` de envio seria o caminho para a lista de um utilizador
    enviar em nome de outro.

    Soltar (`None`) é permitido e é como se recua de uma escolha errada. O que
    não é permitido é escolher um `from` por confirmar sem querer: isso
    degrada-se sozinho, porque `lista_pode_enviar` continua a exigir
    confirmação.
    """
    if lista_do_utilizador(conn, user_id, list_id) is None:
        return False
    if sender_id is not None and remetente_do_utilizador(conn, user_id, sender_id) is None:
        return False
    with transaction(conn):
        conn.execute("UPDATE recipient_lists SET sender_id = ? WHERE id = ?", (sender_id, list_id))
    return True


def pedir_confirmacao_remetente(
    conn: sqlite3.Connection,
    user_id: int,
    sender_id: int,
    settings: Settings,
    agora=None,
) -> None:
    """Manda o código que prova que o `from` é do operador.

    O cooldown vive aqui e é **por remetente**, contra
    `senders.confirmation_sent_at`. Antes era por endereço de destinatário e
    servia para o mesmo fim — não deixar alguém pedir códigos sem parar — mas o
    endereço de destinatário deixou de pedir códigos, e um cooldown que
    protege um estado que não existe é configuração que ninguém sabe porque
    está ali. A setting não mudou de nome nem de valor; mudou o que protege.

    O `INSERT OR IGNORE` e o `UPDATE` seguinte dão o comportamento que o
    utilizador espera: pedir duas vezes não cria duas subscrições, e o segundo
    pedido com o cooldown dentro recusa-se em vez de reescrever o estado.
    """
    momento = agora or security.utcnow()
    remetente = remetente_do_utilizador(conn, user_id, sender_id)
    if remetente is None:
        raise ErroConfirmacao("Remetente inexistente.")
    if remetente["confirmed_at"] is not None:
        raise ErroConfirmacao("Este remetente já está confirmado.")
    if remetente["confirmation_attempts"] >= MAX_CONFIRM_ATTEMPTS:
        raise ErroConfirmacao(
            "Esgotaste as tentativas deste remetente. Escolhe outro endereço de envio."
        )

    ultima = remetente["confirmation_sent_at"]
    passado = _segundos_desde(ultima, momento)
    if passado is not None and passado < settings.confirm_cooldown_seconds:
        faltam = int(settings.confirm_cooldown_seconds - (passado or 0))
        raise ErroConfirmacao(
            f"Pediste um código há {faltam}s. Espera mais {faltam}s antes de pedir outro."
        )

    codigo = security.new_otp()
    expira = security.iso(security.otp_expiry(now=momento, ttl_minutes=settings.otp_ttl_minutes))
    with transaction(conn):
        conn.execute(
            "UPDATE senders"
            " SET confirmation_hash = ?, confirmation_expires_at = ?,"
            "     confirmation_sent_at = ?, confirmation_attempts = 0"
            " WHERE id = ?",
            (security.hash_otp(codigo), expira, security.iso(momento), sender_id),
        )
    mailer.send_sender_confirmation(settings, remetente["email"], codigo, settings.otp_ttl_minutes)
    # O código nunca entra aqui. A resposta HTTP não tem onde o pôr, e a única
    # forma de o mostrar ao operador seria imprimi-lo na página.


def confirmar_remetente(
    conn: sqlite3.Connection, user_id: int, sender_id: int, codigo: str, agora=None
) -> bool:
    """Confirma o `from`. `True` se passou, `False` se não.

    O que isto prova é que o endereço pertence a quem tem a sessão. Não prova
    consentimento de destinatário nenhum, e o nome da função diz `remetente`
    por isso e não por estilo.

    Três coisas que um código de confirmação tem e que não se podem omitir:
    `hmac.compare_digest` na comparação (um `==` numa string de utilizador é
    um `timing` leak, e este é o único segredo que o utilizador escolhe), o
    tecto de tentativas, e o hash apagado ao confirmar.
    """
    momento = agora or security.utcnow()
    remetente = remetente_do_utilizador(conn, user_id, sender_id)
    if remetente is None:
        return False
    if remetente["confirmed_at"] is not None:
        return True
    if remetente["confirmation_attempts"] >= MAX_CONFIRM_ATTEMPTS:
        return False
    expira = remetente["confirmation_expires_at"]
    if expira is not None:
        try:
            if security.parse_iso(expira) <= momento:
                return False
        except (ValueError, TypeError, OverflowError):
            return False

    guardado = remetente["confirmation_hash"]
    if not guardado or not hmac.compare_digest(str(guardado), security.hash_otp(codigo)):
        with transaction(conn):
            conn.execute(
                "UPDATE senders SET confirmation_attempts = confirmation_attempts + 1 WHERE id = ?",
                (sender_id,),
            )
        return False

    with transaction(conn):
        # O hash é apagado no mesmo statement que confirma: é o que impede um
        # código válido de voltar a confirmar, e o que faz `guardado` ser `None`
        # para um remetente já confirmado.
        conn.execute(
            "UPDATE senders SET confirmed_at = ?, confirmation_hash = NULL,"
            " confirmation_expires_at = NULL"
            " WHERE id = ?",
            (security.iso(momento), sender_id),
        )
    return True


def link_descadenciar(settings, list_id: int, address_id: int) -> str:
    """URL de descadência, com token assinado.

    Este é o URL que o `T017-A` **não** pode apagar. Sem confirmação por
    destinatário não há ninguém a quem o produto peça presença, e a descadência
    passa a ser a única forma de uma pessoa sair: é a linha de
    `unsubscribed_at IS NULL` a ser levada a sério (FR-6.7).

    O `address_id` não é lido do caminho pela rota que consome o link — vem de
    dentro do token. Sem ele, `address_id` é um inteiro adivinhável e enumerar
    inteiros dá a lista inteira. (B-04)
    """
    token = web.assinar_link(
        settings, web.PURPOSE_UNSUBSCRIBE, list_id, address_id, web.LINK_SALT_UNSUBSCRIBE
    )
    return f"{settings.base_url()}/listas/{list_id}/descadenciar/{address_id}?token={token}"


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

    **O `UPDATE` perdeu uma coluna e essa perda é o `T017-A` a falar.** Este
    `statement` limpava também `confirmation_hash`, porque descadenciar tinha de
    apagar um código de confirmação à espera — um endereço que se descadencia
    não fica a meio de uma subscrição pendente. Sem confirmação por destinatário
    não há hash para apagar, e deixar a coluna no `SQL` dava `no such column` a
    cada clique no link de descadência: um erro de 500 na única forma de uma
    pessoa sair de uma lista.

    É a clase de falha que a migração traz com ela, e a razão de `make check`
    correr a suite inteira: um `SQL` que se refere a uma coluna que a migração
    levou só falha quando alguém clica, não quando o teste do serviço passa.
    """
    with transaction(conn):
        cur = conn.execute(
            "UPDATE list_addresses SET unsubscribed_at = ?"
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
    "confirmar_remetente",
    "contar_pendentes_de_envio",
    "contar_enderecos",
    "criar_lista",
    "definir_from_da_lista",
    "descadenciar",
    "destinatarios",
    "eliminar_lista",
    "endereco_da_lista",
    "enderecos_da_lista",
    "importar_csv",
    "link_descadenciar",
    "lista_do_utilizador",
    "lista_pode_enviar",
    "lista_publico",
    "listas_do_utilizador",
    "pedir_confirmacao_remetente",
    "registar_remetente",
    "remetente_do_utilizador",
    "remetentes_do_utilizador",
    "remover_endereco",
]
