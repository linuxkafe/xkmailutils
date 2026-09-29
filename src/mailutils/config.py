"""Leitura de configuração a partir do ambiente e de `.env`.

Porquê um módulo só: a app tem de recusar arrancar em produção sem `SECRET_KEY`.
Essa decisão tem de estar num sítio único e testável, não espalhada por
`main.py`. (NFR-5)
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "var" / "mailutils.db"
DEFAULT_MEDIA_DIR = BASE_DIR / "var" / "media"

#: Prefixo de path por omissão.
#:
#: A aplicação é desenhada para ser servida em `/xkmailutils/...` e não na raiz
#: do domínio. Três razões, e a terceira é a que mata:
#:
#: 1. Coexiste com o que já esteja no mesmo host.
#: 2. O URL da imagem fica `{host}/xkmailutils/media/{ficheiro}` — tem o
#:    namespace da aplicação, não um caminho genérico que colide com outro
#:    serviço. É este o formato que o projecto exige e que os testes fixam.
#: 3. Um proxy invertido que não stripping de prefixo passa a ser impossível
#:    de configurar mal sem que o bug seja visível na primeira imagem.
DEFAULT_PATH_PREFIX = "/xkmailutils"

#: Valores sentinela que nunca são aceitáveis como segredo real.
_WEAK_SECRETS = frozenset({"", "changeme", "secret", "dev", "insecure"})


class ConfigError(RuntimeError):
    """Arranque recusado por configuração inválida. Erro deliberado, não aviso."""


def _load_dotenv(path: Path) -> None:
    """Lê `KEY=value` de `.env` para `os.environ` sem sobrescrever o ambiente real.

    O ambiente real ganha sempre: em produção o `.env` não existe e injectar
    valores de ficheiro seria um caminho silencioso para defaults fracos.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _ephemeral_dev_secret() -> str:
    """Segredo aleatório para desenvolvimento sem `.env`.

    Vive só neste processo: reiniciar invalida as sessões. Aceitável em dev,
    motivo de recusa em produção — daí `_WEAK_SECRETS` e a validação de
    comprimento.
    """
    return secrets.token_urlsafe(48)


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} tem de ser um inteiro, veio {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} tem de ser >= {minimum}, veio {value}")
    return value


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "sim"}


def _normalise_prefix(raw: str) -> str:
    """Normaliza o prefixo de path para `""` ou `"/algo"`.

    Sem normalizar, `/xkmailutils/` produziria `//media/...` — que é um URL
    diferente em vários clientes de email, e uma imagem que não carrega é uma
    assinatura sem logótipo. Porquê tolerar `""`: alguém pode querer servir
    na raiz, e um `if` que o impede é um if que alguém contorna.
    """
    value = (raw or "").strip()
    if not value or value == "/":
        return ""
    value = "/" + value.strip("/")
    if not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)+", value):
        raise ConfigError(
            f"MAILUTILS_PATH_PREFIX inválido: {raw!r}. Use um caminho tipo "
            "'/xkmailutils', sem espaços nem caracteres especiais."
        )
    return value


@dataclass(frozen=True)
class Settings:
    """Configuração imutável de um arranque. Uma instância por processo."""

    env: str
    is_production: bool
    secret_key: str
    db_path: Path
    media_dir: Path
    public_base_url: str
    path_prefix: str
    https: bool
    secure_cookies: bool
    trust_proxy: bool
    allow_insecure_media: bool
    warnings: tuple[str, ...]

    admin_email: str
    admin_password: str

    session_days: int
    otp_ttl_minutes: int
    otp_max_attempts: int
    otp_cooldown_seconds: int
    max_devices: int
    invite_ttl_hours: int
    login_max_attempts: int
    login_window_minutes: int

    max_upload_bytes: int
    logo_max_px: int
    max_visible_links: int

    mail_backend: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_starttls: bool
    mail_from: str
    mail_from_name: str

    def base_url(self) -> str:
        """URL da raiz da aplicação, com o prefixo.

        É isto que um cliente de teste usa como `base_url`, e é o que faz com
        que os testes peçam `/entrar` e acabem em `/xkmailutils/entrar` — a
        mesma concatenação que um browser faz.
        """
        return f"{self.public_base_url.rstrip('/')}{self.path_prefix}"

    def url(self, path: str) -> str:
        """Junta o prefixo a um caminho interno.

        Uso único: URLs que o browser segue. Sem isto, servir a app em
        `/xkmailutils` obriga a duplicar o prefixo em cada `href` e `action` de
        cada template, e basta esquecer um para o formulário deixar de ter
        token CSRF.
        """
        return f"{self.path_prefix}{path if path.startswith('/') else '/' + path}"

    def public_media_url(self, filename: str) -> str:
        """URL absoluta da imagem do logótipo.

        Formato: `{host}{prefixo}/media/{ficheiro}`, com o prefixo
        obrigatório. É este caminho que o teste `test_media_url` fixa: a imagem
        entra no email de um destinatário que não conhece a instalação, e o
        URL tem de ser não só correcto como *não ambíguo* com outro serviço no
        mesmo host.

        `data:` e `http://` são recessos no score de spam (FR-4.2). A barreira
        é aqui, num sítio só, não espalhada pelo renderer.
        """
        return f"{self.public_base_url.rstrip('/')}{self.path_prefix}/media/{filename}"

    def public_url(self, path: str) -> str:
        """URL absoluta de uma página da aplicação."""
        return f"{self.public_base_url.rstrip('/')}{self.url(path)}"


