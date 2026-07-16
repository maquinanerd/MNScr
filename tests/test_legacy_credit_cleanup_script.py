from scripts.clean_legacy_rssprime_credit import clean_content


def test_clean_content_removes_legacy_rssprime_credit_suffix():
    content = (
        '<figcaption>Kara e Krypto. <em>Crédito: DC Studios/Warner Bros. Pictures '
        'via RSSPRIME Superfeed.</em></figcaption>'
    )

    cleaned = clean_content(content)

    assert "via RSSPRIME Superfeed" not in cleaned
    assert "Crédito: DC Studios/Warner Bros. Pictures." in cleaned
