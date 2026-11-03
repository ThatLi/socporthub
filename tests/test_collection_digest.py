from bot.notifications import _format_todays_collections


def collection(cca, requester, title, time="10:00", **amounts):
    base = {"plates": 0, "cups": 0, "bowls": 0, "forks": 0, "spoons": 0}
    return {"cca": cca, "requester": requester, "title": title, "time": time, **base, **amounts}


def test_no_collections_means_no_digest():
    assert _format_todays_collections([]) is None


def test_digest_groups_by_cca_with_subtotals_and_shows_requesters():
    text = _format_todays_collections([
        collection("Block 1", "@ling", "Movie night", cups=10),
        collection("block 1 ", "@sam", "Quiz", time="09:00", cups=5, plates=2),
        collection("personal", "@alex", "Birthday", plates=4),
    ])
    assert text.count("<b>Block 1</b>") == 1  # capitalization and spacing do not split a group
    assert "• 09:00 — @sam: <b>Quiz</b>: 2 plates, 5 cups" in text
    assert "• 10:00 — @ling: <b>Movie night</b>: 10 cups" in text
    assert "<b>personal</b>" in text and "• 10:00 — @alex: <b>Birthday</b>: 4 plates" in text
    assert "<i>Subtotal: 2 plates, 15 cups</i>" in text  # Block 1 group
    assert "<i>Subtotal: 4 plates</i>" in text  # personal group
    assert "<b>Total: 6 plates, 15 cups</b>" in text
    assert text.index("<b>Block 1</b>") < text.index("<b>personal</b>")


def test_special_characters_are_escaped():
    text = _format_todays_collections([collection("R&D <club>", "@x", "A & B", cups=1)])
    assert "R&amp;D &lt;club&gt;" in text and "A &amp; B" in text
