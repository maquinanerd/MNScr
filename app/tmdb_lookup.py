"""Nome -> `tmdb_id`. O segundo campo de identidade do item de resolução.

Existe por um número: o corte de auto-verificação do Cinerie é **0.9**, e o
casamento por nome vale **0.85**. Medido ao vivo sobre oito matérias reais, 25
de 25 vínculos casaram por `exact_name` — ou seja, **nenhum** nascia verificado.
Não é lento, é nunca. O `tmdb_id` é o único casamento que vale **1.0**, e ele
estava inalcançável porque o item enviado carregava só `kind` + `name`.

Este módulo faz uma coisa só: descobrir o id da TMDB para um nome que o texto
citou. Quem transforma isso em bloco é o `cinerie.entity_resolve`; quem casa o
id com o catálogo é a rota do Cinerie.

**A regra que organiza tudo aqui é a mesma do `entity_resolve`: `None` é
inofensivo, id errado é mentira publicada.** E aqui ela é ainda mais afiada, por
uma razão medida na sonda: a rota **não** volta ao nome quando o id não está no
catálogo. Um `tmdbId` errado não produz "casamento pior" — produz `null` onde
antes havia vínculo. Por isso nada neste módulo desempata por popularidade,
por ordem de resultado ou por "o mais provável":

| busca | resposta |
| --- | --- |
| um resultado com o nome EXATO | o id dele |
| dois ou mais com o nome exato (homônimos) | ``None`` |
| nenhum com o nome exato | ``None`` |

"Chris Evans" — o ator e o apresentador britânico — é o caso que a ADR usa para
recusar baixar o corte para 0.85, e é exatamente o caso que cai em ``None``
aqui. Sem id, o vínculo segue pelo caminho antigo, a 0.85, e a unicidade no
catálogo do Cinerie continua sendo quem o segura.

**Credencial.** Ver `config.tmdb_credential`: o modo (Bearer v4 ou `api_key`
v3) vem da FORMA do valor, não do nome da variável.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Final, MutableMapping, Optional, Sequence, Tuple

import requests

logger = logging.getLogger(__name__)

TMDB_BASE_URL: Final[str] = "https://api.themoviedb.org/3"

#: Os três tipos que a rota do Cinerie resolve e que a TMDB também conhece.
#: `season`/`episode` não entram: a rota responde `unsupported_kind` para eles,
#: verificado ao vivo, e nenhuma entidade desse tipo chega a existir no MNScr.
_SEARCH_PATH: Final[Dict[str, str]] = {
    "movie": "/search/movie",
    "tv": "/search/tv",
    "person": "/search/person",
}

#: Onde cada tipo guarda o nome. Os dois campos são conferidos: a TMDB devolve
#: o título traduzido em `title` e o de origem em `original_title`, e a matéria
#: em português cita ora um, ora outro.
_NAME_FIELDS: Final[Dict[str, Tuple[str, ...]]] = {
    "movie": ("title", "original_title"),
    "tv": ("name", "original_name"),
    "person": ("name", "original_name"),
}

#: O parâmetro de ano muda de nome por tipo, e `person` não tem nenhum.
_YEAR_PARAM: Final[Dict[str, Optional[str]]] = {
    "movie": "primary_release_year",
    "tv": "first_air_date_year",
    "person": None,
}

#: Teto do cache compartilhado. Um processo que roda o dia inteiro não pode
#: crescer sem fim, e o corte é FIFO porque a ordem de chegada é a única
#: informação de recência que existe aqui sem custo.
_CACHE_MAX: Final[int] = 4096

#: Compartilhado entre instâncias de propósito: matérias seguidas do mesmo
#: cluster citam as MESMAS pessoas, e o pipeline constrói o cliente mais de uma
#: vez. Um cache por instância pagaria a mesma busca a cada matéria.
_SHARED_CACHE: Dict[Tuple[str, str, Optional[int]], Optional[int]] = {}


def fold(value: str) -> str:
    """NFD sem acento, minúsculo, espaços colapsados.

    A mesma dobra do `cinerie.entity_resolve` — e ela precisa ser a mesma, senão
    "Chloe Zhao" e "Chloé Zhao" seriam nomes diferentes de um lado e iguais do
    outro, e o id encontrado aqui não corresponderia ao nome enviado lá.
    """
    texto = unicodedata.normalize("NFD", str(value or ""))
    sem_acento = "".join(ch for ch in texto if unicodedata.category(ch) != "Mn")
    return " ".join(sem_acento.split()).lower()


@dataclass(frozen=True)
class TmdbCredential:
    """O que autentica, e COMO. Nunca entra em log e nunca entra em `repr`."""

    value: str
    mode: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"TmdbCredential(mode={self.mode!r}, len={len(self.value)})"

    @property
    def usable(self) -> bool:
        return bool(self.value and self.mode)


class TmdbLookup:
    """`(kind, nome, ano)` -> `tmdb_id`, ou ``None``. **Nunca levanta.**

    Toda falha — rede, 401, 429, JSON quebrado — vira ``None``, e ``None`` faz o
    item viajar como sempre viajou: só com nome. O pior caso desta classe é o
    comportamento de ontem, e essa é a propriedade que a torna segura de ligar.

    O cache guarda o **negativo** também. Sem isso, um nome que não resolve
    (a maioria: ruído que tem forma de nome próprio) seria perguntado de novo em
    cada matéria do cluster — e é justamente o que mais aparece.
    """

    def __init__(
        self,
        credential: TmdbCredential,
        *,
        language: str = "pt-BR",
        timeout_seconds: float = 10.0,
        session: Optional[Any] = None,
        cache: Optional[MutableMapping[Tuple[str, str, Optional[int]], Optional[int]]] = None,
        base_url: str = TMDB_BASE_URL,
    ) -> None:
        self.credential = credential
        self.language = (language or "pt-BR").strip()
        self.timeout_seconds = float(timeout_seconds)
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._cache = _SHARED_CACHE if cache is None else cache

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"TmdbLookup({self.credential!r}, cache={len(self._cache)})"

    # -- transporte ---------------------------------------------------------

    def _get(self, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """`GET` autenticado. Devolve o corpo, ou ``None`` em qualquer recusa."""
        headers = {"Accept": "application/json"}
        consulta = dict(params)
        if self.credential.mode == "bearer":
            headers["Authorization"] = f"Bearer {self.credential.value}"
        else:
            consulta["api_key"] = self.credential.value

        caller = self._session if self._session is not None else requests
        try:
            resposta = caller.request(
                "GET",
                f"{self.base_url}{path}",
                headers=headers,
                params=consulta,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - busca nunca custa a matéria
            logger.warning(
                "[TMDB] busca indisponível (%s) em %s: %s; segue sem id",
                type(exc).__name__, path, exc,
            )
            return None

        codigo = getattr(resposta, "status_code", 0)
        if codigo != 200:
            # Classificado pelo código. `401` com token na forma certa quer dizer
            # credencial revogada — e não "o nome não existe", que é a leitura
            # errada que este log existe para impedir.
            logger.warning(
                "[TMDB] busca recusada (HTTP %s, modo=%s) em %s; segue sem id",
                codigo, self.credential.mode, path,
            )
            return None
        try:
            corpo = resposta.json()
        except Exception:  # noqa: BLE001
            logger.warning("[TMDB] resposta de %s não é JSON; segue sem id", path)
            return None
        return corpo if isinstance(corpo, dict) else None

    # -- busca --------------------------------------------------------------

    def _unique_exact(
        self, results: Sequence[Any], *, kind: str, alvo: str
    ) -> Optional[int]:
        """O id do ÚNICO resultado cujo nome bate exatamente. Senão ``None``.

        A TMDB ordena por popularidade e responde a aproximações — pegar o
        primeiro resultado seria pegar "o mais famoso parecido", que é o
        homônimo com cara de certo. O que sai daqui é igualdade de dobra ou nada.
        """
        campos = _NAME_FIELDS[kind]
        encontrados: set = set()
        for item in results or []:
            if not isinstance(item, dict):
                continue
            identificador = item.get("id")
            if not isinstance(identificador, int) or identificador <= 0:
                continue
            if any(fold(item.get(campo) or "") == alvo for campo in campos):
                encontrados.add(identificador)
        if len(encontrados) == 1:
            return next(iter(encontrados))
        if len(encontrados) > 1:
            logger.info(
                "[TMDB_LOOKUP] %s",
                {"nome": alvo, "kind": kind, "tmdb_id": None, "motivo": "homonimos",
                 "quantos": len(encontrados)},
            )
        return None

    def find(self, kind: str, name: str, year: Optional[int] = None) -> Optional[int]:
        """O `tmdb_id` de `name`, ou ``None``. Uma linha de log por resolução."""
        tipo = (kind or "").strip()
        nome = (name or "").strip()
        alvo = fold(nome)
        if tipo not in _SEARCH_PATH or not alvo:
            return None
        if not self.credential.usable:
            return None

        chave = (tipo, alvo, year)
        if chave in self._cache:
            achado = self._cache[chave]
            logger.info(
                "[TMDB_LOOKUP] %s",
                {"nome": nome, "kind": tipo, "ano": year, "tmdb_id": achado, "cache": True},
            )
            return achado

        params: Dict[str, Any] = {
            "query": nome,
            "language": self.language,
            "include_adult": "false",
            "page": 1,
        }
        parametro_ano = _YEAR_PARAM[tipo]
        if year is not None and parametro_ano:
            params[parametro_ano] = int(year)

        corpo = self._get(_SEARCH_PATH[tipo], params)
        achado = (
            None if corpo is None else self._unique_exact(corpo.get("results"), kind=tipo, alvo=alvo)
        )

        # Falha de transporte também é guardada. Guardar `None` aqui evita
        # repetir a mesma chamada quebrada vinte vezes na mesma matéria; o custo
        # é não reencontrar o id enquanto o processo viver, e é o custo barato.
        self._remember(chave, achado)
        logger.info(
            "[TMDB_LOOKUP] %s",
            {"nome": nome, "kind": tipo, "ano": year, "tmdb_id": achado, "cache": False},
        )
        return achado

    def _remember(
        self, chave: Tuple[str, str, Optional[int]], valor: Optional[int]
    ) -> None:
        if len(self._cache) >= _CACHE_MAX:
            for antiga in list(self._cache)[: _CACHE_MAX // 4]:
                self._cache.pop(antiga, None)
        self._cache[chave] = valor


def lookup_from_config() -> Optional[TmdbLookup]:
    """O buscador, ou ``None`` quando não há credencial utilizável.

    ``None`` **não** é erro: o item continua viajando com nome, exatamente como
    viajava antes desta função existir. O que não pode acontecer é a ausência
    passar em silêncio — por isso o WARNING nomeia a variável que falta.
    """
    from .config import TMDB_ACCESS_TOKEN_VAR, TMDB_CONFIG, tmdb_credential

    valor, modo = tmdb_credential()
    if not valor:
        logger.warning(
            "[TMDB] busca de id DESLIGADA (%s ausente ou em formato desconhecido); "
            "os itens seguem só com nome, e o vínculo casa no máximo a 0.85",
            TMDB_ACCESS_TOKEN_VAR,
        )
        return None
    return TmdbLookup(
        TmdbCredential(value=valor, mode=modo),
        language=str(TMDB_CONFIG.get("language") or "pt-BR"),
        timeout_seconds=float(TMDB_CONFIG.get("timeout_seconds") or 10.0),
    )


__all__ = [
    "TMDB_BASE_URL",
    "TmdbCredential",
    "TmdbLookup",
    "fold",
    "lookup_from_config",
]
