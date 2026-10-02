"""Testes das listas de destinatários.

O ficheiro está organizado pela ordem em que um endereço passa a poder receber.
A razão é que o teste mais importante — *ninguém recebe sem confirmar* — só faz
sentido depois de o leitor ter visto o que é um endereço por confirmar.

A mutação M-18 troca `confirmed_at IS NOT NULL` por `1=1` no `SELECT` de
`destinatarios()` e tem de morrer. Se algum dia ela passar, a lista com 5000
pendentes está a receber email e este ficheiro deixou de dizer a verdade.
"""

from __future__ import annotations

import pytest
from asgi_client import SyncASGIClient
from conftest import csrf_from
from test_auth_flows import _signed_in

from mailutils import config, db, security
from mailutils.lists import service

UA_OUTRO = "Mozilla/5.0 (Windows NT 10.0) OutroBrowser/1.0"

CSV_SIMPLES = b"nome,email\nAna,ana@exemplo.pt\nBruno,bruno@exemplo.pt\n"


def _app_settings(**overrides) -> config.Settings:
    base = config.load_settings(env="development")
    return config.Settings(
        **{
            **{field: getattr(base, field) for field in base.__dataclass_fields__},
            "public_base_url": "https://mailutils.exemplo.pt",
            **overrides,
        }
    )


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


def _confirmar(conn, list_id: int, settings, user_id: int, email: str) -> None:
    """Faz o caminho inteiro: pede código e confirma com o código certo."""
    ids = _enderecos(conn, list_id)
    service.pedir_confirmacao(conn, user_id, settings, list_id, [ids[email]])
    codigo = _ultimo_codigo(email)
    service.confirmar(conn, list_id, ids[email], codigo)


