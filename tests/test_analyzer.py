"""Testes de `analyzer/`.

O objectivo não é provar que a heurística acerta — não há ground truth. É
provar três coisas:

1. Que os sinais óbvios de phishing **disparam**, sempre.
2. Que email legítimo **não** dispara por engano. Um analisador que dá
   falsos positivos em email normal é um analisador que as pessoas deixam de
   abrir ao fim de uma semana.
3. Que a escala e o formato do relatório são os mesmos que os do gerador de
   assinaturas, para a interface ter um só caminho de renderização.
"""

from __future__ import annotations

import pytest

from mailutils.analyzer import reader, scoring
from mailutils.signatures import spam


def analisar(texto: str) -> dict:
    return scoring.analyse(reader.parse(texto))


def regras(relatorio: dict) -> set[str]:
    return {r["regra"] for r in relatorio["regras"]}


def wrap(headers: str, corpo: str = "Olá,\n\nTudo bem?\n\nAbraços.") -> str:
    return f"{headers}\n{corpo}"


def wrap_html(cabecalho_html: str, corpo_html: str) -> str:
    """Email com parte HTML declarada como tal.

    Um `<a href>` dentro de uma parte `text/plain` é só texto — e é assim que
    deve ser lido. Passar HTML a um email que se declara texto simples e depois
    estranhar que as regras de HTML não disparam é estar a testar a coisa
    errada.
    """
    return (
        "From: a@b.pt\nTo: c@d.pt\nSubject: x\n"
        "Date: Tue, 15 Sep 2026 10:00:00 +0100\n"
        "Message-ID: <abc@exemplo.pt>\n"
        "MIME-Version: 1.0\n"
        "Content-Type: text/html; charset=utf-8\n"
        f"\n{cabecalho_html}{corpo_html}"
    )


LIMPO = wrap(
    "From: Ana Silva <ana@exemplo.pt>\n"
    "To: bruno@exemplo.pt\n"
    "Subject: Reunião de terça\n"
    "Date: Tue, 15 Sep 2026 10:00:00 +0100\n"
    "Message-ID: <abc123@exemplo.pt>"
)

# Um phishing de exemplo: cada linha acrescenta um sinal distinto.
#: Phishing de exemplo, com HTML e ligações — como o phishing real. Um exemplo
#: só em texto não exercita as regras de ligações, de imagens nem de HTML, e um
#: teste que passa só porque o fixture é pobre não prova nada.
PHISHING_HTML = """From: "Banco Exemplo — Segurança" <seguranca@banco-exemplo-verificacao.top>
Reply-To: recuperar@outro-dominio-suspeito.ru
Return-Path: <bounce@mailer-promocao.example>
To: vitima@exemplo.pt
Subject: URGENTE: a sua conta será suspensa — verifique imediatamente
Authentication-Results: mx.exemplo.pt; spf=fail (sender IP is not listed)
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<p>A sua conta será suspensa em 24 horas.</p>
<p>Verifique já a sua conta:</p>
<ul>
  <li><a href="https://bit.ly/verificar-conta-urgente">Continuar a verificação</a></li>
  <li><a href="http://192.168.44.7/login">Entrar directamente</a></li>
  <li><a href="https://login.secure-account-verification.exemplo.pt/">Apoio ao cliente</a></li>
  <li><img src="https://e.pt/open.png" width="1" height="1" alt=""></li>
</ul>
<p style="display:none">oferta escondida www.exemplo.pt/oferta-secreta</p>
<script>track()</script>
"""

#: O caso completo, para os testes de fim de fim.
PHISHING = PHISHING_HTML


# ================================================================== reader ==


