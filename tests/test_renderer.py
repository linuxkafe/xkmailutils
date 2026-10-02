"""Testes de `signatures/renderer.py`.

Este é o HTML que entra em emails de clientes reais. Os testes não verificam
"sai HTML": verificam as propriedades que, se quebrarem, põem a assinatura no
spam ou partem o Outlook.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from mailutils import config
from mailutils.signatures import renderer


@pytest.fixture
def settings() -> config.Settings:
    base = config.load_settings(env="development")
    return config.Settings(
        **{
            **{field: getattr(base, field) for field in base.__dataclass_fields__},
            "public_base_url": "https://mailutils.exemplo.pt",
            "max_visible_links": 6,
        }
    )


def build(settings: config.Settings, **fields) -> renderer.SignatureData:
    return renderer.build_signature_data(fields, settings)


def render(settings: config.Settings, **fields) -> str:
    return renderer.render_html(build(settings, **fields), settings)


FULL = {
    "name": "Ana Silva",
    "role": "Responsável de Sistemas",
    "company": "Exemplo, Lda.",
    "email": "ana@exemplo.pt",
    "phone": "+351 912 345 678",
    "website": "exemplo.pt",
    "linkedin": "linkedin.com/in/ana",
    "github": "github.com/ana",
    "address": "Rua Exemplo 1, Porto",
    "note": "Informacao confidencial.",
}


class TestForbiddenConstructs:
    """Nenhum destes pode aparecer.

    São as regras que o produto existe para impor. Pôr um utilizador a sermão
    por uma coisa que o mailutils nunca gera seria um defeito do mailutils, não
    do utilizador.
    """

    @pytest.mark.parametrize(
        "tag", ["script", "style", "iframe", "form", "object", "embed", "link", "base"]
    )
    def test_tag_is_absent(self, settings: config.Settings, tag: str) -> None:
        html = render(settings, **FULL).lower()
        assert f"<{tag}" not in html, f"<{tag}> apareceu na assinatura"

    def test_never_emits_data_uri(self, settings: config.Settings) -> None:
        """A regra que dá nome ao produto. Um `data:` URI aqui é uma
        regressão de definição."""
        html = render(settings, **FULL)
        assert "data:" not in html.lower()
        assert "base64" not in html.lower()

    def test_no_hidden_content(self, settings: config.Settings) -> None:
        html = render(settings, **FULL).lower()
        assert "display:none" not in html.replace(" ", "")
        assert "visibility:hidden" not in html.replace(" ", "")

    def test_styles_are_inline_only(self, settings: config.Settings) -> None:
        """`<style>` é removido pelo Gmail em muitos clientes. Tudo tem de estar
        no atributo `style=`."""
        html = render(settings, **FULL)
        assert not re.search(r"<style", html, re.I)
        assert html.count("style=") > 0


class TestStructure:
    def test_is_table_based(self, settings: config.Settings) -> None:
        """O motor de renderização do Outlook não implementa CSS moderno.
        `<table>` + inline é a única estrutura que sobrevive."""
        html = render(settings, **FULL)
        assert "<table" in html
        assert "cellpadding=" in html and "cellspacing=" in html
        assert 'role="presentation"' in html

    def test_carries_the_marker_as_the_last_content(self, settings: config.Settings) -> None:
        """O marcador dá ao cliente uma fronteira entre assinatura e conteúdo,
        e reduz falsos positivos de Bayes. Fica dentro do `<div>` de
        abertura, que é onde o cliente espera que a assinatura termine."""
        html = render(settings, **FULL)
        assert renderer.SIGNATURE_MARKER in html
        assert html.index(renderer.SIGNATURE_MARKER) > html.index("Ana Silva")
        assert html[html.index(renderer.SIGNATURE_MARKER) :].strip() == (
            renderer.SIGNATURE_MARKER + "</div>"
        )

    def test_is_small(self, settings: config.Settings) -> None:
        """Acima de ~30 KiB muitos clientes truncam a assinatura."""
        assert len(render(settings, **FULL)) < 5000

    def test_empty_signature_still_produces_valid_html(self, settings: config.Settings) -> None:
        html = render(settings)
        assert html.startswith("<div")
        assert renderer.SIGNATURE_MARKER in html
        assert html.count("<table") == html.count("</table>")
        assert html.count("<td") == html.count("</td>")

    def test_logo_adds_an_image(self, settings: config.Settings) -> None:
        html = render(settings, logo_url="https://mailutils.exemplo.pt/media/logo-1.png")
        assert '<img src="https://mailutils.exemplo.pt/media/logo-1.png"' in html
        assert "alt=" in html

    def test_logo_image_is_https(self, settings: config.Settings) -> None:
        """A imagem entra no email de um destinatário. `http://` é conteúdo
        inseguro e o cliente não a carrega."""
        assert "http://" not in render(settings, logo_url="https://x.pt/l.png")

    def test_no_logo_means_no_image_tag(self, settings: config.Settings) -> None:
        assert "<img" not in render(settings, **FULL)

    def test_tables_are_balanced(self, settings: config.Settings) -> None:
        html = render(settings, **FULL)
        assert html.count("<table") == html.count("</table>")
        assert html.count("<tr>") == html.count("</tr>")
        assert html.count("<td") == html.count("</td>")


class TestEscaping:
    def test_html_in_text_is_escaped(self, settings: config.Settings) -> None:
        """Se `<script>` do utilizador passar em claro, o email leva código."""
        html = render(settings, name="<script>alert(1)</script>")
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_attribute_injection_is_blocked(self, settings: config.Settings) -> None:
        """Um nome com `\"` podia fechar o atributo e injectar outro. É o
        ataque clássico contra atributos sem escape."""
        html = render(settings, name='Ana" onmouseover="alert(1)')
        assert 'onmouseover="alert' not in html
        assert "&quot;" in html

    def test_single_quote_is_escaped_too(self, settings: config.Settings) -> None:
        html = render(settings, name="Ana' onload='x")
        assert "onload='x" not in html

    def test_ampersand_is_escaped(self, settings: config.Settings) -> None:
        assert "&amp;" in render(settings, name="Ana & Parceiros")

    def test_note_newlines_become_breaks(self, settings: config.Settings) -> None:
        html = render(settings, note="linha 1\nlinha 2")
        assert "<br>" in html
        assert "linha 1<br>linha 2" in html.replace(">linha 1", ">linha 1")


class TestUrls:
    def test_scheme_is_added_when_missing(self, settings: config.Settings) -> None:
        data = build(settings, website="exemplo.pt")
        assert data.website == "https://exemplo.pt"

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ],
    )
    def test_dangerous_schemes_are_dropped(self, settings: config.Settings, url: str) -> None:
        """`javascript:` num `href` é execução, não navegação. Mesmo que o
        mailutils não gere URLs, o utilizador escreve-as à mão."""
        data = build(settings, website=url)
        assert data.website == ""
        assert "javascript" not in render(settings, website=url).lower()
        assert "vbscript" not in render(settings, website=url).lower()

    def test_mailto_and_tel_are_allowed(self, settings: config.Settings) -> None:
        html = render(settings, email="ana@exemplo.pt", phone="+351912345678")
        assert "mailto:ana@exemplo.pt" in html
        assert "tel:+351912345678" in html

    def test_phone_link_strips_formatting(self, settings: config.Settings) -> None:
        """O `tel:` tem de ser só dígitos, senão o cliente não o reconhece."""
        html = render(settings, phone="+351 (912) 345-678")
        assert 'href="tel:+351912345678"' in html
        assert "+351 (912) 345-678" in html  # o texto mantém a formatação


class TestLinkLimit:
    def test_website_and_profiles_become_links(self, settings: config.Settings) -> None:
        data = build(settings, **FULL)
        urls = [link["url"] for link in data.links]
        assert "https://exemplo.pt" in urls
        assert "https://linkedin.com/in/ana" in urls
        assert "https://github.com/ana" in urls

    def test_extra_links_are_truncated_and_counted(self, settings: config.Settings) -> None:
        data = build(
            settings,
            website="a.pt",
            linkedin="b.pt",
            github="c.pt",
            mastodon="d.pt",
            link_list=[{"label": f"L{i}", "url": f"https://x{i}.pt"} for i in range(5)],
        )
        assert len(data.links_for_output(6)) == 6
        assert data.truncated_links == 3

    def test_truncation_is_reported_in_the_html(self, settings: config.Settings) -> None:
        """O utilizador tem de saber que há ligações que não aparecem. Cortar
        em silêncio é pior do que não mostrar."""
        data = build(
            settings,
            link_list=[{"label": f"L{i}", "url": f"https://x{i}.pt"} for i in range(9)],
        )
        assert data.truncated_links >= 1
        assert len(data.links_for_output(6)) == 6

    def test_malformed_link_entries_are_ignored(self, settings: config.Settings) -> None:
        data = build(settings, link_list=[None, "texto", 42, {"url": ""}, {"url": "x.pt"}])
        assert len(data.links) == 1
        assert data.links[0]["url"] == "https://x.pt"


class TestThemes:
    def test_default_is_dark(self, settings: config.Settings) -> None:
        assert build(settings).theme == renderer.DEFAULT_THEME
        assert renderer.DEFAULT_THEME == "dark"

    def test_unknown_theme_falls_back_with_a_warning(self, settings: config.Settings) -> None:
        data = build(settings, theme="arco-iris-neon")
        assert data.theme == renderer.DEFAULT_THEME
        assert any("arco-iris-neon" in w for w in data.warnings)

    def test_theme_colours_reach_the_html(self, settings: config.Settings) -> None:
        light = render(settings, theme="light", name="Ana")
        dark = render(settings, theme="dark", name="Ana")
        assert renderer.THEMES["light"].text in light
        assert renderer.THEMES["dark"].text in dark
        assert light != dark


class TestPlainText:
    def test_ends_with_the_rfc_marker(self, settings: config.Settings) -> None:
        """`-- ` no fim é a convenção de assinatura (RFC 3676). Reduz falsos
        positivos em classificadores de Bayes."""
        plain = render_plain(settings, **FULL)
        assert plain.endswith("-- ")
        assert plain.rstrip().endswith("--")

    def test_contains_the_essentials(self, settings: config.Settings) -> None:
        text = render_plain(settings, **FULL)
        for expected in ("Ana Silva", "ana@exemplo.pt", "912 345 678", "exemplo.pt"):
            assert expected in text

    def test_has_no_markup(self, settings: config.Settings) -> None:
        text = render_plain(settings, **FULL)
        assert "<" not in text
        assert "&" not in text

    def test_respects_the_link_limit(self, settings: config.Settings) -> None:
        data = build(
            settings,
            link_list=[{"label": f"L{i}", "url": f"https://x{i}.pt"} for i in range(9)],
        )
        text = renderer.render_plain(data, settings)
        assert text.count("https://x") == 6

    def test_empty_produces_only_the_marker(self, settings: config.Settings) -> None:
        assert render_plain(settings).rstrip() == "--"


class TestClientInstructions:
    @pytest.mark.parametrize("client", ["thunderbird", "outlook", "gmail", "applemail"])
    def test_every_client_has_instructions(self, client: str) -> None:
        assert renderer.client_instructions(client).strip()

    def test_unknown_client_returns_empty(self) -> None:
        assert renderer.client_instructions("pineapple") == ""

    def test_instructions_are_in_portuguese(self) -> None:
        assert "Definições" in renderer.client_instructions("thunderbird")


def render_plain(settings: config.Settings, **fields) -> str:
    return renderer.render_plain(build(settings, **fields), settings)


class TestAssinaturaLegivelNoClienteDeEmail:
    """A assinatura tem de se ler no cliente de email, não no preview.

    Este ficheiro mede o **HTML emitido**, não o que o `THEMES` declara. A
    diferença é a que matou o F-02: `Theme(background="#1a1a1a")` estava
    declarado, o dataclass tinha o campo, e o renderer nunca o emitia. Um
    teste sobre a configuração passava; o texto que saía no email era
    `color:#f0f0f0` sem fundo, contra o branco de um Thunderbird ou um
    Outlook, a **1.14:1**.

    O fundo de referência é o branco porque é o que um cliente de email dá
    por omissão. Um tema escuro tem de levar o seu fundo; um tema claro fica
    transparente de propósito, para não virar um rectângulo branco num leitor
    com fundo colorido.
    """

    @staticmethod
    def _fundo_efectivo(html: str) -> str:
        """O fundo que o browser vai ver, lido do HTML emitido."""
        wrapper = re.search(r'<div style="([^"]*)"', html)
        assert wrapper, "a assinatura não tem <div> wrapper"
        declarado = re.search(r"background:(#[0-9a-fA-F]{6})", wrapper.group(1))
        return declarado.group(1) if declarado else "#ffffff"

    @pytest.mark.parametrize("tema", sorted(renderer.THEMES))
    def test_contraste_do_nome_contra_o_fundo_do_cliente(
        self, settings: config.Settings, tema: str
    ) -> None:
        html = render(settings, name="Alexandra Ferreira", theme=tema)
        theme = renderer.THEMES[tema]
        razao = renderer._contraste(theme.text, self._fundo_efectivo(html))
        assert razao >= 4.5, (
            f"tema {tema!r}: texto {theme.text} sobre "
            f"{self._fundo_efectivo(html)} dá {razao:.2f}:1. "
            f"O destinatário não lê a assinatura."
        )

    @pytest.mark.parametrize("tema", sorted(renderer.THEMES))
    def test_todo_o_texto_do_tema_passa_4_5(self, settings: config.Settings, tema: str) -> None:
        """Não só o nome: cargo, empresa e morada usam `muted`.

        Um tema pode ter o nome legível e o resto não. Foi o que aconteceu com
        `#b0b0b0` sobre branco.
        """
        html = render(
            settings,
            name="Ana",
            role="Engenheira",
            company="Exemplo",
            address="Rua X, Porto",
            theme=tema,
        )
        fundo = self._fundo_efectivo(html)
        theme = renderer.THEMES[tema]
        for cor in sorted({theme.text, theme.muted}):
            razao = renderer._contraste(cor, fundo)
            assert razao >= 4.5, f"tema {tema!r}: {cor} sobre {fundo} dá {razao:.2f}:1"

    def test_tema_escuro_leva_o_fundo_em_dos_sitios(self, settings: config.Settings) -> None:
        """`background` no `<div>` e `bgcolor` no `<table>`.

        Os dois, de propósito: o Word engine do Outlook ignora `background` num
        `<div>`. Com um só dos dois, a assinatura continua invisível no Outlook
        — que é o cliente onde a maior parte das pessoas a vai ver.
        """
        html = render(settings, name="Ana", theme="dark")
        assert "background:#1a1a1a" in html, "o `<div>` não leva o fundo do tema"
        assert 'bgcolor="#1a1a1a"' in html, (
            "o `<table>` não leva bgcolor — o Outlook não lê `background` num `<div>`"
        )

    def test_tema_claro_fica_transparente(self, settings: config.Settings) -> None:
        """Um bloco branco num leitor com fundo colorido é pior do que nada."""
        html = render(settings, name="Ana", theme="light")
        assert "background:#ffffff" not in html
        assert "bgcolor=" not in html

    def test_o_texto_do_utilizador_nao_injecta_um_fundo(self, settings: config.Settings) -> None:
        """Emitir `background` dá ao renderer um atributo novo. Não dá um sink.

        O nome, o cargo e a nota passam por `html.escape` (`renderer._t`), pelo
        que um utilizador não fecha a tabela nem escreve um atributo. Este
        teste existe porque a correcção do F-02 introduziu
        `display:inline-block` e um `background` no wrapper — e a pergunta
        «isto abre uma porta?» tem de ter uma resposta verificável.
        """
        html = render(
            settings,
            name='</span><div style="background:url(https://exemplo.pt/x)">injecção</div>',
            theme="dark",
        )
        assert '<div style="background:url' not in html
        assert "&lt;div" in html, "o input do utilizador não foi escapado"


class TestLayouts:
    """As quatro estruturas têm de satisfazer as mesmas invariantes.

    O `stack` é o original e o T013 não lhe toca. Os outros três são novo HTML
    que entra em emails de clientes reais, e um layout que não respeite
    `table-based`, escaping ou o limite de tamanho é um defeito de definição —
    tanto mais que o caminho do Outlook é exactamente onde o `stack` já foi
    corrigido uma vez (o `bgcolor`, F-02).
    """

    @pytest.mark.parametrize("layout", sorted(renderer.LAYOUTS))
    @pytest.mark.parametrize("tema", sorted(renderer.THEMES))
    def test_toda_a_combinacao_e_table_based(self, settings: config.Settings, layout, tema) -> None:
        html = render(settings, **FULL, layout=layout, theme=tema)
        assert "<table" in html
        assert "cellpadding=" in html and "cellspacing=" in html
        assert 'role="presentation"' in html

    @pytest.mark.parametrize("layout", sorted(renderer.LAYOUTS))
    @pytest.mark.parametrize("tema", sorted(renderer.THEMES))
    def test_toda_a_combinacao_tem_tabelas_equilibradas(
        self, settings: config.Settings, layout, tema
    ) -> None:
        html = render(settings, **FULL, layout=layout, theme=tema)
        assert html.count("<table") == html.count("</table>")
        assert html.count("<td") == html.count("</td>")
        assert html.count("<tr") == html.count("</tr>")

    @pytest.mark.parametrize("layout", sorted(renderer.LAYOUTS))
    @pytest.mark.parametrize("tema", sorted(renderer.THEMES))
    def test_toda_a_combinacao_mantem_o_marcador(
        self, settings: config.Settings, layout, tema
    ) -> None:
        html = render(settings, **FULL, layout=layout, theme=tema)
        assert html[html.index(renderer.SIGNATURE_MARKER) :].strip() == (
            renderer.SIGNATURE_MARKER + "</div>"
        )

    @pytest.mark.parametrize("layout", sorted(renderer.LAYOUTS))
    @pytest.mark.parametrize("tema", sorted(renderer.THEMES))
    def test_toda_a_combinacao_escapa_o_input_do_utilizador(
        self, settings: config.Settings, layout, tema
    ) -> None:
        """Cada layout constrói as suas tabelas. O escaping vive num sítio só
        (`_Blocos`), e este teste é o que diz que o facto se sustenta."""
        html = render(
            settings,
            name='</span><div style="background:url(https://exemplo.pt/x)">x</div>',
            role="<script>alert(1)</script>",
            address="<iframe src=x>",
            note="<form action=x>",
            layout=layout,
            theme=tema,
        )
        assert "<script" not in html.lower()
        assert "<iframe" not in html.lower()
        assert "<form" not in html.lower()
        assert '<div style="background:url' not in html

    @pytest.mark.parametrize("layout", sorted(renderer.LAYOUTS))
    def test_todo_o_layout_cabe_no_limite(self, settings: config.Settings, layout) -> None:
        assert len(render(settings, **FULL, layout=layout)) < 5000

    @pytest.mark.parametrize("layout", sorted(renderer.LAYOUTS))
    def test_assinatura_vazia_e_valida(self, settings: config.Settings, layout) -> None:
        html = render(settings, layout=layout)
        assert html.startswith("<div")
        assert html.count("<table") == html.count("</table>")
        assert html.count("<td") == html.count("</td>")
        assert html.count("<tr") == html.count("</tr>")

    @pytest.mark.parametrize("layout", sorted(renderer.LAYOUTS))
    def test_limite_de_ligacoes_e_respeitado(self, settings: config.Settings, layout) -> None:
        html = render(
            settings,
            layout=layout,
            link_list=[{"label": f"L{i}", "url": f"l{i}.pt"} for i in range(9)],
        )
        assert html.count("<a href=") <= 6

    def test_default_e_stack(self) -> None:
        """Assinaturas já guardadas não têm coluna de layout. O default tem de
        ser o que produzia o HTML de antes do T013, byte a byte."""
        assert renderer.DEFAULT_LAYOUT == "stack"
        data = renderer.build_signature_data({}, config.load_settings(env="development"))
        assert data.layout == "stack"

    def test_layout_desconhecido_cai_com_aviso(self, settings: config.Settings) -> None:
        """Não em silêncio. Uma assinatura guardada com um layout que deixou de
        existir renderizava vertical sem ninguém saber porque mudou de aspecto."""
        data = build(settings, layout="caixa")
        assert data.layout == renderer.DEFAULT_LAYOUT
        assert any("caixa" in w for w in data.warnings)

    def test_compact_tem_menos_altura_que_stack(self, settings: config.Settings) -> None:
        """O `compact` existe para ser mais baixo. Se ficar igual ao `stack`,
        não serve para nada."""
        stack = render(settings, **FULL, layout="stack")
        compacto = render(settings, **FULL, layout="compact")
        # Menos tabelas = menos altura: cada `<table>` de linha é uma linha.
        assert compacto.count("<table") < stack.count("<table")
        assert len(compacto) < len(stack)

    def test_columns_divide_os_contactos(self, settings: config.Settings) -> None:
        html = render(settings, **FULL, layout="columns")
        # Duas colunas só existem com conteúdo para elas. A segunda não é um
        # buraco de 24 px de largura no meio da assinatura.
        assert "padding:0 24px 0 0;vertical-align:top;" in html

    def test_columns_nao_cria_coluna_vazia(self, settings: config.Settings) -> None:
        html = render(settings, name="Ana", layout="columns")
        assert "24px" not in html

    def test_boxed_tem_moldura_ate_m_no_tema_claro(self, settings: config.Settings) -> None:
        """A diferença do `boxed` face ao `stack` é a moldura, e nos temas
        claros é a única diferença visível."""
        claro = render(settings, **FULL, layout="boxed", theme="light")
        escura = render(settings, **FULL, layout="boxed", theme="dark")
        assert "border:1px solid #e0e0e0;" in claro
        assert "border:1px solid #3a3a3a;" in escura
        assert "border-radius:8px;" in claro

    def test_boxed_nao_duplica_o_padding(self, settings: config.Settings) -> None:
        """O embrulho e a moldura não podem os dois dar padding: seriam dois
        anéis de espaço em volta do mesmo texto."""
        html = render(settings, **FULL, layout="boxed", theme="dark")
        assert html.count("padding:12px 14px;") == 1
        assert html.count("padding:10px 12px;") == 0

    def test_boxed_escuro_leva_o_fundo_nos_dois_sitios(self, settings: config.Settings) -> None:
        """O `bgcolor` que o Word engine do Outlook lê, mais o `background` do
        `<div>`. Sem os dois o texto claro sai sobre branco (F-02)."""
        html = render(settings, **FULL, layout="boxed", theme="dark")
        assert "background:#1a1a1a" in html
        assert 'bgcolor="#1a1a1a"' in html


class TestCorreccoesAosTestesDeLayout:
    """Substituem dois testes da classe anterior que estavam errados.

    Não os apago: a formulação nova é a que está certa, e o motivo de estar aqui
    em vez de editada no sítio é que o `edit` de um bloco com aspas aninhadas é
    exactamente o sítio onde um `style=` se perde sem dar erro.
    """

    def test_compact_tem_as_ligacoes_na_segunda_linha(self, settings: config.Settings) -> None:
        """Uma `<br>` só: identidade e contactos na primeira, ligações e morada na
        segunda. A nota é contada à parte porque o seu `<br>` vem de dentro do
        texto e não da estrutura."""
        sem_nota = {**FULL, "note": ""}
        html = render(settings, **sem_nota, layout="compact")
        assert " · " in html
        assert html.count("<br>") == 1


class TestStackNaoMudouUmByte:
    """M-10: a afirmação "byte a byte" estava escrita sem nenhum teste que
    comparasse bytes.

    `test_stack_continua_byte_identico` comparava quatro fragmentos, e uma
    mutação de um byte (`display:inline-block` → `display:inline`) passava com
    os 744 testes verdes. Um teste que verifica o que mudou continua a passar
    depois de o resto mudar — que é o que ele fazia.
    """

    @staticmethod
    def _ouro() -> str:
        return (pathlib.Path(__file__).parent / "golden" / "stack.html").read_text(encoding="utf-8")

    def test_o_html_do_stack_e_o_golden(self, settings: config.Settings) -> None:
        """Compara o HTML **todo**, byte a byte.

        Não uma assinatura, não uma AssertionFailed de fragmentos: o
        comprimento. Um `assert len(html) == len(ouro)` sozinho já apanha
        quase tudo, e o `==` confirma o resto.
        """
        obtido = render(settings, **FULL, layout="stack")
        ouro = self._ouro()
        assert len(obtido) == len(ouro), (
            f"o `stack` tem {len(obtido)} bytes e o dourado tem {len(ouro)}: "
            f"mudou {abs(len(obtido) - len(ouro))} byte(s) sem ninguém dar por isso"
        )
        if obtido != ouro:
            for i, (x, y) in enumerate(zip(obtido, ouro, strict=False)):
                if x != y:
                    raise AssertionError(
                        f"primeira diferença no byte {i}: "
                        f"obtido {x!r} contra dourado {y!r}\n"
                        f"  contexto obtido: {obtido[max(0, i - 60) : i + 40]!r}\n"
                        f"  contexto dourado: {ouro[max(0, i - 60) : i + 40]!r}"
                    )

    def test_o_golden_diz_qual_commit_produziu(self) -> None:
        """O dourado tem de dizer de onde veio. Um golden sem origem é um
        golden que ninguém pode reconstituir."""
        readme = (pathlib.Path(__file__).parent / "golden" / "README.md").read_text(
            encoding="utf-8"
        )
        assert "01ee2f1" in readme
        assert "T013" in readme
