SHELL := /bin/bash
PYTHON ?= python3

AES_LANGUAGE := python
AES_LINT := ruff check
AES_TEST := $(PYTHON) -m pytest
AES_FORMAT := ruff format
AES_RUN := $(PYTHON) -m mailutils.main
AES_RUN_VARS := MAILUTILS_ENV=development MAILUTILS_MAIL_BACKEND=console

export PYTHONPATH := src

.PHONY: setup run test e2e lint format check docs-check code-check test-check \
        lint-check format-check e2e-check mutation-check verify clean doctor help

# ---------------------------------------------------------------------- setup
setup:
	@echo "A instalar dependências de desenvolvimento..."
	@$(PYTHON) -m pip install -q -e ".[dev]" 2>/dev/null \
		|| echo "aviso: pip install falhou ou não há rede. As dependências de sistema já servem para correr e testar."

# ------------------------------------------------------------------------ run
run:
	@mkdir -p var
	$(AES_RUN_VARS) $(PYTHON) -c "from mailutils.main import main; main()"

# ----------------------------------------------------------------------- test
test:
	@$(AES_TEST)

# ----------------------------------------------------------------------- lint
lint:
	@$(AES_LINT) src tests e2e

format:
	@$(AES_FORMAT) src tests e2e

# ============================================================== QUALITY GATES
#
# `make check` é *o* gate. Todos os sub-gates falham de verdade: um gate que
# imprime "coverage abaixo de 80%" e sai com 0 é um gate que não existe, e é
# pior do que não ter gate nenhum — dá confiança falsa.

check: docs-check code-check test-check lint-check format-check e2e-check mutation-check
	@echo ""
	@echo "make check: TODOS OS GATES VERDES"

# Cada doc tem de existir E ter a secção que o contrato exige. Os títulos
# estão em português porque a interface e a documentação são pt-PT (FR-5.4);
# um gate que procure "Problem" ou "Roadmap" em inglês está a verificar a
# língua, não a substance.
docs-check:
	@echo "docs-check"
	@for spec in \
		"docs/VISION.md:## Problem" \
		"docs/VISION.md:## Limites honestos" \
		"docs/PERSONAS.md:## Persona 1" \
		"docs/REQUIREMENTS.md:## Functional" \
		"docs/REQUIREMENTS.md:## Non-Functional" \
		"docs/ROADMAP.md:## Backlog" \
		"docs/DESIGN.md:## Regras de superfície" \
		"CLAUDE.md:## Never Do" \
		; do \
		file="$${spec%%:*}"; marker="$${spec#*:}"; \
		if [ ! -f "$$file" ]; then echo "  FALTA $$file"; exit 1; fi; \
		if ! grep -qF "$$marker" "$$file"; then \
			echo "  $$file não tem a secção $$marker"; exit 1; \
		fi; \
		echo "  ok $$file $$marker"; \
	done
	@if ! grep -qF "Universidade do Porto" CLAUDE.md; then \
		echo "  FALHA: CLAUDE.md deixou de declarar a regra de zero menções"; exit 1; \
	fi
	@if grep -rqiE "universidade do porto|up\.pt" src/ 2>/dev/null; then \
		echo "  FALHA: menção institucional no código que entra em produção"; exit 1; \
	fi
	@echo "  ok regra declarada em CLAUDE.md e ausente de src/"

# A regra que o produto existe para honrar, verificada no código e não na
# intenção. Um `data:` URI no renderer é uma regressão de definição.
code-check:
	@echo "code-check"
	@if grep -R "TODO:" src/ tests/ 2>/dev/null; then \
		echo "  FALHA: TODO: em src/ ou tests/"; exit 1; \
	fi
	@if grep -REn "src\s*=\s*[\"']data:" src/mailutils/signatures/ 2>/dev/null; then \
		echo "  FALHA: data: URI no gerador de assinatura"; exit 1; \
	fi
	@$(PYTHON) scripts/check-no-secret-leak.py
	@if grep -REn "http://" src/mailutils/signatures/renderer.py 2>/dev/null; then \
		echo "  FALHA: http:// no renderer de assinatura"; exit 1; \
	fi
	@$(PYTHON) -c "import mailutils.main" 2>/dev/null || { \
		echo "  FALHA: src/mailutils não importa"; exit 1; }
	@echo "  ok sem TODO, sem data: URI, sem segredos impressos, sem http:// na assinatura"
	@echo "  ok o pacote importa"

