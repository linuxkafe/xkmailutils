"""Suite E2E com Playwright (T008).

Este `__init__.py` não é formality: sem ele, o `e2e/conftest.py` é importado
como o módulo de topo `conftest` e sombreia o `tests/conftest.py`, porque os
dois directórios entram no `sys.path`. O resultado é um erro de importação que
não tem nada a ver com o que se está a testar:

    ImportError: cannot import name 'csrf_from' from 'conftest'

Com o pacote, o ficheiro é importado como `e2e.conftest` e os dois suites
convivem — `pytest tests e2e` passa a ser um comando que funciona.
"""
