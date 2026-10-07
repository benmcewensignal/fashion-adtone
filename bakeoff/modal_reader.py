"""A reader for the bake-off on Modal: an open vision model with pinned weights, answering the registered
questions (tone-v1) for one picture, or saying which of two pictures lies further along an axis
(pairs-v1). Exploratory: the registered reader is reader/modal_app.py and is not touched.

    BAKEOFF_MODEL=Qwen/Qwen3-VL-32B-Instruct-FP8 BAKEOFF_APP=adtone-bakeoff-qwen3 BAKEOFF_GPU=H100 \
        modal run bakeoff/modal_reader.py          # pin: resolve the weights' commit and store them
    BAKEOFF_REVISION=<sha> ... modal deploy bakeoff/modal_reader.py

Pictures arrive in memory with each call and are not kept. The model sees the pictures and the
question only, never the house.
"""
import base64
import os

import modal

MODEL = os.environ.get("BAKEOFF_MODEL", "Qwen/Qwen3-VL-32B-Instruct-FP8")
REVISION = os.environ.get("BAKEOFF_REVISION", "")
GPU = os.environ.get("BAKEOFF_GPU", "H100")
APP = os.environ.get("BAKEOFF_APP", "adtone-bakeoff-qwen3")
VLLM = "vllm==0.11.0"

image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install(VLLM, "huggingface_hub[hf_transfer]", "pillow")
         .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "BAKEOFF_MODEL": MODEL, "BAKEOFF_REVISION": REVISION,
               "BAKEOFF_GPU": GPU, "BAKEOFF_APP": APP}))
weights = modal.Volume.from_name("adtone-reader-weights", create_if_missing=True)
app = modal.App(APP)


def _without(obj, key: str):
    if isinstance(obj, dict):
        return {k: _without(v, key) for k, v in obj.items() if k != key}
    if isinstance(obj, list):
        return [_without(v, key) for v in obj]
    return obj


def _path(model: str, revision: str) -> str:
    return f"/weights/{model.replace('/', '--')}/{revision}"


def _url(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}}


@app.function(image=image, volumes={"/weights": weights}, timeout=3600)
def pin(model: str = MODEL, revision: str = "main") -> dict:
    from huggingface_hub import HfApi, snapshot_download
    sha = HfApi().model_info(model, revision=revision).sha
    snapshot_download(model, revision=sha, local_dir=_path(model, sha))
    weights.commit()
    return {"model": model, "revision": sha}


@app.cls(image=image, gpu=GPU, volumes={"/weights": weights}, timeout=3600, scaledown_window=60, max_containers=1)
class Reader:
    @modal.enter()
    def load(self):
        from vllm import LLM
        if not REVISION:
            raise RuntimeError("BAKEOFF_REVISION is not set: deploy after pinning the weights")
        self.llm = LLM(model=_path(MODEL, REVISION), max_model_len=8192, max_num_seqs=16, seed=0,
                       limit_mm_per_prompt={"image": 2})

    def _chat(self, conversations, constraint: dict | None, max_tokens: int):
        """With the constraint if the engine takes it, else without; the mode in use is reported."""
        from vllm import SamplingParams
        from vllm.sampling_params import StructuredOutputsParams
        errors = []
        for mode in ([constraint, None] if constraint else [None]):
            try:
                so = StructuredOutputsParams(**mode) if mode else None
                outs = self.llm.chat(conversations, SamplingParams(temperature=0, max_tokens=max_tokens, structured_outputs=so),
                                     use_tqdm=False)
                self.mode = "constrained" if mode else "free"
                self.errors = errors
                return [o.outputs[0].text for o in outs]
            except Exception as e:   # an engine that cannot take this constraint says so here
                errors.append(f"{e.__class__.__name__}: {str(e)[:200]}")
        raise RuntimeError("no decoding mode worked: " + " | ".join(errors))

    @modal.method()
    def read(self, jpegs: list[bytes], system: str, schema: dict, grammar: str | None = None) -> list[str]:
        """tone-v1 for each picture, under the rubric's JSON schema."""
        conversations = [[{"role": "system", "content": system},
                          {"role": "user", "content": [_url(j), {"type": "text", "text": "Score this image. Return only the JSON object."}]}]
                         for j in jpegs]
        return self._chat(conversations, {"json": _without(schema, "uniqueItems")}, 700)

    @modal.method()
    def compare(self, pairs: list[tuple[bytes, bytes]], system: str, prompts: list[str], choices: list[str]) -> list[str]:
        """For each pair, which picture lies further along the axis in its prompt: one of `choices`."""
        conversations = [[{"role": "system", "content": system},
                          {"role": "user", "content": [{"type": "text", "text": "First picture:"}, _url(a),
                                                       {"type": "text", "text": "Second picture:"}, _url(b),
                                                       {"type": "text", "text": q}]}]
                         for (a, b), q in zip(pairs, prompts)]
        return self._chat(conversations, {"choice": list(choices)}, 4)

    @modal.method()
    def identity(self) -> dict:
        import vllm
        return {"model": MODEL, "revision": REVISION, "gpu": GPU, "vllm": vllm.__version__,
                "mode": getattr(self, "mode", None), "mode_errors": getattr(self, "errors", [])}


@app.local_entrypoint()
def main(revision: str = "main"):
    import json
    print("BAKEOFF_PIN " + json.dumps(pin.remote(MODEL, revision)))