def load_settings(env: str | None = None) -> Settings:
    """Lê a configuração e valida as invariantes de arranque.

    Levanta `ConfigError` — não corrigir, não avisar — quando a configuração é
    perigosa. Um arranque silenciosamente inseguro é pior que não arrancar.
    """
    _load_dotenv(BASE_DIR / ".env")

    env_name = (env or os.environ.get("MAILUTILS_ENV") or "development").strip()
    is_production = env_name == "production"

    secret_key = os.environ.get("MAILUTILS_SECRET_KEY", "")
    if is_production:
        if secret_key in _WEAK_SECRETS:
            raise ConfigError(
                "MAILUTILS_SECRET_KEY tem de ser definida em produção "
                f"(valor recebido: {secret_key!r}). "
                'Gere uma com: python -c "import secrets;'
                ' print(secrets.token_urlsafe(48))"'
            )
        if len(secret_key) < 32:
            raise ConfigError(
                "MAILUTILS_SECRET_KEY tem de ter pelo menos 32 caracteres em produção."
            )

    if not secret_key:
        secret_key = _ephemeral_dev_secret()

    public_base_url = (
        (os.environ.get("MAILUTILS_PUBLIC_BASE_URL", "http://127.0.0.1:8000")).strip().rstrip("/")
    )
    https = _bool("MAILUTILS_HTTPS", default=is_production)

    # Confiar em `X-Forwarded-For`.
    #
    # Só ligar atrás de um proxy reverso que se controle. O limitador de
    # tentativas de login é por email+IP, e sem isto **todo o tráfego parece vir
    # do proxy**: cinco tentativas erradas de uma pessoa bloqueariam a
    # instalação inteira.
    #
    # O outro lado: ligado sem proxy, o `X-Forwarded-For` é controlado por quem
    # faz o pedido, e o limitador deixa de ser um limitador. Por isso é uma
    # decisão explícita e não um automatismo.
    trust_proxy = _bool("MAILUTILS_TRUST_PROXY", default=False)

    # `.get` com omissão explícita, e **não** `or`.
    #
    # `os.environ.get(X, PADRAO)` devolve `""` se a variável existir e estiver
    # vazia — que é exactamente o que "quero a aplicação na raiz" significa.
    # Com `or DEFAULT`, uma variável vazia seria indistinguível de não
    # definida, e a única forma de servir na raiz seria editar o código.
    path_prefix = _normalise_prefix(os.environ.get("MAILUTILS_PATH_PREFIX", DEFAULT_PATH_PREFIX))

    # TLS é a recomendação, não a imposição.
    #
    # Um servidor atrás de NAT, uma rede interna ou um ambiente de testes pode
    # legitimamente não ter portas 80/443. Proibir http:// em produção deixaria
    # essas instalações sem arrancar, e a resposta certa não é afrouxar a regra
    # em silêncio: é dar-lhe um nome, para que quem escreve a configuração saiba
    # exactamente o que está a aceitar.
    #
    # A consequência é real e não é secreta. O logótipo vai entrar no email por
    # http://, o score de spam penaliza-o (regra INSECURE_URL), e vários
    # clientes não carregam imagens http. Está escrito no `.env.example`, no
    # README e na interface.
    allow_insecure = _bool("MAILUTILS_ALLOW_INSECURE_MEDIA")
    warnings: list[str] = []
    if is_production and not public_base_url.startswith("https://"):
        if not allow_insecure:
            raise ConfigError(
                "MAILUTILS_PUBLIC_BASE_URL tem de ser https:// em produção. "
                f"Recebido: {public_base_url!r}. Sem isto, a imagem do logótipo "
                "entra no email por http://, vários clientes não a carregam, e "
                "o próprio mailutils penaliza a assinatura. "
                "Se esta instalação é deliberadamente sem TLS, ponha "
                "MAILUTILS_ALLOW_INSECURE_MEDIA=1 e aceite a penalização."
            )
        warnings.append(
            "MAILUTILS_ALLOW_INSECURE_MEDIA está activo: as imagens do logótipo "
            "vão entrar nos emails por http://. Vários clientes não as carregam e "
            "o score de spam penaliza a assinatura."
        )

    mail_backend = (os.environ.get("MAILUTILS_MAIL_BACKEND") or "").strip().lower()
    if not mail_backend:
        mail_backend = "smtp" if os.environ.get("SMTP_HOST") else "console"
    if mail_backend not in {"smtp", "console", "null"}:
        raise ConfigError(
            f"MAILUTILS_MAIL_BACKEND inválido: {mail_backend!r}. "
            "Valores aceitos: smtp, console, null."
        )

    smtp_host = (os.environ.get("SMTP_HOST") or "").strip()
    if mail_backend == "smtp" and not smtp_host:
        raise ConfigError(
            "MAILUTILS_MAIL_BACKEND=smtp exige SMTP_HOST definido. "
            "Em desenvolvimento use MAILUTILS_MAIL_BACKEND=console."
        )

    db_path = Path(os.environ.get("MAILUTILS_DB_PATH") or DEFAULT_DB_PATH)
    media_dir = Path(os.environ.get("MAILUTILS_MEDIA_DIR") or DEFAULT_MEDIA_DIR)

    return Settings(
        env=env_name,
        is_production=is_production,
        secret_key=secret_key,
        db_path=db_path,
        media_dir=media_dir,
        public_base_url=public_base_url,
        path_prefix=path_prefix,
        https=https,
        # `Secure` é ligado pelo esquema **público**, não pelo facto de estar em
        # produção. Um cookie `Secure` servido por http:// nunca volta ao
        # servidor: o browser descarta-o. Ligar isto só porque `env=production`
        # tornava a aplicação inutilizável numa instalação deliberadamente sem
        # TLS — o login funcionaria uma vez e a sessão desapareceria a seguir.
        #
        # A segurança que `Secure` dá continua a ter de ser pensada: em produção
        # sobre http, um cookie de sessão viaja em claro na rede. É por isso que
        # a recusa de http continua a existir, e por isso que
        # `MAILUTILS_ALLOW_INSECURE_MEDIA` é uma decisão nomeada em vez de um
        # default.
        secure_cookies=https,
        trust_proxy=trust_proxy,
        allow_insecure_media=allow_insecure,
        warnings=tuple(warnings),
        admin_email=(os.environ.get("MAILUTILS_ADMIN_EMAIL") or "").strip().lower(),
        admin_password=os.environ.get("MAILUTILS_ADMIN_PASSWORD") or "",
        session_days=_int("MAILUTILS_SESSION_DAYS", 30, minimum=1),
        otp_ttl_minutes=_int("MAILUTILS_OTP_TTL_MINUTES", 10, minimum=1),
        otp_max_attempts=_int("MAILUTILS_OTP_MAX_ATTEMPTS", 5, minimum=1),
        otp_cooldown_seconds=_int("MAILUTILS_OTP_COOLDOWN_SECONDS", 60, minimum=0),
        max_devices=_int("MAILUTILS_MAX_DEVICES", 20, minimum=1),
        invite_ttl_hours=_int("MAILUTILS_INVITE_TTL_HOURS", 72, minimum=1),
        login_max_attempts=_int("MAILUTILS_LOGIN_MAX_ATTEMPTS", 5, minimum=1),
        login_window_minutes=_int("MAILUTILS_LOGIN_WINDOW_MINUTES", 15, minimum=1),
        max_upload_bytes=_int("MAILUTILS_MAX_UPLOAD_BYTES", 2 * 1024 * 1024, minimum=1024),
        logo_max_px=_int("MAILUTILS_LOGO_MAX_PX", 300, minimum=16),
        max_visible_links=_int("MAILUTILS_MAX_VISIBLE_LINKS", 6, minimum=1),
        mail_backend=mail_backend,
        smtp_host=smtp_host,
        smtp_port=_int("SMTP_PORT", 587, minimum=1),
        smtp_user=os.environ.get("SMTP_USER") or "",
        smtp_password=os.environ.get("SMTP_PASSWORD") or "",
        smtp_starttls=_bool("SMTP_STARTTLS", default=True),
        mail_from=(os.environ.get("MAILUTILS_MAIL_FROM") or "mailutils@localhost").strip(),
        mail_from_name=(os.environ.get("MAILUTILS_MAIL_FROM_NAME") or "mailutils").strip(),
    )