# Aplica o caminho completo de login, incluindo o segundo factor. É o teste
# mais caro e o que mais vale: uma falha aqui é uma falha de segurança.
test-check:
	@echo "test-check"
	@$(AES_TEST) --cov=mailutils --cov-report=term-missing --cov-fail-under=80
	@echo "  ok suite completa com cobertura >= 80%"

lint-check:
	@echo "lint-check"
	@$(AES_LINT) src tests e2e

format-check:
	@echo "format-check"
	@$(AES_FORMAT) --check src tests e2e
	@echo "  ok formatação canónica"

# ------------------------------------------------------------------ e2e-check
#
# O T008 (Playwright) vive aqui e não em `test-check`. A separação é
# deliberada: `make test` tem de continuar a correr sem browser para ser
# usável num contentor de CI mínimo, e ao mesmo tempo `make check` — *o* gate
# — não pode dar a impressão de que o caminho login → segundo factor → editor
# → exportação está coberto quando ninguém clicou num botão.
#
# Este gate não tem saída de emergência. Faltam browsers? FALHA, com o
# comando exacto de reparação. Um `|| true` aqui seria a mesma mentira que o
# format-check fazia.
e2e:
	@$(MAKE) e2e-check

e2e-check:
	@echo "e2e-check"
	@$(PYTHON) scripts/check-playwright-browsers.py
	@$(PYTHON) -m pytest e2e --no-cov -q
	@echo "  ok fluxo login → 2F → editor → score → exportar verificados no browser"

# ------------------------------------------------------- mutation-check
#
# A prova por mutação da primeira ronda foi feita à mão, uma vez, e não deixou
# rasto nenhum. Duas personas da revisão provaram, por mutação, que três dos
# testes **não** detectavam as mutações que alegavam detectar — e a garantia
# escrita no ticket e no docstring era verdadeira quando a escrevi e falsa como
# prova. (F-10)
#
# `run-mutations.py --verificar` aplica cada alteração, corre o comando e
# reverte. Sai != 0 se alguma não fizer o gate ficar vermelho, o que significa
# que os testes que dizem provar essas coisas não as provam. Custa ~3 minutos;
# é o preço de uma afirmação ser auditável em vez de lembrada.
mutation-check:
	@echo "mutation-check"
	@$(PYTHON) scripts/run-mutations.py --verificar
	@echo "  ok toda a mutação faz o gate ficar vermelho"

# --------------------------------------------------------------------- verify
verify:
	@./scripts/verify-implementation.sh $(TICKET)

# ---------------------------------------------------------------------- misc
clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache coverage.xml .coverage htmlcov
	@echo "limpo (var/ não é apagado: tem a base de dados e os logótipos)"

doctor:
	@echo "Language:  $(AES_LANGUAGE)"
	@echo "Python:    $$($(PYTHON) --version 2>&1)"
	@echo "Ruff:      $$(ruff --version 2>&1 || echo 'não instalado')"
	@echo "FastAPI:   $$($(PYTHON) -c 'import fastapi; print(fastapi.__version__)' 2>&1 || echo ausente)"
	@echo "httpx:     $$($(PYTHON) -c 'import httpx; print(httpx.__version__)' 2>&1 || echo ausente)"
	@# O browser é requisito de `make check`, não um extra: `e2e-check` falha
	@# alto sem ele. `doctor` é o comando que existe para dizer o que falta,
	@# e silenciar o browser aqui era esconder a única dependência que não se
	@# resolve com `make setup`. (F-13)
	@echo "playwright:$$($(PYTHON) -c 'from importlib.metadata import version; print(version("playwright"))' 2>&1 || echo ausente)"
	@$(PYTHON) scripts/check-playwright-browsers.py 2>&1 | sed 's/^/  /' || true
	@echo ""
	@echo "Nota: starlette 0.31 usa TestClient(app=...), removido no httpx 0.28."
	@echo "      Os testes usam tests/asgi_client.py. Não rebaixar o httpx do sistema."

help:
	@echo "make setup         instalar dependências de desenvolvimento"
	@echo "make run           arrancar em http://127.0.0.1:8000"
	@echo "make check         O GATE: docs + código + testes + lint + formatação + E2E"
	@echo "make setup         instala dependências. Depois: python3 -m playwright install chromium"
	@echo "make test          pytest com cobertura (HTTP, sem browser)"
	@echo "make e2e           Playwright: login → 2F → editor → score → exportar"
	@echo "make mutations     a prova por mutação, corrida a sério (~3 min)"
	@echo "make lint          ruff check"
	@echo "make format        ruff format"
	@echo "make verify        scripts/verify-implementation.sh"
	@echo "make doctor        estado do ambiente"
