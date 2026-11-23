import voice


def test_interrupted_reply_keeps_only_what_was_heard(monkeypatch, tmp_path):
    f = tmp_path / "v.onnx"; f.write_text("x")
    monkeypatch.setattr(voice, "VOICE", str(f))
    monkeypatch.setattr(voice, "_synthesize_wav", lambda t: t)
    import barge_in
    class M:
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(barge_in, "MicMonitor", M)
    plays = []
    def play(wav, monitor):
        plays.append(wav)
        return len(plays) == 3          # cut in during the 3rd sentence
    monkeypatch.setattr(voice, "_play_wav_interruptible", play)
    cut = voice.speak_streamed("One is here. Two is here. Three is lost. Four is lost.", interruptible=True)
    assert cut is True
    assert voice.last_spoken == "One is here. Two is here."


def test_uninterrupted_reply_is_fully_spoken(monkeypatch, tmp_path):
    f = tmp_path / "v.onnx"; f.write_text("x")
    monkeypatch.setattr(voice, "VOICE", str(f))
    monkeypatch.setattr(voice, "_synthesize_wav", lambda t: t)
    monkeypatch.setattr(voice, "_play_wav", lambda w: None)
    assert voice.speak_streamed("One here. Two here.") is False
    assert voice.last_spoken == "One here. Two here."
