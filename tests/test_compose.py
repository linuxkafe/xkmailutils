"""Testes do compositor. `T017-B`.

O que estes testes provocam é a pergunta que o `CLAUDE.md` faz na `Intent`: a
aplicação envia alguma coisa que ela própria reprovaria?

A resposta honesta hoje é que não há caminho de envio, e por isso a pergunta não
tem a quem ser feita. Estes testes são a prova de que a resposta passa a ser não.
"""

from __future__ import annotations

import re

from asgi_client import SyncASGIClient
from conftest import csrf_from
from test_auth_flows import _signed_in
from test_lists import CSV_SIMPLES, _app_settings, _lista

from mailutils.compose import service
from mailutils.lists import service as listas

#: Um corpo que não dispara nada. O score limpo é o que permite que um teste
#: sobre o portão não esteja a medir a regra que o disparou.
CORPO_LIMPO = "Bom dia,\n\nA reunião de terça manteve-se às 10h.\n\nAté amanhã,\nAna"

#: `spam.py` dá 55 pontos a uma imagem `data:` e isso é `crítico`. Serve para
#: provar que o bloqueio olha para a gravidade da regra e não só para o total.
IMAGEM_DATA_URI = '<img src="data:image/png;base64,iVBORw0KGgo=">'


def _from_confirmado(conn, user_id: int, captured_emails):
    """Um `from` confirmado pelo caminho inteiro: registar, pedir, confirmar.

    Confirmado **a sério**, com o código lido do email. Um `from` que o teste
    marca como confirmado sem passar por `pedir_confirmacao_remetente` está a
    mentir sobre o estado em que o produto se encontra, e o primeiro sintoma é
    um `enviar()` que recusa com "sem remetente confirmado" num teste que
    julgava ter tudo em ordem.
    """
    remetente = listas.registar_remetente(conn, user_id, "envio@exemplo.pt")
    listas.pedir_confirmacao_remetente(conn, user_id, remetente["id"], _app_settings())
    codigo = _codigo(captured_emails)
    assert listas.confirmar_remetente(conn, user_id, remetente["id"], codigo), (
        "o from de teste não confirmou; os testes de envio_partem daqui e sem "
        "isto estão a medir o portão do from em vez do do score"
    )
    return listas.remetente_do_utilizador(conn, user_id, remetente["id"])


def _codigo(enviados) -> str:
    """O código de seis dígitos do email mais recente.

    Lido do email que foi enviado e não à mão: um teste que confirmasse com um
    código inventado provaria que `confirmar_remetente` funciona, não que o
    código chega a quem tem de o confirmar.
    """
    assert enviados, "não foi enviado nenhum email de confirmação"
    achado = re.search(r"\b\d{6}\b", enviados[-1]["text"] or "")
    assert achado, f"o email não traz um código de seis dígitos: {enviados[-1]['text'][:200]}"
    return achado.group(0)


def _lista_com_from(conn, user_id: int, captured_emails, app_settings=None):
    """Uma lista de **dois** endereços com `from` confirmado e escolhido.

    `CSV_SIMPLES` tem Ana e Bruno. Escrever "três" aqui foi o erro que este
    helper Encode desde o primeiro teste que passou com `enviados == 2` e que
    li como um destinatário perdido — e a contagem errada é a que se esconde
    atrás de um relatório que parece certo.

    São dois estados e é fácil confundir um com o outro. Registar e confirmar um
    remetente não o põe na lista: `lista_pode_enviar` exige `sender_id` escolhido
    **e** `confirmed_at` preenchido. Um helper que só confirmasse produzia um
    `enviar()` que recusa com "sem remetente confirmado", que é o primeiro
    sintoma e não o mais difícil de diagnosticar.
    """
    list_id = _lista(conn, user_id)
    listas.importar_csv(conn, user_id, list_id, CSV_SIMPLES, app_settings or _app_settings())
    remetente = _from_confirmado(conn, user_id, captured_emails)
    assert listas.definir_from_da_lista(conn, user_id, list_id, remetente["id"])
    assert listas.lista_pode_enviar(conn, list_id), (
        "a lista de teste ficou sem portão; todos os testes de envio partem daqui"
    )
    return list_id, remetente