class TestReader:
    def test_reads_plain_headers(self) -> None:
        email = reader.parse(LIMPO)
        assert email.sender == "Ana Silva <ana@exemplo.pt>"
        assert email.subject == "Reunião de terça"
        assert email.to_addrs == ["bruno@exemplo.pt"]
        assert email.text_body.strip().startswith("Olá,")

    def test_missing_header_is_empty_not_none(self) -> None:
        """A diferença entre "cabeçalho ausente" e "cabeçalho vazio" é um
        sinal. `None` não deixa distinguir."""
        assert reader.parse(LIMPO).header("reply-to") == ""

    def test_reads_html_part(self) -> None:
        raw = (
            "From: a@b.pt\nTo: c@d.pt\nSubject: Olá\n"
            "MIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n\n"
            "<p>Olá</p><a href='https://exemplo.pt'>ligação</a>"
        )
        email = reader.parse(raw)
        assert email.has_html
        assert email.html_links == ["https://exemplo.pt"]
        assert email.visible_text == "Olá ligação"

    def test_reads_multipart(self) -> None:
        raw = (
            "From: a@b.pt\nTo: c@d.pt\nSubject: Olá\n"
            "MIME-Version: 1.0\n"
            'Content-Type: multipart/alternative; boundary="X"\n\n'
            "--X\nContent-Type: text/plain; charset=utf-8\n\n"
            "texto simples\n"
            "--X\nContent-Type: text/html; charset=utf-8\n\n"
            "<p>html</p>\n"
            "--X--\n"
        )
        email = reader.parse(raw)
        assert email.has_text and email.has_html
        assert email.multipart is True
        assert email.parse_errors == []

    def test_attachments_are_named_not_parsed(self) -> None:
        raw = (
            "From: a@b.pt\nTo: c@d.pt\nSubject: Ficheiro\n"
            "MIME-Version: 1.0\n"
            'Content-Type: multipart/mixed; boundary="X"\n\n'
            "--X\nContent-Type: text/plain\n\nveja em anexo\n"
            "--X\nContent-Type: application/octet-stream\n"
            'Content-Disposition: attachment; filename="relatorio.pdf"\n\n'
            "conteudo\n--X--\n"
        )
        email = reader.parse(raw)
        assert email.attachments == ["relatorio.pdf"]
        assert "veja em anexo" in email.text_body

    def test_pasted_html_without_headers_degrades_gracefully(self) -> None:
        """O caso mais comum: o utilizador cola o HTML do email. Não é erro."""
        email = reader.parse("<p>Olá, clique <a href='https://x.pt'>aqui</a></p>")
        assert email.has_html
        assert email.parse_errors
        assert "cabeçalhos" in email.parse_errors[0]

    def test_pasted_text_without_headers(self) -> None:
        email = reader.parse("Olá, isto é só texto.")
        assert email.has_text
        assert not email.has_html

    def test_empty_input(self) -> None:
        email = reader.parse("")
        assert not email.has_text and not email.has_html

    def test_oversized_is_refused(self) -> None:
        with pytest.raises(reader.InputTooLarge):
            reader.parse("x" * (reader.MAX_INPUT_BYTES + 1))

    def test_bytes_input(self) -> None:
        email = reader.parse(LIMPO.encode("utf-8"))
        assert email.subject == "Reunião de terça"

    def test_deeply_nested_mime_does_not_recurse_forever(self) -> None:
        """Um `.eml` com anexos dentro de anexos é o caminho para estourar a pilha.
        A leitura é iterativa e tem profundidade limitada."""
        raw = "From: a@b.pt\nSubject: x\n" + ("Content-Type: multipart/mixed\n\n--X\n" * 200)
        email = reader.parse(raw)
        assert isinstance(email, reader.ParsedEmail)

    def test_looks_like_email_detects_headers(self) -> None:
        assert reader.looks_like_email(LIMPO) is True
        assert reader.looks_like_email("<p>Olá</p>") is False

    def test_visible_text_ignores_comments(self) -> None:
        email = reader.parse("<p>Olá<!-- segredo --></p>")
        assert "segredo" not in email.visible_text


# ================================================================= scoring ==


