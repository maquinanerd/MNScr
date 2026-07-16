# MNScr

Motor editorial em Python para **The Screen** e **Cinerie**. Ele recebe pautas por RSS, extrai e estrutura o conteúdo, produz uma versão editorial com IA, publica no WordPress e acompanha a indexação.

## O que faz

- Prioriza feeds e evita duplicidades com SQLite e fila persistente.
- Extrai conteúdo, imagens e vídeos de múltiplas fontes.
- Reescreve e valida artigos em português brasileiro com Gemini.
- Gera HTML compatível com Gutenberg, metadados, links internos e mídia.
- Publica no WordPress e aciona rotinas de sitemap, ping e recrawl.
- Inclui enriquecimento opcional por TMDB e um dashboard local de operação.

## Estrutura

- `app/`: núcleo do pipeline e integrações.
- `templates/`: interface do dashboard e páginas TMDB.
- `tests/`: testes automatizados.
- `main.py`: execução do publicador.
- `indexer.py`: execução do serviço de indexação.

## Instalação

Requer Python 3.11 ou superior.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Preencha no `.env` as credenciais do WordPress, ao menos uma `GEMINI_KEY_*`, as URLs do site e a identidade editorial do portal antes de executar.

## Operação

Executar um ciclo do publicador:

```powershell
python main.py --once
```

Executar continuamente:

```powershell
python main.py
```

Executar o indexador uma vez:

```powershell
python indexer.py --once
```

## Segurança

Nunca versione `.env`, contas de serviço, bancos SQLite, logs ou resultados de execução. O repositório mantém somente modelos de configuração sem credenciais.
