"""Pictures for the site's illustration of the clothes reader, made with open image models on Modal: the
start picture by Qwen-Image, each later picture an edit of the one before by Qwen-Image-Edit, held to a
mask so that nothing outside it changes (adtone/flex.py drives it). Both models are licensed Apache 2.0:
the pin takes the first candidate whose model card says so and passes over the rest.

    modal run bakeoff/modal_paint.py                  # pin: resolve both models' commits, store the weights
    PAINT_GEN=<repo>@<sha> PAINT_EDIT=<repo>@<sha> modal deploy bakeoff/modal_paint.py

Pictures arrive and leave as bytes and are not kept here. How an edit is held to its mask: the edit model
draws the whole picture, but after every step of its denoising the parts outside the mask are put back to
the picture before, noised to the same step (the way inpainting holds a picture), so the new parts are
drawn to meet the old ones; at the end the picture before is laid back over everything outside the mask,
with a soft edge, so those pixels are the picture before exactly.
"""
import io
import os

import modal

GEN = os.environ.get("PAINT_GEN", "")          # repo@commit of the text-to-image model, once pinned
EDIT = os.environ.get("PAINT_EDIT", "")        # repo@commit of the editing model, once pinned
GPU = os.environ.get("PAINT_GPU", "H100")
APP = os.environ.get("PAINT_APP", "adtone-paint")
GEN_CANDIDATES = ["Qwen/Qwen-Image-2512", "Qwen/Qwen-Image"]
EDIT_CANDIDATES = ["Qwen/Qwen-Image-Edit-2511", "Qwen/Qwen-Image-Edit-2509"]
LICENCE = "apache-2.0"
SKIP = ["*.gguf", "*.ckpt", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.mp4", "assets/*"]

# Everything as it stood when diffusers 0.40.0 came out (21 August 2026), resolved with uv for Python 3.12
# on linux x86_64; torch 2.8.0 is the build the reader already runs on these GPUs.
RESOLVED_AS_OF = "2026-08-22T00:00:00Z"
image = (modal.Image.debian_slim(python_version="3.12")
         .uv_pip_install("torch==2.8.0", "torchvision==0.23.0", "diffusers==0.40.0", "transformers==5.15.1",
                         "accelerate==1.14.0", "huggingface_hub[hf_xet]==1.28.0", "safetensors==0.8.0",
                         "pillow==12.3.0", "numpy==2.5.2",
                         extra_options=f"--exclude-newer {RESOLVED_AS_OF}")
         .env({"PAINT_GEN": GEN, "PAINT_EDIT": EDIT, "PAINT_GPU": GPU, "PAINT_APP": APP}))
weights = modal.Volume.from_name("adtone-reader-weights", create_if_missing=True)
app = modal.App(APP)


def _path(repo: str, sha: str) -> str:
    return f"/weights/{repo.replace('/', '--')}/{sha}"


def _split(pinned: str) -> tuple[str, str]:
    repo, _, sha = pinned.partition("@")
    if not repo or not sha:
        raise RuntimeError("the model is not pinned: deploy with PAINT_GEN and PAINT_EDIT set to repo@commit")
    return repo, sha


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def _plainly(fn):
    """Any failure passed back as a plain error the caller can read: the runner has no diffusers or torch
    to unpickle their exceptions with."""
    try:
        return fn()
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {str(e)[:600]}") from None


@app.function(image=image, volumes={"/weights": weights}, timeout=5400, cpu=8, memory=32768)
def pin(candidates: list[str]) -> dict:
    """The first candidate whose model card gives the Apache 2.0 licence, its weights stored at the commit
    resolved now. A candidate not found, or under another licence, is reported and passed over."""
    from huggingface_hub import HfApi, snapshot_download
    api, passed = HfApi(), []
    for repo in candidates:
        try:
            info = api.model_info(repo)
        except Exception as e:
            passed.append({"model": repo, "why": f"not found ({type(e).__name__})"})
            continue
        licences = sorted({t.split(":", 1)[1] for t in (info.tags or []) if t.startswith("license:")})
        if licences != [LICENCE]:
            passed.append({"model": repo, "why": f"licence {licences or 'not given'}"})
            continue
        sha = info.sha
        snapshot_download(repo, revision=sha, local_dir=_path(repo, sha), ignore_patterns=SKIP)
        weights.commit()
        size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(_path(repo, sha)) for f in fs)
        return {"model": repo, "revision": sha, "licence": LICENCE, "bytes": size, "passed_over": passed}
    raise RuntimeError(f"no candidate could be used: {passed}")


def _versions() -> dict:
    import diffusers
    import torch
    import transformers
    return {"torch": torch.__version__, "diffusers": diffusers.__version__, "transformers": transformers.__version__,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}


@app.cls(image=image, gpu=GPU, volumes={"/weights": weights}, timeout=3600, scaledown_window=120, max_containers=1)
class Starter:
    """The start picture: Qwen-Image from a prompt, one picture for each seed."""

    def _load(self):
        if getattr(self, "pipe", None) is None:
            import torch
            from diffusers import QwenImagePipeline
            repo, sha = _split(GEN)
            self.pipe = QwenImagePipeline.from_pretrained(_path(repo, sha), torch_dtype=torch.bfloat16).to("cuda")
        return self.pipe

    def _make(self, prompt: str, negative: str, seeds: list[int], width: int, height: int, steps: int,
              cfg: float) -> list[bytes]:
        import torch
        pipe = self._load()
        out = []
        for s in seeds:
            g = torch.Generator(device="cuda").manual_seed(int(s))
            try:
                img = pipe(prompt=prompt, negative_prompt=negative, width=width, height=height,
                           num_inference_steps=steps, true_cfg_scale=cfg, generator=g).images[0]
            except torch.cuda.OutOfMemoryError:     # the decoder at full size; once more in tiles
                torch.cuda.empty_cache()
                pipe.vae.enable_tiling()
                g = torch.Generator(device="cuda").manual_seed(int(s))
                img = pipe(prompt=prompt, negative_prompt=negative, width=width, height=height,
                           num_inference_steps=steps, true_cfg_scale=cfg, generator=g).images[0]
            out.append(_png(img.convert("RGB")))
        return out

    @modal.method()
    def make(self, prompt: str, negative: str, seeds: list[int], width: int, height: int, steps: int = 50,
             cfg: float = 4.0) -> list[bytes]:
        return _plainly(lambda: self._make(prompt, negative, seeds, width, height, steps, cfg))

    @modal.method()
    def identity(self) -> dict:
        return _plainly(lambda: {"model": GEN, **_versions()})


@app.cls(image=image, gpu=GPU, volumes={"/weights": weights}, timeout=3600, scaledown_window=180, max_containers=2)
class Editor:
    """One edit of a picture, held to a mask, drawn once for each seed."""

    def _load(self):
        if getattr(self, "pipe", None) is None:
            import torch
            from diffusers import QwenImageEditPlusPipeline
            repo, sha = _split(EDIT)
            self.pipe = QwenImageEditPlusPipeline.from_pretrained(_path(repo, sha), torch_dtype=torch.bfloat16).to("cuda")
        return self.pipe

    def _edit(self, picture: bytes, mask: bytes, prompt: str, negative: str, seeds: list[int], steps: int,
              cfg: float, feather: float) -> list[bytes]:
        import numpy as np
        import torch
        from PIL import Image, ImageFilter
        pipe = self._load()
        before = Image.open(io.BytesIO(picture)).convert("RGB")
        W, H = before.size
        if W % 16 or H % 16:
            raise ValueError(f"the picture's sides must be multiples of 16, not {W}x{H}")
        drawn = np.asarray(Image.open(io.BytesIO(mask)).convert("L").resize((W, H), Image.Resampling.NEAREST)) > 127
        # The model works in squares of 16 pixels: a square is redrawn if any of it is in the mask.
        squares = drawn.reshape(H // 16, 16, W // 16, 16).any(axis=(1, 3))
        dev, dtype = "cuda", pipe.transformer.dtype
        with torch.no_grad():
            px = pipe.image_processor.preprocess(before, H, W).unsqueeze(2).to(dev, dtype)
            lat = pipe._encode_vae_image(px, generator=None)
            known = pipe._pack_latents(lat, 1, lat.shape[1], lat.shape[3], lat.shape[4])
        free = torch.from_numpy(squares.astype(np.float32)).to(dev, dtype).reshape(1, -1, 1)
        if known.shape[1] != free.shape[1]:
            raise ValueError(f"mask squares {free.shape[1]} do not match the picture's {known.shape[1]}")
        # The redrawn squares taken whole, with a soft edge running outwards where the edit model's picture is
        # the picture before passed through its own encoder and decoder; beyond the edge, the picture before
        # is untouched.
        edge = Image.fromarray((squares * 255).astype(np.uint8)).resize((W, H), Image.Resampling.NEAREST)
        hard = np.asarray(edge, dtype=np.float32) / 255.0
        soft = np.asarray(edge.filter(ImageFilter.GaussianBlur(feather)), dtype=np.float32) / 255.0
        alpha = np.maximum(hard, soft)[..., None]
        old = np.asarray(before, dtype=np.float32)
        out = []
        for s in seeds:
            g = torch.Generator(device=dev).manual_seed(int(s))
            noise = torch.randn(known.shape, generator=g, device=dev, dtype=dtype)

            def hold(p, i, t, kw, noise=noise):
                ts = t.reshape(1) if torch.is_tensor(t) else torch.tensor([t], device=dev)
                outside = p.scheduler.scale_noise(known, ts, noise)    # the picture before, at this step's noise
                return {"latents": (1 - free) * outside + free * kw["latents"]}

            img = pipe(image=[before], prompt=prompt, negative_prompt=negative, true_cfg_scale=cfg, height=H,
                       width=W, num_inference_steps=steps, latents=noise.clone(), generator=g,
                       callback_on_step_end=hold, callback_on_step_end_tensor_inputs=["latents"]).images[0]
            new = np.asarray(img.convert("RGB").resize((W, H), Image.Resampling.LANCZOS), dtype=np.float32)
            mixed = old * (1.0 - alpha) + new * alpha
            out.append(_png(Image.fromarray(np.clip(np.floor(mixed + 0.5), 0, 255).astype(np.uint8))))
        return out

    @modal.method()
    def edit(self, picture: bytes, mask: bytes, prompt: str, negative: str, seeds: list[int], steps: int = 40,
             cfg: float = 4.0, feather: float = 6.0) -> list[bytes]:
        return _plainly(lambda: self._edit(picture, mask, prompt, negative, seeds, steps, cfg, feather))

    @modal.method()
    def identity(self) -> dict:
        return _plainly(lambda: {"model": EDIT, **_versions()})


@app.local_entrypoint()
def main():
    import json
    gen, edit = list(pin.map([GEN_CANDIDATES, EDIT_CANDIDATES], return_exceptions=True))
    for name, res in (("gen", gen), ("edit", edit)):
        if isinstance(res, BaseException):
            res = {"error": f"{type(res).__name__}: {str(res)[:600]}"}
        print("PAINT_PIN " + json.dumps({"role": name, **res}))