class TestLegitimateEmail:
    def test_clean_email_scores_zero(self) -> None:
        relatorio = analisar(LIMPO)
        assert relatorio["regras"] == []
        assert relatorio["score"] == 0
        assert relatorio["categoria"] == "SEGURO"

    def test_no_false_positive_on_normal_business_email(self) -> None:
        """Um email de trabalho com assunto, anexos e várias ligações não pode
        disparar nada. Se disparar, as pessoas deixam de usar a ferramenta."""
        raw = wrap(
            "From: Ana Silva <ana@exemplo.pt>\n"
            "To: bruno@exemplo.pt, carla@exemplo.pt\n"
            "Subject: Reunião de terça — pauta anexa\n"
            "Date: Tue, 15 Sep 2026 10:00:00 +0100\n"
            "Message-ID: <abc@exemplo.pt>",
            "Olá,\n\nA pauta está em anexo. Links: "
            "https://exemplo.pt/pauta e https://docs.exemplo.pt/minuta.\n\n"
            "Ana",
        )
        relatorio = analisar(raw)
        assert relatorio["score"] <= 19, relatorio["regras"]
        assert relatorio["categoria"] == "SEGURO"

    def test_newsletter_with_conditions_is_not_punished(self) -> None:
        """Código condicional do Outlook é o que distingue uma newsletter bem
        feita de uma empurrada de spam. Tem de dar **crédito**, não penalizar.

        Um analisador que penaliza newsletters bem feitas é um analisador cujas
        pessoas param de usar ao fim da primeira semana.
        """
        relatorio = analisar(
            wrap_html(
                "",
                "<p>Olá, a newsletter da semana, com texto suficiente para "
                "parecer conteúdo e não um cabeçalho vazio.</p>"
                "<!--[if mso]><v:rect><v:textbox><p>Só no Outlook</p>"
                "</v:textbox></v:rect><![endif]-->",
            )
        )
        creditos = {c["regra"] for c in relatorio["creditos"]}
        assert "HTML_CONDICIONAL_OUTLOOK" in creditos
        for credito in relatorio["creditos"]:
            assert credito["pontos"] < 0, "um crédito tem de ter pontos negativos"
        assert relatorio["categoria"] == "SEGURO"
        assert relatorio["score"] < 20


