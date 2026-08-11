"""Contrato do Cinerie: artefatos, hash, validacao e compatibilidade real.

O teste central deste arquivo e o de **compatibilidade**: as fixtures que o
proprio Cinerie publicou no commit `f4c49c4` sao rodadas contra a validacao
Python. Uma fixture valida do Cinerie precisa ser aceita aqui, e cada um dos 17
contraexemplos precisa ser recusado — cada um por UM motivo isolado.

Isso importa mais do que parece: metade dos contraexemplos passaria batido por um
validador que fosse **so** JSON Schema, porque as regras que os pegam vivem em
``superRefine`` do Zod e sao descartadas na geracao do schema. O teste e o que
impede a reimplementacao em ``refinements.py`` de envelhecer em silencio.

Tudo offline. Nenhum socket e aberto: a fixture ``_no_network`` garante isso.
"""

import hashlib
import json
import socket

import pytest

from app.cinerie.contract import (
    CONTRACT_NAME,
    CONTRACTS_DIR,
    compute_schema_hash,
    load_fixtures,
    load_manifest,
    load_schema,
    load_source_record,
    local_identity,
    manifest_entry,
    schema_bytes,
)
from app.cinerie.errors import ContractArtifactError, ForbiddenFieldError, SchemaValidationError
from app.cinerie.forbidden import (
    find_duplicate_seo_key,
    find_forbidden_key,
    find_forbidden_publication_key,
    find_forbidden_seo_key,
    normalize_key,
)
from app.cinerie.policy import AUTO_PUBLISH_INELIGIBLE, BLOCKING, WARNING, seo_policy
from app.cinerie.refinements import check_iso_datetime, check_markup_everywhere, check_request
from app.cinerie.validation import check_identity, ensure_valid, validate_request

#: O hash que o Screen-App declarou ao entregar o pacote. NAO e usado pelo codigo
#: de producao — la o valor e recalculado do arquivo. Aqui ele existe para provar
#: que o que foi copiado e o que foi prometido.
DELIVERED_SCHEMA_HASH = "sha256:930243294465802778f73151d53ee510a2313d44673de9e6e7866032bfe6c6f8"

#: Commit do Screen-App contra o qual o snapshot foi revalidado.
#:
#: O hash acima NAO mudou de `f4c49c4` para `4279abd`, e isso e um fato sobre o
#: contrato, nao sorte: `blocks.ts` e `common.ts` mudaram entre os dois, mas por
#: acrescimo, e o acrescimo serve ao corpo PUBLICADO. Ver `revalidation` em
#: contracts/cinerie/SOURCE.json para os blobs comparados.
SOURCE_COMMIT = "4279abd"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de contrato do Cinerie tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def fixtures():
    return load_fixtures()


@pytest.fixture
def valid_request(fixtures):
    return json.loads(json.dumps(fixtures["valid"]["publicationRequest"]))


# ===========================================================================
# Artefatos
# ===========================================================================


def test_all_contract_artifacts_are_present():
    for name in (
        "editorial-publication-request-v1.schema.json",
        "contract-manifest.json",
        "seo-policy.json",
        "SOURCE.json",
        "fixtures/publication-fixtures.json",
    ):
        assert (CONTRACTS_DIR / name).is_file(), f"artefato ausente: {name}"


