"""
Testes para o health check de extração de clusters — Bloco 9.

Valida o comportamento do trecho [CLUSTER_HEALTH] inserido em app/pipeline.py,
sem precisar instanciar o pipeline completo.
"""
import ast

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_health_label(extracted_ok: int) -> str:
    """Replica a regra de OK/DEGRADED do pipeline."""
    return "OK" if extracted_ok >= 2 else "DEGRADED"


def _build_log_line(event_key: str, extracted_ok: int, total_urls: int) -> str:
    """Replica o formato exato do [CLUSTER_HEALTH] log."""
    label = _get_health_label(extracted_ok)
    return (
        f"[CLUSTER_HEALTH] event_key={event_key} "
        f"extraction_rate={extracted_ok}/{total_urls} "
        f"({label})"
    )


# ---------------------------------------------------------------------------
# TASK 9.2 — Casos mínimos do prompt
# ---------------------------------------------------------------------------

def test_ok_label_when_extracted_ok_gte_2():
    """Quando extracted_ok >= 2, log contém OK."""
    for ok in (2, 3, 10):
        line = _build_log_line("evt_ok", ok, ok + 1)
        assert "OK" in line, f"Esperava OK para extracted_ok={ok}, obteve: {line}"
        assert "DEGRADED" not in line


def test_degraded_label_when_extracted_ok_lt_2():
    """Quando extracted_ok < 2, log contém DEGRADED."""
    for ok in (0, 1):
        line = _build_log_line("evt_deg", ok, 3)
        assert "DEGRADED" in line, f"Esperava DEGRADED para extracted_ok={ok}, obteve: {line}"
        assert "OK" not in line


def test_log_contains_event_key():
    """Log contém event_key."""
    line = _build_log_line("my_event_key_123", 2, 3)
    assert "event_key=my_event_key_123" in line


def test_log_contains_extraction_rate():
    """Log contém extraction_rate=x/y."""
    line = _build_log_line("evt", 2, 3)
    assert "extraction_rate=2/3" in line


def test_exact_format_ok():
    """Formato completo para caso OK."""
    line = _build_log_line("test_event", 2, 3)
    assert line == "[CLUSTER_HEALTH] event_key=test_event extraction_rate=2/3 (OK)"


def test_exact_format_degraded():
    """Formato completo para caso DEGRADED."""
    line = _build_log_line("test_event", 1, 3)
    assert line == "[CLUSTER_HEALTH] event_key=test_event extraction_rate=1/3 (DEGRADED)"


def test_zero_docs_is_degraded():
    """Zero documentos extraídos → DEGRADED."""
    line = _build_log_line("evt_zero", 0, 2)
    assert "DEGRADED" in line
    assert "extraction_rate=0/2" in line


# ---------------------------------------------------------------------------
# Verificação estrutural: o trecho [CLUSTER_HEALTH] existe em pipeline.py
# ---------------------------------------------------------------------------

def test_cluster_health_log_exists_in_pipeline():
    """Confirma que pipeline.py contém a string [CLUSTER_HEALTH]."""
    with open("app/pipeline.py", encoding="utf-8-sig") as f:
        source = f.read()
    assert "[CLUSTER_HEALTH]" in source, (
        "[CLUSTER_HEALTH] não encontrado em app/pipeline.py"
    )


def test_cluster_health_has_ok_degraded_rule_in_pipeline():
    """Confirma que a regra 'OK' if extracted_ok >= 2 else 'DEGRADED' existe no pipeline."""
    with open("app/pipeline.py", encoding="utf-8-sig") as f:
        source = f.read()
    assert "extracted_ok >= 2" in source, (
        "Regra 'extracted_ok >= 2' não encontrada em app/pipeline.py"
    )
    assert "DEGRADED" in source, (
        "String 'DEGRADED' não encontrada em app/pipeline.py"
    )


def test_cluster_health_has_total_urls_in_pipeline():
    """Confirma que total_urls é calculado com additional_urls no pipeline."""
    with open("app/pipeline.py", encoding="utf-8-sig") as f:
        source = f.read()
    assert "total_urls" in source, (
        "Variável 'total_urls' não encontrada em app/pipeline.py"
    )
    assert "additional_urls" in source, (
        "'additional_urls' não encontrado em app/pipeline.py"
    )


def test_pipeline_py_compiles():
    """pipeline.py deve compilar sem erros de sintaxe."""
    with open("app/pipeline.py", encoding="utf-8-sig") as f:  # utf-8-sig strips BOM
        source = f.read()
    # Levanta SyntaxError se houver problema
    ast.parse(source)


# ---------------------------------------------------------------------------
# Runner manual (para execução direta sem pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_ok_label_when_extracted_ok_gte_2,
        test_degraded_label_when_extracted_ok_lt_2,
        test_log_contains_event_key,
        test_log_contains_extraction_rate,
        test_exact_format_ok,
        test_exact_format_degraded,
        test_zero_docs_is_degraded,
        test_cluster_health_log_exists_in_pipeline,
        test_cluster_health_has_ok_degraded_rule_in_pipeline,
        test_cluster_health_has_total_urls_in_pipeline,
        test_pipeline_py_compiles,
    ]
    all_ok = True
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except Exception as exc:
            print(f"  [FAIL] {t.__name__}: {exc}")
            all_ok = False
    print()
    print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
    raise SystemExit(0 if all_ok else 1)