class TestPhishingSignals:
    def test_reply_to_mismatch_fires(self) -> None:
        """O sinal mais fácil de verificar sem técnica nenhum, e o mais
        ignorado. Tem de disparar sempre."""
        raw = wrap(
            "From: Ana <ana@exemplo.pt>\n"
            "To: bruno@exemplo.pt\n"
            "Reply-To: alguem@outro.pt\n"
            "Subject: Olá\n"
            "Date: Tue, 15 Sep 2026 10:00:00 +0100\n"
            "Message-ID: <a@b.pt>"
        )
        assert "REPLY_TO_OUTRO_DOMINIO" in regras(analisar(raw))

    def test_spf_failure_fires(self) -> None:
        raw = LIMPO.replace(
            "Message-ID: <abc123@exemplo.pt>",
            "Message-ID: <abc123@exemplo.pt>\n"
            "Authentication-Results: mx; spf=fail (not authorized)",
        )
        assert "SPF_FALHA" in regras(analisar(raw))

    def test_softfail_fires(self) -> None:
        """`SoftFail` conta como falha. SPF é autorizativo: ou o remetente está
        autorizado, ou não está, e um "quase" é um não."""
        raw = wrap(
            "From: Ana <ana@exemplo.pt>\nTo: b@exemplo.pt\nSubject: x\n"
            "Date: x\nMessage-ID: <a@b.pt>\n"
            "Received-SPF: SoftFail (domain of x not designated)"
        )
        assert "SPF_FALHA" in regras(analisar(raw))

    def test_missing_from_fires(self) -> None:
        relatorio = analisar("To: bruno@exemplo.pt\nSubject: Olá\n\ncorpo")
        assert "SEM_FROM" in regras(relatorio)

    def test_malformed_from_fires(self) -> None:
        raw = wrap("From: Banco Exemplo\nSubject: x\nDate: x\nMessage-ID: <a>")
        assert "FROM_MALFORMADO" in regras(analisar(raw))

    def test_missing_date_and_message_id_fire(self) -> None:
        relatorio = analisar("From: a@b.pt\nTo: c@d.pt\nSubject: x\n\ncorpo")
        r = regras(relatorio)
        assert "SEM_DATA" in r
        assert "SEM_MESSAGE_ID" in r

    def test_urgent_subject_fires(self) -> None:
        for assunto in (
            "URGENTE: a sua conta será suspensa",
            "Verifique a sua conta imediatamente",
            "Parabéns, ganhou um prémio",
            "Clique aqui para reclamar",
        ):
            raw = LIMPO.replace("Reunião de terça", assunto)
            relatorio = analisar(raw)
            assert "ASSUNTO_GOLPE" in regras(relatorio) or "ASSUNTO_GRITADO" in regras(relatorio), (
                assunto
            )

    def test_shouting_subject_fires(self) -> None:
        raw = LIMPO.replace("Reunião de terça", "PROMOÇÃO IMPERDÍVEL AGORA")
        assert "ASSUNTO_GRITADO" in regras(analisar(raw))

    def test_urgency_language_in_body_fires(self) -> None:
        raw = LIMPO.replace(
            "Tudo bem?",
            "Aja imediatamente, clique aqui, é a sua última chance dentro de 24 hours.",
        )
        assert "LINGUAGEM_URGENTE" in regras(analisar(raw))

    def test_html_only_fires(self) -> None:
        raw = (
            "From: a@b.pt\nTo: c@d.pt\nSubject: x\nDate: x\nMessage-ID: <a>\n"
            "MIME-Version: 1.0\nContent-Type: text/html\n\n<p>Olá</p>"
        )
        assert "SEM_ALTERNATIVA_TEXTO" in regras(analisar(raw))


class TestHtmlSignals:
    def _html(self, corpo: str) -> dict:
        return analisar(wrap_html("", corpo))

    @pytest.mark.parametrize("tag", ["script", "iframe", "form", "object", "embed", "base"])
    def test_forbidden_tag_is_critical(self, tag: str) -> None:
        relatorio = self._html(f"<p>Olá</p><{tag}>x</{tag}>")
        assert f"HTML_TAG_{tag.upper()}" in regras(relatorio)
        assert relatorio["score"] >= 45

    def test_javascript_link_fires(self) -> None:
        assert "HTML_JAVASCRIPT_LINK" in regras(
            self._html("<a href='javascript:alert(1)'>clique</a>")
        )

    def test_hidden_text_fires(self) -> None:
        relatorio = self._html(
            "<p>Olá, vencedor</p><span style='font-size:0'>comprar já www.exemplo.pt/oferta</span>"
        )
        assert "TEXTO_OCULTO" in regras(relatorio)

    def test_display_none_fires(self) -> None:
        relatorio = self._html("<p>Olá</p><span style='display:none'>texto escondido</span>")
        assert "TEXTO_OCULTO" in regras(relatorio)

    def test_css_expression_fires(self) -> None:
        assert "CSS_EXPRESSAO" in regras(
            self._html("<style>body{width:expression(alert(1))}</style>")
        )

    def test_giant_html_fires(self) -> None:
        relatorio = self._html("<div>" + ("conteúdo " * 30_000) + "</div>")
        assert "HTML_ENORME" in regras(relatorio)


