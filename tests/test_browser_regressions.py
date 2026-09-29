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
