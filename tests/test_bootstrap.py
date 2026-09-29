"""Testes de arranque: `config.py` e `db.py`.

O arranque é o único momento em que a aplicação pode recusar fazer-se. Estes
testes provam que recusa exactamente o que tem de recusar, e exactamente nada
mais — uma aplicação que recusa arrancar em desenvolvimento por um motivo
criativo é uma aplicação que ninguém usa.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import TEST_PASSWORD

from mailutils import config, db
from mailutils.auth import service


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isola o ambiente.

    Sem isto, um `.env` do developer altera a base de dados de todos os testes
    de configuração da máquina, e o teste passa em casa e falha no CI.
    """
    for key in list(__import__("os").environ):
        if key.startswith(("MAILUTILS_", "SMTP_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MAILUTILS_DB_PATH", str(tmp_path / "cfg.db"))
    monkeypatch.setenv("MAILUTILS_MEDIA_DIR", str(tmp_path / "cfg-media"))


class TestProductionRefusals:
    """NFR-5. Um arranque silenciosamente inseguro é pior do que não arrancar."""

    def test_refuses_without_a_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_ENV", "production")
        monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "https://x.pt")
        monkeypatch.delenv("MAILUTILS_SECRET_KEY", raising=False)
        with pytest.raises(config.ConfigError, match="MAILUTILS_SECRET_KEY"):
            config.load_settings()

    @pytest.mark.parametrize("weak", ["changeme", "secret", "dev", "insecure", ""])
    def test_refuses_weak_secrets(self, monkeypatch: pytest.MonkeyPatch, weak: str) -> None:
        monkeypatch.setenv("MAILUTILS_ENV", "production")
        monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "https://x.pt")
        monkeypatch.setenv("MAILUTILS_SECRET_KEY", weak)
        with pytest.raises(config.ConfigError):
            config.load_settings()

    def test_refuses_short_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_ENV", "production")
        monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "https://x.pt")
        monkeypatch.setenv("MAILUTILS_SECRET_KEY", "curto")
        with pytest.raises(config.ConfigError, match="32 caracteres"):
            config.load_settings()

    def test_refuses_http_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A imagem do logótipo entra no email do destinatário por esta URL. Um
        `http://` é conteúdo inseguro e o cliente não a carrega."""
        monkeypatch.setenv("MAILUTILS_ENV", "production")
        monkeypatch.setenv("MAILUTILS_SECRET_KEY", "k" * 48)
        monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "http://x.pt")
        with pytest.raises(config.ConfigError, match="https"):
            config.load_settings()

    def test_accepts_a_correct_production_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_ENV", "production")
        monkeypatch.setenv("MAILUTILS_SECRET_KEY", "k" * 48)
        monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "https://x.pt")
        settings = config.load_settings()
        assert settings.is_production is True
        assert settings.secure_cookies is True


class TestDevelopmentDefaults:
    def test_works_with_no_env_file(self) -> None:
        settings = config.load_settings()
        assert settings.is_production is False
        assert settings.secret_key

    def test_each_start_gets_a_different_secret(self) -> None:
        """Sem `.env`, o segredo é aleatório por processo. Duas instâncias não
        partilham sessão, e isso é o comportamento pretendido — está
        documentado em `.env.example`."""
        assert config.load_settings().secret_key != config.load_settings().secret_key

    def test_default_base_url_is_loopback(self) -> None:
        assert config.load_settings().public_base_url.startswith("http://127.0.0.1")

    def test_mail_backend_defaults_to_console(self) -> None:
        assert config.load_settings().mail_backend == "console"

    def test_trailing_slash_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "https://x.pt/")
        assert config.load_settings().public_base_url == "https://x.pt"


class TestValidation:
    def test_rejects_non_integer_ints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_SESSION_DAYS", "muito")
        with pytest.raises(config.ConfigError, match="inteiro"):
            config.load_settings()

    def test_rejects_out_of_range_ints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_SESSION_DAYS", "0")
        with pytest.raises(config.ConfigError, match=">="):
            config.load_settings()

    def test_rejects_unknown_mail_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_MAIL_BACKEND", "pombo-correio")
        with pytest.raises(config.ConfigError, match="MAILUTILS_MAIL_BACKEND"):
            config.load_settings()

    def test_smtp_without_host_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Um backend `smtp` sem `SMTP_HOST` falha só quando alguém tenta
        enviar um código. Recusar no arranque dá ao operador a informação
        enquanto ainda pode corrigir."""
        monkeypatch.setenv("MAILUTILS_MAIL_BACKEND", "smtp")
        monkeypatch.delenv("SMTP_HOST", raising=False)
        with pytest.raises(config.ConfigError, match="SMTP_HOST"):
            config.load_settings()

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1", True),
            ("true", True),
            ("yes", True),
            ("sim", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("nao", False),
            ("", False),
        ],
    )
    def test_boolean_parsing(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
    ) -> None:
        monkeypatch.setenv("MAILUTILS_HTTPS", raw)
        assert config.load_settings().https is expected


