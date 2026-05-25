"""Token counting via tiktoken. Encoders are cached at module level for speed."""

import tiktoken

_ENCODERS: dict[str, tiktoken.Encoding] = {}


def _get_encoder(name: str) -> tiktoken.Encoding:
    enc = _ENCODERS.get(name)
    if enc is None:
        enc = tiktoken.get_encoding(name)
        _ENCODERS[name] = enc
    return enc


def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    """Count tokens using tiktoken. Encoders are cached per-encoding at module level."""
    if not text:
        return 0
    return len(_get_encoder(encoding).encode(text))