class TestLinkSignals:
    def _com_ligacoes(self, ligacoes: str) -> dict:
        return analisar(wrap_html("", f"<p>Olá, aqui estão as ligações</p>{ligacoes}"))

    def test_many_links_fire(self) -> None:
        html = "".join(f"<a href='https://e.pt/{i}'>ligação {i}</a>" for i in range(45))
        assert "MUITAS_LIGACOES" in regras(self._com_ligacoes(html))

    def test_shortener_fires(self) -> None:
        assert "LINK_ENCURTADO" in regras(
            self._com_ligacoes("<a href='https://bit.ly/abc123'>clique</a>")
        )

    def test_ip_link_fires(self) -> None:
        assert "LIGACAO_POR_IP" in regras(
            self._com_ligacoes("<a href='http://192.168.44.7/login'>entrar</a>")
        )

    def test_insecure_only_fires(self) -> None:
        assert "LIGACOES_SEM_TLS" in regras(self._com_ligacoes("<a href='http://exemplo.pt'>a</a>"))

    def test_mixed_scheme_does_not_fire(self) -> None:
        relatorio = self._com_ligacoes(
            "<a href='http://antigo.pt'>a</a><a href='https://novo.pt'>b</a>"
        )
        assert "LIGACOES_SEM_TLS" not in regras(relatorio)

    def test_suspicious_subdomain_fires(self) -> None:
        assert "SUBDOMINIO_SUSPEITO" in regras(
            self._com_ligacoes(
                "<a href='https://login.secure-account-verification.exemplo.pt/'>entrar</a>"
            )
        )


class TestImageSignals:
    def _com_imagens(self, imagens: str, texto: str = "Olá") -> dict:
        return analisar(wrap_html("", f"<p>{texto}</p>{imagens}"))

    def test_tracking_pixel_fires(self) -> None:
        relatorio = self._com_imagens(
            "<img src='https://e.pt/open.png'>"
            "<img src='https://e.pt/beacon.gif'>"
            "<img src='https://e.pt/px.png'>",
            texto="Uma newsletter com texto suficiente para não parecer um beacon. "
            "Este parágrafo existe só para ter caracteres visíveis e não "
            "disparar a regra de imagem sem texto.",
        )
        assert "PIXEL_DE_RASTREIO" in regras(relatorio)

    def test_images_without_text_fire(self) -> None:
        relatorio = self._com_imagens(
            "<img src='https://e.pt/1.jpg'>"
            "<img src='https://e.pt/2.jpg'>"
            "<img src='https://e.pt/3.jpg'>",
            texto="olá",
        )
        assert "IMAGENS_SEM_TEXTO" in regras(relatorio)

    def test_image_with_text_does_not_fire(self) -> None:
        relatorio = self._com_imagens(
            "<img src='https://e.pt/logo.png'>",
            texto="Ana Silva, responsável de sistemas, contactos e morada",
        )
        assert "IMAGENS_SEM_TEXTO" not in regras(relatorio)


class TestStructure:
    def test_executable_attachment_is_critical(self) -> None:
        raw = (
            "From: a@b.pt\nTo: c@d.pt\nSubject: x\nDate: x\nMessage-ID: <a>\n"
            "MIME-Version: 1.0\n"
            'Content-Type: multipart/mixed; boundary="X"\n\n'
            "--X\nContent-Type: text/plain\n\nveja em anexo\n"
            "--X\nContent-Type: application/octet-stream\n"
            'Content-Disposition: attachment; filename="factura.exe"\n\n'
            "MZ\n--X--\n"
        )
        relatorio = analisar(raw)
        assert "ANEXO_EXECUTAVEL" in regras(relatorio)
        assert relatorio["score"] >= 45

    def test_pdf_attachment_is_only_a_note(self) -> None:
        raw = (
            "From: a@b.pt\nTo: c@d.pt\nSubject: x\nDate: x\nMessage-ID: <a>\n"
            "MIME-Version: 1.0\n"
            'Content-Type: multipart/mixed; boundary="X"\n\n'
            "--X\nContent-Type: text/plain\n\nveja em anexo\n"
            "--X\nContent-Type: application/pdf\n"
            'Content-Disposition: attachment; filename="relatorio.pdf"\n\n'
            "%PDF\n--X--\n"
        )
        relatorio = analisar(raw)
        assert "TEM_ANEXOS" in regras(relatorio)
        assert "ANEXO_EXECUTAVEL" not in regras(relatorio)


