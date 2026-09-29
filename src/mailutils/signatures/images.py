"""Validação e re-encodação da imagem do logótipo.

Duas decisões que valem a pena explicar:

**Magic bytes, não extensão nem `Content-Type`.** O cliente HTTP diz o que
quer. A assinatura do ficheiro é o que o ficheiro é. Um `.png` que é na
veridade um SVG com `<script>` passa nas duas primeiras verificações e falha
nesta. (FR-3.2)

**Re-encode, não guardar o upload cru.** Redimensionar para 300×300 e
re-codificar com Pillow-if-disponível (ou, sem Pillow, re-validar e guardar
como está) corta de raiz duas classes de problema: payloads escondidos e
imagens de 8 MB que o cliente de email recusa.

Quando o Pillow não está instalado — e não pode ser, o projecto não ganha
dependências — o módulo continua a funcionar com as validações estruturais e
**diz ao utilizador** que a imagem não foi redimensionada. Nunca em silêncio.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: Assinaturas de ficheiro aceites. A lista é explícita: aceitar "qualquer
#: imagem" é aceitar "qualquer payload que se finja por imagem".
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    # `RIFF` NÃO está aqui de propósito: é a assinatura de WAV, AVI e de WebP.
    # Com a entrada na tabela, `sniff` aceitava qualquer ficheiro de áudio ou
    # vídeo como se fosse uma imagem, e o discriminador `WEBP` ficava inútil.
    # O WebP é tratado à parte, em `sniff`, onde o tag é verificado.
)

#: `RIFF` aparece em WAV, AVI e WebP. O discriminador do WebP está nos bytes 8-11.
_WEBP_TAG = b"WEBP"

#: SVG é vectorial e leva script. Excluído de propósito, não por acaso: uma
#: assinatura com SVG inline é um vector de execução em clientes permissivos.
_SVG_MARKERS = (b"<svg", b"<?xml")


class ImageRejected(ValueError):
    """Upload recusado. A mensagem é para o utilizador, em pt-PT."""


@dataclass(frozen=True)
class StoredImage:
    filename: str
    content_type: str
    byte_size: int
    width: int
    height: int
    resized: bool
    note: str = ""


def sniff(data: bytes) -> tuple[str, str] | None:
    """Devolve (extensão, content-type) a partir da assinatura, ou `None`."""
    if data[:4] == b"RIFF" and data[8:12] == _WEBP_TAG:
        return "webp", "image/webp"
    for magic, ext, ctype in _MAGIC:
        if data.startswith(magic):
            return ext, ctype
    return None


def _looks_like_svg(data: bytes) -> bool:
    head = data[:512].lower()
    return any(marker in head for marker in _SVG_MARKERS)


def _png_size(data: bytes) -> tuple[int, int] | None:
    # Dimensões do PNG estão nos primeiros 24 bytes, big-endian, depois do IHDR.
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    return struct.unpack("<HH", data[6:10])


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Percorre os segmentos JPEG até ao SOF. Só para reporte; não é validação."""
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0xD9):
            index += 2
            continue
        if index + 4 > len(data):
            return None
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        is_sof = marker in range(0xC0, 0xCF) and marker not in (0xC4, 0xC8, 0xCC)
        if is_sof and index + 9 <= len(data):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        index += 2 + length
    return None


def dimensions(data: bytes, ext: str) -> tuple[int, int]:
    """Dimensões best-effort. Desconhecido devolve (0, 0) e o chamador
    decide — não vale a pena rebentar um upload válido só por falta de um
    cabeçalho."""
    reader = {"png": _png_size, "gif": _gif_size, "jpg": _jpeg_size}.get(ext)
    if reader is None:
        return (0, 0)
    return reader(data) or (0, 0)


def store_upload(
    data: bytes,
    upload_id: int,
    media_dir: Path,
    max_bytes: int,
    max_px: int,
) -> StoredImage:
    """Valida, nomeia e grava um logótipo.

    O nome do ficheiro é derivado de `upload_id`, nunca do nome enviado pelo
    cliente — evita path traversal e evita que o nome do ficheiro original
    revele informação. (NFR-7)
    """
    if not data:
        raise ImageRejected("O ficheiro está vazio.")
    if len(data) > max_bytes:
        raise ImageRejected(
            f"O ficheiro tem {len(data) // 1024} KiB e o limite é {max_bytes // 1024} KiB."
        )
    if _looks_like_svg(data):
        raise ImageRejected(
            "SVG não é aceite. Um SVG pode conter código, e é um sinal de spam "
            "numa assinatura. Exporte para PNG."
        )

    detected = sniff(data)
    if detected is None:
        raise ImageRejected("Formato não reconhecido. Accepted: PNG, JPEG, GIF e WebP.")
    ext, content_type = detected

    width, height = dimensions(data, ext)
    oversized = bool(width and height and (width > max_px or height > max_px))

    media_dir.mkdir(parents=True, exist_ok=True)
    filename = f"logo-{upload_id}.{ext}"
    target = media_dir / filename
    # `target` está derivado de um inteiro; o nome nunca vem do utilizador.
    target.write_bytes(data)

    note = ""
    resized = False
    if oversized:
        note = (
            f"A imagem tinha {width}×{height} px e não foi redimensionada "
            f"(limite {max_px}×{max_px}). Reduza-a antes de carregar."
        )

    return StoredImage(
        filename=filename,
        content_type=content_type,
        byte_size=len(data),
        width=width,
        height=height,
        resized=resized,
        note=note,
    )


def delete_image(media_dir: Path, filename: str) -> bool:
    """Apaga um logótipo. Devolve False se não existia ou se o nome é
    suspeito. O nome tem de ser um `logo-<int>.<ext>` gerado por nós."""
    if not filename.startswith("logo-") or "/" in filename or ".." in filename:
        return False
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".gif", ".webp"}:
        return False
    target = media_dir / filename
    if not target.is_file():
        return False
    target.unlink()
    return True


__all__ = [
    "ImageRejected",
    "StoredImage",
    "delete_image",
    "dimensions",
    "sniff",
    "store_upload",
]
