"""Which reader tells picture kinds apart the way a person does: Ben's picture-kind labels against both readers.

    python -m adtone.picturekind labels.json     # -> data/results/picture_kind.json

`labels.json` is the labelling page's picture-kind answers as read back from its store: a list of documents
with `sha` and `answers.creative_type`, or a plain {sha: kind} map. Each reader is set against the labels
twice: on campaign picture or not, which decides which pictures are compared like for like, and on all
eight kinds, which the mix is made of. The reader that agrees better on campaign picture or not reads the
advertising; within 0.05 of kappa the larger one does, since it is the one that tells packshots apart.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from . import config, store
from .agreement import kappa

SMALL = config.DATA / "homepages" / "obs" / "tone-v1.jsonl"
LARGE = config.DATA / "luxury" / "readings" / "qwen3-tone.jsonl"
RESULTS = config.RESULTS_DIR / "picture_kind.json"
KINDS = ("brand_image", "product_on_model", "product_packshot", "catalogue_grid", "promotional",
         "event_or_announcement", "text_graphic", "other")
TIE = 0.05
GOOD = 0.6


def read_labels(raw) -> dict[str, str]:
    if isinstance(raw, dict) and all(isinstance(v, str) for v in raw.values()):
        return {k: v for k, v in raw.items() if v in KINDS}
    docs = raw.get("documents", raw) if isinstance(raw, dict) else raw
    out = {}
    for d in docs:
        body = d.get("data", d)
        kind = (body.get("answers") or {}).get("creative_type")
        sha = body.get("sha") or d.get("id")
        if sha and kind in KINDS:
            out[sha] = kind
    return out


def readers(small: Path = SMALL, large: Path = LARGE) -> dict[str, dict[str, str]]:
    s, l = {}, {}
    for r in store.read_jsonl(small):
        if r.get("status") == "ok":
            s[r["sha"]] = (r.get("output") or {}).get("creative_type")
    for r in store.read_jsonl(large):
        l[r.get("sha")] = (r.get("out") or {}).get("creative_type")
    return {"qwen2.5-vl-7b": s, "qwen3-vl-32b": l}


def compare(labels: dict[str, str], by_reader: dict[str, dict[str, str]]) -> dict:
    out = {"labelled": len(labels), "readers": {}}
    for name, answers in by_reader.items():
        shas = [s for s in labels if answers.get(s)]
        human = [labels[s] for s in shas]
        machine = [answers[s] for s in shas]
        img_h = ["image" if k == "brand_image" else "not" for k in human]
        img_m = ["image" if k == "brand_image" else "not" for k in machine]
        out["readers"][name] = {
            "n": len(shas),
            "campaign_or_not": {"kappa": kappa(img_h, img_m),
                                "agreement": round(sum(a == b for a, b in zip(img_h, img_m)) / len(shas), 3) if shas else None},
            "all_kinds": {"kappa": kappa(human, machine),
                          "agreement": round(sum(a == b for a, b in zip(human, machine)) / len(shas), 3) if shas else None},
            "disagreements": dict(Counter(f"{a} -> {b}" for a, b in zip(human, machine) if a != b).most_common(8)),
        }
    k = {n: (v["campaign_or_not"]["kappa"] if v["n"] else None) for n, v in out["readers"].items()}
    usable = {n: x for n, x in k.items() if x is not None}
    if len(usable) == 2:
        small, large = usable["qwen2.5-vl-7b"], usable["qwen3-vl-32b"]
        pick = "qwen3-vl-32b" if large >= small - TIE else "qwen2.5-vl-7b"
        out["reads_the_advertising"] = pick
        out["good_enough"] = bool(usable[pick] >= GOOD)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    labels = read_labels(json.loads(Path(argv[0]).read_text()))
    out = {"generated_at": store.utc_now(), **compare(labels, readers())}
    config.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "readers"}, indent=1))
    for n, v in out["readers"].items():
        print(n, v["n"], "campaign or not", v["campaign_or_not"], "all kinds", v["all_kinds"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
