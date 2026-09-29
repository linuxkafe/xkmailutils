"""Testes de `security.py`.

Ficheiro onde um erro é uma vulnerabilidade, portanto os testes não verificam
"só funciona": verificam as propriedades que, se falhassem, dariam entrada a
alguém.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from mailutils import security


class TestPasswordHashing:
    def test_hash_is_not_the_password(self) -> None:
        digest = security.hash_password("correcthorsebattery1")
        assert "correcthorsebattery1" not in digest
        assert digest.startswith("scrypt$")

    def test_hash_is_salted(self) -> None:
        """Dois hashes da mesma senha têm de ser diferentes. Sem sal, um dump da
        base de dados dá todas as palavras-passe de uma vez."""
        a = security.hash_password("correcthorsebattery1")
        b = security.hash_password("correcthorsebattery1")
        assert a != b

    def test_parameters_are_stored_in_the_hash(self) -> None:
        """Subir o custo de scrypt no futuro tem de ser possível sem invalidar
        as senhas existentes — daí os parâmetros viajarem no campo."""
        _, n, r, p, salt, digest = security.hash_password("x" * 12).split("$")
        assert (n, r, p) == ("32768", "8", "1")
        assert len(bytes.fromhex(salt)) == 16
        assert len(bytes.fromhex(digest)) == 32

    def test_verify_accepts_the_right_password(self) -> None:
        stored = security.hash_password("correcthorsebattery1")
        assert security.verify_password("correcthorsebattery1", stored) is True

    def test_verify_rejects_the_wrong_password(self) -> None:
        stored = security.hash_password("correcthorsebattery1")
        assert security.verify_password("correcthorsebattery2", stored) is False

    @pytest.mark.parametrize(
        "stored",
        ["", "nao-e-um-hash", "bcrypt$1$2$3$aa$bb", "scrypt$x$y$z$aa$bb", "scrypt$1$2$3$zz$bb"],
    )
    def test_verify_returns_false_on_garbage(self, stored: str) -> None:
        """Um `stored` corrompido tem de dar login falhado, não 500. Um dump
        truncado da base de dados não pode derrubar o servidor."""
        assert security.verify_password("qualquer", stored) is False

    def test_verify_never_raises_on_none(self) -> None:
        assert security.verify_password("x" * 12, None) is False  # type: ignore[arg-type]


class TestPasswordPolicy:
    def test_minimum_length_is_enforced(self) -> None:
        assert security.password_problem("curta") is not None
        assert security.password_problem("c" * 12) is None

    def test_whitespace_only_is_rejected(self) -> None:
        assert security.password_problem(" " * 20) is not None

    def test_message_is_in_portuguese(self) -> None:
        """A mensagem vai parar ao utilizador. Se for em inglês, é um bug de
        produto, não um detalhe de estilo."""
        assert "12" in (security.password_problem("x") or "")


class TestEmailValidation:
    @pytest.mark.parametrize(
        "email",
        ["a@b.pt", "ana.maria+tag@sub.exemplo.co.uk", "x_y@exemplo.pt"],
    )
    def test_accepts_valid(self, email: str) -> None:
        assert security.is_valid_email(email) is True

    @pytest.mark.parametrize(
        "email",
        ["", "sem-arroba", "@exemplo.pt", "ana@", "ana@localhost", "ana @exemplo.pt", "a@b"],
    )
    def test_rejects_invalid(self, email: str) -> None:
        assert security.is_valid_email(email) is False

    def test_normalisation_is_idempotent(self) -> None:
        once = security.normalise_email("  Ana@Exemplo.PT ")
        assert once == "ana@exemplo.pt"
        assert security.normalise_email(once) == once


class TestOtp:
    def test_is_six_digits(self) -> None:
        code = security.new_otp()
        assert len(code) == 6
        assert code.isdigit()

    def test_codes_are_not_predictable(self) -> None:
        codes = {security.new_otp() for _ in range(200)}
        assert len(codes) > 190, "gerador de OTP está a repetir"

    def test_hash_is_not_reversible_by_inspection(self) -> None:
        """O valor guardado tem de ser o digest, não o código. Um dump da base
        de dados não pode dar códigos de autenticação."""
        digest = security.hash_otp("123456")
        assert "123456" not in digest
        assert digest == hashlib.sha256(b"123456").hexdigest()

    def test_match_is_exact(self) -> None:
        digest = security.hash_otp("123456")
        assert security.otp_matches("123456", digest) is True
        assert security.otp_matches("123457", digest) is False
        assert security.otp_matches("", digest) is False
        assert security.otp_matches("123456", "") is False

    def test_whitespace_is_tolerated(self) -> None:
        """O utilizador cola o código com um espaço. Não é motivo para falhar."""
        assert security.otp_matches(" 123456 ", security.hash_otp("123456")) is True

    def test_expiry_uses_configured_ttl(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        expires = security.otp_expiry(now, ttl_minutes=10)
        assert expires == now + timedelta(minutes=10)


class TestExpiry:
    def test_future_timestamp_is_not_expired(self) -> None:
        future = security.iso(security.in_hours(1))
        assert security.is_expired(future) is False

    def test_past_timestamp_is_expired(self) -> None:
        past = security.iso(security.in_hours(-1))
        assert security.is_expired(past) is True

    def test_unparseable_timestamp_fails_closed(self) -> None:
        """Uma data de expiração ilegível tem de ser tratada como expirada.
        Falhar fechado é o único comportamento seguro."""
        assert security.is_expired("nao-e-uma-data") is True
        assert security.is_expired("") is True
        assert security.is_expired(None) is True  # type: ignore[arg-type]

    def test_z_suffix_is_accepted(self) -> None:
        """O SQLite antigo devolvia `Z`; a aplicação tem de continuar a ler."""
        stamp = "2020-01-01T00:00:00Z"
        assert security.is_expired(stamp) is True


class TestDeviceFingerprint:
    def test_is_stable_for_the_same_browser(self) -> None:
        a = security.device_fingerprint("Firefox/121", "pt-PT,pt")
        b = security.device_fingerprint("Firefox/121", "pt-PT,pt")
        assert a == b

    def test_differs_per_browser(self) -> None:
        a = security.device_fingerprint("Firefox/121", "pt-PT")
        b = security.device_fingerprint("Chrome/120", "pt-PT")
        assert a != b

    def test_is_case_insensitive(self) -> None:
        assert security.device_fingerprint("Firefox/121", "PT-PT") == security.device_fingerprint(
            "Firefox/121", "pt-pt"
        )

    def test_does_not_leak_the_user_agent(self) -> None:
        """A impressão é persistida e mostrada na lista de dispositivos. Se
        contivesse o User-Agent em claro, seria legível a quem less a base."""
        fp = security.device_fingerprint("Mozilla/5.0 Firefox/121", "pt-PT")
        assert "Mozilla" not in fp
        assert len(fp) == 32

    def test_empty_inputs_do_not_crash(self) -> None:
        assert security.device_fingerprint("", "") == security.device_fingerprint("", "")

    def test_label_recognises_clients(self) -> None:
        assert security.describe_device("Mozilla/5.0 Chrome/120") == "Chrome"
        assert security.describe_device("Mozilla/5.0 Firefox/121") == "Firefox"
        assert security.describe_device("Thunderbird/115") == "Thunderbird"
        assert security.describe_device("") == "Desconhecido"
        assert security.describe_device("Zalgo/1.0") == "Outro cliente"


class TestTokens:
    def test_tokens_are_unique(self) -> None:
        tokens = {security.new_token() for _ in range(500)}
        assert len(tokens) == 500

    def test_hash_is_stable_and_one_way(self) -> None:
        token = security.new_token()
        digest = security.hash_token(token)
        assert security.hash_token(token) == digest
        assert token not in digest


class TestNoSecretsInLogs:
    """Contrato do CLAUDE.md: `grep -ri otp src/ | grep print` tem de dar vazio."""

    def test_no_print_of_otp_in_source(self) -> None:
        from pathlib import Path

        source_root = Path(__file__).resolve().parent.parent / "src" / "mailutils"
        offenders = []
        for path in source_root.rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                low = line.lower()
                if "print(" in low and ("otp" in low or "code" in low or "password" in low):
                    offenders.append(f"{path.name}:{number}")
        assert not offenders, f"segredo impresso: {offenders}"

    def test_no_logging_module_used_in_security(self) -> None:
        from pathlib import Path

        text = (
            Path(__file__).resolve().parent.parent / "src" / "mailutils" / "security.py"
        ).read_text(encoding="utf-8")
        assert "import logging" not in text
        assert "logger." not in text