#: O código que saiu, por endereço. Um teste que confirmasse com um código
#: inventado não provaria nada: a confirmação tem de ser feita com o código
#: **real**, e é isso que se está a testar.
_codigos: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _captura_codigos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarda o código que saiu, para poder confirmar com ele."""
    _codigos.clear()
    from mailutils import mailer

    def fake(settings, to_address, code, list_name):  # noqa: ANN001
        _codigos[to_address] = code

    monkeypatch.setattr(mailer, "send_confirmation", fake)


def _ultimo_codigo(email: str) -> str:
    return _codigos[email]


def _app_settings(**overrides) -> config.Settings:
    base = config.load_settings(env="development")
    return config.Settings(
        **{
            **{field: getattr(base, field) for field in base.__dataclass_fields__},
            "public_base_url": "https://mailutils.exemplo.pt",
            **overrides,
        }
    )


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


def _confirmar(conn, list_id: int, settings, user_id: int, email: str) -> None:
    """Faz o caminho inteiro: pede o código e confirma com o código certo."""
    ids = _enderecos(conn, list_id)
    service.pedir_confirmacao(conn, user_id, settings, list_id, [ids[email]])
    service.confirmar(conn, list_id, ids[email], _codigos[email])


class TestInvarianteCentral:
    """`confirmed_at IS NOT NULL` — a regra que separa listas de spam."""

    def test_importado_nao_e_destinatario(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        assert service.destinatarios(conn, list_id) == []

    def test_ter_o_codigo_nao_chega_a_ser_destinatario(self, conn, normal_user: int) -> None:
        """O passo onde um produto mal feito vira relay: o código **saiu** para
        um endereço que ninguém pediu, e o endereço ainda não pode receber."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = list(_enderecos(conn, list_id).values())

        resultado = service.pedir_confirmacao(conn, normal_user, settings, list_id, ids)

        assert resultado["enviados"] == 2, "o código não chegou a sair"
        assert service.destinatarios(conn, list_id) == [], (
            "um endereço que recebeu o código mas não confirmou já é destinatário"
        )

    def test_confirmado_passa_a_ser_destinatario(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        _confirmar(conn, list_id, settings, normal_user, "ana@exemplo.pt")

        emails = {d["email"] for d in service.destinatarios(conn, list_id)}
        assert emails == {"ana@exemplo.pt"}

    def test_o_hash_e_apagado_ao_confirmar(self, conn, normal_user: int) -> None:
        """Sem isto o código confirmava outra vez, e sobrevivia a uma
        descadencia."""
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = _enderecos(conn, list_id)
        service.pedir_confirmacao(conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]])
        service.confirmar(conn, list_id, ids["ana@exemplo.pt"], _codigos["ana@exemplo.pt"])

        linha = service.endereco_da_lista(conn, list_id, ids["ana@exemplo.pt"])
        assert linha["confirmation_hash"] is None
        assert linha["confirmation_expires_at"] is None

    def test_descadenciado_deixa_de_ser_destinatario(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        _confirmar(conn, list_id, settings, normal_user, "ana@exemplo.pt")
        assert len(service.destinatarios(conn, list_id)) == 1

        service.descadenciar(conn, _enderecos(conn, list_id)["ana@exemplo.pt"])
        assert service.destinatarios(conn, list_id) == []

    def test_descadencia_e_irreversivel_pelo_produto(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        _confirmar(conn, list_id, settings, normal_user, "ana@exemplo.pt")
        address_id = _enderecos(conn, list_id)["ana@exemplo.pt"]

        service.descadenciar(conn, address_id)
        assert service.descadenciar(conn, address_id) is False, (
            "descadenciar outra vez reescreveu o estado: a descadencia tem de ser "
            "idempotente e irreversivel pelo produto"
        )


class TestConfirmacaoPorCodigo:
    def test_codigo_errado_nao_confirma(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = _enderecos(conn, list_id)
        service.pedir_confirmacao(conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]])

        with pytest.raises(service.ErroConfirmacao):
            service.confirmar(conn, list_id, ids["ana@exemplo.pt"], "000000")
        assert service.destinatarios(conn, list_id) == []

    def test_o_codigo_real_confirma(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = _enderecos(conn, list_id)
        service.pedir_confirmacao(conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]])
        service.confirmar(conn, list_id, ids["ana@exemplo.pt"], _codigos["ana@exemplo.pt"])
        assert len(service.destinatarios(conn, list_id)) == 1

    def test_o_codigo_e_de_uso_unico(self, conn, normal_user: int) -> None:
        """Reconfirmar não faz mal — é idempotente — mas o código já não existe.

        A propriedade que importa não é "levanta erro", é que o hash foi apagado:
        é isso que impede o código de voltar a valer e, pior, de sobreviver a uma
        descadência.
        """
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = _enderecos(conn, list_id)
        service.pedir_confirmacao(conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]])
        codigo = _codigos["ana@exemplo.pt"]
        service.confirmar(conn, list_id, ids["ana@exemplo.pt"], codigo)

        assert (
            service.endereco_da_lista(conn, list_id, ids["ana@exemplo.pt"])["confirmation_hash"]
            is None
        )
        service.confirmar(conn, list_id, ids["ana@exemplo.pt"], codigo)
        assert len(service.destinatarios(conn, list_id)) == 1

    def test_exceder_tentativas_invalida_o_codigo(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = _enderecos(conn, list_id)
        service.pedir_confirmacao(conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]])
        codigo = _codigos["ana@exemplo.pt"]

        for _ in range(service.MAX_CONFIRM_ATTEMPTS):
            with pytest.raises(service.ErroConfirmacao):
                service.confirmar(conn, list_id, ids["ana@exemplo.pt"], "000000")

        # O codigo certo ja nao vale, porque foi invalidado.
        with pytest.raises(service.ErroConfirmacao):
            service.confirmar(conn, list_id, ids["ana@exemplo.pt"], codigo)

    def test_codigo_expirado_nao_confirma(self, conn, normal_user: int) -> None:
        settings = _app_settings(otp_ttl_minutes=10)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = _enderecos(conn, list_id)
        service.pedir_confirmacao(conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]])
        codigo = _codigos["ana@exemplo.pt"]

        conn.execute(
            "UPDATE list_addresses SET confirmation_expires_at = ? WHERE id = ?",
            (security.iso(security.utcnow()), ids["ana@exemplo.pt"]),
        )
        with pytest.raises(service.ErroConfirmacao):
            service.confirmar(conn, list_id, ids["ana@exemplo.pt"], codigo)


