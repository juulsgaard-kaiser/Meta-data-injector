"""Independent container/packet validation through FFprobe, without decoding."""

import hashlib
import subprocess
import sys
import tempfile

from apex_injector.config import get_config


def packet_fingerprint(path):
    tool = get_config().tools.ffprobe
    if not tool:
        raise ValueError("FFprobe is required to verify this container")
    # Packet output can be enormous; spool to disk and hash in bounded chunks.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        proc = subprocess.run(
            [
                tool,
                "-v",
                "error",
                "-show_packets",
                "-show_streams",
                "-show_data_hash",
                "sha256",
                "-show_entries",
                "packet=stream_index,pts,dts,duration,size,data_hash:stream=index,codec_name,codec_type,codec_tag_string,width,height,sample_rate,channels,extradata_hash:stream_tags=:stream_disposition=:stream_side_data=:packet_side_data=",
                "-of",
                "compact",
                str(path),
            ],
            stdout=output,
            stderr=errors,
            timeout=3600,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        errors.seek(0)
        error = errors.read(4096)
        if proc.returncode or error:
            raise ValueError(f"FFprobe could not validate media: {error.decode(errors='replace')}")
        output.seek(0)
        h = hashlib.sha256()
        packets = 0
        for line in output:
            if line.startswith(b"packet|"):
                if b"data_hash=SHA256:" not in line:
                    raise ValueError("FFprobe omitted a packet hash")
                packets += 1
            h.update(line)
        if not packets:
            raise ValueError("FFprobe found no media packets")
        return h.hexdigest()
