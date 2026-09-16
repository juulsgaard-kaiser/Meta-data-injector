"""ID3 text updates shared by WAV and AIFF, with explicit frame handling."""

from mutagen.id3 import COMM, TXXX, Frames

ALIASES = {
    "title": "TIT2",
    "artist": "TPE1",
    "creator": "TPE1",
    "album": "TALB",
    "date": "TDRC",
    "genre": "TCON",
    "copyright": "TCOP",
    "rights": "TCOP",
    "comment": "COMM",
    "description": "COMM",
}


def update_tags(tags, fields):
    for field in fields:
        key = ALIASES.get(field.key.lower(), field.key)
        values = field.value if isinstance(field.value, list) else [field.value]
        values = [str(v) for v in values]
        if key == "COMM":
            tags.add(COMM(encoding=3, lang="eng", desc="", text=values))
        elif key in Frames and key.startswith("T") and key != "TXXX":
            tags.add(Frames[key](encoding=3, text=values))
        elif key.startswith("TXXX:"):
            tags.add(TXXX(encoding=3, desc=key[5:], text=values))
        else:
            raise ValueError(f"Unsupported ID3 field: {field.key}; use TXXX:name for custom text")


def verify_tags(tags, fields):
    for field in fields:
        key = ALIASES.get(field.key.lower(), field.key)
        frames = tags.getall("COMM") if key == "COMM" else tags.getall(key)
        expected = field.value if isinstance(field.value, list) else [field.value]
        expected = [str(v) for v in expected]
        if not any([str(v) for v in getattr(frame, "text", [])] == expected for frame in frames):
            raise ValueError(f"ID3 readback failed: {field.key}")
