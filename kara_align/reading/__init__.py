"""Reading preparation: rules, manual edits, transliteration profiles, AI round trip."""

from .ai import PatchReport, PromptBundle, apply_patch, build_prompt, extract_json, validate_patch
from .japanese import split_morae, to_hiragana
from .prepare import (capability_warnings, prepare_doc, prepare_line, resegment_line,
                      set_segment_reading)
from .profiles import get_profile

__all__ = [
    "PatchReport", "PromptBundle", "apply_patch", "build_prompt", "extract_json", "validate_patch",
    "split_morae", "to_hiragana", "capability_warnings", "prepare_doc", "prepare_line",
    "resegment_line", "set_segment_reading", "get_profile",
]
