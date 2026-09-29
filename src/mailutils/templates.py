"""Motor de templates Jinja2 e o contexto comum a todas as páginas.

Um único `Templates` para a aplicação inteira. O contexto base — tema,
utilizador, token CSRF, rota activa — é montado aqui e não em cada handler,
para que nenhuma página fique sem cabeçalho, sem rodapé ou sem token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.responses import Response

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

#: Rotas sem sessão onde o cabeçalho de navegação não faz sentido.
_PUBLIC_PAGES = frozenset({"/entrar", "/verificar", "/convite"})

#: Mensagens de interface indexadas por chave estável.
#:
#: A chave viaja no URL (`?erro=bloqueado`); a mensagem fica aqui. Duas
#: razões: o texto nunca vem do cliente, portanto nunca é reflectido sem
#: escape; e a tradução para outro idioma passa a ser uma tabela, não uma
#: caça a strings em templates.
MESSAGENS: dict[str, str] = {
    # sessão e_csrf
    "csrf": "A sessão expirou. Recarregue a página e tente novamente.",
    "acesso": "Não tem permissão para ver essa página.",
    # login
    "invalidos": "Email ou palavra-passe inválidos.",
    "inativa": "Esta conta está desactivada. Fale com o administrador.",
    "bloqueado": "Demasiadas tentativas. Espere 15 minutos e tente de novo.",
    "excedido": "Excedeu as tentativas deste código. Peça um novo.",
    "codigo": "Código inválido ou expirado. Peça um novo.",
    "codigo-enviado": "Enviámos um código de 6 dígitos para o seu email.",
    "curta": "A palavra-passe precisa de pelo menos 12 caracteres.",
    # convites
    "convite": "Este convite já foi usado, foi revogado ou expirou.",
    "envio": "Não foi possível enviar o email. Verifique a configuração do SMTP.",
    "auto": "Não pode desactivar a sua própria conta.",
    "inexistente": "Utilizador inexistente.",
    # editor
    "vazio": "Guarde a assinatura antes de exportar.",
    # analisador
    "grande": "O conteúdo excede o limite de tamanho.",
    "logotipo": "Escolha um ficheiro de imagem.",
    # convites / convites
    "nao-coincidem": "As palavras-passe novas não coincidem.",
    "atual-incorrecta": "A palavra-passe actual está incorrecta.",
    "ja-existe": "Já existe um utilizador com esse email.",
    "email-invalido": "Email inválido.",
    # sucessos
    "sessao-terminada": "Sessão terminada.",
    "guardada": "Assinatura guardada.",
    "logotipo-guardado": "Logótipo carregado.",
    "logotipo-guardado-pequeno": "Logótipo carregado, mas maior do que o limite.",
    "logotipo-removido": "Logótipo removido.",
    "convite-enviado": "Convite enviado.",
    "revogado": "Dispositivo revogado.",
    "alterada": "Palavra-passe alterada.",
    "bem-vindo": "Sessão iniciada.",
    "estado-alterado": "Estado da conta alterado.",
}


def resolve_message(value: str) -> str:
    """Traduz uma chave de query string numa mensagem em pt-PT.

    Uma chave desconhecida passa em claro. Prefere-se uma mensagem estranha a
    uma página em branco — e o valor só pode vir de um redirect do próprio
    código, nunca do utilizador.
    """
    return MESSAGENS.get(value, value)


def setup_templates(app: FastAPI) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["app_name"] = "mailutils"
    templates.env.globals["csrf_field"] = "csrf_token"
    templates.env.globals["mensagens"] = MESSAGENS
    app.state.templates = templates
    return templates


def page(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    """Renderiza uma página com o contexto base já preenchido.

    Nunca levanta quando falta uma chave — `Jinja2Templates` com
    `undefined=ChainableUndefined` faria com que uma referência a `sessao` numa
    página pública não rebentasse o pedido 500.
    """
    templates: Jinja2Templates = request.app.state.templates
    # A chave e o texto andam juntos. Os templates precisam da chave para
    # decidir *qual* aviso mostrar (`{% if aviso_chave == 'logotipo-removido' %}`)
    # e do texto para o escrever. Só com um dos dois, ou o template compara
    # texto traduzido — e traduzir a interface passa a significar editar cada
    # comparação — ou o utilizador lê um slug.
    erro_chave = request.query_params.get("erro", "")
    aviso_chave = request.query_params.get("aviso", "")

    settings = request.app.state.settings
    base: dict[str, Any] = {
        "request": request,
        "settings": settings,
        # Prefixo de path, para os templates escreverem `{{ raiz }}/entrar` em
        # vez de `/entrar`. Com a app em `/xkmailutils`, um `href` sem prefixo
        # é um 404 — e num `action` de formulário significa um `POST` que não
        # chega a lado nenhum, ou seja, um formulário que parece funcionar e
        # não faz nada.
        "raiz": settings.path_prefix,
        "sessao": getattr(request.state, "session", None),
        "tema": getattr(request.state, "theme", "dark"),
        "rota": request.url.path,
        "publica": request.url.path in _PUBLIC_PAGES,
        "erro": resolve_message(erro_chave),
        "aviso": resolve_message(aviso_chave),
        "erro_chave": erro_chave,
        "aviso_chave": aviso_chave,
    }
    if context:
        base.update(context)
    return templates.TemplateResponse(
        request=request, name=template, context=base, status_code=status_code
    )
