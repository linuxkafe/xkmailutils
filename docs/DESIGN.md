---
schema: aes/design-v1
design_system: "mailutils-native"
design_system_base: "linuxkafe-native"
version: "1.0.0"
tokens:
  colors:
    primary: "#F8B400"
    primary-dark: "#E6A500"
    primary-light: "#FFC433"
    background: "#FFFFFF"
    background-alt: "#F5F5F5"
    surface: "#FFFFFF"
    surface-elevated: "#FFFFFF"
    chrome: "#2d2d2d"
    chrome-sticky: "#1a1a1a"
    text: "#212121"
    text-light: "#555555"
    text-on-dark: "#E0E0E0"
    text-on-primary: "#212121"
    border: "#E0E0E0"
    border-dark: "#444444"
    link: "#0056b3"
    accent-success: "#22c55e"
    accent-error: "#ef4444"
    accent-info: "#3b82f6"
    accent-warning: "#F8B400"
    spam-safe: "#22c55e"
    spam-warn: "#F8B400"
    spam-high: "#f97316"
    spam-critical: "#ef4444"
  typography:
    font-family-base: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    font-family-heading: "Inter, system-ui, -apple-system, sans-serif"
    font-family-mono: "'Fira Code', 'JetBrains Mono', ui-monospace, monospace"
    scale:
      display: "2rem"
      h1: "1.5rem"
      h2: "1.25rem"
      h3: "1.125rem"
      body: "1rem"
      small: "0.875rem"
      micro: "0.75rem"
    weights:
      regular: 400
      medium: 500
      semibold: 600
      bold: 700
  spacing:
    base: "4px"
    scale: ["4px", "8px", "16px", "24px", "32px", "48px", "64px"]
  border-radius:
    sm: "4px"
    md: "8px"
    lg: "12px"
    full: "9999px"
  transitions:
    fast: "150ms ease-out"
    normal: "250ms ease-out"
  z-index:
    sticky-header: 1000
    dropdown: 100
    modal: 2000
    toast: 4000
  breakpoints:
    mobile: "480px"
    tablet: "768px"
    desktop: "1024px"
    wide: "1440px"
---

# DESIGN — mailutils

## Referência e divergência declarada

- **Base de cor:** `LINUXKAFE-wp-theme` (`docs/DESIGN.md` v2.1.0). Tokens copiados
  de `style.css` e do plugin `xkaichat`.
- **Base de layout:** `rodolfomatos/pdftools` — **INACESSÍVEL (HTTP 404, repositório
  privado).** Não foi possível auditar. A estrutura adoptada é a convenção AES
  (header + conteúdo em cartões + footer), não uma reprodução verificada.
  **Se o visual não corresponder ao esperado, corrige-se aqui e em
  `src/mailutils/static/app.css` — não noutro sítio.**
- **Divergência do pdftools (explícita):** em darkmode o fundo do **cabeçalho é
  idêntico ao do rodapé** (`--surface-chrome`). Sem gradiente, sem tom distinto.
- **Divergência do pdftools (explícita):** **zero menções à Universidade do Porto**
  em qualquer ficheiro, template ou texto de interface.

## Tema

Cybercafé Linux, sóbrio. Fundo escuro neutro, um único acento amarelo usado com
poupança (uma acção primária por ecrã), tipografia Inter, cantos de 8 px.
O amarelo é a identidade; o resto é cinzento para não competir com o logótipo
que o utilizador carrega.

## Regras de superfície

| Superfície | Light | Dark | Token |
|---|---|---|---|
| Fundo da página | `#FFFFFF` | `#121212` | `--bg` |
| Cartão | `#F5F5F5` | `#1e1e1e` | `--surface` |
| **Cabeçalho** | `#2d2d2d` | `#1a1a1a` | `--chrome` |
| **Rodapé** | `#2d2d2d` | `#1a1a1a` | `--chrome` |
| Borda | `#E0E0E0` | `#333333` | `--border` |
| Texto | `#212121` | `#E0E0E0` | `--text` |
| Texto suave | `#555555` | `#9a9a9a` | `--text-soft` |

