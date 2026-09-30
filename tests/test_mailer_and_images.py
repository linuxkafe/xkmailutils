"""Testes de `mailer.py` e `signatures/images.py`.

Os dois módulos que ficaram com menor cobertura na primeira passagem, e os
dois com mais caminho de erro: SMTP que falha, imagens que não se deixam
dimensionar, ficheiros que não podem ser apagados.

Nenhum teste toca a rede. O backend `smtp` é exercitado com um objecto
`SMTP` injectado, para se poderem testar tanto o caminho feliz como o erro sem
depender de um servidor real.
"""

from __future__ import annotations

import dataclasses
import smtplib
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

from mailutils import config, mailer
from mailutils.signatures import images

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)
GIF = b"GIF89a\x10\x00\x20\x00" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 20
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'


@pytest.fixture
def settings() -> config.Settings:
    base = config.load_settings(env="development")
    return config.Settings(**{field: getattr(base, field) for field in base.__dataclass_fields__})


class FakeSMTP:
    """Servidor SMTP em memória.

    Regista o que recebeu e deixa falhar quando lhe pedirem. Serve para
    exercitar o caminho feliz e o caminho de erro sem rede.
    """

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.messages: list[object] = []
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, context: object = None) -> None:
        if self.fail == "starttls":
            raise smtplib.SMTPNotSupportedError("STARTTLS indisponível")
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        if self.fail == "login":
            raise smtplib.SMTPAuthenticationError(535, b"credenciais invalidas")
        self.logged_in = (user, password)

    def send_message(self, message: object) -> None:
        if self.fail == "send":
            raise smtplib.SMTPRecipientsRefused({})
        self.messages.append(message)


@pytest.fixture
def smtp_settings(settings: config.Settings) -> config.Settings:
    """Configuração com o backend SMTP.

    O fixture base fica em `console` (é o default de desenvolvimento), por isso
    os testes de SMTP têm de o trocar explicitamente — senão `send()` escreve no
    stdout e nunca chega ao `SMTP` injectado, e o teste passa a medir a coisa
    errada.
    """
    return config.Settings(
        **{
            **settings.__dict__,
            "mail_backend": "smtp",
            "smtp_host": "smtp.exemplo.pt",
            "smtp_user": "u",
            "smtp_password": "p",
        }
    )


# ------------------------------------------------------------------- mailer --


class TestSmtpDelivery:
    def test_sends_to_smtp(
        self, smtp_settings: config.Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeSMTP()
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: server)
        mailer.send(smtp_settings, "ana@exemplo.pt", "Assunto", "texto", "<p>html</p>")
        assert len(server.messages) == 1
        assert server.started_tls is True
        assert server.logged_in == ("u", "p")

    def test_message_is_multipart_with_alternative(
        self, smtp_settings: config.Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Texto simples primeiro, HTML como alternativa. Ao contrário, muitos
        clientes mostram só o HTML — e o texto simples é o que dá a pontuação
        Bayes mais baixa."""
        server = FakeSMTP()
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: server)
        mailer.send(smtp_settings, "ana@exemplo.pt", "Assunto", "texto", "<p>html</p>")
        message = server.messages[0]
        assert message.is_multipart()
        subtypes = [part.get_content_subtype() for part in message.walk()]
        assert "plain" in subtypes
        assert "html" in subtypes
        assert subtypes.index("plain") < subtypes.index("html")

    def test_from_and_to_are_set(
        self, smtp_settings: config.Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeSMTP()
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: server)
        mailer.send(smtp_settings, "ana@exemplo.pt", "Assunto", "texto")
        message = server.messages[0]
        assert message["To"] == "ana@exemplo.pt"
        assert "mailutils" in message["From"]

    @pytest.mark.parametrize("stage", ["starttls", "login", "send"])
    def test_failures_raise_mail_error(
        self, smtp_settings: config.Settings, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        """Engole a excepção seria pior: o utilizador ficaria à espera de um
        código que nunca chega, sem indicação de porquê."""
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: FakeSMTP(fail=stage))
        with pytest.raises(mailer.MailError):
            mailer.send(smtp_settings, "ana@exemplo.pt", "Assunto", "texto")

    def test_error_does_not_leak_the_password(
        self, smtp_settings: config.Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As excepções do SMTP podem trazer credenciais na mensagem. Por isso
        só o *tipo* vai para o `MailError`."""
        configured = config.Settings(
            **{
                **smtp_settings.__dict__,
                "smtp_user": "ana",
                "smtp_password": "segredo-muito-privado",
            }
        )
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: FakeSMTP(fail="login"))
        with pytest.raises(mailer.MailError) as caught:
            mailer.send(configured, "x@exemplo.pt", "a", "b")
        assert "segredo-muito-privado" not in str(caught.value)

    def test_connection_refused_is_wrapped(
        self, smtp_settings: config.Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: object, **k: object) -> None:
            raise ConnectionRefusedError("sem rede")

        monkeypatch.setattr(smtplib, "SMTP", boom)
        with pytest.raises(mailer.MailError, match="ConnectionRefusedError"):
            mailer.send(smtp_settings, "ana@exemplo.pt", "Assunto", "texto")

    def test_recipient_is_required(self, settings: config.Settings) -> None:
        with pytest.raises(mailer.MailError, match="destinatário"):
            mailer.send(settings, "", "Assunto", "texto")


