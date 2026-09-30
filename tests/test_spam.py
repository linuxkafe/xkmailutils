"""Testes de `spam.py`.

A regra do CLAUDE.md para este ficheiro: **se enviesar para baixo, o produto
mente ao utilizador.** Por isso os testes não verificam só que o score sobe —
verificam que sobe *o suficiente* e que as regras que o fazem subir são
realmente as que estão documentadas.
"""

from __future__ import annotations

import pytest

from mailutils.signatures import spam

#: HTML de referência: o que a aplicação produz por omissão. Se este HTML
#: deixar de estar em SEGURO, o produto regrediu.
CLEAN = (
    '<div style="font-family:Arial">'
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
    "<td><table><tr><td><span>Ana Silva</span></td></tr></table></td>"
    "</tr></table>"
    '<a href="https://exemplo.pt">exemplo.pt</a>'
    "</div>"
)


def rules_fired(html: str, plain: str = "") -> set[str]:
    report = spam.score_signature(html, plain)
    return {r["regra"] for r in report["regras"]}


class TestCategories:
    def test_zero_is_safe(self) -> None:
        label, _ = spam.categorise(0)
        assert label == "SEGURO"

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0, "SEGURO"),
            (19, "SEGURO"),
            (20, "ATENCAO"),
            (44, "ATENCAO"),
            (45, "ELEVADO"),
            (69, "ELEVADO"),
            (70, "CRITICO"),
            (100, "CRITICO"),
        ],
    )
    def test_thresholds_match_the_requirements(self, score: int, expected: str) -> None:
        """FR-4.7. Os limites estão escritos nos requisitos; se o código divergir,
        a documentação está a mentir e alguém vai construir UI em cima do
        número errado."""
        assert spam.categorise(score)[0] == expected

    def test_labels_have_accents_for_the_ui(self) -> None:
        assert spam.label_for_score(30) == "ATENÇÃO"
        assert spam.label_for_score(80) == "CRÍTICO"

    def test_accented_label_is_a_translation_of_the_internal_one(self) -> None:
        """Duas funções porque há duas audiences: `categorise` devolve a chave
        estável que o CSS e a máquina usam, `label_for_score` devolve o texto
        com acentos que o utilizador lê. Perder a ligação entre as duas é
        como a barra fica verde e o texto diz CRÍTICO."""
        for score in (0, 25, 50, 75):
            assert spam.label_for_score(score) == spam._LABELS_UTF8[spam.categorise(score)[0]]


class TestCleanHtml:
    def test_default_signature_is_safe(self) -> None:
        report = spam.score_signature(CLEAN, "Ana Silva\nexemplo.pt\n-- ")
        assert report["score"] <= 19, f"HTML limpo pontuou {report['score']}"
        assert report["categoria"] == "SEGURO"
        assert report["exportacao_bloqueada"] is False

    def test_clean_html_has_no_findings(self) -> None:
        assert spam.score_signature(CLEAN)["regras"] == []

    def test_clean_html_can_reach_zero(self) -> None:
        """Os bónus existem para que um HTML impecável chegue a 0. Sem isso o
        utilizador vê sempre um número > 0 e aprende a ignorar a escala."""
        compact = (
            '<div><table role="presentation" cellpadding="0" cellspacing="0" border="0">'
            "<tr><td><span>Ana Silva, responsavel de sistemas</span></td></tr></table></div>"
        )
        report = spam.score_signature(compact, "Ana Silva\n-- ")
        assert report["score"] == 0

    def test_always_reports_statistics(self) -> None:
        report = spam.score_signature(CLEAN)
        stats = report["estatisticas"]
        assert stats["ligacoes_http"] == 1
        assert stats["imagens"] == 0
        assert stats["caracteres_visiveis"] > 0

    def test_always_carries_the_heuristic_warning(self) -> None:
        """FR-4.8. O score nunca é apresentação sem o aviso de que é
        heurístico — é a diferença entre uma ferramenta honesta e uma que
        promete o que não pode cumprir."""
        assert "heurística" in spam.score_signature(CLEAN)["aviso"]


