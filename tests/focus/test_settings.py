from focus.settings import FocusSettings


def test_settings_defaults_match_demo_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FOCUS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WATCHER_PAIRING_CODE", raising=False)
    settings = FocusSettings(_env_file=None)
    assert settings.focus_port == 8780
    assert settings.focus_demo_duration_seconds == 90
    assert settings.focus_demo_capture_interval_seconds == 10
    assert settings.focus_batch_size == 4
    assert settings.focus_max_frames_per_session == 100
    assert settings.stepfun_vlm_model == "step3-vl-focus"
    assert settings.watcher_pairing_code.get_secret_value() == ""
    assert "123456" not in repr(settings)