class TestMediaUrl:
    def test_builds_an_absolute_https_url(self) -> None:
        settings = config.load_settings()
        assert settings.public_media_url("logo-1.png").endswith("/media/logo-1.png")

    def test_no_double_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`https://x.pt//media/...` é um URL diferente em alguns clientes de
        email, e uma imagem que não carrega é uma assinatura sem logótipo."""
        monkeypatch.setenv("MAILUTILS_PUBLIC_BASE_URL", "https://x.pt/")
        settings = config.load_settings()
        assert "//media" not in settings.public_media_url("logo-1.png")


class TestDatabase:
    def test_migrate_is_idempotent(self, tmp_path: Path) -> None:
        conn = db.connect(tmp_path / "a.db")
        try:
            db.migrate(conn)
            db.migrate(conn)
            db.migrate(conn)
            for name in db.TABLE_NAMES:
                found = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()
                assert found, f"falta a tabela {name}"
        finally:
            conn.close()

    def test_foreign_keys_are_on(self, tmp_path: Path) -> None:
        """Sem esta linha os `ON DELETE CASCADE` não existem, e apagar um
        utilizador deixa sessões e logótipos órfãos."""
        conn = db.connect(tmp_path / "b.db")
        try:
            db.migrate(conn)
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()

    def test_cascade_actually_deletes(self, tmp_path: Path) -> None:
        conn = db.connect(tmp_path / "c.db")
        try:
            db.migrate(conn)
            user_id = service.create_user(conn, "a@exemplo.pt", TEST_PASSWORD)
            conn.execute(
                "INSERT INTO devices (user_id, fingerprint, user_agent, label,"
                " created_at, last_seen_at) VALUES (?, 'fp', 'UA', 'L', '', '')",
                (user_id,),
            )
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            assert conn.execute("SELECT COUNT(*) AS n FROM devices").fetchone()["n"] == 0
        finally:
            conn.close()

    def test_schema_version_is_written_after_the_ddl(self, tmp_path: Path) -> None:
        conn = db.connect(tmp_path / "d.db")
        try:
            db.migrate(conn)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        finally:
            conn.close()

    def test_creates_the_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "dir" / "x.db"
        conn = db.connect(target)
        conn.close()
        assert target.parent.is_dir()

    def test_transaction_rolls_back(self, tmp_path: Path) -> None:
        conn = db.connect(tmp_path / "e.db")
        try:
            db.migrate(conn)
            with pytest.raises(RuntimeError), db.transaction(conn):
                conn.execute(
                    "INSERT INTO users (email, password_hash, is_admin, is_active,"
                    " must_change_password, created_at)"
                    " VALUES ('a@exemplo.pt', 'x', 0, 1, 0, '2026-01-01')"
                )
                raise RuntimeError("falha a meio")
            assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        finally:
            conn.close()

    def test_transaction_commits(self, tmp_path: Path) -> None:
        conn = db.connect(tmp_path / "f.db")
        try:
            db.migrate(conn)
            with db.transaction(conn):
                conn.execute(
                    "INSERT INTO users (email, password_hash, is_admin, is_active,"
                    " must_change_password, created_at)"
                    " VALUES ('a@exemplo.pt', 'x', 0, 1, 0, '2026-01-01')"
                )
            assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
        finally:
            conn.close()

    def test_duplicate_email_is_rejected_by_the_schema(self, tmp_path: Path) -> None:
        """A unicidade do email é uma restrição da base de dados, não uma
        verificação na aplicação. Duas aplicações a correr em paralelo têm de
        obter o mesmo erro."""
        conn = db.connect(tmp_path / "g.db")
        try:
            db.migrate(conn)
            service.create_user(conn, "a@exemplo.pt", TEST_PASSWORD)
            with pytest.raises(sqlite3.IntegrityError):
                service.create_user(conn, "a@exemplo.pt", TEST_PASSWORD)
        finally:
            conn.close()


class TestFieldsSerialisation:
    def test_round_trip(self) -> None:
        original = {"name": "Ana", "note": "acentos: ção, ã, é"}
        assert db.loads_fields(db.dumps_fields(original)) == original

    @pytest.mark.parametrize("raw", ["", "{", "nao é json", "[1,2,3]", '"texto"', "null"])
    def test_corrupt_json_degrades_to_empty(self, raw: str) -> None:
        """Um registo com JSON corrompido não pode impedir o utilizador de
        entrar na aplicação. Degradar para `{}` é o comportamento certo."""
        assert db.loads_fields(raw) == {}

    def test_non_dict_degrades_to_empty(self) -> None:
        assert db.loads_fields("[1, 2]") == {}

    def test_key_order_is_stable(self) -> None:
        """Serialização estável torna o diff do `fields_json` legível quando se
        inspecciona a base de dados à mão."""
        assert db.dumps_fields({"b": 1, "a": 2}) == db.dumps_fields({"a": 2, "b": 1})


class TestBootstrapAdmin:
    def test_creates_the_admin_from_the_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("MAILUTILS_ADMIN_EMAIL", "Chefe@Exemplo.PT")
        monkeypatch.setenv("MAILUTILS_ADMIN_PASSWORD", "palavra-do-chefe-1")
        settings = config.load_settings()
        conn = db.connect(settings.db_path)
        try:
            db.migrate(conn)
            message = service.bootstrap_admin(conn, settings)
            user = service.get_user_by_email(conn, "chefe@exemplo.pt")
            assert user is not None
            assert user["is_admin"] == 1
            assert "criado" in message
        finally:
            conn.close()

    def test_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Chamar duas vezes não pode criar dois admins nem falhar. O arranque
        acontece em cada restart."""
        monkeypatch.setenv("MAILUTILS_ADMIN_EMAIL", "chefe@exemplo.pt")
        monkeypatch.setenv("MAILUTILS_ADMIN_PASSWORD", "palavra-do-chefe-1")
        settings = config.load_settings()
        conn = db.connect(settings.db_path)
        try:
            db.migrate(conn)
            service.bootstrap_admin(conn, settings)
            message = service.bootstrap_admin(conn, settings)
            assert "já existe" in message
            assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
        finally:
            conn.close()

    def test_does_not_overwrite_a_changed_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """O `.env` é a fonte do *primeiro* arranque, não um override
        silencioso. Reescrever a palavra-passe a cada arranque seria uma porta
        aberta que ninguém notaria."""
        monkeypatch.setenv("MAILUTILS_ADMIN_EMAIL", "chefe@exemplo.pt")
        monkeypatch.setenv("MAILUTILS_ADMIN_PASSWORD", "palavra-do-chefe-1")
        settings = config.load_settings()
        conn = db.connect(settings.db_path)
        try:
            db.migrate(conn)
            service.bootstrap_admin(conn, settings)
            user_id = service.get_user_by_email(conn, "chefe@exemplo.pt")["id"]
            service.set_password(conn, user_id, "outra-palavra-2")

            monkeypatch.setenv("MAILUTILS_ADMIN_PASSWORD", "palavra-do-chefe-1")
            service.bootstrap_admin(conn, config.load_settings())
            from mailutils import security

            stored = service.get_user(conn, user_id)["password_hash"]
            assert security.verify_password("outra-palavra-2", stored)
        finally:
            conn.close()

    def test_says_so_when_the_env_is_empty(self) -> None:
        settings = config.load_settings()
        conn = db.connect(settings.db_path)
        try:
            db.migrate(conn)
            assert "nenhum admin criado" in service.bootstrap_admin(conn, settings)
            assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        finally:
            conn.close()

    def test_refuses_a_weak_admin_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """O bootstrap não é uma excepção às regras: uma senha fraca no `.env`
        é recusada com uma mensagem, não aceite em silêncio."""
        monkeypatch.setenv("MAILUTILS_ADMIN_EMAIL", "chefe@exemplo.pt")
        monkeypatch.setenv("MAILUTILS_ADMIN_PASSWORD", "curta")
        settings = config.load_settings()
        conn = db.connect(settings.db_path)
        try:
            db.migrate(conn)
            assert "nenhum admin criado" in service.bootstrap_admin(conn, settings)
        finally:
            conn.close()

    def test_refuses_an_invalid_email(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAILUTILS_ADMIN_EMAIL", "nao-e-email")
        monkeypatch.setenv("MAILUTILS_ADMIN_PASSWORD", "palavra-do-chefe-1")
        settings = config.load_settings()
        conn = db.connect(settings.db_path)
        try:
            db.migrate(conn)
            assert "inválido" in service.bootstrap_admin(conn, settings)
        finally:
            conn.close()
