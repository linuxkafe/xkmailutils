"""Testes das listas de destinatários.

O ficheiro está organizado pela ordem em que um endereço passa a poder receber.
A razão é que o teste mais importante — *ninguém recebe sem confirmar* — só faz
sentido depois de o leitor ter visto o que é um endereço por confirmar.

A mutação M-18 troca `confirmed_at IS NOT NULL` por `1=1` no `SELECT` de
`destinatarios()` e tem de morrer. Se algum dia ela passar, a lista com 5000
pendentes está a receber email e este ficheiro deixou de dizer a verdade.
"""

from __future__ import annotations

import datetime
import inspect
import re

import pytest
from asgi_client import SyncASGIClient
from conftest import csrf_from
from test_auth_flows import _signed_in

from mailutils import config, db, security, web
from mailutils.lists import service

UA_OUTRO = "Mozilla/5.0 (Windows NT 10.0) OutroBrowser/1.0"

CSV_SIMPLES = b"nome,email\nAna,ana@exemplo.pt\nBruno,bruno@exemplo.pt\n"


#: Cache das settings derivadas das da aplicação.
#:
#: Importa porque o `secret_key` da aplicação é fixo no fixture `settings` do
#: `conftest`, e **um token assinado com uma chave não abre numa rota que
#: verifica com outra**. A primeira versão deste ficheiro chamava
#: `load_settings()` aqui, o que gera um segredo novo a cada chamada — e as
#: rotas de confirmação respondiam 303 a um token perfeitamente válido. É o
#: género de bug que só aparece quando se junta o token à rota, e que por isso
#: os testes de token têm de usar as settings da aplicação e não as suas.
_derivadas: dict[tuple, config.Settings] = {}


def _derivar(base: config.Settings, **overrides) -> config.Settings:
    chave = tuple(sorted(overrides.items()))
    if chave not in _derivadas:
        _derivadas[chave] = config.Settings(
            **{
                **{field: getattr(base, field) for field in base.__dataclass_fields__},
                **overrides,
            }
        )
    return _derivadas[chave]


@pytest.fixture(autouse=True)
def _limpa_derivadas() -> None:
    _derivadas.clear()


def _app_settings(settings=None, **overrides) -> config.Settings:
    """Settings derivadas **da aplicação**.

    Chamar isto sem o fixture usa `load_settings`, que é o que os testes que
    não passam por rota podem fazer. Os que passam por rota têm de usar a
    fixture `settings`.
    """
    base = settings if settings is not None else config.load_settings(env="development")
    base = config.Settings(
        **{
            **{field: getattr(base, field) for field in base.__dataclass_fields__},
            "public_base_url": "https://mailutils.exemplo.pt",
        }
    )
    return _derivar(base, **overrides)


def _criar_utilizador(conn, email: str) -> int:
    conn.execute(
        "INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?, 'x', 0, 't')",
        (email,),
    )
    return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


def _lista(conn, user_id: int, nome: str = "Clientes") -> int:
    return service.criar_lista(conn, user_id, nome)


def _enderecos(conn, list_id: int) -> dict[str, int]:
    return {row["email"]: row["id"] for row in service.enderecos_da_lista(conn, list_id)}


def _fechar_from(conn, list_id: int, user_id: int, captured_emails) -> None:
    """Escolhe e confirma o `from` de uma lista, pelo caminho inteiro."""
    remetente = _remetente_confirmado(conn, user_id, captured_emails)
    assert service.definir_from_da_lista(conn, user_id, list_id, remetente["id"])


