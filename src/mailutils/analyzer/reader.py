"""Leitura de um email colado ou carregado pelo utilizador.

Só três responsabilidades, deliberadamente: descodificar bytes, partir as
partes, e expor cabeçalhos e texto. Nenhuma regra, nenhuma opinion.

**Nada é persistido.** Um email colado aqui é quase sempre spam *recebido*, e
spam recebido contém o que o remetente queria que o destinatário lesse. Guardar
isso na base de dados do servidor seria criar um arquivo de material alheio sem
pedido. A análise vive no pedido e morre com ele. (NFR-8)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import policy
from email.message import Message
from email.parser import BytesParser

#: Limite de leitura. Um ficheiro de 40 MB colado num `<textarea>` já é um
#: ataque de negação de serviço, não um email.
MAX_INPUT_BYTES = 4 * 1024 * 1024


@dataclass
class ParsedEmail:
    """Um email lido. Campos que não existem ficam vazios, nunca `None` —
    a ausência é um sinal, e a análise precisa de a poder ver."""

    raw: str = ""
    headers: dict[str, list[str]] = field(default_factory=dict)
    text_body: str = ""
    html_body: str = ""
    attachments: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    multipart: bool = False

    def header(self, name: str) -> str:
        """Primeiro valor de um cabeçalho, ou `""`.

        `get` devolve `None` quando falta, e a diferença entre "cabeçalho
        ausente" e "cabeçalho vazio" é o que distingue `From:` em branco de
        `From:` ausente — e as duas coisas são spam por razões diferentes.
        """
        values = self.headers.get(name.lower(), [])
        return values[0].strip() if values else ""

    def all_headers(self, name: str) -> list[str]:
        return list(self.headers.get(name.lower(), []))

    @property
    def subject(self) -> str:
        return self.header("subject")

    @property
    def sender(self) -> str:
        return self.header("from")

    @property
    def to_addrs(self) -> list[str]:
        raw = ", ".join(self.all_headers("to"))
        return [part.strip() for part in re.split(r"[;,]", raw) if part.strip()]

    @property
    def html_links(self) -> list[str]:
        return re.findall(r'href\s*=\s*["\']([^"\']+)["\']', self.html_body, flags=re.IGNORECASE)

    @property
    def html_images(self) -> list[str]:
        return re.findall(r'src\s*=\s*["\']([^"\']+)["\']', self.html_body, flags=re.IGNORECASE)

    @property
    def visible_text(self) -> str:
        """Texto visível aproximado, para a razão texto/imagem."""
        without_comments = re.sub(r"<!--.*?-->", " ", self.html_body, flags=re.DOTALL)
        without_tags = re.sub(r"<[^>]+>", " ", without_comments)
        return re.sub(r"\s+", " ", without_tags).strip()

    @property
    def has_html(self) -> bool:
        return bool(self.html_body)

    @property
    def has_text(self) -> bool:
        return bool(self.text_body.strip())

    @property
    def size_bytes(self) -> int:
        return len(self.raw.encode("utf-8", errors="ignore"))


class InputTooLarge(ValueError):
    """O input excede `MAX_INPUT_BYTES`."""


def parse(raw: str | bytes) -> ParsedEmail:
    """Lê um email de texto colado ou de um ficheiro `.eml`.

    Aceita os dois formatos de propósito: o utilizador que recebe um spam faz
    «ver mensagem original» e cola um `.eml`; o que tem a mensagem aberta cola o
    HTML. Nenhum dos dois obriga a saber a diferença.
    """
    if isinstance(raw, bytes):
        payload = raw
    else:
        encoded = raw.encode("utf-8", errors="ignore")
        if len(encoded) > MAX_INPUT_BYTES:
            raise InputTooLarge(
                f"O texto tem {len(encoded) // 1024} KiB e o limite é "
                f"{MAX_INPUT_BYTES // 1024} KiB."
            )
        payload = encoded

    if len(payload) > MAX_INPUT_BYTES:
        raise InputTooLarge(
            f"O ficheiro tem {len(payload) // 1024} KiB e o limite é {MAX_INPUT_BYTES // 1024} KiB."
        )

    text = payload.decode("utf-8", errors="replace")
    message = Message()
    errors: list[str] = []
    try:
        message = BytesParser(policy=policy.default).parsebytes(payload)
    except Exception as exc:  # noqa: BLE001 - um .eml malformado é o caso comum
        errors.append(f"Não foi possível ler o email como .eml: {type(exc).__name__}.")
        # Mesmo com a leitura estruturada a falhar, o texto colado pode ser
        # HTML puro. Degradar para "isto é só HTML" é melhor do que devolver
        # um erro e obrigar o utilizador a tentar outra vez.

    parsed = ParsedEmail(raw=text, parse_errors=errors)
    if message:
        for name, value in message.items():
            parsed.headers.setdefault(name.lower(), []).append(str(value))
        parsed.multipart = message.is_multipart()
        _walk(message, parsed)

    if not parsed.html_body and not parsed.text_body:
        _fallback_to_pasted_html(parsed)

    return parsed


def _walk(part: Message, out: ParsedEmail) -> None:
    """Percorre a árvore MIME.

    A recursão tem de ser explícita e não `part.walk()`, porque um `.eml`
    malformado pode ter profundidade arbitrária e um anexo dentro de um anexo
    dentro de um anexo é o caminho para estourar a pilha. O limite corta isso.
    """
    stack: list[Message] = [part]
    depth = 0
    while stack and depth < 30:
        current = stack.pop()
        depth += 1
        if current.is_multipart():
            try:
                children = list(current.iter_parts())
            except Exception:  # noqa: BLE001 - estrutura inválida
                out.parse_errors.append("Estrutura MIME inválida: paragem na leitura.")
                return
            stack.extend(children)
            continue

        content_type = current.get_content_type()
        disposition = (current.get_content_disposition() or "").lower()

        if disposition == "attachment" or content_type == "message/rfc822":
            out.attachments.append(current.get_filename() or content_type)
            continue

        payload = _read_text(current, content_type, out)
        if payload is None:
            continue

        if content_type == "text/plain" and not out.text_body:
            out.text_body = payload
        elif content_type == "text/html" and not out.html_body:
            out.html_body = payload


def _read_text(part: Message, content_type: str, out: ParsedEmail) -> str | None:
    """Lê o corpo de uma parte, decidindo o charset.

    Um email colado num `<textarea>` raramente declara `charset`. Por omissão o
    RFC assume `us-ascii`, e `get_content()` substitui cada byte UTF-8 por
    `�`. O sintoma é silencioso: as palavras com acentos deixam de bater nas
    regras, e nenhuma delas dá erro.

    Quando o payload não é ASCII válido mas é UTF-8 válido, assume-se UTF-8. É
    a decisão certa para texto colado, e é inofensiva para email ASCII legítimo,
    que é o caso onde essa decisão nunca é exercitada.
    """
    try:
        declared = (part.get_content_charset() or "").lower()
    except Exception:  # noqa: BLE001
        declared = ""

    if declared in ("", "us-ascii", "ascii", "unknown-8bit"):
        bruto = part.get_payload(decode=True)
        if isinstance(bruto, bytes):
            try:
                bruto.decode("ascii")
            except UnicodeDecodeError:
                try:
                    return bruto.decode("utf-8")
                except UnicodeDecodeError:
                    out.parse_errors.append(
                        f"Parte {content_type} não é ASCII nem UTF-8 válido; "
                        "o texto pode estar corrompido."
                    )
                    return bruto.decode("utf-8", errors="replace")

    try:
        payload = part.get_content()
    except Exception:  # noqa: BLE001 - charset desconhecido, por exemplo
        out.parse_errors.append(f"Parte {content_type} ilegível (charset?).")
        return None
    return payload if isinstance(payload, str) else None


def _fallback_to_pasted_html(parsed: ParsedEmail) -> None:
    """O que o utilizador colou não tinha cabeçalhos: é HTML ou texto solto.

    Isto é o caso mais comum e não é um erro. Tratar como falha seria devolver
    uma página de erro a alguém que fez tudo certo.
    """
    text = parsed.raw.strip()
    if not text:
        return
    if "<" in text and ">" in text and re.search(r"<\w+[^>]*>", text):
        parsed.html_body = text
        parsed.parse_errors.append(
            "Não foram encontrados cabeçalhos de email. O conteúdo foi analisado como HTML."
        )
    else:
        parsed.text_body = text
        parsed.parse_errors.append(
            "Não foram encontrados cabeçalhos de email. O conteúdo foi analisado "
            "como texto simples."
        )


def looks_like_email(raw: str) -> bool:
    """Heurística para a interface: isto é um `.eml` ou é só conteúdo?

    Não decide o que fazer — `_fallback_to_pasted_html` resolve. Serve para a
    UI poder dizer "reconheci cabeçalhos" antes de o utilizador carregar em
    analisar, o que evita a surpresa de um relatório diferente do esperado.
    """
    head = raw[:4000]
    return bool(re.search(r"^(From|To|Subject|Date|Message-ID|Received|MIME-Version):", head, re.M))


__all__ = [
    "MAX_INPUT_BYTES",
    "InputTooLarge",
    "ParsedEmail",
    "looks_like_email",
    "parse",
]