class TestForbiddenConstructs:
    def test_data_uri_image_is_critical(self) -> None:
        """O sinal mais severo numa assinatura. Tem de passar de CRÍTICO e
        bloquear a exportação."""
        html = '<div><img src="data:image/png;base64,iVBORw0KGgo="></div>'
        report = spam.score_signature(html, "texto")
        assert "IMG_DATA_URI" in {r["regra"] for r in report["regras"]}
        assert report["exportacao_bloqueada"] is True

    @pytest.mark.parametrize(
        "tag", ["script", "iframe", "object", "embed", "form", "input", "video", "audio"]
    )
    def test_every_forbidden_tag_blocks_export(self, tag: str) -> None:
        html = f"<div><{tag}></{tag}>Ana Silva, contactos e texto suficiente</div>"
        report = spam.score_signature(html, "Ana Silva")
        assert f"FORBIDDEN_TAG_{tag.upper()}" in {r["regra"] for r in report["regras"]}
        assert report["exportacao_bloqueada"] is True

    def test_script_with_attributes_still_detected(self) -> None:
        html = '<div><script type="text/javascript">alert(1)</script>texto</div>'
        assert "FORBIDDEN_TAG_SCRIPT" in rules_fired(html)

    def test_hidden_content_is_high(self) -> None:
        html = '<div><span style="display:none;">comprar já</span>Ana Silva</div>'
        assert "HIDDEN_CONTENT" in rules_fired(html)

    def test_mso_hide_counts_as_hidden(self) -> None:
        html = '<div><span style="mso-hide:all;">escondido</span>Ana</div>'
        assert "HIDDEN_CONTENT" in rules_fired(html)

    def test_visibility_hidden_counts(self) -> None:
        html = '<div><span style="visibility:hidden;">x</span>Ana</div>'
        assert "HIDDEN_CONTENT" in rules_fired(html)


class TestDensity:
    def test_many_links_is_medium(self) -> None:
        links = "".join(f'<a href="https://exemplo.pt/{i}">ligação {i}</a>' for i in range(8))
        assert "LINK_DENSITY" in rules_fired(f"<div>{links}</div>")

    def test_five_links_is_a_soft_warning(self) -> None:
        links = "".join(f'<a href="https://e.pt/{i}">e{i}</a>' for i in range(5))
        report = spam.score_signature(f"<div>{links}</div>")
        assert "LINK_COUNT_MODERATE" in {r["regra"] for r in report["regras"]}
        assert report["exportacao_bloqueada"] is False

    def test_mailto_and_tel_do_not_count_as_web_links(self) -> None:
        """Uma assinatura normal tem mailto: e tel:. Contá-los como ligações
        HTTP puniria o utilizador por usar o email dele."""
        html = (
            '<div><a href="mailto:ana@exemplo.pt">ana@exemplo.pt</a>'
            '<a href="tel:+351912345678">912 345 678</a>Ana Silva</div>'
        )
        assert spam.score_signature(html)["estatisticas"]["ligacoes_http"] == 0

    def test_image_without_text_is_high(self) -> None:
        """Um email quase só imagem é classificado como promocional pela
        maioria dos filtros."""
        html = '<div><img src="https://exemplo.pt/logo.png"></div>'
        report = spam.score_signature(html)
        assert "IMAGE_WITHOUT_TEXT" in {r["regra"] for r in report["regras"]}
        assert report["score"] >= 20

    def test_image_with_text_is_fine(self) -> None:
        html = (
            '<div><img src="https://exemplo.pt/l.png">'
            "<span>Ana Silva, responsável de sistemas, contactos e morada completa</span></div>"
        )
        assert "IMAGE_WITHOUT_TEXT" not in rules_fired(html)

    def test_tracking_pixel_is_critical(self) -> None:
        html = '<div><img src="https://exemplo.pt/track/1x1.gif"><span>Ana Silva</span></div>'
        report = spam.score_signature(html)
        assert "TRACKING_PIXEL" in {r["regra"] for r in report["regras"]}
        assert report["exportacao_bloqueada"] is True

    @pytest.mark.parametrize(
        "src",
        [
            "https://exemplo.pt/beacon.gif",
            "https://exemplo.pt/px.gif",
            "https://exemplo.pt/track/open.png",
            "https://exemplo.pt/impression?id=1",
            "https://exemplo.pt/1x1.gif",
        ],
    )
    def test_common_beacon_paths_are_detected(self, src: str) -> None:
        """Cada um destes caminhos é o que um beacon real usa. A regra anterior
        exigia `1x1` *e* uma palavra-chave, e deixava passar o caso mais
        comum: `/beacon.gif`."""
        html = f'<div><img src="{src}"><span>Ana Silva</span></div>'
        assert "TRACKING_PIXEL" in rules_fired(html)

    def test_a_normal_logo_is_not_a_tracking_pixel(self) -> None:
        html = '<div><img src="https://exemplo.pt/media/logo-12.png"><span>Ana Silva</span></div>'
        assert "TRACKING_PIXEL" not in rules_fired(html)


