"""Regressões de bugs que só apareceram com um browser a sério.

Cada teste aqui responde a um bug real, encontrado pelo suite E2E do
Playwright em 2026-09-29, e que a suite por HTTP não via. O padrão é o mesmo
nos dois casos: **o servidor respondia 200 e o cookie estava certo, mas o que
chegava ao browser estava errado.** Um teste que verifica um código de estado
ou um cookie não alcança nenhum dos dois.

O ficheiro existe para que a próxima pessoa não reintroduza a mesma falha a
partir de "isto já funcionava".
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from asgi_client import SyncASGIClient
from test_auth_flows import _signed_in

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "src" / "mailutils" / "templates"
APP_JS = ROOT / "src" / "mailutils" / "static" / "app.js"
APP_CSS = (ROOT / "src" / "mailutils" / "static" / "app.css").read_text(encoding="utf-8")


def _tema_do_html(html: str) -> str:
    achado = re.search(r'<html[^>]*\sdata-tema="([^"]*)"', html)
    assert achado, "a página não traz data-tema no <html>: o CSS não tem selector para aplicar"
    return achado.group(1)


class TestOTemaDaAplicacaoChegaAoBrowser:
    """O tema da aplicação é lido do cookie e escrito no `<html>`.

    Antes, o middleware definia `request.state.theme` *depois* de `call_next`
    (`main.py`), e os templates lêem esse valor durante o render. O resultado
    era uma página com o tema por omissão e um cookie com o tema pedido: a
    aplicação escrevia a escolha do utilizador no sítio certo e não a lia de
    volta nenhuma. Nenhum teste via porque todos verificavam o cookie, que
    estava sempre certo.
    """

    def test_login_com_cookie_claro_da_html_claro(self, app: SyncASGIClient) -> None:
        app.set_cookie("mailutils_theme", "light")
        assert _tema_do_html(app.get("/entrar").text) == "light"

    def test_sem_cookie_ou_com_valor_invalido_fica_escuro(self, app: SyncASGIClient) -> None:
        assert _tema_do_html(app.get("/entrar").text) == "dark"
        app.set_cookie("mailutils_theme", "arco-iris")
        assert _tema_do_html(app.get("/entrar").text) == "dark"

    def test_editor_nao_sobrescreve_o_tema_da_aplicacao(
        self, app: SyncASGIClient, conn: sqlite3.Connection, normal_user: int
    ) -> None:
        """A colisão que matava o tema claro na página que mais importa.

        `/assinatura` passava `tema` no contexto — o tema **da assinatura**,
        que vive na base de dados e define a cor no email. Como o contexto do
        handler é aplicado por cima do contexto base (`templates.py`), esse
        `tema` ganhava por cima do `tema` da aplicação, e o `<html>` recebia o
        tema da assinatura. Com a assinatura por omissão em escuro, a página do
        editor era sempre escura, com o cookie `light` posto.

        As duas coisas têm agora nomes diferentes. Este teste afirma as duas de
        uma vez: o `<html>` segue o cookie, e o campo escondido segue o que
        está guardado. Era a primeira que estava a falhar.
        """
        _signed_in(app, conn, normal_user)
        app.set_cookie("mailutils_theme", "light")
        html = app.get("/assinatura").text

        assert _tema_do_html(html) == "light", "o editor sobrepôs o tema da aplicação"

        campo = re.search(r'id="campo-theme"[^>]*value="([^"]*)"', html)
        assert campo, "o editor não tem campo escondido com o tema da assinatura"
        assert campo.group(1) in {"dark", "light"}


class TestCamposDeUrlNaoBloqueiamOFormulario:
    """`type="url"` exige esquema; o produto pede o domínio sem esquema.

    O campo "Website" diz ao utilizador, na própria dica, para escrever só
    `exemplo.pt` — o servidor acrescenta `https://` (`renderer._normalise_url`)
    e esse contrato está testado em `test_renderer.py`. Com `type="url"`, o
    browser considerava `exemplo.pt` inválido, `form.checkValidity()` devolvia
    `False`, e o browser **recusava submeter o formulário**: o botão "Guardar
    assinatura" não fazia nada e o utilizador não tinha erro visível para
    perceber porquê.

    Encontrado pelo clique no botão no Playwright. Nenhum teste de código de
    estado via: a rota respondia 200 a quem lhe mandasse um `POST` directo.
    """

    def test_nenhum_campo_de_url_exige_esquema(self) -> None:
        editor = (TEMPLATES / "editor.html").read_text(encoding="utf-8")
        for tag in re.findall(r"<input\b[^>]*>", editor):
            if 'type="url"' in tag:
                campo = re.search(r'id="([^"]*)"', tag)
                raise AssertionError(
                    f'o campo {campo.group(1) if campo else tag} é type="url": '
                    "o browser recusa um domínio sem esquema e o formulário não submete. "
                    'Usar type="text" — a normalização é do servidor.'
                )

    def test_a_dita_pede_um_dominio_sem_esquema(self) -> None:
        """A dica e o atributo têm de concordar. Este é o teste que falha
        primeiro da próxima vez que alguém «corrigir» o `type` para `url`."""
        editor = (TEMPLATES / "editor.html").read_text(encoding="utf-8")
        assert "Sem esquema, escreva só o domínio" in editor
        assert 'placeholder="exemplo.pt"' in editor


class TestNenhumEstiloInlineSobrevive:
    """Nenhum `style=""` em markup ou em HTML construído por JavaScript.

    A `Content-Security-Policy` tem `style-src 'self'`, e isso bloqueia
    atributos `style`. Quando havia 43 deles em 8 templates, o browser
    descartava-os todos: cada `margin`, cada `text-align`, cada
    `font-size` declarado no markup era deitado fora, e o `app.css` ficava a
    descrever um ecrã que ninguém via. Nada na suite HTTP notava — os testes
    verificam texto e cookies, e um atributo `style` é visível no texto mas
    é o browser que o decide.

    A alternativa seria pôr `'unsafe-inline'` em `style-src`. Não é o caminho:
    abriria um sink de injecção de CSS no ficheiro onde o utilizador escreve
    o texto da assinatura. Os valores que o CSS não consegue expressar estão em
    `app.css` — a largura da barra, indexada por `data-score`.
    """

    def _ficheiros(self) -> list[Path]:
        return sorted(TEMPLATES.glob("*.html")) + [APP_JS]

    def test_nenhum_style_inline(self) -> None:
        ofensores = [
            f"{ficheiro.relative_to(ROOT)}:{numero}: {linha.strip()[:70]}"
            for ficheiro in self._ficheiros()
            for numero, linha in enumerate(ficheiro.read_text(encoding="utf-8").splitlines(), 1)
            if "style=" in linha
        ]
        assert not ofensores, (
            '`style=""` está bloqueado pela CSP e seria descartado pelo browser. '
            "Usar uma classe de `app.css`.\n  " + "\n  ".join(ofensores)
        )

    def test_a_largura_da_barra_esta_no_css(self) -> None:
        """O score é a única coisa que o CSS não consegue expressar sozinho.

        Confirma que a largura da barra continua a serINDEXada por `data-score`
        e que o score é emitido como atributo, nos dois sítios onde há barra:
        o editor e o analisador.
        """
        for nome in ("editor.html", "analisar.html"):
            html = (TEMPLATES / nome).read_text(encoding="utf-8")
            assert 'class="score__fill" data-score=' in html, (
                f"{nome} não emite data-score na barra: sem ele a barra fica a 0% "
                "para sempre, porque o browser descarta o style inline"
            )

    def test_a_tabela_do_score_esta_completa(self) -> None:
        """101 regras: uma por cada score possível, de 0 a 100.

        Este é o teste que impede a barra de voltar a mentir em silêncio. Se um
        dia o score passar a fraccionário, `data-score="42.5"` não casa com
        nenhuma regra e a barra esvazia — este teste falha antes de isso
        chegar a um utilizador.
        """
        import re

        # Os comentários são removidos primeiro, e não por elegância: a tabela
        # foi inserida dentro de um comentário por engano uma vez, e o teste
        # anterior contou 101 regras que o browser não via. Um teste que passa
        # sobre CSS que não está lá não é um teste.
        sem_comentarios = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)

        encontradas = {
            int(n) for n in re.findall(r'\.score__fill\[data-score="(\d+)"\]', sem_comentarios)
        }
        faltam = sorted(set(range(101)) - encontradas)
        assert not faltam, f"faltam regras da barra para os scores: {faltam}"
        assert not encontradas - set(range(101)), "a tabela tem scores fora do intervalo 0-100"

    def test_a_barra_tem_zero_por_omissao(self) -> None:
        """Sem `width`, um `div` enche a barra inteira: score 0 com barra cheia.

        A regra base tem de ser `width: 0`, para que um score sem regra
        mostre uma barra vazia. A barra cheia é a mentira que este ficheiro
        veio apagar.
        """
        import re

        sem_comentarios = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
        base = re.search(r"\.score__fill \{([^}]*)\}", sem_comentarios)
        assert base, "app.css não tem regra .score__fill"
        assert "width: 0;" in base.group(1), ".score__fill não tem width: 0 por omissão"


class TestNenhumSelectorOrfaoEmAppCss:
    """Toda a classe definida em `app.css` tem de ser usada por algum lado.

    A migração dos 43 atributos `style=` para classes deixou 11 selectores que
    nada referenciava: quatro utilitários de espaçamento que substituíam
    margens que ninguém tinha, e sete selectores que já estavam mortos. Uma
    revisão contou-os por extracção, e admitiu que não conseguia dizer quais
    eram novos e quais já lá estavam — porque `app.css` entrou no git no mesmo
    commit. A resposta honesta é não precisar de saber: a regra vale para
    todos, e a falta de baseline deixa de importar. (F-11)

    O teste substitui um lint, e é melhor que um lint porque conhece a
    verdade: um seletor morto não é um erro de sintaxe, é código que ninguém
    pediu e que ninguém vai remover.
    """

    @staticmethod
    def _definidos() -> set[str]:
        css = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
        return set(re.findall(r"\.([a-zA-Z][\w-]*)", css))

    @staticmethod
    def _usados() -> set[str]:
        usados: set[str] = set()
        fontes = sorted(TEMPLATES.glob("*.html")) + [APP_JS]
        for ficheiro in fontes:
            texto = ficheiro.read_text(encoding="utf-8")
            for grupo in re.findall(r'class="([^"]*)"', texto):
                usados.update(grupo.split())
            for grupo in re.findall(r'className\s*=\s*"([^"]*)"', texto):
                usados.update(grupo.split())
        return usados

    def test_todo_o_selector_definido_e_usado(self) -> None:
        orfaos = sorted(self._definidos() - self._usados())
        assert not orfaos, (
            f"selectores em `app.css` que nenhum template nem `app.js` referencia: "
            f"{orfaos}. Ou se usam, ou saem — um selector morto é dívida que "
            f"ninguém pediu."
        )

    def test_a_moldura_do_preview_continua_branca(self) -> None:
        """Branco é decisão, e a decisão está escrita.

        O preview mostra a assinatura como ela aparece no cliente de email, e o
        cliente de email é claro. Um painel do preview escuro seria mais bonito
        e mentiroso. O que torna a decisão legível é a moldura ter bordo em
        ambos os temas, para se perceber que é uma superfície e não um bug.
        """
        assert ".preview__frame" in APP_CSS
        bloco = re.search(
            r"\.preview__frame\s*\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
        )
        assert bloco, "a moldura do preview desapareceu"
        for linha in ("border:", "background: #fff"):
            assert linha in bloco.group(1), f"`.preview__frame` perdeu `{linha}`"
        assert "molduras de imagem com fundo branco" in Path(ROOT / "docs" / "DESIGN.md").read_text(
            encoding="utf-8"
        ), "a decisão deixou de estar documentada"


class TestNaoHaCiEOGateELocal:
    """Não há GitHub Actions. O gate é `make check`, corrido à mão.

    Esta classe foi `TestAInstalaOCiTemDeTerTudo`, e protegia uma coisa que já
    não existe. O que ficou de aproveitável foi a lição do F-03: `make setup`
    instala o **pacote** `playwright`, e não o browser — quem corre `make e2e`
    sem o Chromium falha alto numa mensagem que não diz o que fazer.

    A CI escondia esse problema. Sem CI, a lição vive onde quem a encontra vai
    ler: no `Makefile`, na linha que lembra, e no `README`, no bloco de
    instalação. Estes testes verificam que essa documentação não desaparece em
    silêncio — que era o risco real de tirar a CI.
    """

    @staticmethod
    def _workflows() -> list[Path]:
        raiz = ROOT / ".github" / "workflows"
        if not raiz.is_dir():
            return []
        return sorted(raiz.glob("*.yml")) + sorted(raiz.glob("*.yaml"))

    def test_nao_ha_workflows(self) -> None:
        """A CI foi removida de propósito. Este teste diz que foi de propósito.

        Sem ele, o primeiro `git push` de um workflow encontrado num gist ou
        numa máquina antiga reintroduz a CI sem ninguém decidir. Não é um
        teste de que a CI funciona; é um teste de que a decisão continua de pé.
        """
        assert not self._workflows(), (
            f"voltou a haver workflow em .github/workflows/: "
            f"{[p.name for p in self._workflows()]}. A CI foi removida de "
            f"propósito; o gate é `make check`, em local. Se avoltares, é uma "
            f"decisão nova — e então este teste vai abaixo com ela, não contornado."
        )

    def test_o_makefile_diz_que_o_browser_se_instala_a_separado(self) -> None:
        """A lição do F-03 sobrevive à saída da CI, escrita onde se lê."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert "playwright install chromium" in makefile, (
            "o Makefile deixou de dizer que o browser do Playwright se instala "
            "à parte de `make setup`. Sem CI, este é o único sítio que avisa "
            "quem vai correr `make e2e` — e o F-03 foi exactamente um `make "
            "check` a falhar por o Chromium não estar instalado (F-03)"
        )

    def test_o_readme_diz_o_mesmo(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "playwright install chromium" in readme, (
            "o README deixou de dizer que `make setup` instala o pacote e não o browser (F-03)"
        )

    def test_o_dockerfile_nao_precisa_do_browser(self) -> None:
        """A imagem de runtime não corre testes, logo não precisa do Chromium.

        Se algum dia levar testes para a imagem, este teste é o que diz para
        rever a decisão em vez de a descobrir em produção.
        """
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert "playwright" not in dockerfile.lower(), (
            "o Dockerfile menciona playwright; a imagem de runtime não corre o "
            "suite E2E e não precisa do browser"
        )


class TestAInterfaceNaoPrometeOQueNaoCumpre:
    """A página de verificação diz o que aconteceu, não o que queríamos.

    Duas mentiras que custaram uma hora a alguém, e que nenhuma suite apanhava
    porque ambas envolvem **configuração**, não código. (F-16)

    1. Com `MAILUTILS_MAIL_BACKEND=console` — o que uma instalação nova traz —
       **não sai email nenhum**. A página dizia «Enviámos um código para o seu
       email», e a pessoa foi procurar um email que nunca existiu.
    2. Um pedido dentro da janela de espera não envia código, mas devolvia a
       mesma razão de um envio verdadeiro, e dizia a mesma frase.

    Cada teste constrói a sua própria aplicação, porque o `mail_backend` é o
    que está em causa. A conta é criada na base de dados **dessa** aplicação: a
    fixture `normal_user` semeia noutra, e um login contra uma base onde a
    conta não existe nunca chega a `/verificar`.
    """

    @staticmethod
    def _app(make_app, **overrides) -> SyncASGIClient:
        from conftest import TEST_PASSWORD

        from mailutils.auth import service

        app = make_app(**overrides)
        ligacao = app.app.state.db_factory()
        try:
            service.create_user(ligacao, "ana@exemplo.pt", TEST_PASSWORD)
        finally:
            ligacao.close()
        return app

    @staticmethod
    def _ate_verificar(app: SyncASGIClient) -> None:
        """Login num dispositivo novo, que é o caminho que leva a `/verificar`."""
        from conftest import TEST_PASSWORD, login

        login(app, "ana@exemplo.pt", TEST_PASSWORD, user_agent=f"agente-novo-{id(app)}")

    def test_com_console_diz_onde_esta_o_codigo(self, make_app) -> None:
        """Com `console`, a página diz que o email não sai e onde está o código."""
        app = self._app(make_app, mail_backend="console")
        self._ate_verificar(app)
        html = app.get("/verificar", follow_redirects=True).text
        assert "não envia email" in html, (
            "com o backend `console` a página tem de dizer que o email não sai e "
            "onde está o código. Sem isto, diz que enviou."
        )
        assert "email:console" in html, "a página tem de dizer o que se procura nos logs"

    def test_com_console_nao_diz_que_enviou(self, make_app) -> None:
        """A prova é por ausência, e é essa a forma que sobrevive ao tempo."""
        app = self._app(make_app, mail_backend="console")
        self._ate_verificar(app)
        html = app.get("/verificar", follow_redirects=True).text
        assert "Enviámos um código de 6 dígitos para o email" not in html, (
            "com o backend `console` não foi enviado email nenhum e a página "
            "afirma que foi. Foi isto que fez alguém procurar um email inexistente."
        )

    def test_com_smtp_diz_o_que_diz_de_si(self, make_app, captured_emails) -> None:
        """Com SMTP a sério, a frase é a certa e o aviso some.

        Uma instalação de produção não pode levar um aviso a dizer que o email
        não sai, e a página não pode deixar de dizer que envia.
        """
        app = self._app(
            make_app,
            mail_backend="smtp",
            smtp_host="127.0.0.1",
            smtp_port=25,
            mail_from="mailutils@exemplo.pt",
        )
        self._ate_verificar(app)
        html = app.get("/verificar", follow_redirects=True).text
        assert "Enviámos um código de 6 dígitos para o email" in html, (
            "com SMTP configurado a página tem de dizer que enviou — é o que está a acontecer"
        )
        assert "não envia email" not in html, (
            "um servidor com SMTP configurado não pode mostrar um aviso a dizer que o email não sai"
        )

    def test_com_email_desligado_diz_que_nao_ha_codigo(self, make_app) -> None:
        """O terceiro backend — `null` — também não pode mentir."""
        app = self._app(make_app, mail_backend="null")
        self._ate_verificar(app)
        html = app.get("/verificar", follow_redirects=True).text
        assert "desligado" in html
        assert "Enviámos um código de 6 dígitos para o email" not in html

    def test_o_cooldown_diz_que_ainda_falta(self, make_app) -> None:
        """Duas vezes seguidas: a segunda não envia e não pode dizer que enviou.

        O `retry_after` era calculado e nunca usado — o servidor sabia que
        faltavam 47 segundos e a interface não dizia nada. Um ecrã que promete
        um email e não o envia é pior do que um ecrã calado.
        """
        from conftest import TEST_PASSWORD, csrf_from

        # Aqui o cooldown é o do repo, não zero: é o comportamento que se quer.
        app = self._app(make_app, mail_backend="console", otp_cooldown_seconds=60)
        self._ate_verificar(app)
        csrf = csrf_from(app, "/entrar")
        resposta = app.post(
            "/entrar",
            data={
                "csrf_token": csrf,
                "email": "ana@exemplo.pt",
                "password": TEST_PASSWORD,
            },
            headers={"user-agent": f"outro-agente-{id(app)}-b"},
            follow_redirects=False,
        )
        destino = resposta.headers.get("location", "")
        assert "codigo-recentemente-enviado" in destino, (
            f"o cooldown voltou a dizer que envia: {destino}"
        )
        assert "faltam=" in destino, f"o servidor sabe quanto falta e não diz: {destino}"


class TestAMensagemDoCooldownNaoEAMensagemDeEnvio:
    def test_as_duas_mensagens_sao_diferentes(self) -> None:
        from mailutils.templates import MESSAGENS

        assert "codigo-recentemente-enviado" in MESSAGENS
        assert MESSAGENS["codigo-recentemente-enviado"] != MESSAGENS["codigo-enviado"], (
            "se forem iguais, o cooldown volta a mentir"
        )
