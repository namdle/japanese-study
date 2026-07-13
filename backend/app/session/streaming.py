"""Incremental sentence segmentation for the streaming voice pipeline.

The tutor's reply streams in as text deltas. To overlap TTS with LLM
generation we cut the stream at sentence boundaries (。！？!? and newline)
and synthesize each sentence as soon as it completes.

The reply may end with [HIRAGANA]/[EN] reading-aid marker lines. Those are
*text-pipeline* content and must never reach TTS or the incremental display,
so segmentation stops the moment a marker appears; the full raw text
(including markers) is kept for parse_tutor_reply() at the end.
"""

from __future__ import annotations

# Sentence enders: Japanese and ASCII terminal punctuation, plus newline.
_SENTENCE_ENDERS = "。！？!?\n"

# Every marker prefix parse_tutor_reply understands (plus [JA], which some
# replies use to re-introduce the Japanese body).
_AID_MARKERS = (
    "[HIRAGANA]",
    "【HIRAGANA】",
    "[ひらがな]",
    "【ひらがな】",
    "[EN]",
    "[ENGLISH]",
    "【EN】",
    "【ENGLISH】",
    "[JA]",
    "【JA】",
)


class SentenceStreamer:
    """Feed text deltas in; get completed, speakable sentences out.

    - feed(delta) returns the sentences completed by this delta.
    - flush() returns any speakable remainder once the stream ends.
    - full_text is everything received, markers included.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._full: list[str] = []
        self._in_aids = False

    @property
    def full_text(self) -> str:
        return "".join(self._full)

    def feed(self, delta: str) -> list[str]:
        self._full.append(delta)
        if self._in_aids:
            return []
        self._buf += delta

        # Stop speakable output at the first reading-aid marker.
        marker_at = min(
            (i for i in (self._buf.find(m) for m in _AID_MARKERS) if i != -1),
            default=-1,
        )
        if marker_at != -1:
            speakable = self._buf[:marker_at]
            self._buf = ""
            self._in_aids = True
            sentences = self._split_complete(speakable, final=True)
            return sentences

        sentences = self._split_complete(self._buf, final=False)
        return sentences

    def flush(self) -> str | None:
        """Return the trailing speakable fragment (no ender), if any."""
        if self._in_aids:
            return None
        rest = self._buf.strip()
        self._buf = ""
        return rest or None

    def _split_complete(self, text: str, *, final: bool) -> list[str]:
        """Split off complete sentences; keep the tail in the buffer unless
        this is the final (pre-marker) chunk, in which case emit it too."""
        sentences: list[str] = []
        start = 0
        for i, ch in enumerate(text):
            if ch in _SENTENCE_ENDERS:
                piece = text[start : i + 1].strip()
                if piece:
                    sentences.append(piece)
                start = i + 1
        tail = text[start:]
        if final:
            tail = tail.strip()
            if tail:
                sentences.append(tail)
            self._buf = ""
        else:
            self._buf = tail
        return sentences
