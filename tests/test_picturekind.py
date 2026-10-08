from adtone import picturekind as P


def test_labels_are_read_from_the_store_or_a_plain_map():
    docs = [{"id": "a", "data": {"sha": "a", "answers": {"creative_type": "brand_image"}}},
            {"id": "b", "data": {"sha": "b", "answers": {"creative_type": "nonsense"}}}]
    assert P.read_labels(docs) == {"a": "brand_image"}
    assert P.read_labels({"x": "product_packshot", "y": "?"}) == {"x": "product_packshot"}


def test_the_reader_that_matches_the_labels_reads_the_advertising():
    labels = {f"s{i}": k for i, k in enumerate(["brand_image"] * 10 + ["product_packshot"] * 10 + ["product_on_model"] * 10)}
    good = dict(labels)                                                    # the larger reader matches every label
    small = {s: ("product_on_model" if k == "product_packshot" else k) for s, k in labels.items()}
    for s in list(small)[:6]:                                              # and the smaller one misses some campaign pictures
        small[s] = "product_on_model"
    out = P.compare(labels, {"qwen2.5-vl-7b": small, "qwen3-vl-32b": good})
    assert out["reads_the_advertising"] == "qwen3-vl-32b" and out["good_enough"]
    r = out["readers"]["qwen2.5-vl-7b"]
    assert r["campaign_or_not"]["kappa"] < out["readers"]["qwen3-vl-32b"]["campaign_or_not"]["kappa"] == 1.0
    assert r["disagreements"]["product_packshot -> product_on_model"] == 10


def test_a_near_tie_goes_to_the_larger_reader():
    labels = {f"s{i}": ("brand_image" if i % 2 else "product_on_model") for i in range(40)}
    small = dict(labels)
    large = dict(labels); large["s1"] = "product_on_model"                # one miss: within the tie
    assert P.compare(labels, {"qwen2.5-vl-7b": small, "qwen3-vl-32b": large})["reads_the_advertising"] == "qwen3-vl-32b"