class TestBackends:
    def test_null_backend_sends_nothing(
        self, settings: config.Settings, capsys: pytest.CaptureFixture
    ) -> None:
        configured = config.Settings(**{**settings.__dict__, "mail_backend": "null"})
        mailer.send(configured, "ana@exemplo.pt", "Assunto", "texto")
        assert capsys.readouterr().out == ""

    def test_console_backend_writes_to_stdout(
        self, settings: config.Settings, capsys: pytest.CaptureFixture
    ) -> None:
        """`console` é o modo de desenvolvimento. O texto tem de aparecer no
        terminal e em lado nenhum mais — nunca em `logging`, que persiste."""
        configured = config.Settings(**{**settings.__dict__, "mail_backend": "console"})
        mailer.send(configured, "ana@exemplo.pt", "Assunto", "texto")
        out = capsys.readouterr().out
        assert "ana@exemplo.pt" in out
        assert "Assunto" in out

    def test_otp_email_carries_the_code(
        self, settings: config.Settings, capsys: pytest.CaptureFixture
    ) -> None:
        configured = config.Settings(**{**settings.__dict__, "mail_backend": "console"})
        mailer.send_otp(configured, "ana@exemplo.pt", "123456")
        out = capsys.readouterr().out
        assert "123456" in out
        assert "10 minutos" in out

    def test_otp_email_html_part_is_escaped_safe(self) -> None:
        _subject, text, html = mailer._render_otp_email("123456", "mailutils", 10)
        assert "123456" in html
        assert "<script" not in html
        assert "10 minutos" in text

    def test_invite_email_carries_the_url(
        self, settings: config.Settings, capsys: pytest.CaptureFixture
    ) -> None:
        configured = config.Settings(**{**settings.__dict__, "mail_backend": "console"})
        mailer.send_invite(configured, "ana@exemplo.pt", "https://x.pt/convite/abc")
        out = capsys.readouterr().out
        assert "https://x.pt/convite/abc" in out

    def test_invite_mentions_expiry(
        self, settings: config.Settings, capsys: pytest.CaptureFixture
    ) -> None:
        configured = config.Settings(**{**settings.__dict__, "mail_backend": "console"})
        mailer.send_invite(configured, "ana@exemplo.pt", "https://x.pt/convite/abc")
        assert "72 horas" in capsys.readouterr().out


class TestImageSniffing:
    @pytest.mark.parametrize(
        ("payload", "extension", "content_type"),
        [
            (PNG, "png", "image/png"),
            (JPEG, "jpg", "image/jpeg"),
            (GIF, "gif", "image/gif"),
            (WEBP, "webp", "image/webp"),
        ],
    )
    def test_recognises_supported_formats(
        self, payload: bytes, extension: str, content_type: str
    ) -> None:
        assert images.sniff(payload) == (extension, content_type)

    @pytest.mark.parametrize(
        "payload",
        [b"", b"nao sou imagem", b"<svg></svg>", b"RIFF" + b"\x00" * 4, SVG],
    )
    def test_rejects_everything_else(self, payload: bytes) -> None:
        assert images.sniff(payload) is None

    def test_riff_without_webp_tag_is_not_an_image(self) -> None:
        """`RIFF` é a assinatura de WAV e de AVI também. Sem o tag `WEBP` nos
        bytes 8-11, não é uma imagem."""
        assert images.sniff(b"RIFF\x24\x00\x00\x00WAVEfmt ") is None


class TestDimensions:
    def test_reads_png(self) -> None:
        assert images.dimensions(PNG, "png") == (1, 1)

    def test_reads_gif(self) -> None:
        assert images.dimensions(GIF, "gif") == (16, 32)

    def test_unknown_format_is_zero_not_a_crash(self) -> None:
        assert images.dimensions(b"xxxx", "webp") == (0, 0)

    def test_truncated_data_does_not_crash(self) -> None:
        assert images.dimensions(PNG[:10], "png") == (0, 0)

    def test_garbage_after_jpeg_header_does_not_crash(self) -> None:
        assert images.dimensions(b"\xff\xd8\xff" + b"\x00" * 200, "jpg") == (0, 0)


