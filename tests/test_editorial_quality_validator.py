"""
tests/test_editorial_quality_validator.py

Testes unitarios para app.editorial_validator.

Execucao direta:
    .venv\\Scripts\\python.exe tests\\test_editorial_quality_validator.py

Execucao via pytest:
    .venv\\Scripts\\python.exe -m pytest tests/test_editorial_quality_validator.py -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.editorial_validator import (
    validate_editorial_quality,
    detect_content_type,
    build_editorial_audit_report,
    _image_stack_detected,
    _cta_residual_in_content,
    _title_has_html,
    _excerpt_equals_meta,
    _has_h1_inside,
    _sources_skipped_in_credit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_html(words: int = 600, h2_count: int = 2, extra: str = "") -> str:
    """Gera HTML minimo com quantidade de palavras e H2 configuravel."""
    h2s = "".join(f"<h2>Secao {i + 1} do artigo editorial aqui</h2><p>{'Texto da secao. ' * 40}</p>" for i in range(h2_count))
    filler = " ".join(["palavra"] * max(0, words - h2_count * 50))
    return f"<p>{filler}</p>{h2s}{extra}"


def _make_payload(
    title: str = "Titulo de teste editorial do artigo",
    content_html: str = "",
    meta: str = "Meta description de teste com tamanho adequado para SEO e validacao editorial automatica.",
    subtitle: str = "Subtitle editorial diferente da meta e do titulo para validacao correta.",
    sources_skipped: list = None,
    subtitle_status: str = "OK",
    meta_status: str = "OK",
) -> dict:
    return {
        "title": title,
        "content_html": content_html or _make_html(600),
        "meta": meta,
        "subtitle": subtitle,
        "sources_skipped": sources_skipped or [],
        "subtitle_status": subtitle_status,
        "meta_status": meta_status,
    }


# ---------------------------------------------------------------------------
# TASK 16.1 — Artigo curto bloqueia quando formato exige profundidade
# ---------------------------------------------------------------------------

def test_content_too_thin_listicle_blocks():
    """Lista com 500 palavras e abaixo do minimo de 1100 — deve bloquear."""
    content = _make_html(words=500, h2_count=4)
    payload = _make_payload(
        title="Os 10 melhores filmes de super-heroi de todos os tempos",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert not result["ok"], "Lista curta nao bloqueou"
    assert any("CONTENT_TOO_THIN" in i for i in result["issues"])


def test_content_ok_news_500w():
    """Noticia com 500+ palavras e 2 H2 deve passar."""
    content = _make_html(words=520, h2_count=2)
    payload = _make_payload(
        title="Marvel anuncia novo filme para 2027",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert result["ok"] or all("CONTENT_TOO_THIN" not in i for i in result["issues"])


def test_guide_thin_blocks():
    """Guia com 600 palavras bloqueia (minimo 1400)."""
    content = _make_html(words=600, h2_count=3)
    payload = _make_payload(
        title="Guia completo para assistir o MCU em ordem cronologica",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert any("CONTENT_TOO_THIN" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# TASK 16.2 — Lista com imagens empilhadas bloqueia
# ---------------------------------------------------------------------------

def test_image_stack_detected_blocks():
    img_stack_html = (
        "<p>Introducao editorial com conteudo.</p>"
        "<h2>Item 1</h2><p>Texto do item um.</p>"
        "<figure><img src='a.jpg' alt='img1'/></figure>"
        "<figure><img src='b.jpg' alt='img2'/></figure>"
        "<h2>Item 2</h2><p>Texto do item dois.</p>"
    )
    content = img_stack_html + "".join(f"<h2>Secao {i}</h2><p>{'Texto longo ' * 60}</p>" for i in range(3, 10))
    payload = _make_payload(
        title="Os 7 melhores filmes de 2025 segundo a critica",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert result["image_stack_detected"], "IMAGE_STACK nao detectado"
    assert any("IMAGE_STACK_DETECTED" in i for i in result["issues"])
    assert not result["ok"]


def test_no_image_stack_single_images():
    content = (
        "<p>Introducao editorial.</p>"
        "<figure><img src='a.jpg'/></figure>"
        "<h2>Secao 1</h2><p>Texto da primeira secao com bastante conteudo editorial.</p>"
        "<figure><img src='b.jpg'/></figure>"
        "<h2>Secao 2</h2><p>Texto da segunda secao com bastante conteudo editorial relevante.</p>"
    ) + "".join(f"<h2>Secao {i}</h2><p>{'Texto editorial. ' * 50}</p>" for i in range(3, 6))
    payload = _make_payload(
        title="Os 5 melhores filmes de faccao do MCU em ranking",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert not result["image_stack_detected"], "Falso positivo de image_stack"


# ---------------------------------------------------------------------------
# TASK 16.3 — Lista com H2 por item passa na estrutura
# ---------------------------------------------------------------------------

def test_listicle_with_h2_per_item_passes_structure():
    items = "".join(
        f"<h2>{i + 1}. Item da lista aqui com titulo descritivo</h2><p>{'Texto do item com analise. ' * 50}</p>"
        for i in range(7)
    )
    content = f"<p>Introducao contextual da lista editorial.</p>{items}<p>Conclusao da lista.</p>"
    payload = _make_payload(
        title="Os 7 melhores filmes de acao de 2025 segundo a critica especializada",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert result["h2_count"] >= 7
    assert not any("NO_H2" in i for i in result["issues"])
    assert not any("INSUFFICIENT_H2" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# TASK 16.4 — CTA residual bloqueia
# ---------------------------------------------------------------------------

def test_cta_residual_blocks():
    content = _make_html(600) + "<p>Subscribe to our newsletter for more updates!</p>"
    payload = _make_payload(content_html=content)
    result = validate_editorial_quality(payload)
    assert any("CTA_RESIDUAL_DETECTED" in i for i in result["issues"])
    assert not result["ok"]


def test_cta_residual_sign_up_blocks():
    content = _make_html(600) + "<p>Sign up now to get exclusive content</p>"
    payload = _make_payload(content_html=content)
    result = validate_editorial_quality(payload)
    assert any("CTA_RESIDUAL_DETECTED" in i for i in result["issues"])


def test_no_cta_clean_content():
    content = _make_html(600)
    payload = _make_payload(content_html=content)
    result = validate_editorial_quality(payload)
    assert not any("CTA_RESIDUAL_DETECTED" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# TASK 16.5 — Titulo com HTML bloqueia
# ---------------------------------------------------------------------------

def test_title_with_html_blocks():
    payload = _make_payload(title="<b>Titulo com HTML</b> que nao pode publicar")
    result = validate_editorial_quality(payload)
    assert any("TITLE_HAS_HTML" in i for i in result["issues"])
    assert not result["ok"]
    assert result["title_status"] == "INVALID"


def test_clean_title_passes():
    payload = _make_payload(title="Titulo limpo sem HTML para publicacao")
    result = validate_editorial_quality(payload)
    assert not any("TITLE_HAS_HTML" in i for i in result["issues"])
    assert result["title_status"] == "OK"


# ---------------------------------------------------------------------------
# TASK 16.6 — Meta longa regenera (sem bloquear publicacao)
# ---------------------------------------------------------------------------

def test_meta_too_long_logged_not_blocking():
    long_meta = "A" * 165
    payload = _make_payload(meta=long_meta, meta_status="OK")
    result = validate_editorial_quality(payload)
    # Meta longa gera issue mas nao bloca por si so
    assert any("META_TOO_LONG" in i for i in result["issues"])


def test_meta_ok_range():
    meta = "Meta description de teste com tamanho ideal para SEO, contendo entre 120 e 155 caracteres, validado pelo Maquina Nerd editorial."
    assert 120 <= len(meta) <= 160, f"meta tem {len(meta)} chars, ajuste a string"
    payload = _make_payload(meta=meta)
    result = validate_editorial_quality(payload)
    assert not any("META_TOO_LONG" in i or "META_TOO_SHORT" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# TASK 16.7 — Excerpt igual a meta bloqueia/regenera
# ---------------------------------------------------------------------------

def test_subtitle_equals_meta_blocks():
    meta = "Esta e a meta description exata do artigo para validacao."
    payload = _make_payload(meta=meta, subtitle=meta)
    result = validate_editorial_quality(payload)
    assert any("SUBTITLE_EQUALS_META" in i for i in result["issues"])
    assert not result["ok"]


def test_subtitle_equals_title_blocks():
    title = "Titulo que e igual ao subtitle"
    payload = _make_payload(title=title, subtitle=title)
    result = validate_editorial_quality(payload)
    assert any("SUBTITLE_EQUALS_TITLE" in i for i in result["issues"])
    assert not result["ok"]


def test_subtitle_distinct_from_meta_and_title_passes():
    payload = _make_payload(
        title="Titulo unico do artigo editorial",
        meta="Meta description diferente do titulo e do subtitle com conteudo proprio.",
        subtitle="Subtitle editorial diferente da meta e do titulo para validacao.",
    )
    result = validate_editorial_quality(payload)
    assert not any("SUBTITLE_EQUALS_META" in i for i in result["issues"])
    assert not any("SUBTITLE_EQUALS_TITLE" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# TASK 16.8 — Schema ItemList recomendado para listicle
# ---------------------------------------------------------------------------

def test_schema_itemlist_for_listicle():
    items = "".join(
        f"<h2>{i + 1}. Item {i + 1}</h2><p>{'Texto. ' * 60}</p>"
        for i in range(7)
    )
    content = f"<p>Introducao.</p>{items}"
    payload = _make_payload(
        title="Os 7 melhores filmes de ficcao cientifica de todos os tempos",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert result["content_type"] == "listicle", f"tipo detectado: {result['content_type']}"
    assert result["schema_recommendation"] == "ITEMLIST"


def test_schema_newsarticle_for_news():
    content = _make_html(500, h2_count=2)
    payload = _make_payload(
        title="Marvel anuncia novo diretor para Homem-Aranha 4",
        content_html=content,
    )
    result = validate_editorial_quality(payload)
    assert result["schema_recommendation"] == "NEWSARTICLE"


# ---------------------------------------------------------------------------
# TASK 16.9 — H1 dentro do corpo bloqueia
# ---------------------------------------------------------------------------

def test_h1_inside_content_blocks():
    content = "<h1>Titulo dentro do corpo — proibido</h1>" + _make_html(600, h2_count=2)
    payload = _make_payload(content_html=content)
    result = validate_editorial_quality(payload)
    assert any("H1_INSIDE_CONTENT" in i for i in result["issues"])
    assert not result["ok"]


def test_no_h1_inside_content_ok():
    content = _make_html(600, h2_count=2)
    payload = _make_payload(content_html=content)
    result = validate_editorial_quality(payload)
    assert not any("H1_INSIDE_CONTENT" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# TASK 16.10 — sources_skipped no credito bloqueia
# ---------------------------------------------------------------------------

def test_sources_skipped_in_credit_blocks():
    # collider.com foi skipped mas aparece no corpo
    content = _make_html(600) + "<p>Fonte: collider.com publicou esta informacao.</p>"
    skipped = [{"domain": "collider.com", "status": "PAYWALLED"}]
    payload = _make_payload(content_html=content, sources_skipped=skipped)
    result = validate_editorial_quality(payload)
    assert any("SOURCES_SKIPPED_IN_CREDIT" in i for i in result["issues"])
    assert not result["ok"]


def test_sources_skipped_not_in_credit_ok():
    content = _make_html(600)
    skipped = [{"domain": "collider.com", "status": "PAYWALLED"}]
    payload = _make_payload(content_html=content, sources_skipped=skipped)
    result = validate_editorial_quality(payload)
    assert not any("SOURCES_SKIPPED_IN_CREDIT" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# Testes extras de deteccao de tipo
# ---------------------------------------------------------------------------

def test_detect_listicle_by_title():
    ct = detect_content_type("Os 10 melhores personagens do MCU", "")
    assert ct == "listicle"


def test_detect_guide_by_title():
    ct = detect_content_type("Como assistir o MCU em ordem cronologica", "")
    assert ct == "guide"


def test_detect_news_short_text():
    ct = detect_content_type("Marvel anuncia novo filme", "Texto factual curto.")
    assert ct == "news"


# ---------------------------------------------------------------------------
# Teste do relatorio de auditoria
# ---------------------------------------------------------------------------

def test_audit_report_contains_required_sections():
    payload = _make_payload()
    result = validate_editorial_quality(payload)
    report = build_editorial_audit_report(payload, result, wp_post_id=12345)
    assert "# Auditoria editorial" in report
    assert "## Identificacao" in report
    assert "## SEO" in report
    assert "## Conteudo" in report
    assert "## Decisao" in report
    assert "12345" in report


# ---------------------------------------------------------------------------
# Runner direto
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    checks = [
        ("listicle curta bloqueia", test_content_too_thin_listicle_blocks),
        ("noticia 500w ok", test_content_ok_news_500w),
        ("guia 600w bloqueia", test_guide_thin_blocks),
        ("image stack bloqueia", test_image_stack_detected_blocks),
        ("sem image stack ok", test_no_image_stack_single_images),
        ("listicle com H2 por item ok", test_listicle_with_h2_per_item_passes_structure),
        ("CTA subscribe bloqueia", test_cta_residual_blocks),
        ("CTA sign up bloqueia", test_cta_residual_sign_up_blocks),
        ("sem CTA ok", test_no_cta_clean_content),
        ("titulo com HTML bloqueia", test_title_with_html_blocks),
        ("titulo limpo ok", test_clean_title_passes),
        ("meta longa registra issue", test_meta_too_long_logged_not_blocking),
        ("meta ok sem issue", test_meta_ok_range),
        ("subtitle = meta bloqueia", test_subtitle_equals_meta_blocks),
        ("subtitle = titulo bloqueia", test_subtitle_equals_title_blocks),
        ("subtitle diferente ok", test_subtitle_distinct_from_meta_and_title_passes),
        ("schema ITEMLIST para listicle", test_schema_itemlist_for_listicle),
        ("schema NEWSARTICLE para news", test_schema_newsarticle_for_news),
        ("H1 no corpo bloqueia", test_h1_inside_content_blocks),
        ("sem H1 no corpo ok", test_no_h1_inside_content_ok),
        ("sources_skipped no credito bloqueia", test_sources_skipped_in_credit_blocks),
        ("sources_skipped ausente ok", test_sources_skipped_not_in_credit_ok),
        ("detecta listicle por titulo", test_detect_listicle_by_title),
        ("detecta guide por titulo", test_detect_guide_by_title),
        ("detecta news por texto curto", test_detect_news_short_text),
        ("relatorio tem secoes obrigatorias", test_audit_report_contains_required_sections),
    ]

    failed = 0
    for name, fn in checks:
        try:
            fn()
            print(f"  OK  {name}")
        except Exception as exc:
            print(f"  FAIL {name}: {exc}")
            failed += 1

    if failed == 0:
        print(f"\ntest_editorial_quality_validator: ALL CHECKS PASSED ({len(checks)}/{len(checks)})")
    else:
        print(f"\ntest_editorial_quality_validator: {failed} FAILED")
        sys.exit(1)
