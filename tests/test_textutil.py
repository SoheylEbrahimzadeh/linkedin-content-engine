from lce.textutil import (
    canonical,
    claim_numbers,
    content_hash,
    count_emojis,
    hashtags,
    jaccard,
    normalize_text,
    shingles,
    slugify,
    words,
)


def test_hash_is_stable_across_line_endings_and_trailing_space():
    assert content_hash("a \r\nb\n\n") == content_hash("a\nb")
    assert content_hash("a\nb") != content_hash("a\nc")


def test_normalize():
    assert normalize_text("  x  \n\n") == "x\n"


def test_canonical_ignores_case_punctuation_hashtags_urls():
    assert canonical("Hello, World! #ai https://x.example/a") == canonical("hello world")


def test_claim_numbers():
    text = "1. First\nWe saved 37% in 2024, about 3 lessons, 1,200 tickets, 4.5x faster, 12 days."
    nums = claim_numbers(text)
    assert "37" in nums and "1200" in nums and "4.5" in nums and "12" in nums
    assert "2024" not in nums and "3" not in nums and "1" not in nums


def test_emoji_hashtag_words_slug():
    assert count_emojis("ok 🚀 fine ✅") == 2
    assert hashtags("#ai and #rpa but not a#b") == ["#ai", "#rpa"]
    assert words("It's AI-driven.") == ["it's", "ai-driven"]
    assert slugify("Über AI: 2025!") == "uber-ai-2025"


def test_similarity():
    a = shingles(words("the quick brown fox jumps"), 3)
    assert jaccard(a, a) == 1.0
    assert jaccard(a, shingles(words("completely different text here"), 3)) == 0.0
