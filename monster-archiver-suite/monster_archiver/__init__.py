"""Monster Archiver package.

Import order here is load-bearing:
1. config/state load first (state.CONF is populated) — bootstrap needs
   state.CONF.get("DEBUG_MODE") for its ffmpeg-injection error branch.
2. bootstrap runs immediately after — self-installs missing dependencies and
   injects ffmpeg onto PATH — before anything else in the package (or a
   caller) imports a heavy third-party package like torch/librosa/demucs.
"""
from . import config
from . import state

VERSION = config.VERSION

state.CONF = config.validate_config(config.load_config())

from . import bootstrap  # noqa: E402  (must run after CONF is populated, before any other submodule)
