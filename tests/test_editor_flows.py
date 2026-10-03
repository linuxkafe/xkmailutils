"""Testes de fluxo HTTP do editor, upload de logótipo, exportação e health.

O invariante que domina este ficheiro: **o que o preview mostra é o que a
exportação entrega.** Se algum dia divergirem, o utilizador descobre-o ao
enviar email para alguém.
"""

from __future__ import annotations

import json
import re

import pytest
from asgi_client import SyncASGIClient
from conftest import csrf_from
from test_auth_flows import _signed_in

from mailutils import config, db
from mailutils.signatures import renderer, spam

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)
#: GIF89a 1x1 mínimo. Só a assinatura interessa ao validador; o resto é
#: padding para que o ficheiro tenha tamanho plausível.
GIF = (
    b"GIF89a" + b"\x01\x00\x01\x00\x80\x00\x00"
    b"\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00"
    b"\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)

FIELDS = {
    "name": "Ana Silva",
    "role": "Responsável de Sistemas",
    "company": "Exemplo, Lda.",
    "email": "ana@exemplo.pt",
    "phone": "+351 912 345 678",
    "website": "exemplo.pt",
}


def _save(app: SyncASGIClient, fields: dict, theme: str = "dark", layout: str = "stack") -> None:
    token = csrf_from(app, "/assinatura")
    app.post(
        "/assinatura/guardar",
        data={
            "csrf_token": token,
            "fields": json.dumps(fields),
            "theme": theme,
            "layout": layout,
        },
        follow_redirects=True,
    )


def _upload(app: SyncASGIClient, payload: bytes, filename: str = "logo.png"):
    token = csrf_from(app, "/assinatura")
    return app.post(
        "/assinatura/logotipo",
        data={"csrf_token": token},
        files={"file": (filename, payload, "image/png")},
        follow_redirects=True,
    )


class TestEditor:
    def test_anonymous_is_redirected(self, app: SyncASGIClient) -> None:
        response = app.get("/assinatura", follow_redirects=False)
        assert response.status_code == 303

    def test_renders_for_a_signed_in_user(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        response = app.get("/assinatura")
        assert response.status_code == 200
        assert "Risco de spam estimado" in response.text

    def test_uma_assinatura_vazia_nao_promete_que_e_segura(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """Um formulário em branco é um estado, não um score de zero.

        **Este teste afirmava o contrário** — exigia que o editor vazio
        mostrasse «0 / 100 SEGURO» com selo verde. A revisão F-12 encontrou que
        isso treina o utilizador a ignorar o selo antes de escrever uma letra,
        e o dono aceitou a inversão. A inversão está escrita aqui em vez de o
        teste ter sido apagado, e o teste ficou mais forte: passa a afirmar os
        dois lados.

        Se um dia alguém resolver mostrar o score de um formulário vazio, este
        teste diz porque não, e o porquê está no código.
        """
        _signed_in(app, conn, normal_user)
        vazio = app.get("/assinatura").text

        assert 'data-nivel="VAZIO"' in vazio, (
            "um formulário em branco não pode ter um selo de score: o número "
            "0/100 é verdade e não é informação"
        )
        assert "POR PREENCHER" in vazio
        assert "SEGURO" not in vazio.split('id="caixa-score"')[1].split("</section")[0]
        assert "Créditos" not in vazio

        # E o outro lado: com conteúdo, o score é mostrado e é honesto.
        _save(app, {"name": "Ana Silva", "role": "Engenheira de Software"})
        preenchida = app.get("/assinatura").text
        assert "/ 100" in preenchida
        assert 'data-nivel="VAZIO"' not in preenchida
        assert "POR PREENCHER" not in preenchida

    def test_shows_the_heuristic_warning(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        """FR-4.8. Sem este texto, o score é uma promessa que a aplicação não
        pode cumprir."""
        _signed_in(app, conn, normal_user)
        assert "O que este score não é" in app.get("/assinatura").text

    def test_offers_client_instructions(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        body = app.get("/assinatura").text
        assert "Thunderbird" in body
        assert "Outlook" in body
        assert "Gmail" in body


class TestSave:
    def test_saved_fields_come_back(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS)
        body = app.get("/assinatura").text
        assert "Ana Silva" in body
        assert "ana@exemplo.pt" in body

    def test_save_overwrites_rather_than_duplicating(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS)
        _save(app, {**FIELDS, "name": "Ana Maria Silva"})
        rows = conn.execute("SELECT COUNT(*) AS n FROM signatures").fetchone()["n"]
        assert rows == 1
        assert "Ana Maria Silva" in app.get("/assinatura").text

    def test_theme_is_remembered(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS, theme="light")
        assert conn.execute("SELECT theme FROM signatures").fetchone()["theme"] == "light"

    def test_malformed_json_is_ignored(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        """JSON partido não pode devolver 500 — o utilizador perde o trabalho
        do formulário e fica sem saber porquê."""
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/assinatura")
        response = app.post(
            "/assinatura/guardar",
            data={"csrf_token": token, "fields": "{nao é json", "theme": "dark"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    def test_dangerous_link_schemes_are_dropped_from_the_signature(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """O campo do formulário tem de mostrar o que o utilizador escreveu —
        escondê-lo seria pior. O que não pode acontecer é isso chegar ao HTML
        que vai para o email."""
        _signed_in(app, conn, normal_user)
        _save(app, {**FIELDS, "website": "javascript:alert(1)"})
        token = csrf_from(app, "/assinatura")
        generated = app.post(
            "/assinatura/preview",
            data={
                "csrf_token": token,
                "fields": json.dumps({**FIELDS, "website": "javascript:alert(1)"}),
                "theme": "dark",
            },
        ).json()["html"]
        assert "javascript:" not in generated
        # O ficheiro exportado também não o contém: a exportação parte do
        # HTML gerado, não do formulário.
        assert "javascript:" not in app.get("/assinatura/exportar.html").text


class TestPreview:
    def test_returns_json(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/assinatura")
        response = app.post(
            "/assinatura/preview",
            data={"csrf_token": token, "fields": json.dumps(FIELDS), "theme": "dark"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert "Ana Silva" in payload["html"]
        assert payload["score"]["categoria"] == "SEGURO"

    def test_preview_html_is_the_exported_html(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """O invariante central: preview e exportação são o mesmo HTML. Se
        divergirem, o utilizador vê uma coisa e envia outra."""
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS)
        token = csrf_from(app, "/assinatura")
        preview = app.post(
            "/assinatura/preview",
            data={"csrf_token": token, "fields": json.dumps(FIELDS), "theme": "dark"},
        ).json()
        exported = app.get("/assinatura/exportar.html").text
        fragment = re.search(r"<body[^>]*>(.*)</body>", exported, re.DOTALL)
        assert fragment
        assert fragment.group(1).strip() == preview["html"].strip()

    def test_preview_needs_a_csrf_token(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        response = app.post(
            "/assinatura/preview",
            data={"csrf_token": "forjado", "fields": "{}", "theme": "dark"},
        )
        assert response.status_code == 403

    def test_preview_is_not_cached(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        """Um preview em cache mostra a assinatura anterior depois de mudar um
        campo. É o bug mais irritante que esta feature pode ter."""
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/assinatura")
        response = app.post(
            "/assinatura/preview",
            data={"csrf_token": token, "fields": "{}", "theme": "dark"},
        )
        assert response.headers.get("cache-control") == "no-store"


class TestLogoUpload:
    def test_png_is_accepted(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        response = _upload(app, PNG)
        assert conn.execute("SELECT COUNT(*) AS n FROM logos").fetchone()["n"] == 1
        assert "Logótipo carregado" in response.text

    def test_gif_is_accepted(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _upload(app, GIF, "logo.gif")
        assert (
            conn.execute("SELECT content_type FROM logos").fetchone()["content_type"] == "image/gif"
        )

    def test_svg_is_rejected(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        """Um SVG pode conter `<script>`. Passa em `Content-Type: image/png` e
        na extensão `.png`; só a assinatura do ficheiro o denuncia."""
        _signed_in(app, conn, normal_user)
        response = _upload(
            app, b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
        )
        assert conn.execute("SELECT COUNT(*) AS n FROM logos").fetchone()["n"] == 0
        assert "SVG não é aceite" in response.text

    def test_php_disguised_as_png_is_rejected(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        _upload(app, b"<?php system($_GET['c']); ?>" + b"\x00" * 64, "logo.png")
        assert conn.execute("SELECT COUNT(*) AS n FROM logos").fetchone()["n"] == 0

    def test_oversized_is_rejected(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _upload(app, PNG + b"\x00" * (3 * 1024 * 1024))
        assert conn.execute("SELECT COUNT(*) AS n FROM logos").fetchone()["n"] == 0

    def test_stored_name_is_generated_not_client_supplied(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """O nome do ficheiro que o cliente enviou nunca chega ao disco. É o
        que fecha a travessia de directório na escrita."""
        _signed_in(app, conn, normal_user)
        _upload(app, PNG, "../../../../etc/passwd.png")
        filename = conn.execute("SELECT filename FROM logos").fetchone()["filename"]
        assert re.fullmatch(r"logo-\d+\.png", filename)

    def test_upload_uses_the_logo_in_the_signature(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        _upload(app, PNG)
        _save(app, FIELDS)
        token = csrf_from(app, "/assinatura")
        generated = app.post(
            "/assinatura/preview",
            data={"csrf_token": token, "fields": json.dumps(FIELDS), "theme": "dark"},
        ).json()["html"]
        # O HTML exportado, não a página: o `data:,` do favicon vive na
        # casca da aplicação e é inofensivo. A regra é sobre o que vai no email.
        assert "/media/logo-" in generated
        assert "data:" not in generated

    def test_logo_survives_a_page_reload(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _upload(app, PNG)
        _save(app, FIELDS)
        assert "/media/logo-" in app.get("/assinatura").text

    def test_upload_needs_a_csrf_token(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        response = app.post(
            "/assinatura/logotipo",
            data={"csrf_token": "forjado"},
            files={"file": ("logo.png", PNG, "image/png")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "erro=csrf" in response.headers["location"]

    def test_remove_deletes_the_file(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _upload(app, PNG)
        filename = conn.execute("SELECT filename FROM logos").fetchone()["filename"]
        target = app.app.state.settings.media_dir / filename
        assert target.is_file()

        token = csrf_from(app, "/assinatura")
        app.post(
            "/assinatura/logotipo/remover",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        assert not target.is_file()
        assert conn.execute("SELECT COUNT(*) AS n FROM logos").fetchone()["n"] == 0


class TestMedia:
    def test_serves_the_logo(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _upload(app, PNG)
        filename = conn.execute("SELECT filename FROM logos").fetchone()["filename"]
        response = app.get(f"/media/{filename}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize(
        "name",
        [
            "../../../etc/passwd",
            "..%2f..%2f.env",
            "logo-1.png/../../../etc/passwd",
            "nao-e-um-logo.png",
        ],
    )
    def test_traversal_is_refused(
        self, app: SyncASGIClient, conn, normal_user: int, name: str
    ) -> None:
        """`/media` é público — não exige sessão. A única defesa é o nome
        gerado + as rejeições explícitas. Sem elas, este endpoint entrega o
        `.env`."""
        assert app.get(f"/media/{name}").status_code in (400, 404)

    def test_missing_file_is_404(self, app: SyncASGIClient) -> None:
        assert app.get("/media/logo-9999.png").status_code == 404


class TestExport:
    def test_html_export_is_a_complete_document(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS)
        response = app.get("/assinatura/exportar.html")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert response.text.lstrip().startswith("<!doctype html>")
        assert "Ana Silva" in response.text

    def test_exported_document_has_a_csp(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        """O ficheiro vai ser aberto num browser. Sem CSP, um HTML de assinatura
        com script executava com a origem de quem o abre."""
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS)
        assert "Content-Security-Policy" in app.get("/assinatura/exportar.html").text

    def test_text_export(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS)
        response = app.get("/assinatura/exportar.txt")
        assert "text/plain" in response.headers["content-type"]
        assert "Ana Silva" in response.text
        assert "<table" not in response.text

    def test_export_before_saving_is_refused(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        response = app.get("/assinatura/exportar.html", follow_redirects=False)
        assert response.status_code == 303
        assert "erro=vazio" in response.headers["location"]

    def test_export_is_blocked_when_the_score_is_critical(
        self, app: SyncASGIClient, conn, normal_user: int, monkeypatch
    ) -> None:
        """Defesa em profundidade, e o teste tem de dizê-lo.

        Um utilizador normal *nunca* chega a CRÍTICO: o renderer não produz
        HTML perigoso e isso está provado em `test_renderer.py`. O bloqueio
        existe para o caso de uma alteração futura ao renderer, ou de um
        caminho novo de injecção. Um botão que só existe para nunca disparar é
        código morto que parece uma garantia, por isso o teste força o score e
        verifica que a exportação recusa mesmo assim.
        """
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS)

        real = spam.score_signature

        def critical(html: str, plain: str = "") -> dict:
            report = real(html, plain)
            report["exportacao_bloqueada"] = True
            return report

        # O `T017-B` extraiu a construção para `signatures/build.py`, e o score
        # passou a ser calculado lá. Apontar o patch para `routes` era apanhar o
        # sítio errado: o teste passava a testar que a rota existe, não que o
        # score bloqueia. Um monkeypatch que aponta para o sítio errado é um
        # teste que deixa de provar o que diz provar — e passa a verde.
        monkeypatch.setattr("mailutils.signatures.build.spam.score_signature", critical)
        response = app.get("/assinatura/exportar.html", follow_redirects=False)
        assert response.status_code == 303
        assert "erro=bloqueado" in response.headers["location"]


class TestHealth:
    def test_answers_ok(self, app: SyncASGIClient) -> None:
        response = app.get("/saude")
        assert response.status_code == 200
        assert response.text.startswith("ok")

    def test_does_not_leak_user_emails(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        """FR-2.9. A sonda é pública; devolver emails seria enumeração de
        contas de graça."""
        body = app.get("/saude").text
        assert "ana@exemplo.pt" not in body
        assert "@" not in body

    def test_is_not_cached(self, app: SyncASGIClient) -> None:
        assert app.get("/saude").headers.get("cache-control") == "no-store"


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        ("header", "value"),
        [
            ("x-content-type-options", "nosniff"),
            ("x-frame-options", "DENY"),
            ("referrer-policy", "same-origin"),
        ],
    )
    def test_header_is_present(self, app: SyncASGIClient, header: str, value: str) -> None:
        assert app.get("/entrar").headers.get(header) == value

    def test_csp_is_present(self, app: SyncASGIClient) -> None:
        csp = app.get("/entrar").headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_csp_does_not_relax_styles_or_scripts(self, app: SyncASGIClient) -> None:
        """A CSP é o que impede injecção de CSS e de JavaScript.

        Estas três asserções são o que impede que alguém resolva um problema
        de estilo afrouxando a directive errada — que é a forma mais comum de
        um header de segurança se dissolver. `'unsafe-inline'` e `'unsafe-eval'`
        em `style-src` ou `script-src` são exactamente a coisa que o
        `CLAUDE.md` proíbe. O preview do editor precisa de `blob:`, e é a única
        excepção, e é restrita a `frame-src`.
        """
        csp = app.get("/entrar").headers.get("content-security-policy", "")

        assert "style-src 'self'" in csp
        assert "script-src 'self'" in csp
        for proibido in ("'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'"):
            assert proibido not in csp, f"a CSP relaxou-se: {proibido}"

    def test_blob_nao_aparece_em_nenhuma_directive(self, app: SyncASGIClient) -> None:
        """A excepção `blob:` foi removida, e não substituída por outra.

        O preview da assinatura foi um `iframe sandbox` com uma `blob:` URL, o
        que obrigou a `frame-src 'self' blob:` — e o documento `blob:` herda a
        CSP de quem o cria, pelo que a assinatura aparecia sem estilos. Passou
        a ser `/assinatura/preview-documento`, que tem a CSP dele, e a
        aplicação ficou sem `blob:` nenhum. Menos superfície, não mais. (F-04)
        """
        csp = app.get("/entrar").headers.get("content-security-policy", "")
        assert "blob:" not in csp, f"`blob:` voltou a aparecer na CSP: {csp}"
        assert "frame-src 'self'" in csp

    def test_o_documento_do_preview_permite_estilos_e_nao_scripts(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """O documento do preview é o que dá estilo à assinatura engavetada.

        Se `style-src` não permitir inline, o preview volta a mostrar Times New
        Roman a preto — o sintoma medido do F-04, que um teste anterior não
        apanhava porque só afirmava que o texto não estava vazio.
        """
        _signed_in(app, conn, normal_user)
        resposta = app.get('/assinatura/preview-documento?fields={"name":"Ana"}&theme=dark')
        assert resposta.status_code == 200
        csp = resposta.headers.get("content-security-policy", "")
        assert "style-src 'unsafe-inline'" in csp, (
            f"o documento do preview não pode mostrar a assinatura: {csp}"
        )
        assert "script-src 'none'" in csp
        assert "frame-ancestors 'self'" in csp, (
            "sem `frame-ancestors 'self'` o browser recusa engavetar o documento "
            "e o preview fica vazio"
        )
        assert "Times New Roman" not in resposta.text


class TestTheme:
    def test_uses_portuguese_locale(self, app: SyncASGIClient) -> None:
        assert 'lang="pt-PT"' in app.get("/entrar").text

    def test_footer_offers_a_health_link(self, app: SyncASGIClient) -> None:
        assert "/saude" in app.get("/entrar").text


class TestOFicheiroExportadoMostraOSeusEstilos:
    """O ficheiro que o utilizador descarrega tem de se mostrar.

    A CSP do documento exportado era `default-src 'none'; img-src https: http:`.
    Sem `style-src`, a directive cai para `default-src 'none'` — que bloqueia
    os estilos inline **do próprio produto**, incluindo o `background:#ffffff`
    do `<body>` duas linhas abaixo. O ficheiro saía com o HTML certo e
    renderizava-se como texto por formatar. (F-08)

    Estes testes olham para a CSP como browser, não para a intenção: o que
    interessa é se `style-src` permite inline e se o `script-src` continua a
    não permitir script.
    """

    CSP = re.compile(r'http-equiv="Content-Security-Policy"\s*content="([^"]+)"')

    @staticmethod
    def _documento(app: SyncASGIClient, conn, normal_user: int) -> str:
        """O `.html` exportado, com sessão e assinatura guardada.

        Sem assinatura a rota devolve 303 para `/assinatura?erro=vazio` e não há
        documento — que é como um teste de CSP do ficheiro exportado acaba a
        testar o redirect em vez do ficheiro.
        """
        _signed_in(app, conn, normal_user)
        _save(app, {"name": "Ana Silva", "role": "Engenheira", "email": "ana@exemplo.pt"})
        resposta = app.get("/assinatura/exportar.html", follow_redirects=False)
        assert resposta.status_code == 200, (
            f"a exportação devolveu {resposta.status_code} em vez do documento"
        )
        return resposta.text

    def _csp(self, html: str) -> dict[str, str]:
        achado = self.CSP.search(html)
        assert achado, "o documento exportado não traz CSP"
        partes = {}
        for pedaco in achado.group(1).split(";"):
            pedaco = pedaco.strip()
            if pedaco:
                nome, _, valor = pedaco.partition(" ")
                partes[nome] = valor
        return partes

    def test_permite_estilos_inline(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        csp = self._csp(self._documento(app, conn, normal_user))
        assert csp.get("style-src") == "'unsafe-inline'", (
            f"style-src é {csp.get('style-src')!r}: o ficheiro exportado não mostra "
            f"os estilos da assinatura. Sem `style-src`, a directive cai para "
            f"`default-src 'none'` e mata o CSS do próprio produto."
        )

    def test_nao_permite_script_nem_ligacoes(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """Permitir estilos inline não é abrir o ficheiro."""
        csp = self._csp(self._documento(app, conn, normal_user))
        assert csp.get("script-src") == "'none'"
        assert csp.get("connect-src") == "'none'"
        assert csp.get("form-action") == "'none'"

    def test_a_assinatura_esta_no_documento_com_o_seu_fundo(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        html = self._documento(app, conn, normal_user)
        assert "background:#ffffff" in html, (
            "o `<body>` do ficheiro exportado perdeu o fundo — e perdia-o porque "
            "a CSP descartava o atributo, não porque o atributo lá não estivesse"
        )
        assert "mailutils-signature" in html, "o documento não tem a assinatura"


class TestEstruturaDaAssinatura:
    """A estrutura viaja do formulário até ao HTML exportado.

    O caminho é: botão → campo escondido → `POST /guardar` → coluna `layout` →
    `_build` → `render_html`. Um elo em falta aparece como uma assinatura que
    volta ao vertical depois de guardar, e isso é exactamente o tipo de bug que
    o T008 encontrou no editor — indetectável por testes que não vão do browser
    ao HTML.
    """

    def test_default_do_esquema_bate_com_o_default_do_renderer(self) -> None:
        """`db.py` guarda o default como literal, para não importar o renderer.

        A consequence é que os dois podem divergir. Este teste é o que impede.
        """
        assert db.DEFAULT_SIGNATURE_LAYOUT == renderer.DEFAULT_LAYOUT

    def test_toda_a_estrutura_chega_ao_html_exportado(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """Cada estrutura tem de produzir um HTML próprio, e esse HTML tem de
        ser o que o utilizador descarrega.

        O sinal que distingue cada uma está escolhido para sobreviver ao
        `<body>` do documento exportado:
        `compact` põe as ligações na segunda linha, `columns` tem as duas
        colunas com `24px` de separación, `boxed` tem a moldura, e `stack` é o
        único sem nenhuma das três marcas.
        """
        _signed_in(app, conn, normal_user)
        vistos: set[str] = set()
        for estrutura in sorted(renderer.LAYOUTS):
            _save(app, FIELDS, layout=estrutura)
            fragmento = corpo_exportado(app)
            vistos.add(fragmento)
            marco = MARCOS[estrutura]
            assert marco in fragmento, f"a estrutura {estrutura!r} não é visível no HTML exportado"
            # E é exactamente o que o renderer produz para essa estrutura.
            assert fragmento == render_html_de(estrutura)
        assert len(vistos) == len(renderer.LAYOUTS), (
            "duas estruturas diferentes produzem o mesmo HTML: uma delas não está a ser usada"
        )

    def test_as_estruturas_sao_visivelmente_diferentes(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """Um utilizador que escolha «Compacto» tem de ver uma assinatura mais
        baixa. Se as quatro produzissem o mesmo HTML, o selector seria mentira."""
        _signed_in(app, conn, normal_user)
        alturas: dict[str, int] = {}
        for estrutura in sorted(renderer.LAYOUTS):
            _save(app, FIELDS, layout=estrutura)
            alturas[estrutura] = corpo_exportado(app).count("<table")
        assert alturas["compact"] < alturas["stack"], (
            f"o layout compacto não é mais baixo que o vertical: {alturas}"
        )

    def test_a_estrutura_e_guarded(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS, layout="columns")
        row = conn.execute("SELECT layout FROM signatures").fetchone()
        assert row["layout"] == "columns"

    def test_a_estrutura_ao_recarregar_a_pagina(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS, layout="boxed")
        body = app.get("/assinatura").text
        assert 'id="campo-layout" value="boxed"' in body

    def test_por_omissao_e_vertical(self, app: SyncASGIClient, conn, normal_user: int) -> None:
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS, layout=renderer.DEFAULT_LAYOUT)
        row = conn.execute("SELECT layout FROM signatures").fetchone()
        assert row["layout"] == renderer.DEFAULT_LAYOUT

    def test_preview_respeita_a_estrutura(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """O preview e a exportação têm de concordar. Divergir era o caminho
        mais fácil de introduzir aqui: dois sítios a chamar `_build` com
        argumentos diferentes."""
        _signed_in(app, conn, normal_user)
        _save(app, FIELDS, layout="compact")
        token = csrf_from(app, "/assinatura")
        preview = app.post(
            "/assinatura/preview",
            data={
                "csrf_token": token,
                "fields": json.dumps(FIELDS),
                "theme": "dark",
                "layout": "compact",
            },
        ).json()
        assert preview["html"] == render_html_de("compact")

    def test_estrutura_desconhecida_cai_no_preview_sem_500(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """Um `layout` forjado no pedido não pode rebentar o preview."""
        _signed_in(app, conn, normal_user)
        token = csrf_from(app, "/assinatura")
        response = app.post(
            "/assinatura/preview",
            data={
                "csrf_token": token,
                "fields": json.dumps(FIELDS),
                "theme": "dark",
                "layout": "inventado",
            },
        )
        assert response.status_code == 200
        assert response.json()["html"] == render_html_de("stack")

    def test_o_editor_oferece_todas_as_estruturas(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        _signed_in(app, conn, normal_user)
        body = app.get("/assinatura").text
        for estrutura in sorted(renderer.LAYOUTS):
            assert f'data-layout="{estrutura}"' in body

    def test_a_descricao_da_estructura_aparece(
        self, app: SyncASGIClient, conn, normal_user: int
    ) -> None:
        """O botão não pode ser só «Compacto» sem explicar. Um utilizador que
        não sabe a diferença entre as quatro estruturas escolhe ao acaso."""
        _signed_in(app, conn, normal_user)
        body = app.get("/assinatura").text
        assert renderer.LAYOUTS["boxed"].description in body


#: O que distingue cada estrutura no HTML exportado. Ver
#: `test_toda_a_estrutura_chega_ao_html_exportado`.
MARCOS = {
    "stack": 'style="margin:0 0 4px 0;"',
    "compact": " · ",
    "columns": "padding:0 24px 0 0;vertical-align:top;",
    "boxed": "border-radius:8px;",
}


def corpo_exportado(app: SyncASGIClient) -> str:
    """O fragmento da assinatura dentro do documento exportado."""
    exportado = app.get("/assinatura/exportar.html").text
    fragmento = re.search(r"<body[^>]*>(.*)</body>", exportado, re.DOTALL)
    assert fragmento, "o documento exportado não tem <body>"
    return fragmento.group(1).strip()


def render_html_de(estrutura: str) -> str:
    """O HTML de uma estrutura, calculado sem passar pela aplicação."""
    base = config.load_settings(env="development")
    settings = config.Settings(
        **{
            **{f: getattr(base, f) for f in base.__dataclass_fields__},
            "public_base_url": "https://mailutils.exemplo.pt",
        }
    )
    built = renderer.build_signature_data(
        {**FIELDS, "layout": estrutura, "theme": "dark"}, settings
    )
    return renderer.render_html(built, settings)
