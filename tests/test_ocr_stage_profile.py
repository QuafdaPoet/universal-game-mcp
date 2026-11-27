from scripts.benchmark_ocr_pipeline import StageProbe, dominant_stage


def test_stage_wrapper_preserves_result_and_tags_peak():
    readings = iter([("before", 12), ("after", 30)])
    stages = []
    component = StageProbe(lambda a, b: a + b, "detector", stages,
                           lambda: {"os_peak_bytes": next(readings)[1]})
    assert component(a=2, b=3) == 5
    assert len(stages) == 1
    assert stages[0]["stage"] == "detector"
    assert stages[0]["before"]["os_peak_bytes"] == 12
    assert stages[0]["after"]["os_peak_bytes"] == 30
    assert dominant_stage(stages)["peak_increase_bytes"] == 18
