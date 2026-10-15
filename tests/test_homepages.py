

def test_a_priority_house_is_read_first_newest_month_first():
    from adtone import homepages as HP
    index = {"a": {"2026-01": ["x"], "2025-12": ["x"]}, "b": {"2026-01": ["x"], "2014-03": ["x"], "2014-02": ["x"]}}
    assert HP.plan(index, {}, first=[])[:2] == [("a", "2026-01"), ("b", "2026-01")]
    assert HP.plan(index, {}, first=["b"]) == [("b", "2026-01"), ("b", "2014-03"), ("b", "2014-02"),
                                                ("a", "2026-01"), ("a", "2025-12")]
    # houses added together fill their recent years together, rather than one house's whole history at a time
    index["c"] = {"2026-01": ["x"], "2025-12": ["x"], "2014-02": ["x"]}
    assert HP.plan(index, {}, first=["b", "c"])[:5] == [("b", "2026-01"), ("c", "2026-01"), ("c", "2025-12"),
                                                         ("b", "2014-03"), ("b", "2014-02")]
