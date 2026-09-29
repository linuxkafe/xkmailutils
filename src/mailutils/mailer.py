"""Envio de email (códigos OTP e convites).

Três backends, escolhidos por `MAILUTILS_MAIL_BACKEND`:

- `smtp`    — o servidor SMTP configurado. Único modo aceitável em produção.
- `console` — escreve no stdout. Desenvolvimento.
- `null`    — descarta. Testes.

Regra inviolável: o corpo do email **nunca** passa por `logging`. Um código de
6 dígitos num log é um código de autenticação em texto plano num ficheiro que
o CI arquiva. (CLAUDE.md, Never Do)
"""

from __future__ import annotations

import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formataddr

from .config import Settings


class MailError(RuntimeError):
    """Envio falhou. Nunca inclui o conteúdo da mensagem."""


def _render_otp_email(code: str, app_name: str, minutes: int) -> tuple[str, str, str]:
    """Assunto e corpo em texto simples, mais o texto alternativo HTML.

    Texto simples primeiro: é o que chega quando o cliente decide não mostrar
    HTML. E o corpo não diz de que serviço é o código além do nome da app.
    """
    subject = f"{code} é o seu código de acesso"
    text = (
        f"O seu código de acesso ao {app_name} é:\n\n"
        f"    {code}\n\n"
        f"É válido durante {minutes} minutos e só pode ser usado uma vez.\n\n"
        "Se não foi você, ignore este email. O acesso não foi concluído.\n\n"
        f"— {app_name}"
    )
    html = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        f'color:#212121;line-height:22px;max-width:480px;">'
        f"<p>O seu código de acesso ao <strong>{app_name}</strong> é:</p>"
        f'<p style="font-size:28px;font-weight:700;letter-spacing:4px;'
        f'color:#212121;margin:16px 0;">{code}</p>'
        f'<p style="color:#555;font-size:13px;">É válido durante {minutes} minutos '
        f"e só pode ser usado uma vez.</p>"
        f'<p style="color:#555;font-size:13px;">Se não foi você, ignore este '
        f"email. O acesso não foi concluído.</p>"
        f'<p style="color:#555;font-size:13px;">— {app_name}</p>'
        f"</div>"
    )
    return subject, text, html


def _render_invite_email(invite_url: str, app_name: str, hours: int) -> tuple[str, str, str]:
    subject = "Convidado para o " + app_name
    text = (
        f"Foi convidado para usar o {app_name}.\n\n"
        f"    {invite_url}\n\n"
        f"O convite é válido durante {hours} horas.\n\n"
        "Se não esperava este email, ignore-o. Ninguém será notificado.\n\n"
        f"— {app_name}"
    )
    html = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        f'color:#212121;line-height:22px;max-width:480px;">'
        f"<p>Foi convidado para usar o <strong>{app_name}</strong>.</p>"
        f'<p><a href="{invite_url}" style="color:#0056b3;">Aceitar convite</a></p>'
        f'<p style="color:#555;font-size:13px;">Ou copie o endereço:<br>'
        f'<span style="word-break:break-all;">{invite_url}</span></p>'
        f'<p style="color:#555;font-size:13px;">O convite é válido durante '
        f"{hours} horas.</p>"
        f'<p style="color:#555;font-size:13px;">Se não esperava este email, '
        f"ignore-o.</p></div>"
    )
    return subject, text, html


def _build_message(
    settings: Settings,
    to_address: str,
    subject: str,
    text: str,
    html: str | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.mail_from_name, settings.mail_from))
    message["To"] = to_address
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    # SPF/DKIM são configurados no domínio do servidor SMTP. Declarar
    # Authentication-Results aqui seria mentir sobre o que aconteceu.
    return message


def _send_smtp(settings: Settings, message: EmailMessage) -> None:
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        # O tipo de excepção, não a mensagem: as do SMTP podem incluir
        # credenciais na string.
        raise MailError(
            f"Falha ao enviar por SMTP ({type(exc).__name__}). "
            "Verifique SMTP_HOST, SMTP_PORT e as credenciais."
        ) from None


def send(
    settings: Settings,
    to_address: str,
    subject: str,
    text: str,
    html: str | None = None,
) -> None:
    """Envia uma mensagem. Levanta `MailError` em falha — nunca engole.

    Engole seria pior: o utilizador ficaria à espera de um código que não
    existe, sem nenhuma indicação do porque.
    """
    if not to_address:
        raise MailError("Não há destinatário.")
    if settings.mail_backend == "null":
        return
    if settings.mail_backend == "console":
        # stdout, nunca logging. É o único sítio onde o código aparece em
        # desenvolvimento, e é efémero.
        sys.stdout.write(f"\n[email:console] para={to_address} assunto={subject}\n{text}\n---\n")
        sys.stdout.flush()
        return
    _send_smtp(settings, _build_message(settings, to_address, subject, text, html))


def send_otp(settings: Settings, to_address: str, code: str) -> None:
    subject, text, html = _render_otp_email(code, settings.mail_from_name, settings.otp_ttl_minutes)
    send(settings, to_address, subject, text, html)


def send_invite(settings: Settings, to_address: str, invite_url: str) -> None:
    subject, text, html = _render_invite_email(
        invite_url, settings.mail_from_name, settings.invite_ttl_hours
    )
    send(settings, to_address, subject, text, html)


__all__ = ["MailError", "send", "send_invite", "send_otp"]
