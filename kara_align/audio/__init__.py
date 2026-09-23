"""Audio processing: decoding, resampling, separation, sync checks and mixing."""

from .analysis import peaks_json, rms_envelope_db, waveform_peaks
from .io import (
    AudioError,
    file_sha256,
    import_asset,
    load_audio,
    probe_audio,
    sniff_audio_format,
    validate_upload,
    write_wav,
)
from .mix import MIX_RULE_JS, MixError, MixReport, export_mix_wav, mix_stems
from .resample import resample, to_mono
from .separation import PRESETS, SeparationError, SeparationOutput, fix_stem_length, separate
from .sync import SyncError, check_stem_sync, derive_instrumental, estimate_lag

__all__ = [
    "AudioError", "file_sha256", "import_asset", "load_audio", "probe_audio", "sniff_audio_format",
    "validate_upload", "write_wav", "resample", "to_mono", "MIX_RULE_JS", "MixError", "MixReport",
    "export_mix_wav", "mix_stems", "PRESETS", "SeparationError", "SeparationOutput", "fix_stem_length",
    "separate", "SyncError", "check_stem_sync", "derive_instrumental", "estimate_lag",
    "peaks_json", "rms_envelope_db", "waveform_peaks",
]