class TestStoreUpload:
    def test_stores_a_png(self, tmp_path: Path) -> None:
        stored = images.store_upload(PNG, 7, tmp_path, 2 * 1024 * 1024, 300)
        assert stored.filename == "logo-7.png"
        assert stored.content_type == "image/png"
        assert stored.width == 1
        assert (tmp_path / "logo-7.png").is_file()

    def test_creates_the_media_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "media"
        images.store_upload(PNG, 1, target, 1024 * 1024, 300)
        assert target.is_dir()

    def test_rejects_empty(self, tmp_path: Path) -> None:
        with pytest.raises(images.ImageRejected, match="vazio"):
            images.store_upload(b"", 1, tmp_path, 1024 * 1024, 300)

    def test_rejects_oversized(self, tmp_path: Path) -> None:
        with pytest.raises(images.ImageRejected, match="limite"):
            images.store_upload(PNG, 1, tmp_path, 10, 300)

    def test_rejects_svg(self, tmp_path: Path) -> None:
        """Um SVG pode levar `<script>` dentro. Passa em `Content-Type:
        image/png` e na extensão `.png`; só a assinatura do ficheiro o
        denuncia."""
        with pytest.raises(images.ImageRejected, match="SVG"):
            images.store_upload(SVG, 1, tmp_path, 1024 * 1024, 300)

    def test_rejects_embedded_svg_without_declaration(self, tmp_path: Path) -> None:
        with pytest.raises(images.ImageRejected, match="SVG"):
            images.store_upload(b"<svg onload='x'></svg>", 1, tmp_path, 1024 * 1024, 300)

    def test_rejects_unknown_format(self, tmp_path: Path) -> None:
        with pytest.raises(images.ImageRejected, match="Formato não reconhecido"):
            images.store_upload(b"PK\x03\x04zip", 1, tmp_path, 1024 * 1024, 300)

    def test_oversized_dimensions_are_reported_not_hidden(self, tmp_path: Path) -> None:
        """A imagem é aceite (o limite é de *dimensões*, não de bytes), mas o
        utilizador tem de saber que ela é grande. Cortar em silêncio, ou
        fingir que se redimensionou, são as duas coisas a evitar."""
        big = _png_of_size(900, 900)
        stored = images.store_upload(big, 1, tmp_path, 10 * 1024 * 1024, 300)
        assert stored.width == 900
        assert stored.note
        assert "900" in stored.note
        assert "redimensionada" in stored.note
        assert stored.resized is False

    def test_client_filename_is_irrelevant(self, tmp_path: Path) -> None:
        """O nome do ficheiro é derivado de `upload_id`. Doze caracteres de
        `../../etc/passwd` não chegam ao disco."""
        stored = images.store_upload(PNG, 42, tmp_path, 1024 * 1024, 300)
        assert "/" not in stored.filename
        assert ".." not in stored.filename


class TestDeleteImage:
    def test_deletes_an_existing_file(self, tmp_path: Path) -> None:
        images.store_upload(PNG, 1, tmp_path, 1024 * 1024, 300)
        assert images.delete_image(tmp_path, "logo-1.png") is True
        assert not (tmp_path / "logo-1.png").exists()

    def test_missing_file_is_false(self, tmp_path: Path) -> None:
        assert images.delete_image(tmp_path, "logo-99.png") is False

    @pytest.mark.parametrize(
        "name",
        [
            "../../etc/passwd",
            "logo-1.png/../../etc/passwd",
            "logo-1.exe",
            "logo-1.svg",
            "segredo.txt",
            "logo-",
        ],
    )
    def test_refuses_anything_it_did_not_generate(self, tmp_path: Path, name: str) -> None:
        """O nome tem de ser `logo-<int>.<ext_whitelist>`. Qualquer outra coisa
        é recusada sem tocar no disco — é a defesa contra path traversal na
        escrita."""
        (tmp_path / name.replace("/", "_")).write_bytes(b"x")
        assert images.delete_image(tmp_path, name) is False


def _png_of_size(width: int, height: int) -> bytes:
    """PNG mínimo com as dimensões pedidas, para exercitar o aviso de tamanho."""
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00" * (width * height * 3))
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