class TestReportShape:
    def test_uses_the_same_scale_as_the_signature_scorer(self) -> None:
        """A UI tem um só caminho de renderização para a barra e para as
        categorias. Se as escalas divergirem, tem de haver duas implementações
        — e as duas divergem."""
        assert scoring.ESCALA is spam.CATEGORIES
        relatorio = analisar(LIMPO)
        assert relatorio["categoria"] == spam.categorise(0)[0]
        assert relatorio["categoria_acentuada"] == spam.label_for_score(0)
        for score, esperado in ((0, "SEGURO"), (30, "ATENCAO"), (55, "ELEVADO"), (90, "CRITICO")):
            assert spam.categorise(score)[0] == esperado

    def test_all_six_categories_are_always_reported(self) -> None:
        """A UI mostra as seis, mesmo vazias. Uma categoria ausente lê-se como
        "não foi analisada", que é diferente de "não havia nada"."""
        relatorio = analisar(LIMPO)
        nomes = [c["categoria"] for c in relatorio["categorias"]]
        assert nomes == ["cabecalhos", "texto", "html", "ligacoes", "imagens", "estrutura"]

    def test_every_category_has_a_description(self) -> None:
        for categoria in scoring.CATEGORIAS:
            assert categoria.descricao.strip()
            assert categoria.nome.islower()

    def test_findings_have_a_name_a_message_and_a_fix(self) -> None:
        relatorio = analisar(PHISHING)
        assert relatorio["regras"]
        for achado in relatorio["regras"]:
            assert achado["regra"]
            assert achado["mensagem"]
            assert achado["remediacao"]
            assert isinstance(achado["pontos"], int)

    def test_always_carries_the_heuristic_warning(self) -> None:
        relatorio = analisar(PHISHING)
        assert "heurística" in relatorio["aviso"]
        assert "Não é o algoritmo" in relatorio["aviso"]

    def test_states_how_to_use_it(self) -> None:
        assert "defensiva" in analisar(PHISHING)["como_usar"]

    def test_carries_the_email_summary(self) -> None:
        resumo = analisar(PHISHING)["resumo_email"]
        assert "banco-exemplo" in resumo["de"]
        assert "URGENTE" in resumo["assunto"]
        assert "vitima@exemplo.pt" in resumo["para"]

    def test_score_is_bounded(self) -> None:
        relatorio = analisar(PHISHING * 20)
        assert 0 <= relatorio["score"] <= 100

    def test_empty_email_does_not_crash(self) -> None:
        relatorio = analisar("")
        assert 0 <= relatorio["score"] <= 100
        assert len(relatorio["categorias"]) == 6

    def test_phishing_scores_worse_than_a_clean_email(self) -> None:
        """A comparação que tem de ser verdadeira, e que nenhum teste unitário
        individual prova: o número ordena os dois casos na ordem certa."""
        limpo = analisar(LIMPO)["score"]
        phishing = analisar(PHISHING)["score"]
        assert limpo < phishing
        assert phishing >= 70, f"phishing deu {phishing}"


class TestPhishingCaseEndToEnd:
    def test_fires_many_independent_signals(self) -> None:
        """O caso de referência tem de disparar os sinais de todas as
        categorias, menos a das imagens (que dispara à parte, por pixel)."""
        r = regras(analisar(PHISHING))
        for esperada in (
            "REPLY_TO_OUTRO_DOMINIO",
            "RETURN_PATH_OUTRO_DOMINIO",
            "SPF_FALHA",
            "ASSUNTO_GOLPE",
            "LINGUAGEM_URGENTE",
            "LINK_ENCURTADO",
            "LIGACAO_POR_IP",
        ):
            assert esperada in r, f"{esperada} não disparou; disparou {sorted(r)}"
