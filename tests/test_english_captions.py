#!/usr/bin/env python3
"""
Test script para validar a remoção de captions em inglês.
"""

from bs4 import BeautifulSoup

from app.extractor import _clean_english_captions, _is_likely_english_caption


def test_caption_detection():
    """Testa a detecção de captions em inglês."""
    test_cases = [
        # (caption_text, expected_result, description)
        ("jonathan majors as kang in ant man and the wasp quantumania", True, "Example 1 from user"),
        ("original avengers from the battle of new york", True, "Example 2 from user"),
        ("A atriz Scarlett Johansson em Viúva Negra", False, "Portuguese caption"),
        ("O Homem de Ferro voando pelo céu", False, "Portuguese caption"),
        ("Tom Holland in Spider-Man", True, "Simple English caption"),
        ("Iron Man combate villains em New York", False, "Mixed but Portuguese-heavy"),
        ("", False, "Empty caption"),
        ("X", False, "Too short"),
        ("ABC DEF GHI", False, "Non-words"),
        ("The Avengers assemble for battle", True, "Typical English pattern"),
        ("Robert Downey Jr. as Tony Stark", True, "Actor name pattern"),
    ]

    print("=" * 80)
    print("TESTE: Detecção de Captions em Inglês")
    print("=" * 80)

    passed = 0
    failed = 0

    for caption, expected, description in test_cases:
        result = _is_likely_english_caption(caption)
        status = "✓" if result == expected else "✗"

        if result == expected:
            passed += 1
        else:
            failed += 1

        print(f"\n{status} {description}")
        print(f"  Caption: '{caption}'")
        print(f"  Expected: {expected}, Got: {result}")

    print("\n" + "=" * 80)
    print(f"Resultados: {passed} passaram, {failed} falharam")
    print("=" * 80)

    # `assert`, e nao `return`: um teste que DEVOLVE o resultado nao reprova nada: o pytest ignora o retorno,
    # entao a suite ficava verde mesmo com o contador de falhas diferente de zero.
    assert failed == 0, f"{failed} caso(s) de legenda falharam"


def test_caption_cleaning():
    """Testa a limpeza de captions em HTML."""

    html_with_captions = """
    <article>
        <figure>
            <img src="https://example.com/image1.jpg" alt="">
            <figcaption>jonathan majors as kang in ant man and the wasp quantumania</figcaption>
        </figure>
        <p>Some article text here.</p>
        <figure>
            <img src="https://example.com/image2.jpg" alt="">
            <figcaption>O vilão Kang aparece no filme</figcaption>
        </figure>
        <figure>
            <img src="https://example.com/image3.jpg" alt="">
            <figcaption>original avengers from the battle of new york</figcaption>
        </figure>
    </article>
    """

    soup = BeautifulSoup(html_with_captions, 'html.parser')
    article = soup.find('article')

    print("\n" + "=" * 80)
    print("TESTE: Limpeza de Captions em Inglês no HTML")
    print("=" * 80)

    print("\nAntes da limpeza:")
    for i, fig in enumerate(article.find_all('figure'), 1):
        cap = fig.find('figcaption')
        caption_text = cap.get_text(strip=True) if cap else "SEM LEGENDA"
        print(f"  Figura {i}: {caption_text}")

    # Aplica a limpeza
    _clean_english_captions(article, "TestDomain")

    print("\nDepois da limpeza:")
    for i, fig in enumerate(article.find_all('figure'), 1):
        cap = fig.find('figcaption')
        caption_text = cap.get_text(strip=True) if cap else "SEM LEGENDA"
        status = "(vazia - removida)" if not caption_text else "(preservada)"
        print(f"  Figura {i}: {caption_text} {status}")

    # Validação: English captions devem estar vazias, Portuguese captions preservadas
    captions_list = [fig.find('figcaption').get_text(strip=True) for fig in article.find_all('figure')]

    success = (
        captions_list[0] == "" and  # English - deve estar vazio
        captions_list[1] == "O vilão Kang aparece no filme" and  # Portuguese - preservado
        captions_list[2] == ""  # English - deve estar vazio
    )

    print("\n" + "=" * 80)
    if success:
        print("✓ Limpeza de captions funcionou corretamente!")
    else:
        print("✗ Limpeza de captions falhou!")
        print("  Esperado: ['', 'O vilão Kang aparece no filme', '']")
        print(f"  Obtido: {captions_list}")
    print("=" * 80)

    assert success, "limpeza de legenda em ingles reprovou"


if __name__ == "__main__":
    print("\n🧪 Iniciando testes de English Caption Filtering...\n")

    test1_ok = test_caption_detection()
    test2_ok = test_caption_cleaning()

    if test1_ok and test2_ok:
        print("\n✓ Todos os testes passaram!")
        exit(0)
    else:
        print("\n✗ Alguns testes falharam!")
        exit(1)
