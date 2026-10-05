"""Image embeddings, and where they are kept.

Vectors are derived data under the T1 rule, stored float16 in month shards under
data/vectors/<embedder tag>/. A different embedder writes to a different directory,
so vectors from two models can never be mixed by accident.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from . import config


class OpenClipEmbedder:
    """Pinned OpenCLIP model on CPU. Weights download from the Hugging Face hub on first use."""

    def __init__(self, model: str = config.EMBED_MODEL, pretrained: str = config.EMBED_PRETRAINED):
        import open_clip
        import torch
        self._torch = torch
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(model, pretrained=pretrained)
        self.model.eval()
        self.tag = config.EMBED_TAG

    def embed(self, img: Image.Image) -> np.ndarray:
        with self._torch.no_grad():
            x = self.preprocess(img.convert("RGB")).unsqueeze(0)
            v = self.model.encode_image(x).float().numpy()[0]
        return v / np.linalg.norm(v)


class FakeEmbedder:
    """Deterministic stand-in for tests: a fixed projection of simple colour statistics."""

    def __init__(self, dim: int = 16, seed: int = 7):
        self.tag = "fake"
        self._proj = np.random.default_rng(seed).normal(size=(9, dim))

    def embed(self, img: Image.Image) -> np.ndarray:
        a = np.asarray(img.convert("RGB").resize((32, 32)), dtype=np.float64) / 255.0
        stats = np.concatenate([a.mean(axis=(0, 1)), a.std(axis=(0, 1)), np.percentile(a.mean(axis=2), [10, 50, 90])])
        v = stats @ self._proj
        return v / (np.linalg.norm(v) or 1.0)


class VectorStore:
    def __init__(self, tag: str, root: Path = config.VEC_DIR):
        self.dir = root / tag
        self.vecs: dict[str, np.ndarray] = {}
        self._pending: dict[str, dict[str, np.ndarray]] = {}
        for p in sorted(self.dir.glob("*.npz")):
            with np.load(p, allow_pickle=False) as z:
                for sha, v in zip(z["shas"], z["vecs"]):
                    self.vecs.setdefault(str(sha), v.astype(np.float32))

    def __contains__(self, sha: str) -> bool:
        return sha in self.vecs

    def add(self, sha: str, vec: np.ndarray, month: str) -> None:
        if sha in self.vecs:
            return
        self.vecs[sha] = vec.astype(np.float32)
        self._pending.setdefault(month, {})[sha] = vec

    def save(self) -> list[str]:
        written = []
        self.dir.mkdir(parents=True, exist_ok=True)
        for month, new in self._pending.items():
            path = self.dir / f"{month}.npz"
            shas, vecs = [], []
            if path.exists():
                with np.load(path, allow_pickle=False) as z:
                    shas, vecs = [str(s) for s in z["shas"]], list(z["vecs"])
            for sha, v in new.items():
                if sha not in shas:
                    shas.append(sha)
                    vecs.append(v)
            tmp = path.with_suffix(".tmp.npz")
            np.savez_compressed(tmp, shas=np.array(shas, dtype="U64"), vecs=np.array(vecs, dtype=np.float16))
            tmp.replace(path)
            written.append(path.name)
        self._pending.clear()
        return written
