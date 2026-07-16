from app.seo_title_optimizer import optimize_title


def test_optimize_title_does_not_remove_pt_br_modal_verb():
    title = "Spider-Man: Brand New Day pode ter 150 minutos de duração"

    optimized, report = optimize_title(title)

    assert optimized == title
    assert "pode" in optimized.lower()
    assert any("pode" in change for change in report["changes_made"])