def test_schema_is_draft_2020_12():
    assert load_schema()["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_schema_hash_is_recomputed_from_the_file_bytes():
    assert compute_schema_hash(schema_bytes()) == DELIVERED_SCHEMA_HASH


def test_local_identity_matches_the_delivered_package():
    identity = local_identity()
    assert identity.contract_name == CONTRACT_NAME
    assert identity.contract_version == "1.0.0"
    assert identity.schema_hash == DELIVERED_SCHEMA_HASH
    assert identity.source_commit == SOURCE_COMMIT


def test_manifest_and_file_agree():
    assert manifest_entry()["schemaHash"] == compute_schema_hash(schema_bytes())


def test_manifest_lists_the_other_cinerie_contracts():
    names = {entry["contractName"] for entry in load_manifest()}
    assert CONTRACT_NAME in names
    assert "publication-event-v1" in names


def test_source_record_points_at_the_delivered_commit():
    record = load_source_record()
    assert record["sourceCommitShort"] == SOURCE_COMMIT
    assert record["schemaHash"] == DELIVERED_SCHEMA_HASH


def test_snapshot_files_agree_on_the_source_commit():
    """Os dois arquivos do snapshot apontam para o MESMO commit de origem.

    E a regressao de um re-snapshot pela metade: atualizar o `SOURCE.json` e
    esquecer o manifesto (ou o contrario) produz um par que se contradiz sem
    quebrar nada — ate alguem depurar uma divergencia de contrato lendo o
    arquivo errado.
    """
    manifesto = json.loads((CONTRACTS_DIR / "contract-manifest.json").read_text(encoding="utf-8"))
    registro = load_source_record()

    assert manifesto["sourceCommit"].startswith(SOURCE_COMMIT)
    assert registro["sourceCommit"].startswith(SOURCE_COMMIT)


def test_the_request_hash_survived_the_move_to_the_new_commit():
    """`f4c49c4` -> `4279abd` nao mexeu no contrato do PEDIDO.

    Nao e sorte, e vale registrar por que: `blocks.ts` e `common.ts` mudaram
    entre os dois commits, e ambos entram na geracao deste schema. As mudancas
    sao aditivas e servem ao corpo PUBLICADO — `blocks.ts` ganhou `textMark` e
    `publishedEditorialBlock` DEPOIS de `editorialBody`, que e o unico simbolo
    que o pedido importa de la.

    Se um dia o hash mudar, este teste falha aqui — e nao em producao, com todo
    pedido em voo virando 409 CONFLICT no instante do deploy.
    """
    assert compute_schema_hash(schema_bytes()) == DELIVERED_SCHEMA_HASH
    assert manifest_entry()["schemaHash"] == DELIVERED_SCHEMA_HASH
    assert local_identity().source_commit == SOURCE_COMMIT


def test_schema_bytes_are_canonical():
    """Os bytes sao a serializacao canonica — e por isso o hash confere.

    Se alguem reformatar o arquivo com `json.dumps(indent=2)`, o conteudo
    continua o mesmo e o **hash muda**. Este teste falha antes de o preflight
    comecar a recusar tudo em producao.
    """
    reserialized = json.dumps(
        load_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(reserialized).hexdigest() == DELIVERED_SCHEMA_HASH.split(":")[1]


def test_divergent_manifest_is_refused(tmp_path, monkeypatch):
    """Hash do arquivo != hash do manifesto e defeito de repositorio."""
    import app.cinerie.contract as contract_module

    broken = tmp_path / "cinerie"
    (broken / "fixtures").mkdir(parents=True)
    (broken / "editorial-publication-request-v1.schema.json").write_bytes(schema_bytes())
    (broken / "contract-manifest.json").write_text(
        json.dumps(
            {
                "contracts": [
                    {
                        "contractName": CONTRACT_NAME,
                        "contractVersion": "1.0.0",
                        "schemaHash": "sha256:" + "0" * 64,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (broken / "SOURCE.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(contract_module, "CONTRACTS_DIR", broken)
    for cached in (
        contract_module.load_schema,
        contract_module.schema_bytes,
        contract_module.load_manifest,
        contract_module.load_source_record,
        contract_module.local_identity,
    ):
        cached.cache_clear()

    with pytest.raises(ContractArtifactError, match="nao confere"):
        contract_module.local_identity()

    for cached in (
        contract_module.load_schema,
        contract_module.schema_bytes,
        contract_module.load_manifest,
        contract_module.load_source_record,
        contract_module.local_identity,
    ):
        cached.cache_clear()


def test_missing_artifact_is_named_not_guessed(tmp_path, monkeypatch):
    import app.cinerie.contract as contract_module

    monkeypatch.setattr(contract_module, "CONTRACTS_DIR", tmp_path / "vazio")
    contract_module.load_schema.cache_clear()
    with pytest.raises(ContractArtifactError, match="editorial-publication-request-v1.schema.json"):
        contract_module.load_schema()
    contract_module.load_schema.cache_clear()


# ===========================================================================
# Compatibilidade com as fixtures do Cinerie
# ===========================================================================


def test_cinerie_publishes_four_valid_fixtures(fixtures):
    assert set(fixtures["valid"]) == {
        "publicationRequest",
        "publicationUpdate",
        "routedToReviewRequest",
        "conflictingUpdateRequest",
    }


@pytest.mark.parametrize(
    "name",
    ["publicationRequest", "publicationUpdate", "routedToReviewRequest", "conflictingUpdateRequest"],
)
def test_valid_cinerie_fixture_is_accepted_in_python(fixtures, name):
    report = validate_request(fixtures["valid"][name], identity=local_identity())
    assert report.ok, report.summary()


def test_every_invalid_cinerie_fixture_is_refused(fixtures):
    """Os 17 contraexemplos, todos recusados. Nenhum passa.

    O controle POSITIVO acima (fixtures validas aceitas) e o que impede este
    teste de ser vacuoso: um validador que recusasse tudo passaria aqui e
    falharia la.
    """
    survivors = [
        name
        for name, payload in fixtures["invalid"].items()
        if validate_request(payload, identity=local_identity()).ok
    ]
    assert survivors == [], f"fixtures invalidas aceitas indevidamente: {survivors}"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("yoastField", "_yoast_wpseo_title"),
        ("wordpressPostStatus", "post_status"),
        ("canonicalFromProducer", "canonical"),
        ("bypassReview", "bypassReview"),
        ("clientDeclaredTechnicalActor", "technicalActorId"),
        ("clientDeclaredServiceAccount", "serviceAccountId"),
        ("clientDeclaredScopes", "scopes"),
        ("nestedTechnicalActor", "publishedBy"),
        ("duplicateSeoProposal", "seoProposal"),
        ("looseSeoField", "metaDescription"),
    ],
)
def test_forbidden_key_is_named_in_the_message(fixtures, name, expected):
    """A recusa NOMEIA o campo. "propriedade adicional nao permitida" nao ensina."""
    report = validate_request(fixtures["invalid"][name], identity=local_identity())
    assert not report.ok
    assert report.forbidden_key == expected


@pytest.mark.parametrize(
    "name,path",
    [
        ("noExternalSources", "externalSources"),
        ("qaFailedWithoutErrors", "qa.blockingErrors"),
        ("updateWithoutTarget", "targetArticleId"),
        ("altForUnknownMedia", "seo.imageAltSuggestions[0].mediaRef"),
        ("metaTooShort", "seo.metaDescription"),
    ],
)
def test_refinement_only_fixtures_are_caught(fixtures, name, path):
    """Estas cinco NAO sao pegas pelo JSON Schema — so pelos refinamentos.

    O `superRefine` do Zod nao e representavel em JSON Schema e desaparece na
    geracao. Sem ``refinements.py`` as cinco sairiam daqui e morreriam no
    servidor, com a materia ja escrita.
    """
    from app.cinerie.validation import _schema_issues

    payload = fixtures["invalid"][name]
    schema_only = _schema_issues(payload)
    assert schema_only == [], f"{name} deveria passar pelo JSON Schema puro"

    report = validate_request(payload, identity=local_identity())
    assert not report.ok
    assert path in report.paths


def test_incompatible_schema_hash_needs_the_identity_check(fixtures):
    """A fixture do hash incompativel e ESTRUTURALMENTE perfeita.

    Ela atravessa o JSON Schema inteiro e todos os refinamentos: o formato
    `sha256:<64 hex>` esta correto. O que esta errado e a identidade — o caso
    mais perigoso, porque parece certo.
    """
    payload = fixtures["invalid"]["incompatibleSchemaHash"]
    assert validate_request(payload).ok, "sem checagem de identidade ela passa"
    assert not validate_request(payload, identity=local_identity()).ok


def test_identity_check_reports_a_truncated_hash(fixtures):
    issues = check_identity(fixtures["invalid"]["incompatibleSchemaHash"], local_identity())
    assert len(issues) == 1
    # 64 hexadecimais num log nao ajudam ninguem.
    assert "0123456789abcdef0123" not in issues[0].message


# ===========================================================================
# Varreduras de campo
# ===========================================================================


@pytest.mark.parametrize(
    "variant", ["post_status", "postStatus", "POST-STATUS", "Post_Status", "poststatus"]
)
def test_key_normalization_catches_every_spelling(variant):
    assert find_forbidden_seo_key({variant: "publish"}) is not None


def test_normalize_key_drops_separators_and_case():
    assert normalize_key("_yoast_wpseo-Title") == "yoastwpseotitle"


def test_publication_scan_is_deep(valid_request):
    payload = dict(valid_request)
    payload["provenance"] = {**payload["provenance"], "serviceAccountId": "sa-1"}
    finding = find_forbidden_publication_key(payload)
    assert finding is not None
    assert finding.path == "provenance.serviceAccountId"
    assert finding.group == "IDENTIDADE_TECNICA"


def test_seo_duplicate_scan_is_top_level_only(valid_request):
    """`metaDescription` DENTRO de `seo` e o campo real; no topo, e duplicata.

    Varrer fundo recusaria o proprio objeto valido — foi exatamente o que
    aconteceu na primeira versao desta lista, do lado do Cinerie.
    """
    assert find_duplicate_seo_key(valid_request) is None
    assert find_duplicate_seo_key({**valid_request, "metaDescription": "x"}) is not None


def test_identity_group_explains_why(valid_request):
    finding = find_forbidden_key({**valid_request, "publishedBy": "sa-1"})
    assert "credencial autenticada" in finding.reason


def test_state_group_explains_why(valid_request):
    finding = find_forbidden_key({**valid_request, "bypassReview": True})
    assert "estado publico" in finding.reason


def test_valid_request_has_no_forbidden_key(valid_request):
    """Controle NEGATIVO: sem ele a varredura poderia recusar tudo e passar."""
    assert find_forbidden_key(valid_request) is None


# ===========================================================================
# Refinamentos
# ===========================================================================


def test_markup_is_refused_anywhere(valid_request):
    payload = dict(valid_request)
    payload["blocks"] = [{**payload["blocks"][0], "text": "texto com <script>alert(1)</script>"}]
    issues = check_markup_everywhere(payload)
    assert any("script" in issue.message for issue in issues)


def test_clean_request_has_no_markup_issue(valid_request):
    assert check_markup_everywhere(valid_request) == []


def test_javascript_scheme_is_refused():
    issues = check_markup_everywhere({"anchor": "javascript:alert(1)"})
    assert issues and "javascript" in issues[0].message


@pytest.mark.parametrize(
    "value,ok",
    [
        ("2026-07-29T12:00:00.000Z", True),
        ("2026-07-29T12:00:00-03:00", True),
        ("2026-07-29T12:00:00", False),
        ("ontem", False),
        ("", False),
    ],
)
def test_iso_datetime_requires_explicit_timezone(value, ok):
    assert (check_iso_datetime(value, "generatedAt") == []) is ok


def test_duplicate_block_id_is_a_contract_error(valid_request):
    payload = dict(valid_request)
    payload["blocks"] = [
        {"type": "paragraph", "id": "b1", "text": "um"},
        {"type": "paragraph", "id": "b1", "text": "dois"},
    ]
    assert any("duplicado" in issue.message for issue in check_request(payload))


def test_publish_cannot_declare_a_target(valid_request):
    payload = {**valid_request, "targetArticleId": "article-1"}
    assert any(issue.path == "targetArticleId" for issue in check_request(payload))


def test_related_keyphrase_cannot_repeat_the_focus(valid_request):
    payload = json.loads(json.dumps(valid_request))
    payload["seo"]["relatedKeyphrases"] = ["Data De Estreia"]
    issues = check_request(payload)
    assert any("repetir a focusKeyphrase" in issue.message for issue in issues)


def test_internal_link_needs_a_target(valid_request):
    payload = json.loads(json.dumps(valid_request))
    payload["seo"]["internalLinkSuggestions"] = [{"targetType": "article", "anchorText": "veja"}]
    assert any("targetId ou targetPath" in issue.message for issue in check_request(payload))


# ===========================================================================
# ensure_valid
# ===========================================================================


def test_ensure_valid_returns_the_payload(valid_request):
    assert ensure_valid(valid_request) is valid_request


def test_ensure_valid_raises_forbidden_for_a_cms_field(valid_request):
    with pytest.raises(ForbiddenFieldError):
        ensure_valid({**valid_request, "post_status": "publish"})


def test_ensure_valid_raises_schema_error_with_paths(valid_request):
    payload = json.loads(json.dumps(valid_request))
    payload["seo"]["metaDescription"] = "curta"
    with pytest.raises(SchemaValidationError) as exc:
        ensure_valid(payload)
    assert "seo.metaDescription" in exc.value.paths


def test_validation_error_never_echoes_the_content(valid_request):
    """Um erro de validacao que ecoa o payload vira vazamento de materia inedita."""
    payload = json.loads(json.dumps(valid_request))
    secret = "TEXTO INEDITO QUE NAO PODE VAZAR"
    payload["seo"]["metaDescription"] = secret
    with pytest.raises(SchemaValidationError) as exc:
        ensure_valid(payload)
    assert secret not in str(exc.value)


# ===========================================================================
# Politica de SEO
# ===========================================================================


def test_policy_comes_from_the_contract_not_from_literals():
    policy = seo_policy()
    assert (policy.title.min, policy.title.max) == (15, 120)
    assert (policy.title.auto_min, policy.title.auto_max) == (15, 65)
    assert (policy.meta_description.min, policy.meta_description.max) == (70, 320)
    assert (policy.meta_description.auto_min, policy.meta_description.auto_max) == (120, 160)
    assert (policy.meta_description.editorial_min, policy.meta_description.editorial_max) == (140, 155)


@pytest.mark.parametrize(
    "length,expected",
    [
        (10, BLOCKING),
        (15, None),
        (65, None),
        (66, AUTO_PUBLISH_INELIGIBLE),
        (120, AUTO_PUBLISH_INELIGIBLE),
        (121, BLOCKING),
    ],
)
def test_title_transport_versus_auto_publication(length, expected):
    """Transporte e autopublicacao sao faixas DIFERENTES, de proposito."""
    assert seo_policy().title.classify(length) == expected


@pytest.mark.parametrize(
    "length,expected",
    [
        (69, BLOCKING),
        (70, AUTO_PUBLISH_INELIGIBLE),
        (119, AUTO_PUBLISH_INELIGIBLE),
        (120, WARNING),
        (140, None),
        (155, None),
        (160, WARNING),
        (161, AUTO_PUBLISH_INELIGIBLE),
        (320, AUTO_PUBLISH_INELIGIBLE),
        (321, BLOCKING),
    ],
)
def test_meta_description_bands(length, expected):
    assert seo_policy().meta_description.classify(length) == expected


def test_schema_matrix_matches_the_contract():
    policy = seo_policy()
    assert policy.schema_types_for("news") == ("NewsArticle", "Article")
    assert policy.schema_types_for("review") == ("Review", "Article")
    assert policy.schema_types_for("guide") == ("HowTo", "Article")
    assert policy.schema_types_for("list") == ("ItemList", "Article")
    assert policy.schema_types_for("interview") == ("Article",)


def test_content_types_include_review():
    assert "review" in seo_policy().content_types
