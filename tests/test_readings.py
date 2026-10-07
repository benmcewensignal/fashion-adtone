"""Readings: a picture counts once however long it stays up, a planted shift is found and a steady brand
is not, a brand's change is told from the market's, a change of mix is not taken for a change of style,
unreliable or near-constant answers are screened out, and drift is told from turnover."""
import numpy as np

from adtone import readings as R
from adtone.character import period_of
from adtone.score import load_rubric

SPEC = load_rubric().spec
H1 = [f"2024-{m:02d}" for m in range(1, 7)]
H2 = [f"2024-{m:02d}" for m in range(7, 13)]


def _out(people="one", setting="studio_plain", light="high_key", mood=("serene",), ctype="brand_image"):
    return {"creative_type": ctype, "light": light, "colour_temperature": "neutral", "saturation": "muted",
            "setting": setting, "people": people, "gaze": "to_camera", "expression": "neutral", "pose": "posed_static",
            "framing": "medium", "primary_subject": "garment", "styling_register": "formal_tailored",
            "production": "polished_commercial", "text_in_image": "none", "mood": list(mood), "street_couture_axis": 3}


def _pic(house, month, i, vec, kind="campaign", **kw):
    v = np.asarray(vec, float)
    return {"house": house, "month": month, "sha": f"{house}-{month}-{kind}-{i}", "type": kind, "period": period_of(month),
            "out": _out(ctype={"campaign": "brand_image", "on_model": "product_on_model"}[kind], **kw), "vec": v / np.linalg.norm(v)}


def _answers():
    return R.Answers(SPEC, {"people": {"keep": True}, "setting": {"keep": True}, "mood:serene": {"keep": True}})


def _brands(rng, n=8, dim=16):
    return {f"b{k}": rng.normal(size=dim) for k in range(n)}


def _cloud(house, centre, months, rng, per=3, noise=0.6, kind="campaign", **kw):
    return [_pic(house, m, i, centre + rng.normal(scale=noise, size=len(centre)), kind, **kw)
            for m in months for i in range(per)]


def _in(months):
    return lambda m, s=frozenset(months): m in s


def test_a_picture_left_up_for_months_counts_once():
    rng = np.random.default_rng(0)
    c = rng.normal(size=16)
    keep = _pic("a", "2024-01", 0, c)
    ims = [{**keep, "month": m, "period": period_of(m)} for m in H1]          # the same picture all half-year
    ims += [_pic("a", m, 1, c + rng.normal(size=16)) for m in H1]
    ims += [_pic("a", m, 1, c + rng.normal(size=16)) for m in H2]
    houses = R.by_house(ims)
    got = R.compare(houses, "a", _in(H1), _in(H2), _answers(), rng, n_perm=99, market=False)
    assert got["n0"] == 7 and got["n1"] == 6
    assert [i["month"] for i in R.once(ims) if i["sha"] == keep["sha"]] == ["2024-01"]     # where it was first shown


def test_a_planted_shift_is_found_and_steady_brands_are_quiet():
    rng = np.random.default_rng(1)
    centres = _brands(rng)
    ims = []
    for h, c in centres.items():
        ims += _cloud(h, c, H1, rng)
        ims += _cloud(h, c if h != "b0" else rng.normal(size=16), H2, rng)
    houses, ans = R.by_house(ims), _answers()
    moved = R.compare(houses, "b0", _in(H1), _in(H2), ans, rng, n_perm=999, market=False)
    assert moved["own"]["print_p"] <= 0.01 and moved["own"]["exact"] and moved["own"]["splits"] == 924
    assert moved["like_for_like"]["print_p"] <= 0.01
    quiet = [R.compare(houses, h, _in(H1), _in(H2), ans, rng, n_perm=199, market=False)["own"]["print_p"]
             for h in centres if h != "b0"]
    assert np.median(quiet) > 0.2


