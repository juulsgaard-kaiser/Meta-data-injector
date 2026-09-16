import shutil
import subprocess

import pytest

from apex_injector.config import AppConfig, set_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APEX_DATA_DIR", str(tmp_path / "config"))
    config = AppConfig(data_dir=tmp_path / "config")
    config.engine.hash_algorithm = "sha256"
    config.engine.max_workers = 2
    set_config(config)
    yield config
    set_config(None)


@pytest.fixture
def media(tmp_path):
    def make(extension):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("FFmpeg is required for real-media integration tests")
        path = tmp_path / ("sample." + extension)
        args = [ffmpeg, "-v", "error", "-y", "-f", "lavfi"]
        if extension in ("mp4", "mov", "mkv"):
            args += ["-i", "color=c=blue:s=32x32:d=0.2", "-c:v", "mpeg4"]
        else:
            args += ["-i", "sine=frequency=440:duration=0.2"]
        subprocess.run(args + [str(path)], check=True)
        return path

    return make