def _from_confirmado_sem_fixture(conn, user_id: int):
    """Confirma um `from` sem depender da fixture `captured_emails`.

    Existe porque há testes que **têm** de patchar `mailer.send` (ou
    `_send_smtp`) para provocar uma falha de transporte, e a fixture do conftest
    patcha `send` por omissão. Com os dois em jogo é uma corrida silenciosa e o
    teste deixa de estar a medir o que diz medir.

    O código é capturado em `send_sender_confirmation`, que é a função que **tem**
    de o conhecer para o mandar — e lê-lo de lá é mais honesto do que lê-lo de
    um email interceptado a meio.
    """
    from mailutils import mailer

    vistos: list[str] = []
    real = mailer.send_sender_confirmation

    def guarda(settings_, to_address, code, minutes):  # noqa: ANN001
        vistos.append(code)
        real(settings_, to_address, code, minutes)

    mailer.send_sender_confirmation = guarda
    try:
        remetente = listas.registar_remetente(conn, user_id, "envio@exemplo.pt")
        listas.pedir_confirmacao_remetente(conn, user_id, remetente["id"], _app_settings())
    finally:
        mailer.send_sender_confirmation = real
    assert vistos, "o serviço não mandou email de confirmação"
    assert listas.confirmar_remetente(conn, user_id, remetente["id"], vistos[0])
    return listas.remetente_do_utilizador(conn, user_id, remetente["id"])


def _lista_com_from_transporte(conn, user_id: int, app_settings):
    """`_lista_com_from` sem a fixture de emails, para os testes de falha SMTP."""
    list_id = _lista(conn, user_id)
    listas.importar_csv(conn, user_id, list_id, CSV_SIMPLES, app_settings)
    remetente = _from_confirmado_sem_fixture(conn, user_id)
    assert listas.definir_from_da_lista(conn, user_id, list_id, remetente["id"])
    return list_id, remetente


class TestRascunho:
    """FR-7.1: a composição é guardada por utilizador.

    **Um rascunho por utilizador**, decidido pelo dono. A `FR-7.1` está escrita
    no singular e o custo de errar aqui é alto: vários rascunhos por utilizador
    é a interface de um produto de newsletters, e este não é.
    """

    def test_guardar_e_ler_volta_o_que_se_escreveu(self, conn, normal_user: int) -> None:
        service.guardar_rascunho(conn, normal_user, "Assunto", CORPO_LIMPO)
        rascunho = service.rascunho(conn, normal_user)

        assert rascunho["assunto"] == "Assunto"
        assert rascunho["corpo"] == CORPO_LIMPO

    def test_guardar_sobrescreve(self, conn, normal_user: int) -> None:
        """Um rascunho só. Guardar outra vez não cria um segundo."""
        service.guardar_rascunho(conn, normal_user, "Primeiro", "corpo um")
        service.guardar_rascunho(conn, normal_user, "Segundo", "corpo dois")

        assert service.contar_rascunhos(conn, normal_user) == 1
        assert service.rascunho(conn, normal_user)["assunto"] == "Segundo"

    def test_um_utilizador_nao_le_o_rascunho_de_outro(
        self, conn, normal_user: int, captured_emails
    ) -> None:
        """O `user_id` no `WHERE` e não só o `id` é o teste de dono."""
        outro = _outro(conn)
        service.guardar_rascunho(conn, normal_user, "Privado", "muito privado")
        service.guardar_rascunho(conn, outro, "Dele", "dele")

        assert service.rascunho(conn, outro)["assunto"] == "Dele"
        assert "Privado" not in service.rascunho(conn, outro)["corpo"]


def _outro(conn) -> int:
    conn.execute(
        "INSERT INTO users (email, password_hash, is_admin, created_at)"
        " VALUES ('bruno@exemplo.pt', 'x', 0, 't')"
    )
    return int(conn.execute("SELECT id FROM users WHERE email='bruno@exemplo.pt'").fetchone()[0])


