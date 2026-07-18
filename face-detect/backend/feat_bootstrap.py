"""Bootstrap py-feat import on Windows without libtorchcodec.

Our backend only accepts JPEG frames over WebSocket and never decodes video files.
py-feat still imports torchcodec via feat.data -> decode_video at import time, which
fails when FFmpeg shared DLLs are missing in the FACE conda env.
"""
from __future__ import annotations

import sys
import types


def ensure_feat_importable() -> None:
    if "torchcodec" in sys.modules:
        return

    decoders = types.ModuleType("torchcodec.decoders")
    decoders.VideoDecoder = object

    torchcodec = types.ModuleType("torchcodec")
    torchcodec.decoders = decoders

    sys.modules["torchcodec"] = torchcodec
    sys.modules["torchcodec.decoders"] = decoders


ensure_feat_importable()
