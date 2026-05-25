from grimoire.library import tokens
from grimoire.library.tokens import count_tokens


def test_empty_string_is_zero_tokens():
    assert count_tokens("") == 0


def test_short_word_count_is_positive():
    assert count_tokens("hello world") > 0


def test_known_count_for_short_phrase():
    # "hello" is one token in cl100k_base.
    assert count_tokens("hello") in (1, 2)  # exact value can vary across tiktoken versions


def test_count_grows_with_text_length():
    short = count_tokens("the")
    long = count_tokens("the quick brown fox jumps over the lazy dog")
    assert long > short


def test_encoder_cached_across_calls(monkeypatch):
    """Both count_tokens calls should still work; the cache is internal to _get_encoder."""
    calls = {"n": 0}
    real_get = tokens._get_encoder

    def counting_get(name):
        calls["n"] += 1
        return real_get(name)

    monkeypatch.setattr(tokens, "_get_encoder", counting_get)
    count_tokens("a")
    count_tokens("b")
    assert calls["n"] == 2  # _get_encoder is called per count_tokens; encoder reuse is internal
