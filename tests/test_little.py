import pytest

from lce.publish.little import RESERVED, from_little, to_little, unescaped_reserved


@pytest.mark.parametrize("ch", sorted(RESERVED - {"#"}))
def test_every_reserved_character_is_escaped(ch):
    assert to_little(f"a{ch}b") == f"a\\{ch}b"


def test_hashtags_stay_hashtags_other_hash_signs_are_escaped():
    assert to_little("#Gardening and #Tomatoes") == "#Gardening and #Tomatoes"
    assert to_little("issue#12 and # alone") == "issue\\#12 and \\# alone"
    assert to_little("line\n#tag") == "line\n#tag"


def test_real_world_sentence_round_trips():
    s = ("A report by Example Org (fictional): 7% [data] <ops> a_b ~ c*d "
         "@x | {y} \\ path\n\n#Gardening #Compost")
    little = to_little(s)
    assert from_little(little) == s
    assert unescaped_reserved(little) == []
    assert unescaped_reserved(s)  # the raw text would not be safe


def test_plain_text_is_unchanged():
    assert to_little("Plain text, with commas. And 42%!") == "Plain text, with commas. And 42%!"
