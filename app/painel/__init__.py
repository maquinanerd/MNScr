"""Painel do MNScr: um site só de leitura, atrás de login, para acompanhar o robô.

Roda em contêiner próprio (serviço `painel` do docker-compose.coolify.yml), ao lado do
robô, no mesmo volume. Lê o banco do robô (`app.db`) SOMENTE em modo leitura; o que o
painel grava — administrador, senha, época da sessão — fica em `painel.db`, à parte.
Um problema no painel nunca para o robô, e o painel não tem botão que mude nada nele.
"""