class TestStructure:
    def test_css_background_image_is_penalised(self) -> None:
        html = (
            '<div style="background-image:url(https://exemplo.pt/bg.png)">'
            "<span>Ana Silva</span></div>"
        )
        assert "CSS_BACKGROUND_IMAGE" in rules_fired(html)

    def test_plain_inline_colour_is_not_a_background_image(self) -> None:
        """`background-color` é perfeitamente normal numa assinatura. Só
        `background-image` é o problema."""
        html = '<div style="background-color:#ffffff">Ana Silva</div>'
        assert "CSS_BACKGROUND_IMAGE" not in rules_fired(html)

    def test_insecure_only_url_is_penalised(self) -> None:
        html = '<div><a href="http://exemplo.pt">exemplo</a><span>Ana Silva</span></div>'
        assert "INSECURE_URL" in rules_fired(html)

    def test_mixed_scheme_is_not_penalised(self) -> None:
        """Um link http:// antigo com um https:// não é um problema de
        segurança do email — penalizá-lo seria ruído."""
        html = '<div><a href="http://antigo.pt">a</a><img src="https://x.pt/l.png"></div>'
        assert "INSECURE_URL" not in rules_fired(html)

    def test_oversized_html_is_penalised(self) -> None:
        html = "<div>" + ("x" * 31_000) + "</div>"
        assert "OVERSIZED" in rules_fired(html)

    def test_position_fixed_is_flagged(self) -> None:
        html = '<div style="position:fixed">Ana Silva</div>'
        assert "POSITION_FIXED" in rules_fired(html)

    def test_comments_do_not_count_as_visible_text(self) -> None:
        """O marcador de assinatura é um comentário. Se contasse como texto,
        a razão texto/imagem de toda a assinatura ficaria errada."""
        report = spam.score_signature("<div><span>Ana</span><!-- mailutils-signature --></div>")
        assert "mailutils-signature" not in report["estatisticas"]


class TestBounds:
    def test_score_is_clamped_to_100(self) -> None:
        html = (
            "<div><script>x</script><iframe></iframe><form></form><object></object>"
            '<embed></embed><img src="https://e.pt/t/1x1.gif">'
            '<span style="display:none">y</span>'
            + '<a href="http://e.pt">l</a>' * 30
            + "z" * 40_000
            + "</div>"
        )
        assert spam.score_signature(html)["score"] <= 100

    def test_score_is_never_negative(self) -> None:
        report = spam.score_signature(CLEAN, "Ana Silva\n-- ")
        assert report["score"] >= 0

    def test_empty_html_does_not_crash(self) -> None:
        report = spam.score_signature("")
        assert 0 <= report["score"] <= 100
        assert "estatisticas" in report

    def test_malformed_html_does_not_crash(self) -> None:
        for html in ("<<<>>>", "<a href=", "<!--", "<div", "&nbsp;&nbsp;"):
            report = spam.score_signature(html)
            assert 0 <= report["score"] <= 100

    def test_single_quoted_attributes_are_parsed(self) -> None:
        html = "<div><a href='https://exemplo.pt'>x</a></div>"
        assert spam.score_signature(html)["estatisticas"]["ligacoes_http"] == 1


