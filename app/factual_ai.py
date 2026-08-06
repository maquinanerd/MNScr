"""Camada semântica da extração de claims — o lado de I/O do modo ``hybrid``.

Este módulo é a peça que faltava entre ``config/prompts/`` e
``app.factual.extraction.parse_claim_response``. Sem ele o modo ``hybrid``
existia no papel: ``build_factual_assessment`` recebia ``claim_response=None``,
registrava ``NO_SEMANTIC_CLAIM_RESPONSE`` e devolvia exatamente o que o modo
``deterministic`` devolveria — com o rótulo do modo mais caro.

A fronteira é deliberada. Aqui mora tudo que toca o mundo externo: ler o arquivo
de prompt, montar o texto e chamar o modelo. A validação de tudo que volta fica
em ``app.factual.extraction``, que é pura e testável sem rede. Este módulo nunca
interpreta a resposta — ele apenas a entrega.

O modelo é tratado como testemunha, não como autoridade: ele só pode **apontar**
para texto que já está no rascunho, e quem confere isso é ``parse_claim_response``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final, List, Optional

logger = logging.getLogger(__name__)

#: Delimitadores que o prompt usa para cercar o conteúdo não confiável.
_CONTENT_OPEN = "<<<INICIO_CONTEUDO_FONTE>>>"
_CONTENT_CLOSE = "<<<FIM_CONTEUDO_FONTE>>>"

#: Placeholders do template.
_PLACEHOLDER_CONTENT = "{DRAFT_CONTENT}"
_PLACEHOLDER_MAX_CLAIMS = "{MAX_CLAIMS}"

#: Cache do template por caminho resolvido. O arquivo é versionado e imutável em
#: runtime; reler a cada artigo seria I/O sem ganho.
_TEMPLATE_CACHE: dict[str, str] = {}


class ClaimPromptError(Exception):
    """O prompt versionado não pôde ser carregado."""


#: Raiz de instalação, a mesma que `contracts/` e a política do gate usam. No
#: repositório é a raiz do checkout; num wheel instalado é a raiz do
#: `site-packages`.
_INSTALL_ROOT: Final[Path] = Path(__file__).resolve().parents[1]


def resolve_prompt_path(version: str, prompt_dir: str) -> Path:
    """Caminho do prompt, procurado no cwd e depois na raiz de instalação.

    `prompt_dir` é relativo ao DIRETÓRIO DE TRABALHO, o que só funciona rodando
    de dentro do checkout. Um wheel instalado carregava contrato e política e
    morria aqui, com "Disponíveis: nenhum" — mensagem correta e enganosa ao mesmo
    tempo, porque o arquivo existe, só não sob o cwd de quem executa.

    O relativo continua vencendo: é ele que permite apontar para um prompt
    próprio sem reinstalar. A raiz de instalação é a segunda tentativa.
    """
    arquivo = f"{version.replace('-', '_')}.txt"
    base = Path(prompt_dir)
    candidato = base / arquivo
    if candidato.exists() or base.is_absolute():
        return candidato

    instalado = _INSTALL_ROOT / base / arquivo
    return instalado if instalado.exists() else candidato


def load_claim_prompt(version: str, prompt_dir: str) -> str:
    """Carrega o prompt versionado.

    Falha alto e claro: um prompt ausente não pode virar fallback silencioso.
    Se a versão configurada não existe, toda extração semântica sairia vazia e o
    sistema reportaria ``hybrid`` fazendo trabalho de ``deterministic`` — que é
    precisamente o defeito que este módulo existe para corrigir.
    """
    path = resolve_prompt_path(version, prompt_dir)
    key = str(path.resolve())
    cached = _TEMPLATE_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        available = sorted(p.stem for p in Path(prompt_dir).glob("*.txt")) if Path(prompt_dir).is_dir() else []
        raise ClaimPromptError(
            f"Prompt de extração factual '{version}' não encontrado em '{path}'. "
            f"Disponíveis: {', '.join(available) if available else 'nenhum'}"
        ) from exc

    if _PLACEHOLDER_CONTENT not in text:
        raise ClaimPromptError(
            f"Prompt '{version}' não contém o placeholder {_PLACEHOLDER_CONTENT}"
        )

    _TEMPLATE_CACHE[key] = text
    return text


def _neutralize_delimiters(text: str) -> str:
    """Impede que o próprio conteúdo feche o bloco de dado não confiável.

    O prompt cerca o rascunho com marcadores e instrui o modelo a tratar o que
    está dentro como dado. Um rascunho que contivesse o marcador de fechamento
    encerraria o bloco mais cedo, e o resto do texto passaria a ser lido como se
    fosse instrução nossa. Não é um cenário hipotético: o corpo vem de páginas
    de terceiros, e o extrator preserva o texto delas.
    """
    return text.replace(_CONTENT_OPEN, "<<<_>>>").replace(_CONTENT_CLOSE, "<<<_>>>")


def render_draft_content(draft: Any) -> str:
    """Serializa o rascunho com as MESMAS localizações que o extrator determinístico usa.

    O prompt exige ``location`` em ``title | subtitle | summary | meta_description
    | body:p:N``. Reaproveitar ``segment_draft`` garante que o ``body:p:7`` que o
    modelo devolve aponte para o mesmo parágrafo que ``extract_deterministic_claims``
    chamaria de ``body:p:7`` — sem isso, ``merge_claims`` uniria coisas de lugares
    diferentes achando que são o mesmo lugar.
    """
    from .factual.extraction import segment_draft

    lines: List[str] = []
    for segment in segment_draft(draft):
        lines.append(f"[{segment.location}] {_neutralize_delimiters(segment.text)}")
    return "\n\n".join(lines)


def render_claim_prompt(draft: Any, *, template: str, max_claims: int) -> str:
    """Preenche o template com o rascunho já segmentado."""
    content = render_draft_content(draft)
    return template.replace(_PLACEHOLDER_MAX_CLAIMS, str(max_claims)).replace(
        _PLACEHOLDER_CONTENT, content
    )


def request_claim_extraction(
    draft: Any,
    *,
    client: Any = None,
    max_claims: Optional[int] = None,
    version: Optional[str] = None,
    prompt_dir: Optional[str] = None,
) -> Optional[str]:
    """Pede ao modelo a lista de afirmações do rascunho.

    Devolve o texto cru da resposta, ou ``None`` quando não há o que enviar,
    quando não há cliente de IA, ou quando a chamada falhou. ``None`` faz
    ``build_factual_assessment`` registrar ``NO_SEMANTIC_CLAIM_RESPONSE`` e
    seguir com os claims determinísticos: a avaliação factual pode ficar mais
    pobre, mas nunca derruba o draft.

    A resposta NÃO é interpretada aqui — nem para ver se é JSON. Quem valida é
    ``parse_claim_response``, que confere cada afirmação contra o texto do
    rascunho. Adiantar validação neste ponto criaria duas regras de aceitação.
    """
    from . import config

    draft_id = getattr(draft, "draft_id", "") or "-"
    resolved_version = version or config.FACTUAL_PROMPT_VERSION
    resolved_dir = prompt_dir or config.FACTUAL_PROMPT_DIR
    resolved_max = int(max_claims or config.MAX_CLAIMS_PER_DRAFT)

    if client is None:
        client = _default_client()
    if client is None:
        logger.warning(
            "[CLAIM_EXTRACTION_SKIPPED] draft_id=%s motivo=sem_cliente_de_ia", draft_id
        )
        return None

    try:
        template = load_claim_prompt(resolved_version, resolved_dir)
    except ClaimPromptError as exc:
        logger.error("[CLAIM_EXTRACTION_SKIPPED] draft_id=%s erro=%s", draft_id, exc)
        return None

    prompt = render_claim_prompt(draft, template=template, max_claims=resolved_max)
    if _PLACEHOLDER_CONTENT in prompt or not render_draft_content(draft).strip():
        logger.warning(
            "[CLAIM_EXTRACTION_SKIPPED] draft_id=%s motivo=rascunho_sem_conteudo", draft_id
        )
        return None

    logger.info(
        "[CLAIM_EXTRACTION_STARTED] draft_id=%s prompt_version=%s max_claims=%s",
        draft_id, resolved_version, resolved_max,
    )
    try:
        raw, tokens = client.generate_text(prompt)
    except Exception as exc:  # noqa: BLE001 - a avaliação factual nunca custa o draft
        logger.warning(
            "[CLAIM_EXTRACTION_FAILED] draft_id=%s erro=%s: seguindo com claims deterministicos",
            draft_id, type(exc).__name__,
        )
        return None

    if not raw or not str(raw).strip():
        logger.warning("[CLAIM_EXTRACTION_EMPTY] draft_id=%s", draft_id)
        return None

    logger.info(
        "[CLAIM_EXTRACTION_COMPLETED] draft_id=%s chars=%s tokens=%s",
        draft_id, len(str(raw)), _total_tokens(tokens),
    )
    return str(raw)


def _default_client() -> Any:
    """Reaproveita o cliente já em uso pelo pipeline.

    ``AIProcessor._ai_client`` é um singleton de classe que carrega o pool de
    chaves, o rodízio e o backoff. Instanciar um cliente novo aqui criaria um
    segundo rate limiter alheio ao primeiro, e os dois estourariam a cota um do
    outro sem nunca se ver.
    """
    try:
        from .ai_processor import AIProcessor
    except Exception:  # noqa: BLE001
        return None
    return getattr(AIProcessor, "_ai_client", None)


def _total_tokens(tokens: Any) -> Any:
    if isinstance(tokens, dict):
        return tokens.get("total") or tokens.get("total_tokens") or "-"
    return tokens if tokens is not None else "-"


__all__ = [
    "ClaimPromptError",
    "load_claim_prompt",
    "render_claim_prompt",
    "render_draft_content",
    "request_claim_extraction",
    "resolve_prompt_path",
]