> **Invariante `FR-5.1`:** `header` e `footer` partilham o token `--chrome`. Não
> divergem — se divergirem, o teste
> `tests/test_ui_theme.py::test_header_footer_same_chrome` falha.

## Escala de risco de spam (cores funcionais, não décor)

| Nível | Score | Cor | Significado |
|---|---|---|---|
| `SEGURO` | 0–19 | `#22c55e` | Nada de encontrável. |
| `ATENÇÃO` | 20–44 | `#F8B400` | Sinal fraco; alguns filtros penalizam. |
| `ELEVADO` | 45–69 | `#f97316` | Padrão conhecido de spam. |
| `CRÍTICO` | ≥70 | `#ef4444` | Exportação **bloqueada**. |

A cor **nunca** é a única pista: cada nível tem também um rótulo textual e um
ícone em texto (`[OK]`, `[!]`, `[!!]`, `[X]`), para não depender de perceção de cor.

## Temas e estruturas da assinatura

Ao contrário dos tokens da aplicação, os temas da assinatura **não** vivem em
`app.css`: o HTML que sai vai para um cliente de email, e o CSS da aplicação não
chega lá. Vivem em `signatures/renderer.py`, e este quadro é a cópia de
referência — `tests/test_editor_flows.py` e `tests/test_renderer.py` comparam os
dois lados.

### Temas (paleta)

Sete paletas. As três primeiras já existiam antes do T013; as outras foram
escolhidas a calcular contraste, não a olho — ver a nota abaixo do quadro.

| Chave | Nome | Fundo | Texto | Suave | Acento | Borda |
|---|---|---|---|---|---|---|
| `dark` | Escuro | `#1a1a1a` | `#f0f0f0` | `#b0b0b0` | `#F8B400` | `#3a3a3a` |
| `light` | Claro | `#ffffff` | `#212121` | `#555555` | `#0056b3` | `#e0e0e0` |
| `graphite` | Grafite | `#101317` | `#f2f4f7` | `#b3bcc9` | `#7cb8ff` | `#2a3038` |
| `navy` | Azul escuro | `#0d1b2a` | `#eef3f8` | `#aec4d9` | `#ffd166` | `#1b3348` |
| `forest` | Verde escuro | `#0e1f17` | `#eaf4ee` | `#a9c6b6` | `#8fd694` | `#1d3a2b` |
| `paper` | Papel | `#ffffff` | `#2b2620` | `#5f574c` | `#9a3412` | `#ddd6c9` |
| `slate` | Ardósia | `#ffffff` | `#1f2933` | `#52606d` | `#0b5c8a` | `#d5dde3` |

**Toda a tabela passa 4.5:1 para `text` e `muted`, no fundo que sai no HTML.**
Um tema escuro tem de levar o seu fundo (`_precisa_de_fundo`, por luminância e
não por nome); um tema claro fica transparente de propósito, e por isso o fundo
contra o qual o contraste é medido é o branco do cliente, não o `#ffffff`
declarado. `tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail` está
parametrizado sobre `sorted(THEMES)`, portanto **um tema novo entra no gate sem
que ninguém escreva um teste novo** — e é por isso que as cores se escolheram por
cálculo e não a olho.

### Estruturas (forma)

| Chave | Nome | O que muda |
|---|---|---|
| `stack` | Vertical | O original. Identidade e contactos empilhados ao lado do logótipo. |
| `compact` | Compacto | Duas linhas. Logótipo de 40 px alinhado ao meio. |
| `columns` | Duas colunas | Identidade em cima, contactos repartidos. A segunda coluna só existe se houver conteúdo para ela. |
| `boxed` | Com moldura | Vertical dentro de `border:1px solid` + `border-radius:8px`, **visível nos temas claros**. |