class TestReporting:
    def test_every_finding_has_a_rule_name_and_a_message(self) -> None:
        """A UI mostra isto ao utilizador. Uma regra sem nome não é
        accionável, e uma regra sem texto é um número sem explicação."""
        html = '<div><img src="data:image/png;base64,x"><span>a</span></div>'
        for finding in spam.score_signature(html)["regras"]:
            assert finding["regra"]
            assert finding["mensagem"]
            assert finding["remediacao"]
            assert isinstance(finding["pontos"], int)

    def test_messages_are_in_portuguese(self) -> None:
        html = '<div><img src="data:image/png;base64,x"><span>a</span></div>'
        for finding in spam.score_signature(html)["regras"]:
            assert "the" not in finding["mensagem"].lower().split()

    def test_clean_signature_is_not_blocked(self) -> None:
        assert spam.score_signature(CLEAN)["exportacao_bloqueada"] is False

    def test_one_critical_signal_blocks_regardless_of_total(self) -> None:
        """40 pontos de tracking pixel + texto dá um total de ELEVADO. Não
        pode ser exportável: o score mede risco agregado, o bloqueio é política
        sobre o pior sinal individual. Caso contrário, bastava acrescentar mais
        texto para contornar o bloqueio."""
        html = (
            '<div><img src="https://exemplo.pt/track/1x1.gif">'
            "<span>Ana Silva, responsavel de sistemas</span></div>"
        )
        report = spam.score_signature(html)
        assert report["categoria"] != "CRITICO", "este caso deve ficar em ELEVADO"
        assert report["exportacao_bloqueada"] is True


class TestOScoreEInteiro:
    """O score tem de ser um `int` de 0 a 100. Não uma promessa no docstring.

    A barra de score é uma regra de CSS por valor, indexada por `data-score`:
    `app.css` tem 101 regras, uma por cada score de 0 a 100, porque `attr()`
    não devolve percentagens e a CSP bloqueia `style=""`. Se o score passar a
    fraccionário, `data-score="42.5"` não casa com nenhuma regra e a barra
    **esvazia**.

    Uma revisão mutou `analyzer/scoring.py` para devolver um `float` e esta
    suite toda passou. O teste da completude da tabela contava 101 números no
    ficheiro — e continuava a contar com o score quebrado, porque contava
    texto do CSS e não o score de ninguém. A garantia escrita em
    `test_browser_regressions.py` e no ticket T008 era verdadeira no momento em
    que a escrevi e falsa como teste. (F-06)
    """

    @pytest.mark.parametrize("tema", ["dark", "light"])
    def test_a_assinatura_da_um_int(self, tema: str) -> None:
        from mailutils.config import load_settings
        from mailutils.signatures import renderer

        settings = load_settings(env="development")
        campos = {
            "name": "Ana Silva",
            "role": "Engenheira de Software",
            "company": "Exemplo, Lda.",
            "email": "ana@exemplo.pt",
            "website": "exemplo.pt",
            "note": "Documentos em http://exemplo.pt",
        }
        dados = renderer.build_signature_data(campos, settings, theme=tema)
        relatorio = spam.score_signature(
            renderer.render_html(dados, settings), renderer.render_plain(dados, settings)
        )
        score = relatorio["score"]
        assert isinstance(score, int), f"o score é {type(score).__name__}, não int: {score!r}"
        assert 0 <= score <= 100, f"o score saiu do intervalo: {score!r}"

    def test_o_analisador_da_um_int(self) -> None:
        from mailutils.analyzer import reader, scoring

        email = reader.parse(
            "From: ola@exemplo.pt\r\nSubject: Oferta\r\nTo: ana@exemplo.pt\r\n\r\n"
            "Chamada à acção. Desconto de 90% só hoje. http://exemplo.pt/oferta"
        )
        relatorio = scoring.analyse(email)
        score = relatorio["score"]
        assert isinstance(score, int), f"o analisador devolve {type(score).__name__}: {score!r}"
        assert 0 <= score <= 100

    def test_o_contrato_diz_int(self) -> None:
        """O tipo é declarado, não deduzido.

        Se alguém mudar a anotação de `pontos: int` ou a clampagem para `float`,
        este teste apanha o que a barra de score não conseguiria.
        """
        import inspect

        fonte = inspect.getsource(spam)
        assert "pontos: int" in fonte, "os pontos das regras deixaram de ser inteiros"
        assert "score = max(0, min(100, total))" in fonte, (
            "a clampagem do score mudou de forma; a barra de score em `app.css` "
            "indexa por valor e só cobre inteiros de 0 a 100"
        )