class TestInvarianteCentral:
    """A regra que separa listas de spam, depois do `T017-A`.

    **A regra mudou de assunto e continua a ser uma só.** Antes era
    `confirmed_at IS NOT NULL` sobre o endereço: ninguém recebia sem ter
    confirmado. Agora é `senders.confirmed_at IS NOT NULL` sobre a lista: ninguém
    recebe sem um `from` confirmado.

    A propriedade que estes testes provam não é "o consentimento é
    verificado" — isso deixou de ser verdade e nenhum teste pode afirmar o que
    não é. É mais estreita e continua a valer inteira: **uma lista que não
    passou pelo portão não devolve um único destinatário.**

    `destinatarios()` devolve um dicionário e não uma lista pelo mesmo motivo
    pelo qual o `SELECT` recusa em vez de devolver: um chamador que use o valor
    de iterable sem olhar para `enviavel` está a usar uma lista que não pode
    enviar. Os testes verificam as duas coisas.
    """

    def test_importado_nao_e_destinatario(self, conn, normal_user: int, captured_emails) -> None:
        """Importar não é terso o `from` que envia. São coisas separadas."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)

        resultado = service.destinatarios(conn, list_id)
        assert resultado["enviavel"] is False
        assert resultado["destinatarios"] == []

    def test_com_remetente_confirmado_passa_a_ser_destinatario(
        self, conn, normal_user: int, captured_emails
    ) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])

        resultado = service.destinatarios(conn, list_id)
        assert resultado["enviavel"] is True
        emails = {d["email"] for d in resultado["destinatarios"]}
        assert emails == {"ana@exemplo.pt", "bruno@exemplo.pt"}

    def test_descadenciado_deixa_de_ser_destinatario(
        self, conn, normal_user: int, captured_emails
    ) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])
        assert len(service.destinatarios(conn, list_id)["destinatarios"]) == 2

        service.descadenciar(conn, _enderecos(conn, list_id)["ana@exemplo.pt"])
        restantes = service.destinatarios(conn, list_id)["destinatarios"]
        assert [d["email"] for d in restantes] == ["bruno@exemplo.pt"]

    def test_descadencia_e_irreversivel_pelo_produto(
        self, conn, normal_user: int, captured_emails
    ) -> None:
        """Descadenciar duas vezes não ressuscita ninguém.

        Esta é a M-01 com o `from` confirmado: o produto não tem caminho para
        repor uma subscrição, e a ausência desse caminho é a garantia.
        """
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])
        address_id = _enderecos(conn, list_id)["ana@exemplo.pt"]

        service.descadenciar(conn, address_id)
        assert service.descadenciar(conn, address_id) is False, (
            "descadenciar outra vez reescreveu o estado: a descadencia tem de ser "
            "idempotente e irreversivel pelo produto"
        )


# `TestConfirmacaoPorCodigo` saiu no `T017-A`.
#
# A classe provava que o codigo de confirmacao de um **endereco** confirmava,
# expirava, era de uso unico e limitava tentativas. As quatro propriedades
# continuam a valer, e e a raza de a prova nao desaparecer com a suite:
# `TestRemetenteConfirmado` exercita as mesmas quatro, com `senders` como
# assunto e com o codigo lido do email que foi mesmo enviado. Apagar os testes
# sem repor a prova seria deixar o comportamento novo sem rede.
#
# O que se perdeu, e fica escrito: a confirmacao de destinatario **era** a prova
# de consentimento. Nenhum destes testes afirmava que os destinatarios
# consentiram; afirmavam que o operador nao podia forjar um codigo. A segunda
# propriedade migrou intacta. A primeira deixou de existir por decisao do dono,
# e o `CLAUDE.md` diz isso no `Intent`.
class TestLimitesAntiAbuso:
    """Os tectos que sobraram depois do `T017-A`, e os que saíram.

    Saíram dois: o de confirmações pendentes (não há pendentes) e o de pedidos
    de código por destinatário (não há códigos para pedir). O cooldown não saiu
    — mudou de alvo, e é por remetente.
    """

    def test_o_tecto_nao_prende_o_operador(self, conn, normal_user: int, captured_emails) -> None:
        """O tecto de tamanho não pode bloquear a acção que torna a lista útil.

        Antes esta propriedade era sobre pedir confirmação: um tecto de
        pendentes que bloqueasse o pedido deixava o utilizador com uma lista
        cheia de endereços que não podia confirmar e nenhuma forma de os
        activar. Agora o tecto que resta é o de **tamanho**, e a acção que não
        pode ser bloqueada é fechar o `from` — que é o que torna a lista
        enviavél.

        Por isso o teste põe a lista exactamente no tecto e ainda exige que o
        `from` se feche. Uma implementação que aplicasse o tecto de tamanho ao
        `from` passaria num teste que só olhasse para a importação.
        """
        settings = _app_settings(max_list_size=2)
        list_id = _lista(conn, normal_user)
        linhas = ("nome,email\n" + "\n".join(f"P{i},p{i}@exemplo.pt" for i in range(4))).encode()

        service.importar_csv(conn, normal_user, list_id, linhas, settings)
        assert service.contar_enderecos(conn, list_id) == 2, (
            "o ficheiro devia ter parado no tecto de tamanho"
        )

        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        assert service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])
        assert service.lista_pode_enviar(conn, list_id) is True, (
            "uma lista no tecto de tamanho tem de poder fechar o from e enviar; "
            "o tecto que impede isso é um beco sem saída"
        )
        assert len(service.destinatarios(conn, list_id)["destinatarios"]) == 2

    def test_teto_de_enderecos_por_lista(self, conn, normal_user: int) -> None:
        settings = _app_settings(max_list_size=3)
        list_id = _lista(conn, normal_user)
        linhas = ("nome,email\n" + "\n".join(f"P{i},p{i}@exemplo.pt" for i in range(10))).encode()

        resultado = service.importar_csv(conn, normal_user, list_id, linhas, settings)
        assert resultado.importados == 3
        assert service.contar_enderecos(conn, list_id) == 3
        assert resultado.invalidos, "a interrupcao por tecto tem de ser visivel"


class TestImportacao:
    def test_com_cabecalho_nome_e_email(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        nomes = {row["email"]: row["name"] for row in service.enderecos_da_lista(conn, list_id)}
        assert nomes == {"ana@exemplo.pt": "Ana", "bruno@exemplo.pt": "Bruno"}

    def test_sem_cabecalho(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, b"ana@exemplo.pt\n", settings)
        assert len(service.enderecos_da_lista(conn, list_id)) == 1

    def test_separador_ponto_e_virgula(self, conn, normal_user: int) -> None:
        """Um Excel em pt-PT escreve `;`. Recusar o ficheiro inteiro por causa
        disso seria a forma mais rapida de perder o ficheiro."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(
            conn, normal_user, list_id, b"nome;email\nAna;ana@exemplo.pt\n", settings
        )
        assert len(service.enderecos_da_lista(conn, list_id)) == 1

    def test_uma_linha_invalida_nao_aborta(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        conteudo = b"nome,email\nAna,ana@exemplo.pt\nRuim,nao-e-email\nBruno,b@exemplo.pt\n"
        resultado = service.importar_csv(conn, normal_user, list_id, conteudo, settings)

        assert resultado.importados == 2
        assert resultado.total_rejeitado == 1
        assert "nao-e-email" in resultado.invalidos[0]

    def test_duplicado_e_contado(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        resultado = service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        assert resultado.importados == 0
        assert resultado.ja_existentes == 2

    def test_mesmo_email_em_maior_minusculo_e_o_mesmo(self, conn, normal_user: int) -> None:
        """`normalise_email` emeca minusculas. Importar `Ana@X.pt` num sitio e
        `ana@x.pt` noutro criava dois pendentes para a mesma pessoa."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, b"Ana@EXEMPLO.pt\n", settings)
        service.importar_csv(conn, normal_user, list_id, b"ana@exemplo.pt\n", settings)
        assert len(service.enderecos_da_lista(conn, list_id)) == 1

    def test_ficheiro_vazio_e_erro(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        with pytest.raises(service.ErroLista):
            service.importar_csv(conn, normal_user, list_id, b"", settings)

    def test_cabecalho_sem_coluna_de_email_e_erro(self, conn, normal_user: int) -> None:
        """Reconhece-se que é um cabeçalho mas não há onde ler o email — erro,
        não uma lista de linhas inválidas."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        with pytest.raises(service.ErroLista, match="coluna de email"):
            service.importar_csv(conn, normal_user, list_id, b"nome,cidade\nAna,Porto\n", settings)

    def test_uma_coluna_so_sem_cabecalho_nao_e_cabecalho(self, conn, normal_user: int) -> None:
        """`nome@exemplo.pt` contém a palavra "nome" e é um email válido. Sem a
        segunda condição, este ficheiro seria lido como cabeçalho."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, b"nome@exemplo.pt\n", settings)
        assert len(service.enderecos_da_lista(conn, list_id)) == 1

    def test_ficheiro_gigante_e_recusado(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        linhas = "\n".join(f"p{i}@x.pt" for i in range(service.MAX_CSV_ROWS + 10))
        with pytest.raises(service.ErroLista):
            service.importar_csv(conn, normal_user, list_id, linhas.encode(), settings)

    def test_importar_para_a_lista_de_outro_e_recusado(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        lista_dele = _lista(conn, normal_user)
        outro = _criar_utilizador(conn, "outro@exemplo.pt")
        lista_outro = _lista(conn, outro, "De outro")

        with pytest.raises(service.ErroLista):
            service.importar_csv(conn, normal_user, lista_outro, CSV_SIMPLES, settings)
        assert service.contar_enderecos(conn, lista_outro) == 0
        assert service.contar_enderecos(conn, lista_dele) == 0


class TestIsolamentoEntreUtilizadores:
    def test_a_lista_de_um_nao_e_visivel_para_o_outro(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        list_id = _lista(conn, normal_user, "Privada")
        response = app.get(f"/listas/{list_id}")
        assert response.status_code == 200

        _outro_utilizador(app, conn)
        response = app.get(f"/listas/{list_id}", follow_redirects=True)
        assert "Privada" not in response.text
        assert "não existe" in response.text

    def test_eliminar_de_outro_nao_apaga(self, conn, normal_user: int) -> None:
        list_id = _lista(conn, normal_user, "Privada")
        assert service.eliminar_lista(conn, 9999, list_id) is False
        assert service.lista_do_utilizador(conn, normal_user, list_id) is not None


def _outro_utilizador(app: SyncASGIClient, conn, nome: str = "outro@exemplo.pt") -> None:
    """Autentica o cliente como outro utilizador.

    A prova não é a palavra-passe — é o `user_id` no `WHERE`. Alguém com uma
    sessão válida que troque o número da lista na URL tem de levar 404, não a
    lista de outra pessoa.
    """
    from mailutils import web
    from mailutils.auth import service as auth_service

    conn.execute(
        "INSERT INTO users (email, password_hash, is_admin, created_at) VALUES (?, 'x', 0, 't')",
        (nome,),
    )
    user_id = conn.execute("SELECT id FROM users WHERE email = ?", (nome,)).fetchone()["id"]
    settings = app.app.state.settings
    fingerprint = security.device_fingerprint(UA_OUTRO, "pt-PT")
    auth_service.register_device(conn, settings, user_id, fingerprint, UA_OUTRO, "Outro browser")
    token, _ = web.open_session(conn, settings, user_id)
    app.set_cookie("mailutils_session", token)


class TestRotas:
    def test_exige_sessao(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        assert app.get("/listas", follow_redirects=False).status_code == 303

    def test_indice_lista_as_listas(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _lista(conn, normal_user, "Clientes")
        body = app.get("/listas").text
        assert "Clientes" in body

    def test_criar_exige_csrf(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        app.post("/listas", data={"nome": "Sem token"})
        assert service.listas_do_utilizador(conn, normal_user) == []

    def test_criar_e_listar(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/listas")
        response = app.post(
            "/listas",
            data={"csrf_token": token, "nome": "Parceiros"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Parceiros" in response.text

    def test_importar_pelo_formulario(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        list_id = _lista(conn, normal_user)
        token = csrf_from(app, f"/listas/{list_id}")
        response = app.post(
            f"/listas/{list_id}/importar",
            files={"ficheiro": ("contactos.csv", CSV_SIMPLES, "text/csv")},
            data={"csrf_token": token},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert service.contar_enderecos(conn, list_id) == 2
        assert "Importados" in response.text or "importados" in response.text

    def test_importar_exige_csrf(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        list_id = _lista(conn, normal_user)
        app.post(
            f"/listas/{list_id}/importar",
            files={"ficheiro": ("c.csv", CSV_SIMPLES, "text/csv")},
            data={"csrf_token": "forjado"},
            follow_redirects=True,
        )
        assert service.contar_enderecos(conn, list_id) == 0

    def test_o_texto_da_interface_vive_em_MESSAGENS(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """NFR-14. O template decide *qual* aviso mostrar pela chave, e a chave
        tem de existir — um `{{ mensagens[aviso_chave] }}` com chave em falta
        mostra o slug ao utilizador."""
        from mailutils.templates import MESSAGENS

        chaves = ("email", "teto", "duplicado", "confirmado", "selecciona", "lista-inexistente")
        for chave in chaves:
            assert chave in MESSAGENS, f"falta a mensagem {chave!r}"
            assert MESSAGENS[chave], f"a mensagem {chave!r} está vazia"


class TestEsquema:
    def test_a_migracao_e_idempotente(self) -> None:
        conn = db.connect(":memory:")
        db.migrate(conn)
        db.migrate(conn)
        db.migrate(conn)

    def test_as_tabelas_novas_existem(self, conn) -> None:
        nomes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"recipient_lists", "list_addresses"} <= nomes

    def test_as_tabelas_novas_estao_em_TABLE_NAMES(self) -> None:
        assert "recipient_lists" in db.TABLE_NAMES
        assert "list_addresses" in db.TABLE_NAMES

    def test_a_tabela_de_remetentes_esta_no_backup(self) -> None:
        """Um restauro que não traga `senders` deixa todas as listas mudas.

        `TABLE_NAMES` é o que o `deploy.sh` e o `test_bootstrap` usam para
        verificar que uma base tem tudo o que precisa. Uma tabela nova que não
        entra aqui não está errada — está **ausente**, e o erro só apareceria
        na hora de restaurar, que é a pior hora possível para descobrir que o
        `from` de todas as listas se foi.
        """
        assert "senders" in db.TABLE_NAMES, (
            "`senders` fora de TABLE_NAMES: um restauro trazia as listas com "
            "`sender_id` a apontar para linhas inexistentes e nenhuma delas "
            "poderia enviar"
        )

    def test_eliminar_a_lista_elimina_os_enderecos(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        assert service.eliminar_lista(conn, normal_user, list_id)
        assert service.contar_enderecos(conn, list_id) == 0

    def test_eliminar_o_utilizador_elimina_as_listas(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        conn.execute("DELETE FROM users WHERE id = ?", (normal_user,))
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM recipient_lists WHERE user_id = ?", (normal_user,)
            ).fetchone()["n"]
            == 0
        )


class TestLinkAssinado:
    """O token é o que substitui a sessão para quem pede para sair. (B-02, B-04)

    A destinatária não tem conta. A rota de descadência não pode exigir sessão —
    e não pode por isso confiar no `address_id` do caminho, que é um inteiro
    enumerável. Estes testes travam as propriedades, e o `-k` de cada um é o que
    o `scripts/run-mutations.py` usa.

    **Só a descadência sobreviveu ao `T017-A`.** A confirmação deixou de ter
    link porque deixou de existir: ninguém confirma o seu endereço. O token
    assinado continua a ser o que garante que o link não pode ser forçado a
    alguém, e essas são as mesmas garantias sobre um caminho que há-de-vir
    (`FR-6.8`, `List-Unsubscribe`).
    """

    def test_sem_token_nao_abre(
        self, app: SyncASGIClient, conn, normal_user: int, captured_emails
    ) -> None:
        """Um `address_id` adivinhado não desliga ninguém."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        address_id = _enderecos(conn, list_id)["ana@exemplo.pt"]
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])

        # Sem atribuir: o que se prova e o estado da base, nao o redirect.
        app.get(f"/listas/{list_id}/descadenciar/{address_id}", follow_redirects=True)
        restantes = {d["email"] for d in service.destinatarios(conn, list_id)["destinatarios"]}
        assert "ana@exemplo.pt" in restantes, (
            "a rota desligou alguém sem token; um `address_id` é enumerável"
        )

    def test_o_token_da_lista_a_nao_verifica_na_lista_b(self, settings) -> None:
        """A lista está dentro do token assinado, e esta é a prova.

        Testa-se `verificar_link` **directamente**, e não pela rota, porque a
        rota não consegue expressar o ataque: `list_addresses.id` é
        `AUTOINCREMENT` e portanto único na tabela, e o mesmo email em duas
        listas tem ids diferentes. Uma prova pela HTTP passaria pelo motivo
        errado — o `endereco_da_lista` devolveria `None` — e a mutação M-22
        sobreviveria.

        O `T017-A` trocou o sujeito: era o token de confirmação de um endereço, e
        a confirmação saiu. A propriedade é a mesma e continua a valer mais, não
        menos — com confirmação por destinatário, um token vazado dava a alguém a
        capacidade de se beingscrever; agora um token vazado tira alguém de uma
        lista, e o dano é menor, e a propriedade que impede o token abrir a lista
        errada é a mesma.

        Defesa em profundidade que o esquema actual torna inalcançável. Continua
        a valer: o esquema pode mudar, e um token que não leva a lista não pode
        levar a lista.
        """
        app_settings = _app_settings(settings)
        token = service.link_descadenciar(app_settings, 1, 42).split("token=")[1]

        assert web.verificar_link(
            app_settings,
            web.PURPOSE_UNSUBSCRIBE,
            1,
            42,
            token,
            web.LINK_SALT_UNSUBSCRIBE,
            999,
        ), "o token não abre nem a si proprio"
        assert not web.verificar_link(
            app_settings,
            web.PURPOSE_UNSUBSCRIBE,
            2,
            42,
            token,
            web.LINK_SALT_UNSUBSCRIBE,
            999,
        ), "o token da lista 1 abriu a lista 2"

    def test_o_token_de_um_nao_abre_o_outro(
        self, app: SyncASGIClient, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """B-04: um link válido da Ana não desliga o Bruno.

        Esta é a propriedade que dá sentido ao token existir, e é a que a M-20
        apaga: sem a comparação entre o `address_id` do caminho e o que vai
        assinado no payload, o link de uma pessoa vale para qualquer outra da
        mesma lista. O token deixaria de ser posse e passaria a ser uma
        credencial de lista.

        O teste precisa de um token **válido** de uma pessoa e o `address_id` de
        outra. Os testes de token manipulado (`test_o_token_alterado_nao_abre`) não
        apanham isto: eles continuam a passar com a M-20 aplicada, porque um token
        adulterado falha na assinatura e a mutação não toca na assinatura. Foi
        por isso que a M-20 sobreviveu à primeira versão desta suite — a prova
        estava escrita, mas não era desta propriedade.
        """
        settings = _app_settings(settings)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])

        ids = _enderecos(conn, list_id)
        token = service.link_descadenciar(settings, list_id, ids["ana@exemplo.pt"]).split("token=")[
            1
        ]

        # Token válido da Ana, `address_id` do Bruno.
        app.get(
            f"/listas/{list_id}/descadenciar/{ids['bruno@exemplo.pt']}?token={token}",
            follow_redirects=True,
        )

        restantes = {d["email"] for d in service.destinatarios(conn, list_id)["destinatarios"]}
        assert "bruno@exemplo.pt" in restantes, (
            "o link da Ana desligou o Bruno: o token deixou de ser posse de "
            "quem o recebeu e passou a valer para a lista inteira"
        )

    def test_o_token_alterado_nao_abre(
        self, app: SyncASGIClient, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """Nenhuma variante de token manipulado passa."""
        settings = _app_settings(settings)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        address_id = _enderecos(conn, list_id)["ana@exemplo.pt"]
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])

        caminho = f"/listas/{list_id}/descadenciar/{address_id}"
        for bruto in ("", "x", "eyJ4IjoxfQ.aaaa.bbbb", "a.b.c"):
            app.get(f"{caminho}?token={bruto}", follow_redirects=True)
            restantes = {d["email"] for d in service.destinatarios(conn, list_id)["destinatarios"]}
            assert "ana@exemplo.pt" in restantes, f"o token {bruto!r} abriu a descadencia"

    def test_a_descadencia_abre_sem_sessao_e_tira_dos_destinatarios(
        self, app: SyncASGIClient, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """FR-6.7, e desta vez pela aplicação. Um clique, sem sessão, sem CSRF."""
        settings = _app_settings(settings)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])
        assert service.destinatarios(conn, list_id)["destinatarios"]

        caminho = service.link_descadenciar(
            settings, list_id, _enderecos(conn, list_id)["ana@exemplo.pt"]
        )
        resposta = app.get(_caminho_de(caminho), follow_redirects=True)
        assert resposta.status_code == 200
        restantes = {d["email"] for d in service.destinatarios(conn, list_id)["destinatarios"]}
        assert "ana@exemplo.pt" not in restantes

    def test_a_descadencia_e_idempotente(
        self, app: SyncASGIClient, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """Dois cliques no mesmo link: o segundo não faz mal nenhum."""
        settings = _app_settings(settings)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, list_id, remetente["id"])
        address_id = _enderecos(conn, list_id)["ana@exemplo.pt"]
        caminho = _caminho_de(service.link_descadenciar(settings, list_id, address_id))

        app.get(caminho, follow_redirects=True)
        segunda = app.get(caminho, follow_redirects=True)
        assert segunda.status_code == 200
        assert service.descadenciar(conn, address_id) is False


