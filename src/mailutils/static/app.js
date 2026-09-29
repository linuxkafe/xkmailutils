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
  var csrf = (form.querySelector('input[name="csrf_token"]') || {}).value || "";
  var scoreBox = document.getElementById("caixa-score");
  var preview = document.getElementById("preview");
  var outHtml = document.getElementById("saida-html");
  var outTxt = document.getElementById("saida-txt");

  var MARKERS = { SEGURO: "[OK]", ATENCAO: "[!]", ELEVADO: "[!!]", CRITICO: "[X]" };

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

  /* O preview vive num `Blob` `text/html` e não em `srcdoc` porque o
   * `srcdoc` teria de ser-HTML escapado, o que o não renderizaria. O
   * `sandbox=""` no iframe é a barreira: sem `allow-scripts` nem
   * `allow-same-origin`, o conteúdo é opaco e não executa nada. */
  function setPreview(html) {
    if (!preview) return;
    var blob = new Blob([html], { type: "text/html" });
    var url = URL.createObjectURL(blob);
    if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
    preview.dataset.objectUrl = url;
    preview.src = url;
  }

  function renderScore(score) {
    if (!scoreBox) return;
    scoreBox.setAttribute("data-nivel", score.categoria);
    var value = scoreBox.querySelector(".score__value");
    if (value) value.innerHTML = escapeHtml(score.score) + ' <span class="score__denom">/ 100</span>';
    var badge = scoreBox.querySelector(".score__badge");
    if (badge) badge.textContent = score.categoria_acentuada;
    var marker = scoreBox.querySelector(".score__marker");
    if (marker) marker.textContent = MARKERS[score.categoria] || "[?]";
    var fill = scoreBox.querySelector(".score__fill");
    /* O atributo, e não `style.width`: a CSP bloqueia estilos inline, e
     * `app.css` tem uma regra por valor do score porque o score é um inteiro
     * de 0 a 100. `tests/test_ui_theme.py` garante que a tabela está completa
     * — se o score passar a fraccionário, a barra esvazia em vez de mentir. */
    if (fill) fill.setAttribute("data-score", score.score);

    var desc = scoreBox.querySelector(".score__head + .score__bar ~ p");
    if (desc) desc.textContent = score.descricao;

    var findings = scoreBox.querySelector(".findings");
    if (findings) {
      if (score.regras.length === 0) {
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
        setPreview(data.html);
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

  // Sem JS, o preview fica vazio. Preenche com o HTML que o servidor já pôs
  // na textarea — é o mesmo conteúdo, apenas sem a moldura do iframe.
  if (preview && outHtml && outHtml.value) setPreview(outHtml.value);
})();
