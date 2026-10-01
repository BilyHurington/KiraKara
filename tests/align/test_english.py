"""English words in Japanese lyrics (aligned on their letters), the free tail after the last line
(an outro the lyrics do not have), and a retry variant far from its anchor."""

import numpy as np
import pytest

from kara_align.align.ctc import ctc_align, ctc_align_reference
from kara_align.align.decoding import _spelled, split_letters
from kara_align.align.retry import FAR_RESIDUAL_MS, Scored
from kara_align.models import Issue

from .test_rests import rand_logp


def test_english_letters_split_over_the_katakana_units():
    assert split_letters("love", ["ra", "bu"]) == ["lo", "ve"]
    assert split_letters("all", ["o", "", "ru"]) == ["a", "", "ll"]  # ー: no letters
    assert split_letters("you", ["yu", ""]) == ["you", ""]
    assert split_letters("my", ["ma", "i"]) == ["m", "y"]
    assert split_letters("with", ["wi", "zu"]) == ["wi", "th"]
    assert split_letters("spring", ["su", "pu", "ri", "n", "gu"]) == ["s", "p", "ri", "n", "g"]
    assert split_letters("a", ["e", "e"]) in (["a", ""], ["", "a"])  # fewer letters than units: some get none


def test_spelled_letters_keep_their_names():
    assert _spelled("R", "あーる") and _spelled("I", "あい")
    assert _spelled("OK", "おーけー") and _spelled("DJ", "でぃーじぇー")
    assert not _spelled("LOVE", "らぶ") and not _spelled("love", "らぶ") and not _spelled("OK", "おっけー")


def test_english_words_are_aligned_on_their_letters():
    from kara_align.align.decoding import prepare
    from kara_align.lyrics.parse import parse_lyrics_text
    from kara_align.models import Segment, Unit
    from kara_align.reading.profiles import get_profile

    doc = parse_lyrics_text("my love R", mode="plain").doc
    ln = doc.lines[0]
    ln.segments = [Segment(surface="my", reading="まい", units=[Unit(reading="ま"), Unit(reading="い")]),
                   Segment(surface=" ", units=[]),
                   Segment(surface="love", reading="らぶ", units=[Unit(reading="ら"), Unit(reading="ぶ")]),
                   Segment(surface=" ", units=[]),
                   Segment(surface="R", reading="あーる", units=[Unit(reading="あーる")])]
    seen = {}

    def tokenize(ids, texts):
        from kara_align.interfaces import TokenizedUnit

        seen.update(zip(ids, texts))
        return [TokenizedUnit(i, [1] * max(1, len(t)), []) for i, t in zip(ids, texts)]

    prep = prepare(doc, get_profile("ja-hepburn"), tokenize)
    assert [seen[u.id] for s in ln.segments for u in s.units] == ["m", "y", "lo", "ve", "aru"]
    assert all(prep.units[u.id].english for u in ln.segments[2].units) and not prep.units[ln.segments[4].units[0].id].english


@pytest.mark.parametrize("seed", range(6))
def test_free_tail_vectorised_matches_reference(seed):
    rng = np.random.default_rng(seed)
    logp = rand_logp(40, 5, seed)
    targets = [int(x) for x in rng.integers(1, 5, size=4)]
    a = ctc_align(logp, targets, 0, tail_penalty=1.0)
    b = ctc_align_reference(logp, targets, 0, tail_penalty=1.0)
    assert [(s.start_frame, s.end_frame) for s in a.spans] == [(s.start_frame, s.end_frame) for s in b.spans]
    assert np.array_equal(a.states, b.states)


def test_singing_after_the_last_line_is_not_given_to_it():
    # tokens 1 2 sung at frames 2–5, then 30 frames of "other singing" (token 3, not in the lyrics)
    T, V = 40, 4
    logp = np.full((T, V), np.log(0.01))
    for t, k in [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2), (5, 2), (6, 0), (7, 0)] + [(t, 3) for t in range(8, T)]:
        logp[t, k] = np.log(0.97)
    logp[8:, 2] = np.log(0.02)  # the outro sounds a little like the last token, less like silence
    forced = ctc_align(logp, [1, 2], 0)
    free = ctc_align(logp, [1, 2], 0, tail_penalty=1.0)
    assert forced.spans[1].end_frame > 8  # forced: the last token is stretched over the outro
    assert [(s.start_frame, s.end_frame) for s in free.spans] == [(2, 4), (4, 6)]


def test_a_variant_far_from_its_anchor_counts_as_an_error():
    class O:  # a stand-in outcome
        def __init__(self, res):
            self.feasible, self.res, self.mean_acoustic = True, res, None

        def residual(self, _):
            return self.res

    warn = Issue(code="short_unit", severity="warning", line_id="L", message="")
    near = Scored(O(200), [warn, warn], 1.0)
    far = Scored(O(FAR_RESIDUAL_MS + 1000), [Issue(code="anchor_deviation", severity="warning", line_id="L", message="")], 1.0)
    assert near.better_than(far, "L") and not far.better_than(near, "L")
