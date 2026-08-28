"""AcoustID audio fingerprinting — identifies a track from raw audio and
returns the raw fingerprint too, so callers can store it for acoustic
duplicate detection without computing it a second time.
"""
import acoustid

from . import state
from . import ui


def get_metadata_via_fingerprint(file_path):
    """Fingerprint a file via AcoustID.  Returns (metadata_dict_or_None, raw_fingerprint_or_None).
    The raw fingerprint is passed back so callers can store it for acoustic duplicate detection
    without computing the fingerprint a second time."""
    raw_fingerprint = None
    try:
        duration, fingerprint = acoustid.fingerprint_file(file_path)
        raw_fingerprint = fingerprint
        # No API key configured — skip the lookup (would fail anyway) but still return the fingerprint for duplicate detection.
        if not state.CONF.get("ACOUSTID_API_KEY"):
            return None, raw_fingerprint
        res = acoustid.lookup(state.CONF["ACOUSTID_API_KEY"], fingerprint, duration,
                              meta='recordings+tracks+artists')
        if res.get('results'):
            top = res['results'][0]
            score = top.get('score', 0.0)
            # Reject matches scoring below 0.6 — low confidence risks silently tagging the track with wrong metadata.
            if score < 0.6:
                if state.CONF.get("DEBUG_MODE"):
                    ui.log(f"AcoustID: low-confidence match discarded (score={score:.2f})", "yellow")
                return None, raw_fingerprint
            recordings = top.get('recordings', [])
            if recordings:
                best = recordings[0]
                return {
                    "title": best.get("title"),
                    "artist": best.get("artists", [{}])[0].get("name")
                }, raw_fingerprint
    except Exception as e:
        if state.CONF.get("DEBUG_MODE"):
            ui.log(f"AcoustID Err: {e}", "red")
    return None, raw_fingerprint