# `TestBypassDeConsentimento` saiu no `T017-A`, e a raza de ser decisao e nao
# esquecimento esta escrita.
#
# A classe existia por causa de uma correcao que continua verdadeira: o `POST` de
# reposicao fazia so `unsubscribed_at = NULL` e o endereco voltava a receber no
# instante, sem ninguem pedir (M-01). A correcao foi pedir um codigo novo a quem
# tinha cancelado, e nenhuma forma de repor sem o consentimento de quem
# cancelou.
#
# Sem confirmacao por destinatario, essa correcao nao tem de que ser: ja nao ha
# subscricao para repor. O endereco importado esta activo e descadencia-se com
# um link assinado; nao ha estado intermediario, e portanto nao ha o que repor.
# Um `unsubscribed_at = NULL` silencioso deixaria de ter a correcao como
# garantia — por isso a **ausencia** deste caminho e agora a garantia, e e o que
# `TestRemetenteConfirmado::test_o_descadenciado_nunca_entra` prova.
def _caminho_de(url: str) -> str:
    """URL absoluta do email → caminho interno da aplicação.

    Corta em `/listas`, e não no prefixo: o prefixo vem de
    `MAILUTILS_PATH_PREFIX`, que o teste não muda mas o operador muda, e um
    teste que assume `/xkmailutils` passa e o produto não abre. (Finding m-13
    da revisão T008: a prefixo é a terceira razão de o projecto existir.)
    """
    return "/listas/" + url.split("/listas/", 1)[1]


