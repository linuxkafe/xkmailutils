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
    # Não é o mesmo que `codigo-enviado`, e é por isso que existe: aquele
    # responde por um código que saiu, este por um pedido que chegou cedo. (F-16)
    "codigo-recentemente-enviado": (
        "Já enviámos um código há pouco. Espera um pouco e tenta outra vez."
    ),
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

    # Sem sessão, o `csrf` do contexto é o token de pré-sessão do botão de
    # tema. Tem de estar em `base` **antes** de construir a resposta:
    # `TemplateResponse` renderiza no construtor, portanto pô-lo depois é
    # tarde e o campo `hidden` sai vazio. E o cookie tem de sair na mesma
    # resposta, pelo mesmo motivo — o formulário sem token não valida nada, e
    # era isso que o F-01 encontrou: um botão de tema que não fazia nada.
    sem_sessao = base.get("sessao") is None
    if sem_sessao and not base.get("csrf"):
        # Só quando a página não trouxe o seu. A página de login e a de
        # convite já põem o `csrf` delas no contexto, e sobrepor aqui punha o
        # token do tema no formulário de login — que depois era rejeitado como
        # CSRF inválido e o utilizador nunca entrava. (Erro meu, apanhado pela
        # suite: 20 testes vermelhos.) O token do tema é o mesmo valor, e é
        # por isso que o cookie de baixo pode ser posto com o que já está.
        base["csrf"] = _token_de_tema(request)

    response = templates.TemplateResponse(
        request=request, name=template, context=base, status_code=status_code
    )
    if sem_sessao:
        _com_token_de_tema(request, response, base["csrf"])
    return response


def _token_de_tema(request: Request) -> str:
    """Token de CSRF para o `POST` do botão de tema, sem sessão.

    Reutiliza o do cookie quando já existe um válido, e gera um novo quando não
    existe. Sem a reutilização, abrir a página de login e o editor em dois
    separadores daria ao segundo um token novo, e o botão de tema do primeiro
    deixaria de funcionar — que é a mesma razão de `ensure_pre_session_csrf`.
    """
    from . import security
    from .web import PRE_SESSION_COOKIES, _is_opaque_token

    existente = request.cookies.get(PRE_SESSION_COOKIES["tema"], "")
    return existente if _is_opaque_token(existente) else security.new_token(24)


def _com_token_de_tema(request: Request, response: Response, token: str) -> Response:
    """Põe o cookie de CSRF do botão de tema na resposta."""
    from .web import PRE_SESSION_COOKIES, get_settings

    settings = get_settings(request)
    response.set_cookie(
        PRE_SESSION_COOKIES["tema"],
        token,
        max_age=3600,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path=settings.url("/"),
    )
    return response
