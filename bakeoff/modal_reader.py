"""A reader for the bake-off on Modal: an open vision model with pinned weights, answering the registered
questions (tone-v1) for one picture, or saying which of two pictures lies further along an axis
(pairs-v1). Exploratory: the registered reader is reader/modal_app.py and is not touched.

    BAKEOFF_MODEL=Qwen/Qwen3-VL-32B-Instruct-FP8 BAKEOFF_APP=adtone-bakeoff-qwen3 BAKEOFF_GPU=H100 \
        modal run bakeoff/modal_reader.py          # pin: resolve the weights' commit and store them
    BAKEOFF_REVISION=<sha> ... modal deploy bakeoff/modal_reader.py

Pictures arrive in memory with each call, or are read from the private picture volume by their sha
(the luxury reading, adtone/luxury.py), and are not kept. The model sees the pictures and the question
only, never the house. BAKEOFF_CONTAINERS (default 1) lets a long reading spread over more GPUs, and
BAKEOFF_SEQS (default 16) sets how many conversations one GPU takes at once.
"""
import base64
import math
import os

import modal

MODEL = os.environ.get("BAKEOFF_MODEL", "Qwen/Qwen3-VL-32B-Instruct-FP8")
REVISION = os.environ.get("BAKEOFF_REVISION", "")
GPU = os.environ.get("BAKEOFF_GPU", "H100")
APP = os.environ.get("BAKEOFF_APP", "adtone-bakeoff-qwen3")
CONTAINERS = int(os.environ.get("BAKEOFF_CONTAINERS", "1"))
SEQS = int(os.environ.get("BAKEOFF_SEQS", "16"))
VLLM = "vllm==0.11.0"

# Everything resolved as it stood on 20 October 2025, a fortnight after vLLM 0.11.0: it names no upper
# bound for transformers, and transformers 5 (January 2026) is not what it was built against.
RESOLVED_AS_OF = "2025-10-20T00:00:00Z"
image = (modal.Image.debian_slim(python_version="3.12")
         .uv_pip_install(VLLM, "transformers>=4.57.0,<5", "huggingface_hub[hf_transfer]", "pillow",
                         extra_options=f"--exclude-newer {RESOLVED_AS_OF}")
         .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "BAKEOFF_MODEL": MODEL, "BAKEOFF_REVISION": REVISION,
               "BAKEOFF_GPU": GPU, "BAKEOFF_APP": APP, "BAKEOFF_CONTAINERS": str(CONTAINERS),
               "BAKEOFF_SEQS": str(SEQS)}))
weights = modal.Volume.from_name("adtone-reader-weights", create_if_missing=True)
pictures = modal.Volume.from_name("adtone-pictures", create_if_missing=True)
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


def _picture(folder: str, sha: str) -> bytes:
    if not all(c in "0123456789abcdef" for c in sha) or "/" in folder.strip("/") or ".." in folder:
        raise ValueError("not a picture address")
    with open(f"/pictures/{folder.strip('/')}/{sha}.jpg", "rb") as f:
        return f.read()


@app.cls(image=image, gpu=GPU, volumes={"/weights": weights, "/pictures": pictures}, timeout=3600,
         scaledown_window=60, max_containers=CONTAINERS)