Tema e estrutura são ortogonais: 7 × 4 = 28 combinações. `tests/test_renderer.py`
parametriza as invariantes de estrutura (`table-based`, tabelas equilibradas,
marcador no fim, escaping) sobre o produto cartesiano, porque são 28 HTMLs
diferentes a entrar em emails de clientes reais.

`border-radius` no email não é fiável — o Word engine do Outlook não faz
cantos arredondados. É degradação progressiva: onde não há cantos arredondados
há uma moldura recta, que é o que se queria.

## Componentes

| Componente | Estados | Tokens |
|---|---|---|
| Card de formulário | default, invalid | `surface`, `border`, `radius.md` |
| Barra de score | 4 níveis | `spam-*`, `radius.full` |
| Botão primário | default, hover, focus, disabled | `primary`, `text-on-primary` |
| Botão secundário | default, hover, focus | `border`, `text` |
| Tabela de ligações | 0 linhas, 1 linha, 6 linhas, truncada | `border`, `spacing.sm` |
| Cabeçalho | light, dark | `chrome` |
| Rodapé | light, dark | `chrome` |

### Utilitários de superfície

Estão em `app.css` e substituem os atributos `style=""` que os templates
usavam. Não são uma escolha estética: a `Content-Security-Policy` tem
`style-src 'self'` e descarta estilos inline, pelo que tudo o que vivia num
atributo era deitado fora pelo browser sem nenhum erro visível. Um
`style="width: 0%"` descartado faz um `div` encher a barra — foi assim que a
barra de score passou a mostrar 100% com um score de 0.

| Classe | O quê |
|---|---|
| `.m-0` `.mb-0` `.mb-2` | margens a zero / ao fim |
| `.mt-xs` `.mt-1` `.mt-2` `.mt-3` `.mt-4` `.mt-5` | margem ao topo, por degrau da escala de 4 px |
| `.ma-2` `.ma-3` `.ma-3-b` | margens combinadas |
| `.ta-left` `.ta-right` `.ta-center` | alinhamento |
| `.w-full` `.hidden` | largura e visibilidade |
| `.score__denom` | o " / 100" menor e mais leve que o número |
| `.preview__frame` `.logo__thumb` | molduras de imagem com fundo branco |
| `.otp-code` | o campo do segundo factor, monoespaçado e com tracking |

**A moldura do preview é branca de propósito.** O preview mostra a assinatura
como ela aparece no cliente de email, e o cliente de email é claro. Um painel
escuro seria mais bonito e mentiroso. O que torna a decisão legível é a
moldura ter bordo nos dois temas, para se perceber que é uma superfície e não
um bug. O documento do preview é servido por
`/assinatura/preview-documento` com uma CSP própria, e não por um `blob:`: um
documento `blob:` herda a CSP de quem o cria, e a assinatura aparecia sem uma
cor sequer. (F-04)

A largura da barra de score é a única coisa que o CSS não consegue expressar a
partir de um valor do servidor: `width: attr(data-score number)%` foi medido
em Chromium e dá sempre a largura do contentor. Como o score é um inteiro de
0 a 100 (`spam.py:43`, `spam.py:280`), `app.css` tem uma regra por valor,
indexada por `data-score`, e `tests/test_browser_regressions.py` garante que
as 101 existem. Se o score passar a fraccionário, esse teste falha em vez de
a barra passar a mentir.

## Do's and Don'ts

**Fazer**
- Um único botão primário por ecrã.
- Rótulo visível por baixo de 16 px de largura; nunca só `placeholder`.
- Mensagem de erro ao lado do campo, com `role="alert"`.
- Toast para confirmação, com fade de 4 s.

**Não fazer**
- Emoji em código (`.py`, `.html`, `.css`). Ícones em SVG inline ou texto.
- Animação que bloqueie a leitura.
- Verde `#22c55e` como fundo de texto em light mode (contraste < 4.5:1).
- Esconder o score por "ser só heurístico" — mostrar com o aviso, sempre.