class TestLimitesAntiAbuso:
    def test_cooldown_por_endereco(self, conn, normal_user: int) -> None:
        """O cooldown é por endereço e vale entre listas (FR-6.6)."""
        settings = _app_settings(confirm_cooldown_seconds=60)
        a = _lista(conn, normal_user, "A")
        b = _lista(conn, normal_user, "B")
        for list_id in (a, b):
            service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)

        primeiro = service.pedir_confirmacao(
            conn, normal_user, settings, a, [_enderecos(conn, a)["ana@exemplo.pt"]]
        )
        segundo = service.pedir_confirmacao(
            conn, normal_user, settings, b, [_enderecos(conn, b)["ana@exemplo.pt"]]
        )

        assert primeiro["enviados"] == 1
        assert segundo["enviados"] == 0, (
            "o mesmo endereço recebeu dois codigos em listas diferentes: o cooldown "
            "tem de ser por endereco, nao por address_id"
        )
        assert segundo["em_cooldown"] == 1

    def test_cooldown_respeita_o_tempo(self, conn, normal_user: int) -> None:
        settings = _app_settings(confirm_cooldown_seconds=60)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = _enderecos(conn, list_id)

        service.pedir_confirmacao(conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]])
        conn.execute(
            "UPDATE list_addresses SET confirmation_sent_at = ? WHERE id = ?",
            (security.iso(security.in_minutes(-2)), ids["ana@exemplo.pt"]),
        )
        resultado = service.pedir_confirmacao(
            conn, normal_user, settings, list_id, [ids["ana@exemplo.pt"]]
        )
        assert resultado["enviados"] == 1

    def test_teto_de_pendentes_trava_a_importacao(self, conn, normal_user: int) -> None:
        """O tecto e por utilizador, nao por lista: espalhar por 30 listas nao
        contorna nada.

        E e verificado na **importacao**, onde o pendente nasce. Pedir um codigo
        nao cria um pendente novo, pelo que verificar em `pedir_confirmacao`
        era tarde: ou nunca era atingido, ou era atingido a partida e o
        utilizador ficava preso sem poder confirmar o que acabara de importar.
        """
        settings = _app_settings(max_pending_confirmations=2)
        list_id = _lista(conn, normal_user)
        linhas = (
            "nome,email\n" + "\n".join(f"Pessoa{i},p{i}@exemplo.pt" for i in range(5))
        ).encode()

        resultado = service.importar_csv(conn, normal_user, list_id, linhas, settings)
        assert resultado.importados == 2
        assert resultado.invalidos, "o tecto tem de ser visivel, nao silencioso"
        assert any("teto" in i or "tecto" in i for i in resultado.invalidos)

    def test_teto_de_pendentes_acumula_entre_listas(self, conn, normal_user: int) -> None:
        """Duas importacoes de 2 com um tecto de 3: a segunda fica com 1."""
        settings = _app_settings(max_pending_confirmations=3)
        duas = "nome,email\nA,a@exemplo.pt\nB,b@exemplo.pt\n"
        primeira = _lista(conn, normal_user, "Uma")
        segunda = _lista(conn, normal_user, "Duas")

        service.importar_csv(conn, normal_user, primeira, duas.encode(), settings)
        resultado = service.importar_csv(conn, normal_user, segunda, duas.encode(), settings)

        assert service.contar_enderecos(conn, primeira) == 2
        assert service.contar_enderecos(conn, segunda) == 1
        assert resultado.invalidos

    def test_o_teto_nao_prende_o_utilizador(self, conn, normal_user: int) -> None:
        """O que entrou tem de poder pedir confirmacao. Um tecto que bloqueia a
        unica accao que torna o endereco utilizavel e um beco sem saida."""
        settings = _app_settings(max_pending_confirmations=2)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)

        resultado = service.pedir_confirmacao(
            conn, normal_user, settings, list_id, list(_enderecos(conn, list_id).values())
        )
        assert resultado["enviados"] == service.contar_enderecos(conn, list_id)
        assert resultado["excedidos"] == 0

    def test_teto_de_enderecos_por_lista(self, conn, normal_user: int) -> None:
        settings = _app_settings(max_list_size=3)
        list_id = _lista(conn, normal_user)
        linhas = ("nome,email\n" + "\n".join(f"P{i},p{i}@exemplo.pt" for i in range(10))).encode()

        resultado = service.importar_csv(conn, normal_user, list_id, linhas, settings)
        assert resultado.importados == 3
        assert service.contar_enderecos(conn, list_id) == 3
        assert resultado.invalidos, "a interrupcao por tecto tem de ser visivel"

    def test_confirmar_liberta_uma_vaga(self, conn, normal_user: int) -> None:
        """Um confirmado deixa de contar para o tecto de pendentes, para que a
        lista nao fique bloqueada para sempre depois do primeiro envio."""
        settings = _app_settings(max_pending_confirmations=2)
        list_id = _lista(conn, normal_user)
        service.importar_csv(
            conn, normal_user, list_id, b"nome,email\nA,a@x.pt\nB,b@x.pt\n", settings
        )
        service.importar_csv(conn, normal_user, list_id, b"nome,email\nC,c@x.pt\n", settings)
        assert service.contar_enderecos(conn, list_id) == 2

        _confirmar(conn, list_id, settings, normal_user, "a@x.pt")
        assert service.pendentes_por_utilizador(conn, normal_user) == 1

        resultado = service.importar_csv(
            conn, normal_user, list_id, b"nome,email\nD,d@x.pt\n", settings
        )
        assert resultado.importados == 1, (
            "confirmar um endereco nao libertou a vaga do tecto de pendentes"
        )


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

    def test_nao_se_pede_confirmacao_a_outro(self, conn, normal_user: int) -> None:
        settings = _app_settings()
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, settings)
        ids = list(_enderecos(conn, list_id).values())
        with pytest.raises(service.ErroConfirmacao):
            service.pedir_confirmacao(conn, 9999, settings, list_id, ids)


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

    def test_o_codigo_nunca_volta_na_resposta(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """`CLAUDE.md`, `Never Do`: um código de OTP nunca numa resposta HTTP,
        nem em caso de erro."""
        _signed_in(app, conn, normal_user)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, _app_settings())
        ids = list(_enderecos(conn, list_id).values())

        token = csrf_from(app, f"/listas/{list_id}")
        pagina = app.post(
            f"/listas/{list_id}/confirmar-pedido",
            data={"csrf_token": token, "enderecos": ",".join(str(i) for i in ids)},
            follow_redirects=True,
        )

        codigos_reais = [_codigos[e] for e in _codigos]
        assert codigos_reais, "nenhum código saiu: o teste não está a testar nada"
        for codigo in codigos_reais:
            assert codigo not in pagina.text, f"o código de confirmação {codigo!r} está na página"

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

    def test_formulario_de_confirmacao_mostra_o_email(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        list_id = _lista(conn, normal_user)
        service.importar_csv(conn, normal_user, list_id, CSV_SIMPLES, _app_settings())
        address_id = _enderecos(conn, list_id)["ana@exemplo.pt"]
        body = app.get(f"/listas/{list_id}/confirmar?address_id={address_id}").text
        assert "ana@exemplo.pt" in body

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

    def test_a_pagina_diz_que_importar_nao_confirma(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """A regra tem de estar escrita onde o utilizador a encontra, não só no
        codigo."""
        _signed_in(app, conn, normal_user)
        _lista(conn, normal_user)
        body = app.get("/listas").text
        assert "não" in body.lower()
        assert "confirma" in body.lower()


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
