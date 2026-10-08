"""Composition measures v1, from the pixels, with no reader. The method is rubric/composition-v1.md, frozen
before any picture was measured; this module follows it step by step, and its tests check each measure on
pictures whose answer is known.

    from adtone.composition import measure
    measure(pil_image)      # {"complexity": ..., "open_space": ..., ...}
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

VERSION = "composition-v1"
EDGE = 512            # the working picture's longer side
GRID = 64             # the luminance grid for symmetry and saliency
BLOCK = 16            # blocks for open space
PLAIN_SD = 4.0        # a block is plain below this standard deviation, in Y and in both opponent channels
SOBEL_T = 64.0        # an edge, in Sobel magnitude on 0 to 255
JPEG_QUALITY = 75
SAL_SIGMA = 2.5       # smoothing of the saliency map, in cells
SAL_FACTOR = 3.0      # salient: above three times the map's mean (Hou and Zhang)
KEYS = ("complexity", "edge_density", "open_space", "symmetry", "mass_x", "mass_y", "centre_offset", "thirds",
        "figure_size", "depth_of_field", "diagonals", "pleasure", "arousal", "dominance")


def working(img: Image.Image) -> Image.Image:
    """RGB, longer side 512 pixels, Lanczos."""
    im = img.convert("RGB")
    w, h = im.size
    s = EDGE / max(w, h)
    return im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.LANCZOS)


def _luma(a: np.ndarray) -> np.ndarray:
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def _sobel(Y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    P = np.pad(Y, 1, mode="edge")
    gx = (P[:-2, 2:] + 2 * P[1:-1, 2:] + P[2:, 2:]) - (P[:-2, :-2] + 2 * P[1:-1, :-2] + P[2:, :-2])
    gy = (P[2:, :-2] + 2 * P[2:, 1:-1] + P[2:, 2:]) - (P[:-2, :-2] + 2 * P[:-2, 1:-1] + P[:-2, 2:])
    return gx, gy


def _laplacian(Y: np.ndarray) -> np.ndarray:
    P = np.pad(Y, 1, mode="edge")
    return np.abs(P[1:-1, :-2] + P[1:-1, 2:] + P[:-2, 1:-1] + P[2:, 1:-1] - 4 * P[1:-1, 1:-1])


def _grid(Y: np.ndarray) -> np.ndarray:
    """Y averaged into 64 by 64 cells."""
    return np.asarray(Image.fromarray(Y.astype(np.float32)).resize((GRID, GRID), Image.Resampling.BOX),
                      dtype=np.float64)


def _gauss(S: np.ndarray, sigma: float) -> np.ndarray:
    r = int(math.ceil(3 * sigma))
    x = np.arange(-r, r + 1)
    k = np.exp(-x ** 2 / (2 * sigma ** 2))
    k /= k.sum()
    P = np.pad(S, r, mode="reflect")
    rows = sum(k[i] * P[:, i:i + S.shape[1]] for i in range(len(k)))
    return sum(k[i] * rows[i:i + S.shape[0], :] for i in range(len(k)))


def saliency(g: np.ndarray) -> np.ndarray:
    """The spectral residual (Hou and Zhang 2007) of a 64 by 64 grid, smoothed, summing to one."""
    F = np.fft.fft2(g)
    A = np.log(np.abs(F) + 1e-9)
    avg = sum(np.roll(A, (dy, dx), axis=(0, 1)) for dy in (-1, 0, 1) for dx in (-1, 0, 1)) / 9.0
    S = np.abs(np.fft.ifft2(np.exp((A - avg) + 1j * np.angle(F)))) ** 2
    S = _gauss(S, SAL_SIGMA)
    tot = S.sum()
    return S / tot if tot > 0 else np.full_like(S, 1.0 / S.size)


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def measure(img: Image.Image) -> dict:
    im = working(img)
    a = np.asarray(im, dtype=np.float64)
    H, W = a.shape[:2]
    Y = _luma(a)
    out: dict = {}

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=JPEG_QUALITY)
    out["complexity"] = 8.0 * len(buf.getvalue()) / (W * H)

    gx, gy = _sobel(Y)
    G = np.hypot(gx, gy)
    strong = G >= SOBEL_T
    out["edge_density"] = float(strong.mean())

    ny, nx = H // BLOCK, W // BLOCK
    if ny and nx:
        crop = a[:ny * BLOCK, :nx * BLOCK]
        chans = (_luma(crop), crop[..., 0] - crop[..., 1], (crop[..., 0] + crop[..., 1]) / 2 - crop[..., 2])
        plain = np.ones((ny, nx), dtype=bool)
        for c in chans:
            sd = c.reshape(ny, BLOCK, nx, BLOCK).std(axis=(1, 3))
            plain &= sd < PLAIN_SD
        out["open_space"] = float(plain.mean())
    else:
        out["open_space"] = None

    g = _grid(Y)
    m = np.fliplr(g)
    out["symmetry"] = float(np.corrcoef(g.ravel(), m.ravel())[0, 1]) if g.std() > 1e-9 else None

    S = saliency(g)
    region = S > SAL_FACTOR * S.mean()
    w = np.where(region, S, 0.0) if region.any() else S       # the salient region, or the whole map
    ys, xs = np.mgrid[0:GRID, 0:GRID]
    cx = float((w * (xs + 0.5)).sum() / w.sum() / GRID)
    cy = float((w * (ys + 0.5)).sum() / w.sum() / GRID)
    out["mass_x"], out["mass_y"] = cx, cy
    out["centre_offset"] = math.hypot(cx - 0.5, cy - 0.5) / math.hypot(0.5, 0.5)
    pts = [(px, py) for px in (1 / 3, 2 / 3) for py in (1 / 3, 2 / 3)]
    out["thirds"] = min(math.hypot(cx - px, cy - py) for px, py in pts) / math.hypot(1 / 3, 1 / 3)
    out["figure_size"] = float(region.mean())

    L = _laplacian(Y)
    mid = np.zeros((H, W), dtype=bool)
    mid[H // 4:H - H // 4, W // 4:W - W // 4] = True        # the central quarter: the middle half each way
    m_in, m_out = float(L[mid].mean()), float(L[~mid].mean())
    out["depth_of_field"] = math.log2(m_in / m_out) if m_in > 0 and m_out > 0 else None

    if strong.any():
        theta = np.degrees(np.arctan2(gy[strong], gx[strong])) % 180.0
        diag = ((theta > 22.5) & (theta < 67.5)) | ((theta > 112.5) & (theta < 157.5))
        out["diagonals"] = float(G[strong][diag].sum() / G[strong].sum())
    else:
        out["diagonals"] = None

    mx, mn = a.max(axis=2), a.min(axis=2)
    V = mx / 255.0 * 100.0
    Sat = np.where(mx > 0, (mx - mn) / np.where(mx > 0, mx, 1.0), 0.0) * 100.0
    B, Sm = float(V.mean()), float(Sat.mean())
    out["pleasure"] = 0.69 * B + 0.22 * Sm
    out["arousal"] = -0.31 * B + 0.60 * Sm
    out["dominance"] = -0.76 * B + 0.32 * Sm
    return {k: _r(out.get(k)) for k in KEYS}
