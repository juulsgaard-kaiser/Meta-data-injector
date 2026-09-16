"""User-visible edit risk; tool capability and integrity checks still apply."""

from apex_injector.codec_manifest import MetadataSchema

DESCRIPTIVE = {
    "title",
    "creator",
    "artist",
    "description",
    "subject",
    "keywords",
    "rights",
    "copyright",
    "headline",
    "caption-abstract",
    "by-line",
    "comment",
    "album",
    "genre",
    "tit2",
    "tpe1",
    "talb",
    "tcon",
    "tcop",
    "comm",
    "originator",
    "originatorreference",
}
READ_ONLY_GROUPS = {"file", "system", "exiftool", "composite"}


def field_risk(field):
    parts = field.key.split(":")
    name = parts[-1].lower()
    group = parts[0].lower() if len(parts) > 1 else ""
    level = "low"
    reason = "Descriptive metadata with lower compatibility risk. Media integrity checks still apply; retain backups."
    if field.schema == MetadataSchema.EXIFTOOL and group in READ_ONLY_GROUPS:
        level = "read_only"
        reason = "Computed or filesystem information cannot be edited through this application."
    elif (
        field.schema == MetadataSchema.EXIFTOOL and group not in {"xmp-dc", "iptc", "exif", "ifd0"}
    ) or name not in DESCRIPTIVE:
        level = "high"
        reason = (
            "Technical or unfamiliar metadata may change playback, timing, application interpretation, "
            "or compatibility. A readable tag is not necessarily writable; "
            "readback and integrity checks may reject this edit."
        )
    if level == "high":
        if any(token in name for token in ("timereference", "timecode", "timescale", "duration")):
            reason = (
                "Changing timing metadata can move clips out of sync or change their interpreted duration. "
                "Keep the original backup."
            )
        elif any(token in name for token in ("loudness", "truepeak")):
            reason = (
                "Changing loudness measurements can affect broadcast compliance and normalization decisions. "
                "Use measured values."
            )
        elif any(token in name for token in ("date", "time")):
            reason = (
                "Changing dates can affect chronology, asset matching and time-zone interpretation "
                "in other applications."
            )
    return {"schema": field.schema.value, "tag": field.key, "level": level, "reason": reason}


def assess_risks(payload):
    fields = [field_risk(field) for field in payload.fields]
    return {
        "fields": fields,
        "requires_acknowledgement": any(f["level"] == "high" for f in fields),
        "has_read_only": any(f["level"] == "read_only" for f in fields),
    }