class Reader:
    @modal.enter()
    def load(self):
        from vllm import LLM
        if not REVISION:
            raise RuntimeError("BAKEOFF_REVISION is not set: deploy after pinning the weights")
        kw = dict(model=_path(MODEL, REVISION), max_model_len=8192, max_num_seqs=SEQS, seed=0,
                  limit_mm_per_prompt={"image": 2})
        # No cache of processed pictures shared between the engine's two processes: a reading where one picture
        # comes back in many comparisons, over more pictures than the cache holds, can leave the two out of step
        # and stop the engine. The cache only saves preprocessing, so the answers are the same without it.
        for extra in ({"mm_processor_cache_gb": 0}, {"disable_mm_preprocessor_cache": True}, {}):
            try:
                self.llm = LLM(**kw, **extra)
                self.cache_setting = extra
                break
            except TypeError:
                continue

    def _safely(self, fn, *args):
        """The call, with any failure passed back as a plain error the caller can read (vLLM's own exceptions
        do not unpickle where vLLM is not installed). A stopped engine fails every later call, so the container
        then takes no more work and is replaced."""
        try:
            return fn(*args)
        except Exception as e:
            msg = f"{type(e).__name__}: {str(e)[:400]}"
            if "dead" in msg.lower() or "enginecore" in msg.lower():
                try:
                    import modal.experimental
                    modal.experimental.stop_fetching_inputs()
                except Exception:
                    pass
            raise RuntimeError(msg) from None

    def _chat(self, conversations, constraint: dict | None, max_tokens: int):
        """With the constraint if the engine takes it, else without; the mode in use is reported."""
        from vllm import SamplingParams

        def params(mode):
            if not mode:
                return SamplingParams(temperature=0, max_tokens=max_tokens)
            try:      # vLLM 0.11 calls it structured outputs; earlier versions, guided decoding
                from vllm.sampling_params import StructuredOutputsParams
                return SamplingParams(temperature=0, max_tokens=max_tokens, structured_outputs=StructuredOutputsParams(**mode))
            except ImportError:
                from vllm.sampling_params import GuidedDecodingParams
                return SamplingParams(temperature=0, max_tokens=max_tokens, guided_decoding=GuidedDecodingParams(**mode))
        errors = []
        for mode in ([constraint, None] if constraint else [None]):
            try:
                outs = self.llm.chat(conversations, params(mode), use_tqdm=False)
                self.mode = "constrained" if mode else "free"
                self.errors = errors
                return [o.outputs[0].text for o in outs]
            except Exception as e:   # an engine that cannot take this constraint says so here
                errors.append(f"{e.__class__.__name__}: {str(e)[:200]}")
        raise RuntimeError("no decoding mode worked: " + " | ".join(errors))

    def _read(self, jpegs: list[bytes], system: str, schema: dict) -> list[str]:
        conversations = [[{"role": "system", "content": system},
                          {"role": "user", "content": [_url(j), {"type": "text", "text": "Score this image. Return only the JSON object."}]}]
                         for j in jpegs]
        return self._chat(conversations, {"json": _without(schema, "uniqueItems")}, 1200)   # tone-v2 has 30 keys

    def _compare(self, pairs: list[tuple[bytes, bytes]], system: str, prompts: list[str], choices: list[str]) -> list[str]:
        conversations = [[{"role": "system", "content": system},
                          {"role": "user", "content": [{"type": "text", "text": "First picture:"}, _url(a),
                                                       {"type": "text", "text": "Second picture:"}, _url(b),
                                                       {"type": "text", "text": q}]}]
                         for (a, b), q in zip(pairs, prompts)]
        return self._chat(conversations, {"choice": list(choices)}, 4)

    def _ask(self, jpegs: list[bytes], system: str, question: str, choices: list[str]) -> list[str]:
        conversations = [[{"role": "system", "content": system},
                          {"role": "user", "content": [_url(j), {"type": "text", "text": question}]}]
                         for j in jpegs]
        return self._chat(conversations, {"choice": list(choices)}, 16)

    def _compare_probs(self, pairs: list[tuple[bytes, bytes]], system: str, prompts: list[str]) -> list[float]:
        """For each pair, the probability the reader puts on "first" against "second" for its first word,
        from the token probabilities rather than the answer it would write."""
        from vllm import SamplingParams
        conversations = [[{"role": "system", "content": system},
                          {"role": "user", "content": [{"type": "text", "text": "First picture:"}, _url(a),
                                                       {"type": "text", "text": "Second picture:"}, _url(b),
                                                       {"type": "text", "text": q}]}]
                         for (a, b), q in zip(pairs, prompts)]
        outs = self.llm.chat(conversations, SamplingParams(temperature=0, max_tokens=1, logprobs=20), use_tqdm=False)
        probs = []
        for o in outs:
            lp = (o.outputs[0].logprobs or [{}])[0]
            first = second = 0.0
            for cand in lp.values():
                word = (cand.decoded_token or "").strip().lower()
                if word == "first":
                    first += math.exp(cand.logprob)
                elif word == "second":
                    second += math.exp(cand.logprob)
            probs.append(first / (first + second) if first + second > 0 else 0.5)
        return probs

    @modal.method()
    def ask_from(self, folder: str, shas: list[str], system: str, question: str, choices: list[str]) -> list[str]:
        """One question about each picture on the volume, answered with one of `choices`."""
        pictures.reload()
        return self._safely(lambda: self._ask([_picture(folder, s) for s in shas], system, question, choices))

    @modal.method()
    def compare_probs_from(self, folder: str, pairs: list[tuple[str, str]], system: str, prompts: list[str]) -> list[float]:
        """As compare_from, as probabilities."""
        pictures.reload()
        cache = {s: _picture(folder, s) for s in {x for p in pairs for x in p}}
        return self._safely(lambda: self._compare_probs([(cache[a], cache[b]) for a, b in pairs], system, prompts))

    @modal.method()
    def read(self, jpegs: list[bytes], system: str, schema: dict, grammar: str | None = None) -> list[str]:
        """tone-v1 for each picture, under the rubric's JSON schema."""
        return self._safely(lambda: self._read(jpegs, system, schema))

    @modal.method()
    def compare(self, pairs: list[tuple[bytes, bytes]], system: str, prompts: list[str], choices: list[str]) -> list[str]:
        """For each pair, which picture lies further along the axis in its prompt: one of `choices`."""
        return self._safely(lambda: self._compare(pairs, system, prompts, choices))

    @modal.method()
    def read_from(self, folder: str, shas: list[str], system: str, schema: dict) -> list[str]:
        """As read, for pictures on the private picture volume, named by their sha."""
        pictures.reload()
        return self._safely(lambda: self._read([_picture(folder, s) for s in shas], system, schema))

    @modal.method()
    def compare_from(self, folder: str, pairs: list[tuple[str, str]], system: str, prompts: list[str],
                     choices: list[str]) -> list[str]:
        """As compare, for pairs of pictures on the private picture volume, named by their sha."""
        pictures.reload()
        cache = {s: _picture(folder, s) for s in {x for p in pairs for x in p}}
        return self._safely(lambda: self._compare([(cache[a], cache[b]) for a, b in pairs], system, prompts, choices))

    @modal.method()
    def identity(self) -> dict:
        import vllm
        return {"model": MODEL, "revision": REVISION, "gpu": GPU, "vllm": vllm.__version__,
                "mode": getattr(self, "mode", None), "mode_errors": getattr(self, "errors", []),
                "processor_cache": getattr(self, "cache_setting", None)}


@app.local_entrypoint()
def main(revision: str = "main"):
    import json
    print("BAKEOFF_PIN " + json.dumps(pin.remote(MODEL, revision)))