def _enviados(html: str) -> int:
    """O número que o aviso diz, lido do HTML.

    Lê-se o `{{ }}` em vez de fazer `assert "2 código(s)..." in html` porque o
    número está dentro de um `<strong>` e o texto não é contíguo — a primeira
    versão do teste falhava por isso, e um teste que falha por causa de
    espaçamento é um teste que a pessoa seguinte não vai corrigir em vez de
    investigar.
    """
    achado = re.search(r"<strong>(\d+)</strong> código\(s\) enviado", html)
    assert achado, "a página não tem aviso de códigos enviados"
    return int(achado.group(1))


def _token_do_formulario(html: str) -> str:
    """O token escondido no formulário de confirmação."""
    import re as _re

    achado = _re.search(r'name="token" value="([^"]+)"', html)
    assert achado, "o formulário de confirmação não tem token"
    return achado.group(1)


class TestMensagensQueDizemAVerdade:
    """M-07, M-08, M-09: a interface tem de descrever o que aconteceu.

    Os três vieram da persona Utilizador, e os três são o mesmo defeito em três
    sítios: um número que não é o número, uma soma que não é a soma, e uma
    chave de mensagem partilhada por duas situações diferentes.
    """

    def test_a_importacao_truncada_diz_quantas_linhas_perdidas(
        self, conn, normal_user: int
    ) -> None:
        """M-07. A primeira versão dizia "1 rejeitado" com 290 endereços em
        silêncio, porque `total_rejeitado` era `len(invalidos)` e o `break` só
        acrescentava uma causa."""
        settings = _app_settings(max_pending_confirmations=10)
        list_id = _lista(conn, normal_user)
        conteudo = "nome,email\n" + "\n".join(f"P{i},p{i}@x.pt" for i in range(300))

        resultado = service.importar_csv(conn, normal_user, list_id, conteudo.encode(), settings)

        perdidas = 300 - resultado.importados
        assert resultado.total_rejeitado == perdidas, (
            f"a interface diz {resultado.total_rejeitado} rejeitados e "
            f"{perdidas} endereços não entraram"
        )

    def test_um_duplicado_nao_e_um_rejeitado(self, conn, normal_user: int) -> None:
        """ "Já estava na lista" não é "rejeitado". O operador precisa de ver as
        duas coisas, e são duas."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, b"nome,email\nA,a@x.pt\n", settings)

        resultado = service.importar_csv(
            conn, normal_user, list_id, b"nome,email\nA,a@x.pt\nB,b@x.pt\n", settings
        )
        assert resultado.ja_existentes == 1
        assert resultado.total_rejeitado == 0

    def test_uma_linha_invalida_e_um_rejeitado(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        resultado = service.importar_csv(
            conn, normal_user, list_id, b"nome,email\nA,a@x.pt\nMau,mau\n", settings
        )
        assert resultado.importados == 1
        assert resultado.total_rejeitado == 1

    def test_as_duas_mensagens_de_tecto_dizem_different(self) -> None:
        """M-09. Uma chave, duas situações, e quem batia o tecto de confirmações
        era informado de que a lista estava cheia."""
        from mailutils.templates import MESSAGENS

        assert MESSAGENS["teto-lista"] != MESSAGENS["teto-confirmacoes"]
        assert "confirmaç" in MESSAGENS["teto-confirmacoes"].lower()
        assert "endereço" in MESSAGENS["teto-lista"].lower()


def _remetente_pendente(conn, user_id: int, email: str = "envio@exemplo.pt"):
    return service.registar_remetente(conn, user_id, email)


def _codigo_do_ultimo_email(captured_emails) -> str:
    """O código de seis dígitos do email mais recente.

    O teste **não tem** o código à mão: pede-se, o serviço manda por email, e o
    código é lido do que foi enviado. Um teste que tivesse o código provaria que
    `confirmar_remetente` funciona, não que o código chega a quem tem de o
    confirmar.

    Aceita a lista do `conftest` **ou** a captura local: dois fixtures que
    interceptam `send` ao mesmo tempo escrevem no mesmo sítio, e o último a
    correr ganha. Depender da ordem deles seria um teste que passa ou falha
    consoante o ficheiro foi mexido.
    """
    assert captured_emails, "não foi enviado nenhum email"
    corpo = captured_emails[-1]["text"]
    achado = re.search(r"\b\d{6}\b", corpo)
    assert achado, f"não encontrei um código de seis dígitos no email: {corpo[:200]}"
    return achado.group(0)


def _remetente_confirmado(conn, user_id: int, captured_emails, email: str = "envio@exemplo.pt"):
    """Um `from` confirmado pelo caminho inteiro: pedir, email, confirmar."""
    remetente = service.registar_remetente(conn, user_id, email)
    service.pedir_confirmacao_remetente(conn, user_id, remetente["id"], _app_settings())
    codigo = _codigo_do_ultimo_email(captured_emails)
    assert service.confirmar_remetente(conn, user_id, remetente["id"], codigo)
    return service.remetente_do_utilizador(conn, user_id, remetente["id"])


def _id_do_endereco(conn, list_id: int, email: str) -> int:
    for linha in service.enderecos_da_lista(conn, list_id):
        if linha["email"] == email:
            return linha["id"]
    raise AssertionError(f"{email} não está na lista")


class TestRemetenteConfirmado:
    """`T017-A`: o `from` confirmado é o portão que substitui a confirmação de
    destinatários.

    Estes testes existem porque a inversão enfraqueceu a garantia. Antes, cada
    endereço tinha confirmado por código e `confirmed_at IS NOT NULL` era uma
    prova. Agora ninguém confirma nada e o portão é o remetente. Um conjunto de
    testes que não morre quando o portão é removido não está a testar o portão.
    """

    def test_uma_lista_sem_remetente_nao_envia(self, conn, normal_user: int) -> None:
        """A propriedade central, e a que morre com a mutação substituta.

        Uma lista com cinco mil endereços válidos e sem `from` confirmado não
        devolve um único destinatário. É a diferença entre uma ferramenta que
        envia para quem pediu e uma que envia para quem calhou.
        """
        lista_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, lista_id, CSV_SIMPLES, _app_settings())

        resultado = service.destinatarios(conn, lista_id)

        assert resultado["enviavel"] is False, (
            "uma lista sem remetente confirmado devolveu destinatários"
        )
        assert resultado["destinatarios"] == [], (
            f"devolveu {len(resultado['destinatarios'])} endereços sem from confirmado"
        )

    def test_o_remetente_confirmado_abre_a_lista(
        self, conn, normal_user: int, captured_emails
    ) -> None:
        """E o caminho inverso: confirmado é o que a torna enviavél.

        Escolher um `from` por confirmar **não** abre a lista. O operador tem de
        poder montar a lista antes de abrir o email, e a lista não envia até ele
        confirmar — são duas coisas e a segunda não se deduce da primeira.
        """
        lista_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, lista_id, CSV_SIMPLES, _app_settings())
        pendente = service.registar_remetente(conn, normal_user, "envio@exemplo.pt")
        assert service.definir_from_da_lista(conn, normal_user, lista_id, pendente["id"])
        assert service.lista_pode_enviar(conn, lista_id) is False, (
            "escolher um from por confirmar tem de continuar a bloquear o envio"
        )

        service.pedir_confirmacao_remetente(conn, normal_user, pendente["id"], _app_settings())
        codigo = _codigo_do_ultimo_email(captured_emails)
        assert service.confirmar_remetente(conn, normal_user, pendente["id"], codigo)

        assert service.lista_pode_enviar(conn, lista_id) is True
        resultado = service.destinatarios(conn, lista_id)
        assert resultado["enviavel"] is True
        assert {row["email"] for row in resultado["destinatarios"]} == {
            "ana@exemplo.pt",
            "bruno@exemplo.pt",
        }

    def test_o_descadenciado_nunca_entra(self, conn, normal_user: int, captured_emails) -> None:
        """A linha que sobreviveu à inversão, testada com o `from` confirmado.

        Antes esta propriedade era uma das três exclusões; agora é a segunda, e
        continua a ser a que não pode ser esquecida. Um produto que inverteu a
        regra do consentimento e perdeu a da descadência trocou uma coisa má por
        outra.
        """
        lista_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, lista_id, CSV_SIMPLES, _app_settings())
        remetente = _remetente_confirmado(conn, normal_user, captured_emails)
        service.definir_from_da_lista(conn, normal_user, lista_id, remetente["id"])

        alvo = service.endereco_da_lista(
            conn, lista_id, _id_do_endereco(conn, lista_id, "bruno@exemplo.pt")
        )
        service.descadenciar(conn, alvo["id"])

        resultado = service.destinatarios(conn, lista_id)
        assert [row["email"] for row in resultado["destinatarios"]] == ["ana@exemplo.pt"], (
            "quem se descadenciou voltou a entrar no envio"
        )

    def test_o_codigo_nao_volta_na_resposta(self, conn, normal_user: int, settings) -> None:
        """Pedir o código envia email e não devolve nada de utilizável.

        O serviço é quem envia; se devolvesse o código, qualquer rota que o
        Receipt ele devolveria a quem tem sessão, e a confirmação do `from` deixa
        de provar nada.
        """
        remetente = service.registar_remetente(conn, normal_user, "envio@exemplo.pt")
        devolveu = service.pedir_confirmacao_remetente(
            conn, normal_user, remetente["id"], _app_settings(settings)
        )

        assert devolveu is None, "a função devolveu alguma coisa; devolve `None`"

    def test_o_email_de_confirmacao_manda_o_codigo(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """O código chega por email, e o email diz o que está a ser confirmado."""
        remetente = service.registar_remetente(conn, normal_user, "envio@exemplo.pt")
        service.pedir_confirmacao_remetente(
            conn, normal_user, remetente["id"], _app_settings(settings)
        )

        assert captured_emails, "não foi enviado nenhum email de confirmação"
        assert captured_emails[-1]["to"] == "envio@exemplo.pt"
        assert "remetente" in captured_emails[-1]["subject"].lower()

    def test_o_codigo_errado_conta_a_tentativa(self, conn, normal_user: int) -> None:
        """Cinco tentativas e o `from` fica por confirmar para sempre.

        Sem tecto, um código de seis dígitos com seis posições e um hash de
        `scrypt` por tentativa é lento mas não impossível; com tecto, é uma
        aposta com data de validade.
        """
        remetente = _remetente_pendente(conn, normal_user)
        for _ in range(service.MAX_CONFIRM_ATTEMPTS):
            assert (
                service.confirmar_remetente(conn, normal_user, remetente["id"], "000000") is False
            )

        # Um código qualquer continua a ser recusado, porque o tecto já não é um
        # número de tentativas que resta — é uma parede.
        assert service.confirmar_remetente(conn, normal_user, remetente["id"], "111111") is False

        atual = service.remetente_do_utilizador(conn, normal_user, remetente["id"])
        assert atual["confirmed_at"] is None
        assert atual["confirmation_attempts"] >= service.MAX_CONFIRM_ATTEMPTS

    def test_o_codigo_expira(self, conn, normal_user: int, settings, captured_emails) -> None:
        """Passado o prazo, um código que chegou por email não confirma."""
        remetente = _remetente_pendente(conn, normal_user)
        service.pedir_confirmacao_remetente(
            conn, normal_user, remetente["id"], _app_settings(settings)
        )
        codigo = _codigo_do_ultimo_email(captured_emails)

        mais_tarde = security.utcnow() + datetime.timedelta(minutes=90)
        assert (
            service.confirmar_remetente(
                conn, normal_user, remetente["id"], codigo, agora=mais_tarde
            )
            is False
        )

    def test_o_cooldown_e_por_remetente(self, conn, normal_user: int, settings) -> None:
        """Pedir duas vezes seguidas recusa a segunda.

        O cooldown era por endereço de destinatário e passou a ser por
        remetente. Se passasse a ser global por utilizador, dois `from`
        diferentes estorvam-se; se desaparecesse, o pedido de código vira o
        caminho para encher a caixa de entrada de alguém.
        """
        app_settings = _app_settings(settings)
        primeiro = _remetente_pendente(conn, normal_user, "um@exemplo.pt")
        service.pedir_confirmacao_remetente(conn, normal_user, primeiro["id"], app_settings)
        with pytest.raises(service.ErroConfirmacao) as erro:
            service.pedir_confirmacao_remetente(conn, normal_user, primeiro["id"], app_settings)
        assert "cooldown" in str(erro.value).lower() or "Pediste" in str(erro.value)

        # Um `from` **diferente** não é afectado pelo cooldown do primeiro.
        segundo = _remetente_pendente(conn, normal_user, "dois@exemplo.pt")
        service.pedir_confirmacao_remetente(conn, normal_user, segundo["id"], app_settings)

    def test_o_from_da_outro_utilizador_nao_e_aceite(self, conn, normal_user: int) -> None:
        """O teste de dono, na função que decide.

        Aceitar o `id` e confiar no `SELECT` de envio seria tarde: a lista ficaria
        a mostrar um `from` que não pode usar.
        """
        outro = _criar_utilizador(conn, "bruno@exemplo.pt")
        lista_id = _lista(conn, normal_user)
        remetente_alheio = service.registar_remetente(conn, outro, "dele@exemplo.pt")

        assert (
            service.definir_from_da_lista(conn, normal_user, lista_id, remetente_alheio["id"])
            is False
        ), "uma lista aceitou o from de outro utilizador"
        assert service.lista_pode_enviar(conn, lista_id) is False

    def test_confirmar_o_from_de_outro_nao_e_possivel(
        self, conn, normal_user: int, captured_emails
    ) -> None:
        """Nem por via do `sender_id`, que é o mesmo `id` que o outro tem.

        O dono legítimo **mantém** o seu direito a confirmar depois. Um teste que
        bloqueasse os dois deixaria passar uma implementação que destruísse o
        estado do remetente alheio ao tentar — que é uma forma de negação de
        serviço entre utilizadores.
        """
        outro = _criar_utilizador(conn, "bruno@exemplo.pt")
        remetente = service.registar_remetente(conn, outro, "dele@exemplo.pt")
        service.pedir_confirmacao_remetente(conn, outro, remetente["id"], _app_settings())
        codigo = _codigo_do_ultimo_email(captured_emails)

        assert service.confirmar_remetente(conn, normal_user, remetente["id"], codigo) is False, (
            "um utilizador confirmou o from de outro"
        )
        assert service.confirmar_remetente(conn, outro, remetente["id"], codigo) is True, (
            "o dono legítimo deixou de poder confirmar o seu próprio from"
        )


def test_a_importacao_diz_que_o_operador_assume_o_consentimento(conn, normal_user: int) -> None:
    """A afirmação que sustenta a lista tem de ser feita ao operador (FR-6.5).

    Um operador que não sabe que assumiu a responsabilidade não pode ter
    concordado com ela. Se o aviso sumisse em silêncio, o produto estaria a
    assumir o consentimento em nome de alguém que nunca o viu.
    """
    lista_id = _lista(conn, normal_user)
    resultado = service.importar_csv(conn, normal_user, lista_id, CSV_SIMPLES, _app_settings())

    assert resultado.aviso_consentimento, (
        "a importação não diz nada sobre consentimento — e sem isso é o produto "
        "a assumir o consentimento de quem importa em silêncio"
    )
    assert "autoriza" in resultado.aviso_consentimento


def test_a_importacao_nao_manda_email_para_nem(conn, normal_user: int, captured_emails) -> None:
    """Importar não confirma ninguém, e portanto não manda nada a ninguém.

    Este era o caminho por onde 5000 emails de confirmação saíam de uma vez. É o
    relay de email bombing que a revisão T014 temia, e ele desapareceu com a
    confirmação por destinatário.
    """
    lista_id = _lista(conn, normal_user)
    service.importar_csv(conn, normal_user, lista_id, CSV_SIMPLES, _app_settings())

    assert not captured_emails, (
        f"a importação mandou {len(captured_emails)} emails; não devia mandar nenhum"
    )


def test_importar_nao_tem_mais_o_argumento_de_confirmacao() -> None:
    """O flag saiu, e sai por uma razão que não é só de estilo.

    Existia para dar ao operador a opção de assumir o consentimento. A decisão
    foi assumir sem perguntar, e um flag que não muda o comportamento é um flag
    que alguém vai voltar a usar para reintroduzir o caminho que se fechou.
    """
    parametros = inspect.signature(service.importar_csv).parameters
    assert "confirmar_imediatamente" not in parametros, (
        "importar_csv ainda aceita o flag de confirmação; o T017-A removeu-o"
    )
