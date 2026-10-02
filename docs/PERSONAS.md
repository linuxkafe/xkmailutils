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

## Persona 4 — Marta, de uma studio pequena (utilizador de envio)

> **Nota de honestidade epistémica:** esta persona não veio de entrevista nem de
> um pedido documentado. Foi escrita por mim (agente) para satisfazer a regra
> de `ROADMAP.md` — *"um item só entra em `planeado` depois de existir uma
> Persona a precisar dele"* — depois de o dono decidir inverter o Non-Goal.
> **Se a pessoa real for outra, esta persona está errada e as FR-6/7/8 estão
> dimensionadas para o caso errado.** O que está abaixo é o pior caso
> razoável, não o caso observado. Tratar como hipótese, não como evidência.
>
> Nenhuma das personas 1–3 precisa disto. Alexandra envia do Thunderbird, Bruno
> copia HTML, Rui opera. Nenhuma delas tem uma lista de destinatários que
> confirmaram presença. É por isso que isto é uma audiência **nova**, e não uma
> extensão das existentes.

- **Contexto:** coordena três grupos de trabalho. Uma vez por mês escreve uma
  actualização para quem pediu para a receber — clientes do estúdio e
  colaboradores. O Thunderbird chega para escrever uma linha; não chega para
  150 destinatários, cada um com nome e com estado de confirmação diferente, e
  para um envio que às vezes tem de ficar agendado para as 9h de uma terça.
- **Já tentou:** BCC a todos de uma vez (acabou no spam, e a reputação do domínio
  ficou), uma ferramenta de newsletters SaaS (pediu um painel inteiro para
  enviar trinta linhas por mês), e um script próprio que enviava sem confirmar
  quem queria receber.
- **Frustração central:** não tem um sítio onde o destinatário diga *"quero
  receber isto"*. Tudo o que encontrou ou ignora o assunto ou exige um contrato
  de Serum que não leu.
- **O que precisa:** escrever o texto, ver o score de spam **antes** de mandar,
  adicionar endereços, e ter a certeza de que **quem não confirmou não recebe**.
- **Medo:** o oposto de Alexandra. Alexandra tem medo de o email cair no spam;
  Marta tem medo de **ser ela a ser o spam** — e de acordar com o domínio numa
  lista negra por causa de um duplo clique.
- **Sucesso medido:** o envio do mês é um rascunho, um score, e um agendamento.
  Zero BCC. Zero surpresas de quem recebeu.

### A persona 4 foi falsificada pelo dono (2026-10-02)

A nota de honestidade epistémica acima previa que *"se a pessoa real for outra,
esta persona está errada e as FR-6/7/8 estão dimensionadas para o caso
errado"*. O dono respondeu, e o caso real **não** é este.

A necessidade central escrita aqui — *"ter a certeza de que quem não confirmou
não recebe"* e *"não tem um sítio onde o destinatário diga quero receber isto"* —
é o oposto do que foi pedido: importar listas que já existem e **assumir** o
consentimento, com o `from` confirmado a ser o único código que se pede.

Isto vale por três razões:

1. **É a primeira falsificação desta persona por algo que não foi um palpite de
   agente.** Até aqui era uma hipótese; agora é uma hipótese refutada. A nota
   continua válida e a persona continua marcada como hipótese — não a
   reescrevo para lhe dar a razão, que era precisamente o modo de a tornar
   inútil.
2. **A frustração central estava errada, não a solução.** O que o dono tem é
   uma lista onde já tem os endereços, e o que lhe falta é poder *enviar* sem
   montar uma máquina de consentimento para cada importação. Isto move a
   Persona 4 de *"precisa de recolher consentimento"* para *"precisa de enviar e
   de poder sair"*.
3. **O custo é real e está escrito.** Perder a prova de consentimento verificada
   é perder a propriedade mais forte que o produto tinha. É a decisão do dono,
   com o custo nomeado no `CLAUDE.md` e em `REQUIREMENTS.md` FR-6.2/6.9, e com
   os seis portões que a substituem. Não é uma improvement neutra e não é
   descrita como se fosse.

## Fora do âmbito como personas

Consumidores finais, quem quer uma ferramenta de marketing com segmentos e
campanhas, quem precisa de assinatura em GIF animado (bloqueado por peso e por
penalização de spam), e quem precisa de envio transaccional a partir de uma
aplicação própria — bloqueado por NFR-10 (zero dependências) e porque o SMTP é
configurado pelo operador, não gerido pelo produto.