def test_moving_with_the_market_is_told_from_holding_while_it_moves():
    rng = np.random.default_rng(2)
    centres = _brands(rng)
    drift = 2.5 * rng.normal(size=16) / 4
    ims = []
    for h, c in centres.items():
        ims += _cloud(h, c, H1, rng, noise=0.4)
        ims += _cloud(h, c + (0 if h == "b1" else drift), H2, rng, noise=0.4)
    houses, ans = R.by_house(ims), _answers()
    follower = R.compare(houses, "b0", _in(H1), _in(H2), ans, rng, n_perm=199)
    holder = R.compare(houses, "b1", _in(H1), _in(H2), ans, rng, n_perm=199)
    assert follower["own"]["print_p"] <= 0.05 and follower["against_market"]["print_p"] > 0.05
    assert R.reading(follower, "print") == "moved with the market"
    assert holder["own"]["print_p"] > 0.05 and holder["against_market"]["print_p"] <= 0.05
    assert R.reading(holder, "print") == "held while the market moved"
    assert follower["market_brands"] == 7


def test_showing_more_product_is_not_taken_for_a_change_of_style():
    """Campaign pictures and product on a model look different in every brand. One brand moves from mostly
    campaign pictures to mostly product: its homepage changes, but each kind is where it was, on its own and
    against the same kind elsewhere. Over several draws, like for like stays at about the rate chance gives."""
    lfl, mkt = [], []
    for seed in range(8):
        rng = np.random.default_rng(30 + seed)
        centres = _brands(rng)
        kinds = {"campaign": 2.0 * rng.normal(size=16) / 4, "on_model": 2.0 * rng.normal(size=16) / 4}
        ims = []
        for h, c in centres.items():
            for months, share in ((H1, 5), (H2, 1)):
                for m in months:
                    n_camp = share if h == "b0" else 3
                    for i in range(6):
                        k = "campaign" if i < n_camp else "on_model"
                        ims.append(_pic(h, m, i, c + kinds[k] + rng.normal(scale=0.4, size=16), k))
        got = R.compare(R.by_house(ims), "b0", _in(H1), _in(H2), _answers(), rng, n_perm=199)
        assert got["own"]["print_p"] <= 0.01                               # its homepage changed
        assert got["like_for_like"]["mix"] == {"campaign": 0.5, "on_model": 0.5}
        assert set(got["against_market"]["kinds"]) == {"campaign", "on_model"}
        lfl.append(got["like_for_like"]["print_p"])
        mkt.append(got["against_market"]["print_p"])
    assert sum(p <= 0.05 for p in lfl) <= 1 and sum(p <= 0.05 for p in mkt) <= 1     # its style did not


def test_unequal_numbers_of_pictures_do_not_make_a_change():
    """Four pictures a month in one half-year and one a month in the next, each month a campaign of its
    own, and no change: the variance-scaled tests stay quiet about as often as chance allows."""
    ps = []
    for seed in range(12):
        rng = np.random.default_rng(60 + seed)
        centres = _brands(rng)
        ims = []
        for h, c in centres.items():
            for months, per in ((H1, 4 if h == "b0" else 2), (H2, 1 if h == "b0" else 2)):
                for m in months:
                    campaign = rng.normal(scale=0.5, size=16)
                    ims += [_pic(h, m, i, c + campaign + rng.normal(scale=0.4, size=16)) for i in range(per)]
        got = R.compare(R.by_house(ims), "b0", _in(H1), _in(H2), _answers(), rng, n_perm=199)
        ps += [got["own"]["print_p"], got["like_for_like"]["print_p"], got["against_market"]["print_p"]]
    assert sum(p <= 0.05 for p in ps) <= 4


