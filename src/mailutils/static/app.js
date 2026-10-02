/* mailutils — melhoramentos progressivos.
 *
 * Regra estrutural: **a aplicação funciona sem este ficheiro.** O formulário
 * faz `POST` para `/assinatura/guardar`, o preview tem o HTML no servidor e a
 * exportação é um link. O que este ficheiro faz é poupar um clique:
 * recalcular o score enquanto o utilizador escreve e copiar para a
 * área de transferência.
 *
 * Consequência prática: se este ficheiro partir, nada fica inacessível. É
 * deliberado — nenhuma funcionalidade essencial pode depender de JavaScript
 * num produto cujo utilizador pode estar num cliente de email dos anos 2000.
 */

(function () {
  "use strict";

  /* Prefixo de path, lido de um atributo em vez de escrito aqui. A aplicação
   * pode ser servida em `/xkmailutils` ou na raiz; o JavaScript não tem de
   * saber qual dos dois. */
  var RAIZ = document.body.getAttribute("data-raiz") || "";

  /* --- Analisador de email --------------------------------------------
   *
   * Independente do editor: quem chega a `/analisar` não tem
   * `#form-assinatura`, e o script não pode sair com "TypeError" por isso. */
  var botaoExemplo = document.getElementById("usar-exemplo");
  if (botaoExemplo) {
    botaoExemplo.addEventListener("click", function () {
      /* `texto-email`, não `conteudo`: o `<main>` de `base.html` também se
       * chamava `conteudo`, e `getElementById` devolvia o primeiro — o botão
       * focava a página em vez do campo. O `name="conteudo"` continua, porque
       * esse é o contrato com o servidor. */
      var campo = document.getElementById("texto-email");
      if (campo) {
        campo.focus();
        campo.scrollIntoView({ block: "center" });
      }
    });
  }

  var form = document.getElementById("form-assinatura");
  if (!form) return;

  var fieldsInput = document.getElementById("campo-fields");
  var themeInput = document.getElementById("campo-theme");
  var layoutInput = document.getElementById("campo-layout");
  var csrf = (form.querySelector('input[name="csrf_token"]') || {}).value || "";
  var scoreBox = document.getElementById("caixa-score");
  var preview = document.getElementById("preview");
  var outHtml = document.getElementById("saida-html");
  var outTxt = document.getElementById("saida-txt");

  var MARKERS = { SEGURO: "[OK]", ATENCAO: "[!]", ELEVADO: "[!!]", CRITICO: "[X]" };
  /* Um formulário em branco não é uma assinatura segura. Mostrar «0 / 100
   * SEGURO» com um selo verde treina o utilizador a ignorar o selo antes de
   * escrever uma letra. O score continua a ser 0; o que muda é o rótulo. (F-12) */
  var VAZIO = {
    nivel: "VAZIO",
    texto: "\u2014",
    descricao: "Preencha os campos ao lado para ver o score.",
    badge: "POR PREENCHER"
  };

  function collect() {
    var data = {};
    var inputs = form.querySelectorAll("[data-field]");
    for (var i = 0; i < inputs.length; i += 1) {
      data[inputs[i].getAttribute("data-field")] = inputs[i].value;
    }
    return data;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* O preview é um `iframe` que carrega `/assinatura/preview-documento`.
   *
   * Antes era um `Blob` `text/html`, e o browser dava ao documento a CSP de
   * quem o criou: com `style-src 'self'` a assinatura aparecia sem uma cor
   * sequer, em Times New Roman, com os acentos como `TÃ©cnica` porque o Blob
   * não levava charset. Um `<meta http-equiv="Content-Security-Policy">`
   * dentro do blob não resolvia — as políticas juntam-se, e a mais restritiva
   * ganha; foi medido em Chromium antes de se tentar. (F-04)
   *
   * A rota dá ao documento a CSP dele, e isso permite à aplicação ficar sem
   * `blob:` em `frame-src`. O custo é o iframe recarregar a cada alteração,
   * que é o mesmo debounce de 350 ms que já existia. */
  function previewUrl() {
    var body = new URLSearchParams();
    body.set("fields", JSON.stringify(collect()));
    body.set("theme", themeInput ? themeInput.value : "dark");
    body.set("layout", layoutInput ? layoutInput.value : "stack");
    return RAIZ + "/assinatura/preview-documento?" + body.toString();
  }

  function setPreview() {
    if (!preview) return;
    var url = previewUrl();
    // Não recarregar quando o conteúdo não mudou. Sem isto o iframe pisca a
    // cada tecla, mesmo quando o utilizador está a corrigir a mesma palavra.
    if (preview.dataset.rendered === url) return;
    preview.dataset.rendered = url;
    preview.src = url;
  }

  function renderScore(score) {
    if (!scoreBox) return;
    var vazio = score.vazio === true;
    scoreBox.setAttribute("data-nivel", vazio ? VAZIO.nivel : score.categoria);
    var value = scoreBox.querySelector(".score__value");
    if (value) {
      value.innerHTML = vazio
        ? '<span class="score__denom">\u2014</span>'
        : escapeHtml(score.score) + ' <span class="score__denom">/ 100</span>';
    }
    var badge = scoreBox.querySelector(".score__badge");
    if (badge) badge.textContent = vazio ? VAZIO.badge : score.categoria_acentuada;
    var marker = scoreBox.querySelector(".score__marker");
    if (marker && !vazio) marker.textContent = MARKERS[score.categoria] || "[?]";
    var fill = scoreBox.querySelector(".score__fill");
    /* O atributo, e não `style.width`: a CSP bloqueia estilos inline, e
     * `app.css` tem uma regra por valor do score porque o score é um inteiro
     * de 0 a 100. `tests/test_ui_theme.py` garante que a tabela está completa
     * — se o score passar a fraccionário, a barra esvazia em vez de mentir. */
    if (fill) fill.setAttribute("data-score", vazio ? 0 : score.score);

    var desc = scoreBox.querySelector(".score__head + .score__bar ~ p");
    if (desc) desc.textContent = vazio ? VAZIO.descricao : score.descricao;

    var findings = scoreBox.querySelector(".findings");
    var creditos = scoreBox.querySelector(".credit");
    if (creditos) creditos.hidden = vazio;
    if (findings) {
      if (vazio) {
        findings.innerHTML = "";
      } else if (score.regras.length === 0) {
        findings.parentElement.innerHTML =
          '<p class="small m-0">Nenhuma regra disparada.</p>';
      } else {
        findings.innerHTML = score.regras
          .map(function (r) {
            return (
              "<li><span class=\"finding__points\">+" + r.pontos + "</span>" +
              '<span class="finding__rule">' + escapeHtml(r.regra) + "</span>" +
              escapeHtml(r.mensagem) +
              (r.remediacao
                ? '<span class="finding__fix">' + escapeHtml(r.remediacao) + "</span>"
                : "") +
              "</li>"
            );
          })
          .join("");
      }
    }
  }

  var pending = null;

  function refresh() {
    if (pending) pending.abort();
    var controller = new AbortController();
    pending = controller;
    var body = new URLSearchParams();
    body.set("csrf_token", csrf);
    body.set("fields", JSON.stringify(collect()));
    body.set("theme", themeInput ? themeInput.value : "dark");
    body.set("layout", layoutInput ? layoutInput.value : "stack");

    fetch(RAIZ + "/assinatura/preview", {
      method: "POST",
      body: body,
      signal: controller.signal,
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    })
      .then(function (r) {
        if (!r.ok) throw new Error("preview falhou");
        return r.json();
      })
      .then(function (data) {
        pending = null;
        renderScore(data.score);
        if (outHtml) outHtml.value = data.html;
        if (outTxt) outTxt.value = data.plain;
        if (fieldsInput) fieldsInput.value = JSON.stringify(collect());
        setPreview();
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        // Falha silenciosa de propósito: o formulário continua a funcionar
        // sem preview, e a exportação devolve o HTML correcto.
      });
  }

  form.addEventListener("input", function (event) {
    if (event.target && event.target.hasAttribute("data-field")) {
      clearTimeout(refresh.timer);
      refresh.timer = setTimeout(refresh, 350);
    }
  });

  form.addEventListener("submit", function () {
    if (fieldsInput) fieldsInput.value = JSON.stringify(collect());
  });

  Array.prototype.forEach.call(
    form.querySelectorAll(".botao-tema"),
    function (button) {
      button.addEventListener("click", function () {
        Array.prototype.forEach.call(form.querySelectorAll(".botao-tema"), function (b) {
          b.setAttribute("aria-checked", b === button ? "true" : "false");
        });
        if (themeInput) themeInput.value = button.getAttribute("data-theme");
        refresh();
      });
    }
  );

  Array.prototype.forEach.call(
    form.querySelectorAll(".botao-estrutura"),
    function (button) {
      button.addEventListener("click", function () {
        Array.prototype.forEach.call(form.querySelectorAll(".botao-estrutura"), function (b) {
          b.setAttribute("aria-checked", b === button ? "true" : "false");
        });
        if (layoutInput) layoutInput.value = button.getAttribute("data-layout");
        var descricao = document.getElementById("descricao-estrutura");
        if (descricao) descricao.textContent = button.getAttribute("title") || "";
        refresh();
      });
    }
  );

  function copyFrom(element, button) {
    if (!element) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(element.value).then(function () {
        var original = button.textContent;
        button.textContent = "Copiado";
        setTimeout(function () {
          button.textContent = original;
        }, 2000);
      });
    } else {
      element.select();
    }
  }

  var copyHtml = document.getElementById("copiar-html");
  if (copyHtml) copyHtml.addEventListener("click", function () { copyFrom(outHtml, copyHtml); });

  var copyTxt = document.getElementById("copiar-txt");
  if (copyTxt) copyTxt.addEventListener("click", function () { copyFrom(outTxt, copyTxt); });

  // Sem JS, o preview fica vazio: a rota é chamada por JavaScript. Quem não
  // tiver JavaScript tem a textarea "HTML para colar" e a exportação, que são
  // o mesmo conteúdo — o mesmo princípio do resto da aplicação.
  if (preview) setPreview();
})();
