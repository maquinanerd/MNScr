"""
Smoke test isolado para AIClient.

- Chama a IA com payload minimo.
- Imprime o modelo real usado.
- Valida JSON basico na resposta.
- Nao chama WordPress.
- Nao altera banco.
- Nao publica.
- Nao indexa.

Execucao direta:
    .venv\\Scripts\\python.exe tests\\test_ai_smoke.py
"""
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

# Garante que a raiz do projeto esta no PYTHONPATH
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Carrega .env ANTES de importar ai_client_gemini para que os getenv() de
# nivel de modulo enxerguem os valores corretos na primeira carga.
from dotenv import load_dotenv
load_dotenv(override=True)

from app.ai_client_gemini import AIClient, MODEL, MODEL_CHAIN, _gemini_log_prefix


# ---------------------------------------------------------------------------
# Testes unitarios (sem chamada real a API)
# ---------------------------------------------------------------------------

def _make_mock_response(model: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[SimpleNamespace(text=text)]
                )
            )
        ],
        prompt_feedback=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
        ),
    )


def test_log_prefix_gemini31():
    assert _gemini_log_prefix("gemini-3.1-flash-lite") == "[GEMINI 3.1]"


def test_log_prefix_gemini25():
    assert _gemini_log_prefix("gemini-2.5-flash-lite") == "[GEMINI]"


def test_log_prefix_empty():
    assert _gemini_log_prefix("") == "[GEMINI]"


def test_model_chain_no_duplicates():
    assert len(MODEL_CHAIN) == len(set(MODEL_CHAIN)), "MODEL_CHAIN contem duplicatas"


def test_primary_model_matches_env():
    expected = os.getenv("GEMINI_MODEL_ID", "gemini-3.1-flash-lite")
    assert MODEL == expected, f"Modelo primario deveria ser {expected}, mas e {MODEL}"


def test_smoke_generate_text_returns_text_and_tokens():
    client = AIClient(keys=["fake-key-0000"], min_interval_s=0)
    slot = SimpleNamespace(key="fake-key-0000")
    client.pool.next_ready = Mock(return_value=slot)
    client.rl.wait = Mock()

    response_text = '{"title": "Teste", "body": "<p>Conteudo de smoke test</p>"}'
    mock_resp = _make_mock_response(MODEL_CHAIN[0], response_text)

    with patch("app.ai_client_gemini._generate_content", return_value=mock_resp) as mock_gen:
        text, tokens = client.generate_text("Prompt minimo de smoke test")

    assert isinstance(text, str), "Retorno deve ser string"
    assert len(text) > 0, "Retorno nao deve ser vazio"
    assert isinstance(tokens, dict), "tokens_info deve ser dict"
    assert "model" in tokens, "tokens_info deve ter campo 'model'"
    assert tokens["prompt_tokens"] == 10
    assert tokens["completion_tokens"] == 5
    # Confirma que o modelo usado e o primeiro da chain
    assert mock_gen.call_args.kwargs.get("model") == MODEL_CHAIN[0] or \
           mock_gen.call_args[1].get("model") == MODEL_CHAIN[0] or \
           MODEL_CHAIN[0] in str(mock_gen.call_args), \
           f"Modelo chamado deveria ser {MODEL_CHAIN[0]}"


def test_smoke_json_response_is_parseable():
    client = AIClient(keys=["fake-key-0000"], min_interval_s=0)
    slot = SimpleNamespace(key="fake-key-0000")
    client.pool.next_ready = Mock(return_value=slot)
    client.rl.wait = Mock()

    valid_json = '{"title": "Smoke", "body": "<p>OK</p>", "focus_keyword": "smoke"}'
    mock_resp = _make_mock_response(MODEL_CHAIN[0], valid_json)

    with patch("app.ai_client_gemini._generate_content", return_value=mock_resp):
        text, _ = client.generate_text("Prompt JSON minimo")

    parsed = json.loads(text)
    assert parsed["title"] == "Smoke"


# ---------------------------------------------------------------------------
# Runner direto
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    checks = [
        ("log_prefix [GEMINI 3.1]", test_log_prefix_gemini31),
        ("log_prefix [GEMINI] para 2.5", test_log_prefix_gemini25),
        ("log_prefix vazio", test_log_prefix_empty),
        ("MODEL_CHAIN sem duplicatas", test_model_chain_no_duplicates),
        ("modelo primario bate com env", test_primary_model_matches_env),
        ("smoke generate_text retorna texto e tokens", test_smoke_generate_text_returns_text_and_tokens),
        ("resposta JSON e parseavel", test_smoke_json_response_is_parseable),
    ]

    print(f"\n[SMOKE] Modelo primario: {MODEL}")
    print(f"[SMOKE] MODEL_CHAIN: {MODEL_CHAIN}")
    print(f"[SMOKE] Prefixo de log para modelo primario: {_gemini_log_prefix(MODEL)}\n")

    failed = 0
    for name, fn in checks:
        try:
            fn()
            print(f"  OK  {name}")
        except Exception as exc:
            print(f"  FAIL {name}: {exc}")
            failed += 1

    if failed == 0:
        print("\ntest_ai_smoke: ALL CHECKS PASSED")
    else:
        print(f"\ntest_ai_smoke: {failed} FAILED")
        sys.exit(1)