def test_the_screen_drops_answers_that_hardly_vary_or_are_given_differently_to_crops():
    rng = np.random.default_rng(4)
    ims = []
    for k in range(30):
        h, m = f"b{k % 6}", f"2024-{k % 12 + 1:02d}"
        v = rng.normal(size=16)
        people = ("one", "none", "group")[k % 3]
        for j in range(2):                                   # two crops of one picture
            setting = ("studio_plain", "interior", "street_urban", "landscape_nature")[rng.integers(4)]
            ims.append({**_pic(h, m, f"{k}-{j}", v + rng.normal(scale=0.01, size=16), people=people, setting=setting),
                        "sha": f"{h}-{m}-{k}-{j}"})
    got = R.screen(ims, SPEC)
    rows = got["answers"]
    assert got["duplicate_pairs"] == 30
    assert rows["people"]["keep"] and rows["people"]["kappa"] == 1.0
    assert not rows["light"]["keep"] and rows["light"]["top_share"] == 1.0
    assert not rows["setting"]["keep"] and rows["setting"]["kappa"] < R.MIN_KAPPA


def test_drift_is_told_from_turnover():
    """One brand's look turns a little further every half-year; another's gets a new campaign every
    half-year around the same centre. Both differ from one half-year to the next; only the first is
    farther from itself at four half-years than at two."""
    rng = np.random.default_rng(5)
    halves = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)]
    base, step = rng.normal(size=16), rng.normal(size=16)
    ims = []
    for k in range(6):
        months = halves[6 * k: 6 * k + 6]
        ims += _cloud("drifter", base + 0.8 * k * step / 4, months, rng, per=4, noise=0.5)
        ims += _cloud("turnover", base + 0.8 * rng.normal(size=16) / 4, months, rng, per=4, noise=0.5)
    got = R.by_lag(R.by_house(ims), _answers(), rng, draws=20)
    d, t = got["brands"]["drifter"]["print"], got["brands"]["turnover"]["print"]
    assert d["4"] - d["2"] > 0.1
    assert abs(t["4"] - t["2"]) < 0.08
    assert d["1"] > d["0"] and t["1"] > t["0"]


def test_shifts_are_counted_and_corrected_for_how_many_were_looked_at():
    assert R._bh([0.01, 0.04, 0.03, 0.5]) == [0.04, 0.0533, 0.0533, 0.5]
    rng = np.random.default_rng(6)
    centres = _brands(rng, n=6)
    ims = []
    for h, c in centres.items():
        ims += _cloud(h, c, H1, rng) + _cloud(h, c, H2, rng)
    rows = R.shifts(R.by_house(ims), _answers(), rng)
    s = R.shift_summary(rows)
    assert s["measured"] == 6 and s["own_print"]["tested"] == 6
    assert all("print_q" in r["own"] for v in rows.values() for r in v)
    assert s["own_by_answer"]["tested"] == 6 * 3


def test_an_event_is_set_beside_brands_without_a_change():
    rng = np.random.default_rng(7)
    centres = _brands(rng)
    months = [f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)]
    ims = []
    for h, c in centres.items():
        for m in months:
            after = h == "b0" and m >= "2025-01"
            ims += _cloud(h, c if not after else c + 2.0 * rng.normal(size=16) / 4, [m], rng, per=2)
    events = [{"house": "b0", "kind": "creative lead", "who": "new", "date": "2024-10-01", "verified": True},
              {"house": "b1", "kind": "creative lead", "who": "other", "date": "2024-11-01", "verified": True}]
    got = R.event_study(R.by_house(ims), events, _answers(), rng)
    row = [r for r in got["rows"] if r["house"] == "b0"][0]
    assert "b1" not in row["controls"] and len(row["controls"]) == 6
    assert row["own_print_score"] > row["controls_print_score"] and row["controls_print_above"] == 0.0
    assert got["summary"]["tested"] == 2


