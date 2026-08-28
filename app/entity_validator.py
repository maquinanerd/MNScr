import logging
import re
from dataclasses import dataclass
from html import unescape
from typing import Dict, Iterable, List, Tuple

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


_CAPITALIZED_WORD_RE = r"(?:[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÁÀÂÃÉÊÍÓÔÕÚÇáàâãéêíóôõúç'’.-]*|[A-Z]{2,})"
_CONNECTOR_RE = r"(?:of|the|and|de|da|do|das|dos|e|&)"
_ENTITY_SEQUENCE_RE = re.compile(
    rf"\b{_CAPITALIZED_WORD_RE}(?:\s+(?:(?:{_CONNECTOR_RE})\s+)*{_CAPITALIZED_WORD_RE})*",
    re.UNICODE,
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_MULTISPACE_RE = re.compile(r"\s+")

_KNOWN_STUDIO_PATTERNS = (
    "Warner Bros.",
    "Warner Bros. Pictures",
    "DC Studios",
    "DC Universe",
    "DC Studios",
    "Marvel Studios",
    "Sony Pictures",
    "20th Century Studios",
    "Universal Pictures",
    "Paramount Pictures",
    "Walt Disney Pictures",
    "Netflix",
    "Prime Video",
    "HBO Max",
    "Max",
    "Disney+",
)

_COMMON_SENTENCE_STARTERS = {
    "A", "O", "Os", "As", "Um", "Uma", "Após", "Com", "De", "Em", "No", "Na",
    "Para", "Por", "Quando", "Se", "Segundo", "Enquanto", "Isso", "Esse", "Essa",
    "Dessa", "Desse", "Além", "Mas", "Já", "Também", "Ainda", "Outro", "Nova", "Novo",
}


@dataclass(frozen=True)
class EntityCorrection:
    original: str
    corrigido: str
    tipo: str


@dataclass(frozen=True)
class EntityWarning:
    original: str
    canonico_proximo: str
    tipo: str
    distancia: int


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    return unescape(soup.get_text(" ", strip=True))


def _normalize_spaces(value: str) -> str:
    return _MULTISPACE_RE.sub(" ", (value or "").strip())


def _normalize_for_distance(value: str) -> str:
    value = _normalize_spaces(value)
    value = value.replace("..", ".")
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return value.lower()


def levenshtein_distance(a: str, b: str) -> int:
    """Small dependency-free Levenshtein implementation."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (0 if ca == cb else 1)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


def _entity_type(entity: str) -> str:
    if _YEAR_RE.fullmatch(entity):
        return "data"
    normalized = entity.lower()
    studio_markers = (
        "bros", "studios", "pictures", "universe", "netflix", "video",
        "disney", "max", "warner", "marvel", "sony", "paramount",
        "universal", "dc",
    )
    if any(marker in normalized for marker in studio_markers):
        return "estudio"
    if ":" in entity or any(token in entity for token in ("'", "’")):
        return "obra"
    return "pessoa" if len(entity.split()) >= 2 else "entidade"


def _is_valid_entity(candidate: str) -> bool:
    candidate = _normalize_spaces(candidate)
    if not candidate:
        return False
    if _YEAR_RE.fullmatch(candidate):
        return True
    if len(candidate) < 3:
        return False
    words = candidate.split()
    if len(words) == 1:
        if candidate in _COMMON_SENTENCE_STARTERS:
            return False
        return candidate in _KNOWN_STUDIO_PATTERNS or candidate.isupper()
    if words[0] in _COMMON_SENTENCE_STARTERS and len(words) == 2:
        return False
    return any(any(ch.isupper() for ch in word) for word in words)


def _iter_capitalized_entities(text: str) -> Iterable[str]:
    for match in _ENTITY_SEQUENCE_RE.finditer(text or ""):
        entity = _normalize_spaces(match.group(0).strip(" .,;:!?()[]{}"))
        if _is_valid_entity(entity):
            yield entity


def extract_canonical_entities(texto_fonte: str) -> Dict[str, str]:
    """Extract canonical entities from source text, preserving exact source spelling."""
    source_text = _html_to_text(texto_fonte)
    entities: Dict[str, str] = {}

    def add(entity: str) -> None:
        entity = _normalize_spaces(entity.strip())
        if not _is_valid_entity(entity):
            return
        key = _normalize_for_distance(entity)
        entities.setdefault(key, entity)

    def add_with_enumeration_parts(entity: str) -> None:
        add(entity)
        parts = re.split(r"\s+(?:e|and|&)\s+", entity)
        if len(parts) <= 1:
            return
        for part in parts:
            add(part)

    for known in _KNOWN_STUDIO_PATTERNS:
        if re.search(re.escape(known), source_text, flags=re.IGNORECASE):
            match = re.search(re.escape(known), source_text, flags=re.IGNORECASE)
            if match:
                add(match.group(0))

    for year in _YEAR_RE.findall(source_text):
        add(year)

    for entity in _iter_capitalized_entities(source_text):
        add_with_enumeration_parts(entity)

    return entities


def _same_shape(candidate: str, canonical: str) -> bool:
    c_words = candidate.split()
    k_words = canonical.split()
    if len(c_words) != len(k_words):
        return False
    if abs(len(candidate) - len(canonical)) > 2:
        return False
    return candidate[:1].lower() == canonical[:1].lower()


def _is_safe_person_correction(candidate: str, canonical: str) -> bool:
    candidate_words = candidate.split()
    canonical_words = canonical.split()
    if len(candidate_words) < 2 or len(candidate_words) != len(canonical_words):
        return True
    return _normalize_for_distance(candidate_words[0]) == _normalize_for_distance(canonical_words[0])


def _replace_visible_candidate(html: str, original: str, corrigido: str) -> Tuple[str, int]:
    pattern = re.compile(rf"(?<![\wÀ-ÿ]){re.escape(original)}(?![\wÀ-ÿ])")
    return pattern.subn(corrigido, html)


def _is_english_possessive_only(original: str, corrigido: str) -> bool:
    def _strip_possessive(value: str) -> str:
        return re.sub(r"(?:'s|’s)$", "", value.strip(), flags=re.IGNORECASE)

    original_base = _strip_possessive(original)
    fixed_base = _strip_possessive(corrigido)
    robust_possessive_re = re.compile(r"(?:'s|’s|â€™s|'|’|â€™)$", re.IGNORECASE)
    robust_original_base = robust_possessive_re.sub("", original.strip())
    robust_fixed_base = robust_possessive_re.sub("", corrigido.strip())
    if (
        original.strip() != corrigido.strip()
        and robust_original_base == robust_fixed_base
        and robust_possessive_re.search(corrigido.strip())
    ):
        return True
    return (
        original.strip() != corrigido.strip()
        and original_base == fixed_base
        and bool(re.search(r"(?:'s|’s)$", corrigido.strip(), flags=re.IGNORECASE))
    )


def _fix_studio_punctuation(
    texto_reescrito: str,
    canonical_entities: Dict[str, str],
) -> Tuple[str, List[EntityCorrection]]:
    corrections: List[EntityCorrection] = []
    fixed = texto_reescrito

    for canonical in canonical_entities.values():
        if "." not in canonical or _entity_type(canonical) != "estudio":
            continue

        variants = {
            canonical.replace(".", ".."): canonical,
            re.sub(r"\.\s+\.", ".", canonical): canonical,
        }
        for original, corrigido in variants.items():
            if original == corrigido:
                continue
            if _is_english_possessive_only(original, corrigido):
                continue
            fixed, count = _replace_visible_candidate(fixed, original, corrigido)
            if count:
                correction = EntityCorrection(original=original, corrigido=corrigido, tipo="estudio")
                corrections.append(correction)
                logger.info(
                    'ENTITY_FIX original="%s" corrigido="%s" tipo=%s',
                    correction.original,
                    correction.corrigido,
                    correction.tipo,
                )

    fixed = _collapse_studio_period(fixed, canonical_entities)
    return fixed, corrections


def _collapse_studio_period(texto: str, canonical_entities: Dict[str, str]) -> str:
    """`Warner Bros..` -> `Warner Bros.`, para estudio cujo nome termina em ponto.

    Precisa rodar DEPOIS de toda correcao, e nao so dentro da correcao de
    pontuacao, porque quem cria o ponto duplo e o passo seguinte: o candidato
    `Warner Bros`, extraido de `Warner Bros. Discovery`, tem distancia 1 para o
    canonico `Warner Bros.` e e substituido por ele — devolvendo o ponto que ja
    estava la. Foi assim que "a Warner Bros.. Discovery" foi publicado no artigo
    53 em 27/08/2026, com as duas correcoes registradas no log como aplicadas.

    O corte e por entidade CONHECIDA, nunca por um generico "letra + dois
    pontos": reticencia e pontuacao legitima, e colapsa-la seria reescrever o
    texto do reporter.
    """
    corrigido = texto
    for canonical in canonical_entities.values():
        if not canonical.endswith(".") or _entity_type(canonical) != "estudio":
            continue
        corrigido = re.sub(re.escape(canonical) + r"\.+", canonical, corrigido)
    return corrigido


def _fix_anchor_entity_casing(
    html: str,
    canonical_entities: Dict[str, str],
) -> Tuple[str, List[EntityCorrection]]:
    if not html or not canonical_entities:
        return html, []

    soup = BeautifulSoup(html, "html.parser")
    corrections: List[EntityCorrection] = []

    for anchor in soup.find_all("a"):
        for text_node in list(anchor.find_all(string=True)):
            original_text = str(text_node)
            fixed_text = original_text
            for canonical in canonical_entities.values():
                if len(canonical) < 3:
                    continue
                pattern = re.compile(rf"(?<!\w){re.escape(canonical)}(?!\w)", re.IGNORECASE)

                def repl(match: re.Match) -> str:
                    matched = match.group(0)
                    if matched == canonical:
                        return matched
                    corrections.append(
                        EntityCorrection(original=matched, corrigido=canonical, tipo=_entity_type(canonical))
                    )
                    return canonical

                fixed_text = pattern.sub(repl, fixed_text)

            if fixed_text != original_text:
                text_node.replace_with(fixed_text)

    for correction in corrections:
        logger.info(
            'ENTITY_FIX original="%s" corrigido="%s" tipo=%s context=anchor',
            correction.original,
            correction.corrigido,
            correction.tipo,
        )

    return str(soup), corrections


def validate_and_fix_entities(
    texto_fonte: str,
    texto_reescrito: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Correct near-miss proper-name spellings introduced during rewriting.

    Returns the corrected rewritten text and a serializable correction list.
    """
    canonical_entities = extract_canonical_entities(texto_fonte)
    if not canonical_entities or not texto_reescrito:
        return texto_reescrito, []

    fixed_text, punctuation_corrections = _fix_studio_punctuation(texto_reescrito, canonical_entities)
    corrections: List[EntityCorrection] = list(punctuation_corrections)
    warnings: List[EntityWarning] = []

    rewritten_text = _html_to_text(fixed_text)
    candidates = list(dict.fromkeys(_iter_capitalized_entities(rewritten_text)))

    for candidate in candidates:
        normalized_candidate = _normalize_for_distance(candidate)
        if normalized_candidate in canonical_entities:
            continue

        best_entity = ""
        best_distance = 999
        for canonical in canonical_entities.values():
            if not _same_shape(candidate, canonical):
                continue
            distance = levenshtein_distance(
                _normalize_for_distance(candidate),
                _normalize_for_distance(canonical),
            )
            if distance < best_distance:
                best_distance = distance
                best_entity = canonical

        if not best_entity:
            continue

        tipo = _entity_type(best_entity)
        if best_distance in (1, 2):
            if _is_english_possessive_only(candidate, best_entity):
                logger.warning(
                    'ENTITY_WARNING original="%s" canonico_proximo="%s" tipo=%s distancia=%s motivo=english_possessive_only',
                    candidate,
                    best_entity,
                    tipo,
                    best_distance,
                )
                continue
            if tipo == "pessoa" and not _is_safe_person_correction(candidate, best_entity):
                logger.warning(
                    'ENTITY_WARNING original="%s" canonico_proximo="%s" tipo=%s distancia=%s motivo=first_name_diff',
                    candidate,
                    best_entity,
                    tipo,
                    best_distance,
                )
                continue
            fixed_text, count = _replace_visible_candidate(fixed_text, candidate, best_entity)
            if count:
                correction = EntityCorrection(original=candidate, corrigido=best_entity, tipo=tipo)
                corrections.append(correction)
                logger.info(
                    'ENTITY_FIX original="%s" corrigido="%s" tipo=%s',
                    correction.original,
                    correction.corrigido,
                    correction.tipo,
                )
        elif best_distance > 2 and best_distance <= 4:
            warning = EntityWarning(
                original=candidate,
                canonico_proximo=best_entity,
                tipo=tipo,
                distancia=best_distance,
            )
            warnings.append(warning)
            logger.warning(
                'ENTITY_WARNING original="%s" canonico_proximo="%s" tipo=%s distancia=%s',
                warning.original,
                warning.canonico_proximo,
                warning.tipo,
                warning.distancia,
            )

    fixed_text, anchor_corrections = _fix_anchor_entity_casing(fixed_text, canonical_entities)
    corrections.extend(anchor_corrections)

    # Ultima palavra sobre pontuacao de estudio: as correcoes por distancia
    # acima podem ter recolocado o ponto que a primeira passada tirou.
    fixed_text = _collapse_studio_period(fixed_text, canonical_entities)

    serialized = [
        {"original": item.original, "corrigido": item.corrigido, "tipo": item.tipo}
        for item in corrections
    ]
    return fixed_text, serialized
