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
from email.utils import formataddr, formatdate, make_msgid
from html import escape as _escape
from socket import getfqdn

from .config import Settings


def _t(value: str) -> str:
    """Escapa o que o utilizador escreveu e vai para um email.

    O nome da lista aparece no assunto e no corpo do email de confirmação.
    Um nome com `<` nao escapa num cliente HTML, e quem o escreveu ve HTML
    onde esperava texto — num cliente que o dono da lista nem vai ler. (NFR-14)
    """
    return _escape(value or "", quote=True)


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

    # `Date` e `Message-ID` são **obrigatórios** (RFC 5322 §3.6) e o
    # `EmailMessage` não os põe. Sem `Date`, o Amavis injecta um `X-Amavis-Alert`
    # e os filtros de Gmail e Microsoft sobem a pontuação logo à entrada. Sem
    # `Message-ID`, o Postfix gera um com o `myhostname` — que num servidor de
    # rede interna é `.lan`, e um `Message-ID` de domínio não roteável é
    # penalizado de imediato, porque parece um script mal configurado.
    #
    # O domínio do `Message-ID` é o do remetente, não o da máquina. É o único
    # que o destinatário pode verificar contra o domínio de quem diz enviar.
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_dominio_de(settings.mail_from))

    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
        # O `add_alternative` do stdlib copia o `MIME-Version` do topo para a
        # parte nova, e dentro dos limites isso é MIME inválido: a
        # `MIME-Version` pertence só à mensagem. Confirmado — o Python põe
        # `MIME-Version: 1.0` na parte `text/html`. Retirar-se aqui.
        for parte in message.get_payload()[1:]:
            del parte["MIME-Version"]

    # SPF/DKIM são configurados no domínio do servidor SMTP. Declarar
    # Authentication-Results aqui seria mentir sobre o que aconteceu.
    return message


def _dominio_de(endereco: str) -> str:
    """O domínio de um endereço, para o `Message-ID`.

    Cai para o `getfqdn()` quando o remetente não tem domínio — que é o caso
    de uma instalação em `console`, onde o endereço pode nem estar definido. O
    `make_msgid` sem domínio usa o `fqdn` da máquina, que é o melhor que há
    nesse caso.
    """
    _, _, dominio = (endereco or "").rpartition("@")
    return dominio.strip() or getfqdn()


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


def _render_confirmation_email(
    code: str,
    app_name: str,
    list_name: str,
    minutes: int,
    confirmar_url: str,
    descadenciar_url: str,
) -> tuple[str, str, str]:
    """O email que pede a presença numa lista.

    A diferença para o email de acesso, que é a que importa: este tem de
    **dizer contra o que a pessoa está a confirmar**. Um código de seis dígitos
    sem contexto é um código que a pessoa não vai confirmar, porque não sabe o
    que está a autorizar. Por isso o nome da lista vai no assunto e na primeira
    frase.
    """
    #
    # O código vai no assunto E no corpo porque o destinatário é a pessoa que
    # pediu — e ver o pedido num assunto já diz o quê. A excepção é o assunto sem
    # contexto, que é o que transforma um código num mistério.
    #
    # O link é o que torna isto usável. Um código que só existe no corpo do
    # email obriga a pessoa a recitá-lo a partir de um ecrã para outro, e um
    # formulário atrás de sessão que a destinatária não tem não é um caminho,
    # é um beco. As duas coisas juntas eram o BLOCKER B-02 da revisão do T014.
    link = (
        f'<p><a href="{_t(confirmar_url)}" '
        f'style="color:#0056b3;font-size:15px;">Confirmar inscrição</a></p>'
    )
    subject = f"{code} confirma a sua inscrição em {list_name}"
    text = (
        f"Pediu para receber mensagens de {list_name} através do {app_name}.\n\n"
        f"Para confirmar, abra este endereço:\n\n"
        f"    {confirmar_url}\n\n"
        f"E escreva este código:\n\n"
        f"    {code}\n\n"
        f"O código é válido durante {minutes} minutos e só pode ser usado uma vez.\n\n"
        f"Não reconhece este pedido? Não faça nada: sem este código o endereço\n"
        f"não entra na lista e nunca recebe nada.\n\n"
        f"Já não quer receber? Pode cancelar aqui, a qualquer momento:\n\n"
        f"    {descadenciar_url}\n\n"
        f"— {app_name}"
    )
    html = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        f'color:#212121;line-height:22px;max-width:480px;">'
        f"<p>Pediu para receber mensagens de <strong>{_t(list_name)}</strong> "
        f"através do <strong>{_t(app_name)}</strong>.</p>"
        f"{link}"
        f'<p style="font-size:28px;font-weight:700;letter-spacing:4px;'
        f'color:#212121;margin:16px 0;">{_t(code)}</p>'
        f'<p style="color:#555;font-size:13px;">É válido durante {minutes} minutos '
        f"e só pode ser usado uma vez.</p>"
        f'<p style="color:#555;font-size:13px;">Não reconhece este pedido? Não faça '
        f"nada: sem este código o endereço não entra na lista e nunca recebe nada.</p>"
        f'<p style="color:#555;font-size:13px;"><a href="{_t(descadenciar_url)}" '
        f'style="color:#0056b3;">Já não quer receber? Cancele aqui.</a></p>'
        f'<p style="color:#555;font-size:13px;">— {_t(app_name)}</p>'
        f"</div>"
    )
    return subject, text, html


def send_confirmation(
    settings: Settings,
    to_address: str,
    code: str,
    list_name: str,
    confirmar_url: str,
    descadenciar_url: str,
) -> None:
    """Envia o código de confirmação de um endereço.

    Sai por `send`, como tudo o resto, e por isso o corpo nunca passa por
    `logging`: um código de confirmação num log é um código que autoriza a
    inscrição de um endereço que ninguém pediu para inscrever.

    Os dois URLs são o que torna o email usável: o `confirmar_url` é o link
    de um clique, e o `descadenciar_url` é o que torna a saída trivial. Sem
    eles, o destinatário tem de recitar um código de um ecrã para outro, e
    um formulário atrás de sessão que a destinatária não tem não é um
    caminho — é um beco. (B-02, revisão T014.)
    """
    subject, text, html = _render_confirmation_email(
        code,
        settings.mail_from_name,
        list_name,
        settings.otp_ttl_minutes,
        confirmar_url,
        descadenciar_url,
    )
    send(settings, to_address, subject, text, html)


def send_otp(settings: Settings, to_address: str, code: str) -> None:
    subject, text, html = _render_otp_email(code, settings.mail_from_name, settings.otp_ttl_minutes)
    send(settings, to_address, subject, text, html)


def send_invite(settings: Settings, to_address: str, invite_url: str) -> None:
    subject, text, html = _render_invite_email(
        invite_url, settings.mail_from_name, settings.invite_ttl_hours
    )
    send(settings, to_address, subject, text, html)


__all__ = ["MailError", "send", "send_confirmation", "send_invite", "send_otp"]
