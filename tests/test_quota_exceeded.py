"""Rotacao de chave Gemini quando uma retorna 429 (quota excedida).

Substitui o script legado que instanciava um AIClient real no import, carregava
o .env e fazia patch do SDK descontinuado ``google.generativeai`` — ele nao
declarava nenhum teste, engolia toda excecao e portanto nunca podia falhar.

Aqui o alvo e o seam real (``_generate_content``), sem rede, sem .env e sem
espera real de backoff.
"""

import socket
import types

import pytest
from google.api_core import exceptions as google_exceptions

import app.ai_client_gemini as gemini
from app.ai_client_gemini import AIClient

# Deliberadamente fora do formato real de chave Gemini: o guard de seguranca
# procura o prefixo AIza em todo o repositorio, e um valor de teste que o
# imite seria indistinguivel de um vazamento.
KEY_A = "fake-gemini-key-for-tests-aaaa-1111"
KEY_B = "fake-gemini-key-for-tests-bbbb-2222"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("Teste de rotacao de chave tentou acessar a rede")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Backoff e cooldown sao reais em producao; num teste sao so espera."""
    monkeypatch.setattr(gemini.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr("app.limiter.time.sleep", lambda *_a, **_k: None)


@pytest.fixture
def client() -> AIClient:
    return AIClient(keys=[KEY_A, KEY_B], min_interval_s=0, backoff_base=0, backoff_max=0)


def fake_response(text="Conteudo gerado", prompt_tokens=10, completion_tokens=20):
    """Resposta no formato que ``_extract_text`` e a captura de tokens esperam."""
    part = types.SimpleNamespace(text=text)
    content = types.SimpleNamespace(parts=[part])
    candidate = types.SimpleNamespace(content=content, finish_reason=None, safety_ratings=[])
    usage = types.SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=completion_tokens,
        cached_content_token_count=0,
    )
    return types.SimpleNamespace(
        candidates=[candidate], usage_metadata=usage, prompt_feedback=None, text=text
    )


def install_generate(monkeypatch, behaviour):
    """Substitui a chamada real ao Gemini, registrando as chaves usadas."""
    used_keys = []

    def _fake(api_key, prompt, model=None, **kwargs):
        used_keys.append(api_key)
        return behaviour(api_key, len(used_keys))

    monkeypatch.setattr(gemini, "_generate_content", _fake)
    return used_keys


# ---------------------------------------------------------------------------
# Rotacao em 429
# ---------------------------------------------------------------------------


def test_quota_exceeded_rotates_to_another_key(monkeypatch, client):
    """A chave que estourou a quota nao pode ser a que conclui a chamada."""
    exhausted = KEY_A

    def behaviour(api_key, _call):
        if api_key == exhausted:
            raise google_exceptions.ResourceExhausted("Quota exceeded")
        return fake_response()

    used = install_generate(monkeypatch, behaviour)
    text, _tokens = client.generate_text("Prompt de teste")

    assert text == "Conteudo gerado"
    assert used[-1] != exhausted, "concluiu com a chave que estourou a quota"
    assert KEY_B in used, "nunca chegou a tentar a segunda chave"


def test_exhausted_key_is_penalized(monkeypatch, client):
    def behaviour(api_key, _call):
        if api_key == KEY_A:
            raise google_exceptions.ResourceExhausted("Quota exceeded")
        return fake_response()

    install_generate(monkeypatch, behaviour)
    client.generate_text("Prompt de teste")

    cooling = [slot for slot in client.pool.slots if slot.cooldown_until > 0]
    assert [slot.key for slot in cooling] == [KEY_A]


def test_a_plain_429_without_the_typed_exception_also_rotates(monkeypatch, client):
    """Nem todo 429 chega como ResourceExhausted; o codigo numerico tambem conta."""

    class Plain429(Exception):
        code = 429

    def behaviour(api_key, _call):
        if api_key == KEY_A:
            raise Plain429("429 quota")
        return fake_response()

    used = install_generate(monkeypatch, behaviour)
    text, _ = client.generate_text("Prompt de teste")

    assert text == "Conteudo gerado"
    assert used[-1] == KEY_B


def test_success_on_the_first_key_does_not_rotate(monkeypatch, client):
    used = install_generate(monkeypatch, lambda key, n: fake_response())
    client.generate_text("Prompt de teste")
    assert len(used) == 1


def test_every_key_exhausted_raises_instead_of_returning_empty(monkeypatch, client):
    """Sem chave utilizavel, falhar alto e melhor que devolver texto vazio."""

    def always_exhausted(api_key, _call):
        raise google_exceptions.ResourceExhausted("Quota exceeded")

    install_generate(monkeypatch, always_exhausted)

    with pytest.raises(Exception):
        client.generate_text("Prompt de teste")


# ---------------------------------------------------------------------------
# Tokens e mascaramento
# ---------------------------------------------------------------------------


def test_tokens_are_returned_alongside_the_text(monkeypatch, client):
    install_generate(monkeypatch, lambda key, n: fake_response())
    _text, tokens = client.generate_text("Prompt de teste")
    assert tokens["prompt_tokens"] == 10
    assert tokens["completion_tokens"] == 20


def test_last_used_key_is_reported_masked(monkeypatch, client):
    install_generate(monkeypatch, lambda key, n: fake_response())
    client.generate_text("Prompt de teste")

    reported = client.get_last_used_key()
    assert reported.startswith("****")
    assert len(reported) == 8, "a chave nao pode aparecer alem dos 4 ultimos digitos"
    assert KEY_A not in reported and KEY_B not in reported
