"""The adtone reader on Modal: the frozen rubric answered by an open vision model with pinned weights.

    modal run reader/modal_app.py::pin        # resolve the weights' commit and store them on the volume
    modal deploy reader/modal_app.py          # the reader workflow does both, then runs a pilot

Every answer is generated under the rubric's JSON schema, so a reply outside the fixed options cannot be
produced; adtone then validates it with the same code that validates the Claude reader. Images live in
memory for the length of one call and are never written anywhere. The model sees the image and the
rubric only, never the house.
"""
import base64
import os

import modal

MODEL = os.environ.get("ADTONE_OPEN_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")
REVISION = os.environ.get("ADTONE_OPEN_MODEL_REVISION", "")      # a commit hash once pinned
GPU = os.environ.get("ADTONE_MODAL_GPU", "L4")
MAX_PIXELS = 1024 * 28 * 28    # at most about a thousand image tokens: the pipeline already sends 1,568px at most

image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install("vllm==0.8.5", "huggingface_hub[hf_transfer]==0.30.2", "pillow")
         .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "ADTONE_OPEN_MODEL": MODEL, "ADTONE_OPEN_MODEL_REVISION": REVISION}))
weights = modal.Volume.from_name("adtone-reader-weights", create_if_missing=True)
app = modal.App("adtone-reader")


def _without(obj, key: str):
    """A copy of a JSON schema without one keyword, for engines that do not support it."""
    if isinstance(obj, dict):
        return {k: _without(v, key) for k, v in obj.items() if k != key}
    if isinstance(obj, list):
        return [_without(v, key) for v in obj]
    return obj


def _path(model: str, revision: str) -> str:
    return f"/weights/{model.replace('/', '--')}/{revision}"


@app.function(image=image, volumes={"/weights": weights}, timeout=3600)
def pin(model: str = MODEL, revision: str = "main") -> dict:
    """Resolve a branch to its commit and store exactly those weights; returns what to record."""
    from huggingface_hub import HfApi, snapshot_download
    sha = HfApi().model_info(model, revision=revision).sha
    snapshot_download(model, revision=sha, local_dir=_path(model, sha))
    weights.commit()
    return {"model": model, "revision": sha}


@app.cls(image=image, gpu=GPU, volumes={"/weights": weights}, timeout=1800, scaledown_window=120, max_containers=1)
class Reader:
    @modal.enter()
    def load(self):
        from vllm import LLM
        if not REVISION:
            raise RuntimeError("ADTONE_OPEN_MODEL_REVISION is not set: deploy after pinning the weights")
        self.llm = LLM(model=_path(MODEL, REVISION), max_model_len=8192, max_num_seqs=8, seed=0,
                       limit_mm_per_prompt={"image": 1}, mm_processor_kwargs={"max_pixels": MAX_PIXELS})

    def _params(self, mode: str, schema: dict, grammar: str | None):
        from vllm import SamplingParams
        from vllm.sampling_params import GuidedDecodingParams
        if mode == "grammar":
            guided = GuidedDecodingParams(grammar=grammar)
        elif mode == "schema":
            guided = GuidedDecodingParams(json=_without(schema, "uniqueItems"))
        else:
            guided = None
        return SamplingParams(temperature=0, max_tokens=512, guided_decoding=guided)

    def _generate(self, jpegs: list[bytes], system: str, schema: dict, grammar: str | None):
        """Try the exact grammar first, then the schema, then no constraint, and keep the first that the
        engine accepts. The mode in use is reported, because a change of mode changes the instrument."""
        conversations = [[
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(j).decode("ascii")}},
                {"type": "text", "text": "Score this image. Return only the JSON object."}]},
        ] for j in jpegs]
        modes = [self.mode] if getattr(self, "mode", None) else [m for m in ("grammar", "schema", "free")
                                                                   if m != "grammar" or grammar]
        errors = []
        for mode in modes:
            try:
                outs = self.llm.chat(conversations, self._params(mode, schema, grammar), use_tqdm=False)
                self.mode = mode
                self.errors = errors
                return outs
            except Exception as e:   # an engine that cannot take this constraint says so here
                errors.append(f"{mode}: {e.__class__.__name__}: {str(e)[:200]}")
        raise RuntimeError("no decoding mode worked: " + " | ".join(errors))

    @modal.method()
    def read(self, jpegs: list[bytes], system: str, schema: dict, grammar: str | None = None) -> list[str]:
        return [o.outputs[0].text for o in self._generate(jpegs, system, schema, grammar)]

    @modal.method()
    def read_meta(self, jpegs: list[bytes], system: str, schema: dict, grammar: str | None = None) -> list[dict]:
        """The same reading, with why each reply stopped and how long it was: for the pilot."""
        outs = self._generate(jpegs, system, schema, grammar)
        return [{"text": o.outputs[0].text, "finish_reason": o.outputs[0].finish_reason,
                 "tokens": len(o.outputs[0].token_ids), "mode": self.mode, "mode_errors": self.errors}
                for o in outs]

    @modal.method()
    def read_text(self, texts: list[str], system: str, schema: dict, grammar: str | None = None) -> list[str]:
        """The words reader: the same model and the same constrained decoding, given a text and no image."""
        conversations = [[
            {"role": "system", "content": system},
            {"role": "user", "content": "Read this text. Return only the JSON object.\n\n<text>\n" + t.strip() + "\n</text>"},
        ] for t in texts]
        errors = []
        for mode in [m for m in ("grammar", "schema", "free") if m != "grammar" or grammar]:
            try:
                outs = self.llm.chat(conversations, self._params(mode, schema, grammar), use_tqdm=False)
                return [o.outputs[0].text for o in outs]
            except Exception as e:   # an engine that cannot take this constraint says so here
                errors.append(f"{mode}: {e.__class__.__name__}: {str(e)[:200]}")
        raise RuntimeError("no decoding mode worked: " + " | ".join(errors))

    @modal.method()
    def identity(self) -> dict:
        import vllm
        return {"model": MODEL, "revision": REVISION, "gpu": GPU, "vllm": vllm.__version__}


@app.local_entrypoint()
def main(revision: str = "main"):
    """`modal run reader/modal_app.py`: pin the weights and print the line the workflow records."""
    import json
    print("ADTONE_PIN " + json.dumps(pin.remote(MODEL, revision)))
