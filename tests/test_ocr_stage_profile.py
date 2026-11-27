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


def test_detector_details_keep_three_distinct_non_overlapping_stages():
    from types import SimpleNamespace
    from scripts.benchmark_ocr_pipeline import instrument_detector

    detector = SimpleNamespace(
        get_preprocess=lambda _: lambda img: img + 1,
        infer=lambda tensor: tensor * 2,
        postprocess_op=lambda outputs, shape: (outputs, shape),
    )
    observations = []
    readings = iter([10, 20, 20, 70, 70, 74])
    instrument_detector(detector, observations, lambda: {"os_peak_bytes": next(readings)})
    resized = detector.get_preprocess(10)(3)
    result = detector.postprocess_op(detector.infer(resized), (4, 8))
    assert result == (8, (4, 8))
    assert [x["stage"] for x in observations] == ["detector_preprocess", "detector_onnx", "detector_postprocess"]
    assert dominant_stage(observations)["stage"] == "detector_onnx"
