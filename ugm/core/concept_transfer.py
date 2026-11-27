"""Small reviewed lexicon for observation-only concept transfer.

Neither custom concept descriptions nor stored controls become suggestions.
These labels are hypotheses, not game logic or translations.
"""
from ..memory.selector_evidence import locate_exact_label, normalize_label
from .types import OCRItem

_LABELS = {
    "pause_toggle": ("pause", "paused", "unpause", "resume"),
    "cancel_or_back": ("cancel", "back", "close"),
    "cancel_dialog": ("cancel", "dismiss"),
    "confirm_dialog": ("confirm", "accept", "apply"),
    "game_speed": ("game speed", "simulation speed"),
}


def eligible_labels(concept_id: str, source_label: str) -> tuple[str, ...]:
    labels = _LABELS.get(concept_id)
    if labels is None:
        raise ValueError("Unsupported concept for observation transfer.")
    if normalize_label(source_label) not in labels:
        raise ValueError("Source label must belong to the reviewed concept lexicon.")
    return labels


def target_match(labels: tuple[str, ...], items: list[OCRItem]) -> tuple[str, str | None]:
    if len(items) > 512:
        return "target_insufficient", None
    match = None
    uncertain_cue = False
    for label in labels:
        result = locate_exact_label(label, items)
        if result.status == "ambiguous" or (match is not None and result.status == "found"):
            return "target_ambiguous", None
        if result.status == "found":
            match = label
        elif result.status == "inconclusive":
            uncertain_cue = True
    if match is not None and uncertain_cue:
        return "target_ambiguous", None
    return ("candidate", match) if match is not None else ("target_insufficient", None)