def test_measures_on_their_own_scales_are_compared_by_distance():
    """Positions on an axis or colour and light are not directions: with euclid on, brands far apart on
    the measures stand far apart, though their centroids point the same way."""
    rng = np.random.default_rng(7)
    months = H1 + H2
    ims = []
    for k, scale in enumerate((1.0, 4.0, 1.2, 3.8, 0.9, 4.2)):
        for m in months:
            for i in range(3):
                v = scale * np.ones(5) + rng.normal(scale=0.3, size=5)
                ims.append({"house": f"b{k}", "month": m, "sha": f"b{k}-{m}-{i}", "type": "campaign",
                            "period": period_of(m), "out": _out(), "vec": v, "dup": None})
    R.EUCLID = True
    try:
        far = R._distinct(R.by_house(ims), _answers(), rng, n_sub=6, draws=20)
    finally:
        R.EUCLID = False
    near = R._distinct(R.by_house(ims), _answers(), rng, n_sub=6, draws=20)
    assert far["b1"]["print"] > 1.0 > near["b1"]["print"] * 100       # the cosine sees one direction only
    assert R._away(np.ones(3), 2 * np.ones(3)) < 1e-9
    R.EUCLID = True
    try:
        assert abs(R._away(np.ones(3), 2 * np.ones(3)) - 3 ** 0.5) < 1e-9
    finally:
        R.EUCLID = False


def test_crops_are_found_by_the_fingerprint_kept_apart():
    rng = np.random.default_rng(8)
    c = R._unit(rng.normal(size=16))
    a = {"house": "a", "month": "2024-01", "sha": "x", "vec": np.array([1.0, 2.0]), "dup": c}
    b = {"house": "a", "month": "2024-01", "sha": "y", "vec": np.array([5.0, -3.0]), "dup": c}
    assert len(R.duplicate_pairs([a, b])) == 1


def test_brands_moving_towards_the_brand_of_the_moment_are_found_and_still_brands_are_not():
    rng = np.random.default_rng(11)
    halves = [("2024", 1), ("2024", 7), ("2025", 1), ("2025", 7), ("2026", 1)]
    centres = {f"b{k}": rng.normal(size=16) for k in range(8)}
    leaders = {"2024H1": "b0", "2024H2": "b1", "2025H1": "b2", "2025H2": "b3", "2026H1": "b4"}
    momentum = {h: {p: (1.0 if leaders.get(p) == h else 0.0) for p in leaders} for h in centres}

    def world(follow):
        cur = {h: c.copy() for h, c in centres.items()}
        ims = []
        for y, m0 in halves:
            p = f"{y}H{1 if m0 == 1 else 2}"
            for h, c in cur.items():
                for m in range(m0, m0 + 6):
                    month = f"{y}-{m:02d}"
                    for i in range(2):
                        v = c + rng.normal(scale=0.4, size=16)
                        ims.append({"house": h, "month": month, "sha": f"{h}-{month}-{i}", "type": "campaign",
                                    "period": period_of(month), "out": _out(), "vec": v / np.linalg.norm(v)})
            if follow:      # everyone but the leader moves a third of the way towards it
                lead = cur[leaders[p]].copy()
                cur = {h: (c if h == leaders[p] else c + (lead - c) / 3) for h, c in cur.items()}
        return ims
    moved = R.moment(world(True), rng, momentum=momentum, n_perm=199)
    still = R.moment(world(False), rng, momentum=momentum, n_perm=199)
    assert moved["transitions"] == 4 and [r["leader"] for r in moved["half_years"]] == ["b0", "b1", "b2", "b3"]
    assert moved["towards_leader_less_any_brand"] < 0 and moved["p_one_sided"] < 0.05
    assert still["p_one_sided"] > 0.05


def test_ambassador_events_take_the_first_fashion_appointment_of_a_year_away_from_a_new_designer(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("house_id,person,role,category,announced_date,ended_date,source_urls,verified,notes\n"
                 "a,P1,global ambassador,fashion,2024-03-05,,u,true,\n"
                 "a,P2,global ambassador,fashion,2024-01-10,,u,true,\n"
                 "a,P3,ambassador,fragrance,2023-02-01,,u,true,\n"
                 "b,P4,ambassador,fashion,2024-06-01,,u,true,\n"
                 "c,P5,ambassador,fashion,2025-06-01,,u,false,\n", encoding="utf-8")
    leads = [{"house": "b", "kind": "creative lead", "date": "2024-10-01"}]
    ev = R.ambassador_events(f, leads=leads)
    assert [(e["house"], e["who"]) for e in ev] == [("a", "P2")]