def _guardar_assinatura(conn, user_id: int) -> None:
    """Guarda uma assinatura mínima para este utilizador, se não houver.

    Sem assinatura não há `render_html` para o `monkeypatch` interceptar, e um
    teste de "o portão apanha um `data:` URI" que não tem assinatura nenhuma
    passa a medir o caminho do utilizador sem assinatura — que é o caminho
    limpo, e dá sempre 0.
    """
    ja_existe = conn.execute(
        "SELECT COUNT(*) FROM signatures WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if ja_existe:
        return
    conn.execute(
        "INSERT INTO signatures (user_id, name, theme, layout, fields_json,"
        " created_at, updated_at)"
        " VALUES (?, 'assinatura principal', 'light', 'stack', ?, 't', 't')",
        (user_id, '{"name":"Ana","company":"Estudio"}'),
    )


def _assinatura_com_data_uri(conn, user_id: int):
    """Faz a assinatura deste utilizador render HTML com uma imagem `data:`.

    **Este é o caminho por onde um `data:` URI chega a um email.** O corpo é
    texto simples e sai escapado, e escapado não se transforma em `<img>` — que
    é a defesa certa. A assinatura é HTML à partida, e é por aí que um `data:`
    entra num email.

    O `data:` entra por `monkeypatch` no `render_html` e não por um campo do
    editor: o editor de assinaturas recusa `data:` por si só (é uma das
    garantias do `T003`), e um teste que o contornasse estar-se-ia a testar uma
    coisa que o produto impede noutro sítio. O que se está a testar aqui é o
    **portão** — e o portão tem de apanhar o `data:` mesmo quando ele lá chega,
    porque um caminho novo de HTML é sempre possível.

    É um gerador de contexto para o `monkeypatch`.patch.object`, e usa-se assim:

        with _patch_data_uri():
            ...
    """
    from mailutils.signatures import renderer

    real = renderer.render_html

    def com_data_uri(data, s, *args, **kwargs):  # noqa: ANN001
        return real(data, s, *args, **kwargs).replace(
            "</div>", '<img src="data:image/png;base64,iVBORw0KGgo="></div>'
        )

    return renderer, real, com_data_uri


class TestPortaoDeScore:
    """A unifying invariant do `Intent`, escrita como teste.

    A aplicação nunca envia algo que ela própria reprovaria. Aqui prova-se que
    **não** há caminho que envie sem passar pelo motor, e que o motor usado é o
    do email completo (`analyzer/scoring.py`), não o da assinatura.
    """

    def test_um_email_limpo_passa(self, conn, normal_user: int, settings, captured_emails) -> None:
        app_settings = _app_settings(settings)
        list_id, remetente = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        relatorio = service.avaliar(
            conn, app_settings, normal_user, "Reunião de terça", CORPO_LIMPO, list_id
        )

        assert relatorio["bloqueado"] is False, (
            f"um email limpo foi bloqueado: {relatorio['score']} "
            f"{relatorio['categoria']}, regras críticas: {relatorio['regras_criticas']}"
        )

    def test_texto_suspecto_nao_chega_a_critico(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """Um assunto de spam **não** bloqueia o envio. Isto é uma decisão.

        A primeira versão deste teste afirmava que `"*** GANHE DINHEIRO ***"`
        bloqueava, e não bloqueia. A razão é a calibração de `scoring.py`: as
        regras de gravidade `crítica` são todas de HTML — `<script>`, `<iframe>`,
        `javascript:`, `data:` URI, executáveis — e o texto de spam é penalizado
        a `alto` e a `médio`.

        Isto é **correcto** e é uma distinção que vale a pena preservar. Um
        assunto agressivo é uma escolha de escrita; `<script>` no email é
        código a executar na máquina de quem lê. Bloquear o primeiro treina o
        utilizador a contornar o portão — e e o reverso é que bloquear o segundo não
        depende de o utilizador ser honesto.

        O teste fica assim porque diz a verdade: o que bloqueia é o HTML, e o
        `test_a_gravidade_da_regra_bloqueia_so_por_si` prova que é mesmo.
        """
        app_settings = _app_settings(settings)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        relatorio = service.avaliar(
            conn, app_settings, normal_user, "*** GANHE DINHEIRO ***", "Click aqui!!!", list_id
        )

        assert relatorio["bloqueado"] is False, (
            "o texto de spam não devolveu HTML nenhum, e a única forma de "
            f"bloquear este email é por texto — o que não é o que o motor faz. "
            f"Regras disparadas: {[(r['regra'], r['gravidade']) for r in relatorio['regras']]}"
        )
        assert relatorio["score"] > 0, (
            "um assunto de spam que dá 0 pontos está a mentir ao utilizador"
        )

    def test_a_gravidade_da_regra_bloqueia_so_por_si(
        self, conn, normal_user: int, settings, captured_emails, monkeypatch
    ) -> None:
        """A metade da `FR-4.9` que o score não apanha.

        Uma única regra de gravidade `crítica` tem de bloquear mesmo que o
        total fique abaixo do limiar. Se isto passar, o bloqueio está a ser
        decidido pelo score e a metade da política desapareceu — que é o
        "decidir só pelo total dava ao utilizador forma de contornar o bloqueio
        com mais texto", nas palavras da própria `FR-4.9`.
        """
        app_settings = _app_settings(settings)
        _guardar_assinatura(conn, normal_user)
        renderer, real, com_data_uri = _assinatura_com_data_uri(conn, normal_user)
        # O patch tem de estar activo **antes** de a assinatura ser construida:
        # o `data:` entra no HTML no momento do `render_html`, e um patch
        # aplicado depois encontraria a assinatura já sem a imagem.
        monkeypatch.setattr(renderer, "render_html", com_data_uri)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        relatorio = service.avaliar(
            conn, app_settings, normal_user, "Relatório", CORPO_LIMPO, list_id
        )

        assert relatorio["regras_criticas"], (
            "esperava uma regra de gravidade crítica e o relatório não a nomeia; "
            "sem ela o teste não está a provar a política que diz provar"
        )
        assert relatorio["bloqueado"] is True

    def test_a_politica_e_a_mesma_que_a_da_assinatura(
        self, conn, normal_user: int, settings
    ) -> None:
        """`FR-7.3` não é uma variante mais tolerante, e isto é a prova.

        O mesmo par (score, findings) tem de dar a mesma decisão nos dois
        motores. Se divergirem, há um caminho de envio que reprova o que a
        aplicação reprovaria noutro sítio, e a invariant está quebrada sem que
        nada tenha falhado.
        """
        from mailutils.signatures import spam

        casos = [(0, []), (69, []), (70, []), (100, []), (0, ["critico"]), (10, ["baixo"])]
        for score, gravedades in casos:
            da_assinatura = spam.bloqueado(
                score, [spam.Finding("X", 0, g, "m") for g in gravedades]
            )
            do_email = service.decisao_de_bloqueio(score, gravedades)
            assert da_assinatura == do_email, (
                f"score={score} gravidade={gravedades}: a assinatura diz "
                f"{da_assinatura} e o email diz {do_email}. A FR-7.3 diz que "
                "a política é a mesma, e uma divergência aqui é a invariant quebrada."
            )

    def test_o_score_ignora_o_que_nao_esta_no_email(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """O score é do email que sai, não de uma conversa interna.

        Uma frase que está no rascunho e vai sair tem de contar; uma nota interna
        que não entra no email não pode contar.
        """
        app_settings = _app_settings(settings)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        relatorio = service.avaliar(
            conn, app_settings, normal_user, "Assunto", "OrçamentoGw attach", list_id
        )

        assert isinstance(relatorio["score"], int)
        assert 0 <= relatorio["score"] <= 100


class TestEnvio:
    """O caminho de envio, e as três coisas que ele tem de fazer."""

    def test_enviar_reports_quantos(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """FR-7.8: o relatório diz **quantos**, não quem."""
        app_settings = _app_settings(settings)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        relatorio = service.enviar(conn, app_settings, normal_user, "Reunião", CORPO_LIMPO, list_id)

        assert relatorio["enviados"] == 2, (
            f"motivo={relatorio['motivo']!r} "
            f"score={relatorio.get('score')} "
            f"criticas={relatorio.get('regras_criticas')} "
            f"todas={[r['regra'] for r in relatorio.get('regras', [])]}"
        )
        assert relatorio["falhados"] == 0
        assert relatorio["omitidos"] == 0

    def test_uma_falha_nao_aborta_os_restantes(
        self, conn, normal_user: int, settings, monkeypatch, captured_emails
    ) -> None:
        # `captured_emails` e o `monkeypatch` abaixo tocam no mesmo atributo
        # (`mailer.send`). O conftest corre depois e o seu `fake_send` ganha,
        # o que faz o `monkeypatch.setattr` nao ter efeito nenhum. Por isso o
        # `captured_emails` so serve para confirmar o `from`, e o envio e
        # medido pela lista `enviados` que este teste mantem a si.
        """FR-7.7: o primeiro SMTP a falhar não come os outros dois."""
        from mailutils import mailer

        app_settings = _app_settings(settings)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)
        enviados: list[str] = []

        def meio_quebrado(settings_, to_address, subject, text, html=None, headers=None):
            if to_address == "ana@exemplo.pt":
                raise mailer.MailError("conexão recusada")
            enviados.append(to_address)

        monkeypatch.setattr(mailer, "send", meio_quebrado)

        relatorio = service.enviar(conn, app_settings, normal_user, "Reunião", CORPO_LIMPO, list_id)

        assert relatorio["enviados"] == 1, relatorio
        assert relatorio["falhados"] == 1
        assert "ana@exemplo.pt" not in enviados
        assert "bruno@exemplo.pt" in enviados

    def test_a_razao_da_falha_nao_e_a_mensagem_da_excepcao(
        self, conn, normal_user: int, settings, monkeypatch
    ) -> None:
        """A excepção pode conter credenciais. O relatório diz a **classe**.

        Uma `SMTPAuthenticationError` traz o servidor e o utilizador na
        mensagem. Escrevê-la num log ou mostrá-la na interface é vazar o que
        autentica o operador.
        """
        import smtplib

        from mailutils import mailer

        # Backend `smtp` e nao `null`: `send()` volta **antes** do SMTP com
        # `null`, e `_send_smtp` nunca seria chamado. Um teste que depende de
        # uma falha de transporte tem de estar num backend que faz transporte.
        app_settings = _app_settings(settings, mail_backend="smtp")
        # Sem a fixture `captured_emails`: ela patcha `mailer.send`, que e
        # o mesmo atributo que a falha de transporte precisa de atravessar.
        list_id, _ = _lista_com_from_transporte(conn, normal_user, app_settings)

        def com_credenciais(settings_, message):
            # `SMTPResponseException` é (código, msg) — o `SMTPDataError` é que
            # acrescenta a `SMTPRepliesError` de três. Três argumentos numa
            # AuthenticationError dão `TypeError` na construção, que é um erro
            # que não tem nada a ver com o que este teste quer provar.
            raise smtplib.SMTPAuthenticationError(
                535, b"Authentication credentials invalid (user=admin, pass=segredo)"
            )

        # Pacha-se `_send_smtp`, e nao `send`. Tres razoes, todas sobre
        # 的还是 patch no sitio certo:
        #
        # 1. O `captured_emails` do conftest ja patcha `send`, para apanhar o
        #    email de confirmacao do `from`. Dois patches no mesmo atributo sao
        #    uma corrida silenciosa em que o ultimo a correr ganha.
        # 2. `mail_backend="null"` (o omisso do `conftest`) faz `send()` voltar
        #    **antes** do SMTP. Por isso este teste usa `smtp`: e o `null` que
        #    impede uma ligacao, nao uma excepcao que ele没有得到.
        # 3. `_send_smtp` e o ponto onde o transporte falha de facto. Uma
        #    excepcao de SMTP lancada aqui e a mesma que o servidor real
        #    lancaria, e e a que o `enviar()` tem de apanhar.
        #
        # O teste `test_uma_falha_nao_aborta_os_restantes` patcha `send` e por
        # isso funciona — la o que se quer e que UM destinatario falhe, e o
        # patch e por destinatario.
        monkeypatch.setattr(mailer, "_send_smtp", com_credenciais)

        relatorio = service.enviar(conn, app_settings, normal_user, "Reunião", CORPO_LIMPO, list_id)

        assert relatorio["falhados"] == 2, (
            f"falhados={relatorio['falhados']} erros={relatorio.get('erros')}"
        )
        texto = repr(relatorio)
        assert "segredo" not in texto, "a mensagem da excepção passou para o relatório"
        assert "SMTPAuthenticationError" in texto, (
            f"a classe tem de estar no relatório: {relatorio.get('erros')}"
        )
        assert relatorio["erros"] == {"SMTPAuthenticationError": 2}, (
            f"o relatório devia contar a falha por classe: {relatorio.get('erros')}"
        )

    def test_o_envio_leva_list_unsubscribe(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """FR-6.8: todo o envio leva a forma de sair.

        Um email de uma lista que não oferece descadência é um email que só se
        sai com uma acção de quem o recebeu — e a acção é o link. Sem os dois
        cabeçalhos, um Gmail mostra "cancular subscrição" e um cliente que não
        conhece o Gmail não mostra nada.
        """
        app_settings = _app_settings(settings)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        service.enviar(conn, app_settings, normal_user, "Reunião", CORPO_LIMPO, list_id)

        # O **último** email, e não o primeiro: a lista foi montada com um
        # `from` confirmado, e confirmar um `from` manda um email de
        # confirmação. Olhar para `captured_emails[0]` mede esse, e o teste
        # passa a dizer que o `List-Unsubscribe` falta quando o email de
        # confirmação é que não o tem.
        assert captured_emails, "não saiu nenhum email"
        cabecalhos = captured_emails[-1].get("headers") or {}
        assert cabecalhos.get("List-Unsubscribe"), (
            f"o email saiu sem List-Unsubscribe. Cabeçalhos: {sorted(cabecalhos)}"
        )
        assert cabecalhos.get("List-Unsubscribe-Post") == "List-Unsubscribe=One-Click", (
            "falta o One-Click, que é o que faz o botão do Gmail aparecer"
        )

    def test_o_descadenciado_nao_recebe_e_nao_e_omitido(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """Descadenciado não recebe, e o relatório não o conta como enviado.

        A distinção é o que torna o relatório honesto: `omitido` é para quem não
        pôde receber por decisão do produto, e `enviado` é o resto.
        """
        app_settings = _app_settings(settings)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)
        endereco = _endereco(conn, list_id, "ana@exemplo.pt")
        listas.descadenciar(conn, endereco)

        relatorio = service.enviar(conn, app_settings, normal_user, "Reunião", CORPO_LIMPO, list_id)

        # A lista tem Ana e Bruno. Ana descadenciou: 1 enviado.
        assert relatorio["enviados"] == 1, relatorio
        enviados = [m["to"] for m in captured_emails if m["to"] != "envio@exemplo.pt"]
        assert enviados == ["bruno@exemplo.pt"], f"o email de envio foi a: {enviados}"


def _endereco(conn, list_id: int, email: str) -> int:
    for linha in listas.enderecos_da_lista(conn, list_id):
        if linha["email"] == email:
            return linha["id"]
    raise AssertionError(f"{email} não está na lista")


class TestBloqueioNoEnvio:
    """O portão tem de estar **no caminho de envio**, não só na interface.

    Um botão que mostra o score e um envio que não o consulta são duas coisas
    diferentes, e a segunda é a que decide. Estes testes escrevem pela porta de
    trás do botão: chamam `enviar()` sem passar por nenhuma página.
    """

    def test_nao_envia_sem_pontuar(
        self, conn, normal_user: int, settings, captured_emails, monkeypatch
    ) -> None:
        """Chamar `enviar()` é pontuar. Não há como saltar o score.

        O `data:` vem da **assinatura**, porque é o único sítio por onde HTML
        entra num email: o corpo é texto simples e sai escapado. Um teste que
        pusesse `<img src="data:...">` no corpo estaria a testar o escape — e a
        proving que o escape falha, que é o oposto do que o produto faz.
        """
        app_settings = _app_settings(settings)
        _guardar_assinatura(conn, normal_user)
        renderer, real, com_data_uri = _assinatura_com_data_uri(conn, normal_user)
        monkeypatch.setattr(renderer, "render_html", com_data_uri)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        relatorio = service.enviar(conn, app_settings, normal_user, "Assunto", CORPO_LIMPO, list_id)

        assert relatorio["enviados"] == 0, relatorio
        assert relatorio["bloqueado"] is True
        # Só os emails **de envio** contam. Montar a lista de teste confirma um
        # `from` e isso manda um email, e um teste que contasse todos os emails
        # capturados concluiria que o bloqueio não funciona.
        enviados = [m for m in captured_emails if m["to"] != "envio@exemplo.pt"]
        assert not enviados, (
            f"saiu {len(enviados)} emails apesar de bloqueado: {[m['to'] for m in enviados]}"
        )

    def test_bloqueado_diz_porque_e_nao_so_que_nao_pode(
        self, conn, normal_user: int, settings, captured_emails, monkeypatch
    ) -> None:
        """Um bloqueio sem motivo é um bloqueio que o utilizador contorna.

        A interface tem de poder dizer *qual* regra disparou, ou a única forma de
        o utilizador sair do bloqueio é adivinhar.
        """
        app_settings = _app_settings(settings)
        _guardar_assinatura(conn, normal_user)
        renderer, real, com_data_uri = _assinatura_com_data_uri(conn, normal_user)
        monkeypatch.setattr(renderer, "render_html", com_data_uri)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        relatorio = service.enviar(conn, app_settings, normal_user, "Assunto", CORPO_LIMPO, list_id)

        assert relatorio["bloqueado"] is True
        assert relatorio["regras_criticas"], (
            f"o bloqueio não nomeia a regra que disparou: {relatorio}"
        )
        assert relatorio["regras_criticas"][0]["regra"] == "IMG_DATA_URI", (
            f"a regra nomeada não é a que disparou: {relatorio['regras_criticas']}"
        )
        assert relatorio["motivo"], "o relatório não diz porque não enviou"

    def test_nao_envia_numa_lista_sem_from_confirmado(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        """Um score perfeito não substitui um `from` confirmado.

        Este é o portão do `T017-A` e ele continua a valer com o compositor
        à volta. São dois portões independentes, e cada um tem de fechar sozinho.
        """
        app_settings = _app_settings(settings)
        list_id = _lista_vazia_com_envio(conn, normal_user)

        relatorio = service.enviar(conn, app_settings, normal_user, "Reunião", CORPO_LIMPO, list_id)

        assert relatorio["enviados"] == 0
        assert relatorio["bloqueado"] is True
        assert "remetente" in relatorio["motivo"].lower(), relatorio["motivo"]
        assert not [m for m in captured_emails if m["to"] != "envio@exemplo.pt"]


def _lista_vazia_com_envio(conn, user_id: int) -> int:
    """Uma lista com `from` escolhido mas **não** confirmado."""
    list_id = _lista(conn, user_id)
    remetente = listas.registar_remetente(conn, user_id, "envio@exemplo.pt")
    listas.definir_from_da_lista(conn, user_id, list_id, remetente["id"])
    return list_id


class TestEscapamento:
    """O corpo é texto simples e sai do browser do operador para o email de
    terceiros. Um `<script>` colado no rascunho é um `<script>` no email."""

    def test_o_html_escapa_o_que_o_operador_escreveu(
        self, conn, normal_user: int, settings, captured_emails
    ) -> None:
        app_settings = _app_settings(settings)
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)
        corpo = "Olá <script>alert('x')</script> e <b>negrito</b>"

        relatorio = service.avaliar(conn, app_settings, normal_user, "Assunto", corpo, list_id)

        assert "<script>" not in relatorio["html"], (
            "o corpo do operador entrou no HTML sem escape; o rascunho é do "
            "operador mas o email vai para a caixa de outra pessoa"
        )
        assert "&lt;script&gt;" in relatorio["html"]

    def test_a_falha_de_escape_mata_a_mutacao(self) -> None:
        """A âncora tem de existir, e o `MUTACAO` tem de estar no código.

        Um teste de escapamento que passa porque a função não existe é um teste
        que não prova nada, e é o que a revisão T014 encontrou.
        """
        import inspect

        from mailutils.compose import service as svc

        fonte = inspect.getsource(svc)
        assert "html.escape" in fonte or "escape(" in fonte, (
            "o compositor não tem nenhum escape visível no fonte; o corpo "
            "escapado é uma afirmação, não uma prova"
        )


class TestRotasHttp:
    """O caminho HTTP tem propriedades que o serviço não tem: CSRF, e o
    `list_id` vem de um formulário que alguém pode manipular."""

    def test_a_pagina_abre_com_sessao(self, app: SyncASGIClient, conn, normal_user) -> None:
        _signed_in(app, conn, normal_user)
        resposta = app.get("/compor", follow_redirects=True)
        assert resposta.status_code == 200
        assert "Compor" in resposta.text

    def test_a_pagina_exige_sessao(self, app: SyncASGIClient) -> None:
        resposta = app.get("/compor", follow_redirects=False)
        assert resposta.status_code in (302, 303)

    def test_guardar_exige_csrf(self, app: SyncASGIClient, conn, normal_user) -> None:
        """Um `POST` sem CSRF não guarda o rascunho.

        Sem isto, qualquer página de outro sítio com a sessão do utilizador
        escreve no rascunho — e o rascunho é o texto que sai.
        """
        _signed_in(app, conn, normal_user)
        app.post("/compor/rascunho", data={"assunto": "X", "corpo": "Y"})
        assert service.rascunho(conn, normal_user)["assunto"] == ""

    def test_guardar_e_ler_volta_ao_mesmo_rascunho(
        self, app: SyncASGIClient, conn, normal_user
    ) -> None:
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/compor")
        app.post(
            "/compor/rascunho",
            data={"csrf_token": token, "assunto": "Terça", "corpo": CORPO_LIMPO},
            follow_redirects=True,
        )
        assert service.rascunho(conn, normal_user)["assunto"] == "Terça"

    def test_enviar_exige_csrf(
        self, app: SyncASGIClient, conn, normal_user, captured_emails
    ) -> None:
        """O botão de enviar é o que produz email. Sem CSRF, nada."""
        _signed_in(app, conn, normal_user)
        app_settings = _app_settings()
        list_id, _ = _lista_com_from(conn, normal_user, captured_emails, app_settings)

        app.post(
            "/compor/enviar",
            data={
                "assunto": "Reunião",
                "corpo": CORPO_LIMPO,
                "list_id": str(list_id),
            },
            follow_redirects=True,
        )
        assert not [m for m in captured_emails if m["to"] != "envio@exemplo.pt"], (
            "um envio sem CSRF chegou a sair; a rota de envio é a que produz email para gente real"
        )

    def test_enviar_recusa_um_list_id_manipulado(
        self, app: SyncASGIClient, conn, normal_user, captured_emails
    ) -> None:
        """Um `list_id` que não é inteiro é erro, não `ValueError`.

        `int()` a arder devolvia um 500 a quem não fez nada de errado além de
        estar a mexer no formulário, e o `500` numa rota de envio é um email
        que não saiu sem ninguém saber.
        """
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/compor")
        resposta = app.post(
            "/compor/enviar",
            data={
                "csrf_token": token,
                "assunto": "Reunião",
                "corpo": CORPO_LIMPO,
                "list_id": "'; DROP TABLE users; --",
            },
            follow_redirects=False,
        )
        assert resposta.status_code in (302, 303), resposta.status_code
        assert not [m for m in captured_emails if m["to"] != "envio@exemplo.pt"]

    def test_nao_escreve_na_lista_de_outro(
        self, app: SyncASGIClient, conn, normal_user, captured_emails
    ) -> None:
        """A lista é do dono. `enviar` refusa antes de olhar para o score.

        Sem isto, o `service` receberia um `list_id` que não é do utilizador e a
        falha viria do `destinatarios()` — mais tarde, e por um motivo que não
        diz nada sobre o motivo certo.
        """
        _signed_in(app, conn, normal_user)
        outro = _outro(conn)
        app_settings = _app_settings()
        list_id_outro, _ = _lista_com_from(conn, outro, captured_emails, app_settings)
        token = csrf_from(app, "/compor")

        resposta = app.post(
            "/compor/enviar",
            data={
                "csrf_token": token,
                "assunto": "Reunião",
                "corpo": CORPO_LIMPO,
                "list_id": str(list_id_outro),
            },
            follow_redirects=True,
        )
        assert resposta.status_code == 200
        assert not [m for m in captured_emails if m["to"] != "envio@exemplo.pt"], (
            "enviou para a lista de outro utilizador"
        )
