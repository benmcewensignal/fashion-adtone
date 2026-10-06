# The reader on Modal

`modal_app.py` answers the frozen rubric (`rubric/tone-v1.md`) for each ad image with an open vision
model, Qwen2.5-VL-7B-Instruct, on a Modal GPU. It sees the image and the rubric only, never the house.
Replies are generated under the rubric's JSON schema and validated again by `adtone.score`.

It never talks to Meta. GitHub fetches the ads and their images with the Meta token, sends the image
bytes here, and stores the answers. Images live in memory for one call and are never written.

The reader workflow (`.github/workflows/reader.yml`) needs two repository secrets, `MODAL_TOKEN_ID`
and `MODAL_TOKEN_SECRET`; without them it stands down. With them it:

1. pins the weights once, recording their commit in `data/state/reader.json` (never changed again
   unless a person asks for a re-pin, before the freeze only);
2. deploys the reader at that commit;
3. runs a pilot on 64 drawn test cards and records the share of valid replies, cold start, seconds per
   image and cost per 10,000 images at Modal's published GPU price (`data/provenance/reader.jsonl`).

Registered in `PREREGISTRATION-AMENDMENT-2.md`, section 2e.