class TestOsCabecalhosQueOsFiltrosExigem:
    """Três cabeçalhos que não são opcionais, e que o `EmailMessage` não põe.

    Descobertos porque alguém instalou o produto e os emails não chegavam aos
    destinatários: o Amavis injectava `X-Amavis-Alert` por falta de `Date`, o
    Postfix punha um `Message-ID` com o `myhostname` — que é `.lan` num servidor
    de rede interna, e um domínio não roteável é penalizado de imediato —, e a
    parte `text/html` saía com um segundo `MIME-Version`, que é MIME inválido.
    (F-17)

    Nenhum destes é subjectivo. O RFC 5322 torna `Date` obrigatório, o
    `Message-ID` é o identificador que liga conversa e reputação, e a
    `MIME-Version` é um cabeçalho da mensagem e não de cada parte.
    """

    @staticmethod
    def _mensagem(remetente: str = "mailutils@ltmed.pt") -> EmailMessage:
        from mailutils.mailer import _build_message, _render_otp_email

        settings = dataclasses.replace(
            config.load_settings(env="development"),
            mail_backend="smtp",
            smtp_host="smtp.exemplo.pt",
            smtp_port=587,
            mail_from=remetente,
            mail_from_name="mailutils",
        )
        assunto, texto, html = _render_otp_email("363484", "mailutils", 10)
        return _build_message(settings, "ana@exemplo.pt", assunto, texto, html)

    def test_tem_date(self) -> None:
        """RFC 5322 §3.6: `Date` é obrigatório. Sem ele o Amavis alerta."""
        data = self._mensagem().get("Date")
        assert data, "a mensagem não tem Date: o Amavis injecta X-Amavis-Alert"
        # E tem de ser uma data RFC 2822, não uma string qualquer.
        assert parsedate_to_datetime(data) is not None, f"Date inválido: {data!r}"

    def test_o_message_id_usa_o_dominio_do_remetente(self) -> None:
        """O `Message-ID` é o identificador de reputação. Tem de ser verificável.

        Se o Postfix o gera, sai com o `myhostname` — `servidor.ltmed.lan` num
        servidor de rede interna. Aí o Gmail e a Microsoftothytratam como script
        mal configurado, porque `.lan` não é um domínio de que se possa verificar
        a autoria.
        """
        identificador = self._mensagem("mailutils@ltmed.pt").get("Message-ID")
        assert identificador, "sem Message-ID o Postfix põe um com o myhostname"
        assert identificador.endswith("@ltmed.pt>"), (
            f"o Message-ID tem de ser do domínio do remetente: {identificador!r}"
        )

    def test_o_message_id_segue_o_remetente_e_nao_a_maquina(self) -> None:
        """Mudar de remetente tem de mudar o `Message-ID`."""
        primeiro = self._mensagem("mailutils@ltmed.pt").get("Message-ID")
        segundo = self._mensagem("outro@outraempresa.pt").get("Message-ID")
        assert primeiro.endswith("@ltmed.pt>")
        assert segundo.endswith("@outraempresa.pt>")
        assert primeiro != segundo, "dois Messages-ID iguais em mensagens diferentes"

    def test_a_mime_version_so_existe_no_topo(self) -> None:
        """A `MIME-Version` é um cabeçalho da mensagem, não de cada parte.

        O `add_alternative` do stdlib copia-a para a parte nova — confirmado
        com o Python 3.12 — e dentro dos limites isso é MIME inválido. Parsers
        estritos rejeitam a mensagem.
        """
        mensagem = self._mensagem()
        topo = [p for p in mensagem.walk() if not p.is_multipart()]
        assert len(topo) == 2, f"esperava duas partes, tenho {len(topo)}"
        for parte in topo:
            assert parte.get("MIME-Version") is None, (
                f"a parte {parte.get_content_type()} tem MIME-Version: dentro dos "
                f"limites isso é MIME inválido"
            )
        assert mensagem.get("MIME-Version") == "1.0", "a mensagem precisa de um, no topo"

    def test_a_mensagem_nao_tem_alertas_do_amavis(self) -> None:
        """A correcção é a ausência do alerta, não a sua presença.

        Um teste que verificasse «não há `X-Amavis-Alert`» passaria com uma
        mensagem sem `Date` nenhum, porque o Amavis é que o injecta. O que
        mede a correcção é haver `Date`.
        """
        bruta = self._mensagem().as_bytes().decode("utf-8", "replace")
        cabecalho = bruta.split("\n\n", 1)[0]
        assert "X-Amavis-Alert" not in cabecalho
        assert cabecalho.count("MIME-Version:") == 1, (
            "a mensagem tem de ter exactamente um MIME-Version, no topo"
        )
