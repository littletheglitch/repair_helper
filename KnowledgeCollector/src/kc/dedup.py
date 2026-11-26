"""Deduplication: exact, normalized, near-duplicate, cross-source.

Dedup strategy:

1. **Exact**: SHA-256 of the original text. If an existing record has
   the same content_hash, the new record is a duplicate. Cross-source
   duplicates keep the first source's URL and add the later URL to the
   first record's `also_seen_at` list (never delete the original).

2. **Normalized**: SHA-256 of the normalized text (lowercase, strip
   whitespace + punctuation). Catches whitespace/punctuation variants
   that have the same content_hash == different.

3. **Near-duplicate**: Jaccard similarity ≥ 0.9 on 3-shingle sets.
   Used for paraphrases. The record keeps both texts in a `variants`
   list; the dedup never deletes unique measurements (the variant text
   is preserved alongside the original).

4. **Thread/post identity**: `(source_name, thread_id, post_id)` unique;
   re-crawling a thread updates in place.

The dedup module is a pure function over text + hashes; the DB helpers
in `database.py` do the lookup.
"""
from __future__ import annotations

from typing import List, Set, Tuple


def shingles(text: str, k: int = 3) -> Set[str]:
    """Return the set of k-word shingles of the text."""
    words = text.split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def jaccard_3shingle(a: str, b: str) -> float:
    """Jaccard similarity over 3-word shingle sets."""
    sa = shingles(a, 3)
    sb = shingles(b, 3)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def near_duplicate(a: str, b: str, threshold: float = 0.9) -> bool:
    """True if a and b are near-duplicates (Jaccard >= threshold)."""
    return jaccard_3shingle(a, b) >= threshold


def dedup_decision(new_text: str, new_hash: str, new_norm_hash: str,
                   existing_exact: dict = None,
                   existing_near: dict = None,
                   threshold: float = 0.9) -> Tuple[str, str]:
    """Decide what to do with a new record given any existing duplicates.

    Returns (status, reason):
      - ("new", "")                -> store as new
      - ("duplicate", "exact hash match in source X") -> cross-source dup
      - ("near_duplicate", "jaccard=0.93")           -> near-dup variant
    """
    if existing_exact:
        return ("duplicate",
                f"exact content_hash match in source {existing_exact.get('source_name','?')}")
    if existing_near:
        j = jaccard_3shingle(new_text, existing_near.get("text", ""))
        return ("near_duplicate", f"jaccard={j:.2f}")
    return ("new", "")
