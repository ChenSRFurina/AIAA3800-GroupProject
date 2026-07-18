"""Use bundled weights under ../model before hitting Hugging Face Hub."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_DIR = PROJECT_ROOT / "model"

# (repo_id, filename) -> path relative to model dir
LOCAL_MODEL_FILES: dict[tuple[str, str], str] = {
    ("py-feat/retinaface_r34", "model.safetensors"): "model.safetensors",
    ("py-feat/retinaface_r34", "retinaface_r34.safetensors"): "model.safetensors",
    ("py-feat/face_multitask_v2", "face_multitask_v2.safetensors"): "face_multitask_v2.safetensors",
}

_PATCHED = False


def get_model_dir() -> Path:
    override = os.getenv("FEAT_LOCAL_MODEL_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_MODEL_DIR


def resolve_local_model(repo_id: str, filename: str) -> str | None:
    mapped = LOCAL_MODEL_FILES.get((repo_id, filename))
    if mapped is None:
        return None

    candidate = get_model_dir() / mapped
    if candidate.is_file():
        return str(candidate)

    return None


def list_local_models() -> dict[str, str | None]:
    model_dir = get_model_dir()
    return {
        "model_dir": str(model_dir),
        "retinaface_r34": resolve_local_model("py-feat/retinaface_r34", "model.safetensors"),
        "face_multitask_v2": resolve_local_model(
            "py-feat/face_multitask_v2", "face_multitask_v2.safetensors"
        ),
    }


def apply_local_model_overrides() -> None:
    """Patch huggingface_hub downloads to prefer ../model files."""
    global _PATCHED
    if _PATCHED:
        return

    env_retinaface = os.getenv("FEAT_RETINAFACE_WEIGHTS")
    if env_retinaface and Path(env_retinaface).is_file():
        os.environ.setdefault(
            "FEAT_RETINAFACE_WEIGHTS",
            str(Path(env_retinaface).resolve()),
        )

    env_multitask = os.getenv("FEAT_MULTITASK_WEIGHTS")
    if env_multitask and Path(env_multitask).is_file():
        os.environ["FEAT_MULTITASK_WEIGHTS"] = str(Path(env_multitask).resolve())

    multitask_local = resolve_local_model(
        "py-feat/face_multitask_v2", "face_multitask_v2.safetensors"
    )
    if multitask_local and not os.getenv("FEAT_MULTITASK_WEIGHTS"):
        os.environ["FEAT_MULTITASK_WEIGHTS"] = multitask_local

    import huggingface_hub

    original_download = huggingface_hub.hf_hub_download

    def patched_hf_hub_download(*args, **kwargs):
        repo_id = kwargs.get("repo_id")
        filename = kwargs.get("filename")
        if repo_id is None and len(args) >= 2:
            repo_id = args[0]
            filename = args[1]
        if repo_id and filename:
            local_path = resolve_local_model(str(repo_id), str(filename))
            if local_path:
                return local_path
        return original_download(*args, **kwargs)

    huggingface_hub.hf_hub_download = patched_hf_hub_download
    _PATCHED = True
