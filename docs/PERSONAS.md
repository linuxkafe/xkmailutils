# PERSONAS

## Persona 1 — Alexandra, técnica de TI (utilizador principal)

- **Contexto:** envia 40 a 80 emails por dia a partir do Thunderbird num Portable
  Linux. A empresa dá-lhe um logótipo e uma lista de informações (nome, cargo,
  telefone, site, LinkedIn). Ela quer a assinatura bonita **e** quer que os
  clientes a recebam, não na pasta de spam.
- **Já tentou:** colar HTML copiado do Gmail, embeber imagens em base64 "para dar
  jeito", usar um gerador online que lhe pedia a palavra-passe do email.
- **Frustração central:** nenhuma ferramenta diz *qual* parte está a ser punida.
  Perde horas a testar a enviar para si própria.
- **O que precisa:** carregar o logótipo, escrever o texto, copiar. E um número que
  explique o resto.
- **Medo:** devolver um HTML bonito que acabe no spam do destinatário.
- **Sucesso medido:** gera a primeira assinatura em menos de 60 s e não volta ao
  editor.

## Persona 2 — Bruno, responsável de um pequeno negócio (utilizador secundário)

- **Contexto:** quatro colaboradores, Thunderbird em Windows, clientes finais não
  técnicos. Só quer que a assinatura não quebre no Outlook.
- **O que precisa:** a exportação em texto simples e as instruções por cliente,
  porque a equipa não é técnica.
- **Sucesso medido:** consegue colar no Outlook sem pedir ajuda a ninguém.

## Persona 3 — Rui, administrador (operador do servidor)

- **Contexto:** aloja a aplicação numa VM com SQLite e um SMTP interno. Não quer
  gerir mais uma base de dados.
- **O que precisa:** o primeiro utilizador vem do `.env`; cria contas por convite;
  vê quem está activo e desliga quem saiu.
- **Sucesso medido:** nunca precisou de abrir a base de dados à mão.

## Fora do âmbito como personas

Consumidores finais, equipas de marketing com landing pages e quem precisa de
assinatura em GIF animado — bloqueado por peso e por penalização de spam.
