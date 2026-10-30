import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.main import (
    AGENTS,
    HTTPException,
    default_profile,
    keyword_score,
    parse_subtitles,
    parse_wecom_question,
    sha1_signature,
    text_generation_provider,
    transcription_provider,
    validate_agent,
)


def test_srt_keeps_time_ranges():
    chunks = parse_subtitles("1\n00:00:01,000 --> 00:00:04,000\nHello, everyone.\n\n2\n00:00:05,000 --> 00:00:08,000\nLet's compare two sentences.", ".srt")
    assert chunks[0]["start"] == 1
    assert "compare" in chunks[0]["text"]


def test_chinese_method_question_matches_without_word_segmentation():
    assert keyword_score("阅读方法和选项核对", "课程强调阅读方法：先抓中心，再核对选项。") > 0


def test_plain_text_becomes_chunks():
    chunks = parse_subtitles("First paragraph.\n\nSecond paragraph.", ".txt")
    assert len(chunks) == 2
    assert chunks[1]["start"] is None


def test_signature_is_order_independent():
    assert sha1_signature("token", "2", "1") == sha1_signature("1", "token", "2")


def test_only_three_exam_agents_are_accepted():
    assert set(AGENTS) == {"cet4", "cet6", "postgrad"}
    with pytest.raises(HTTPException):
        validate_agent("ielts")


@pytest.mark.parametrize(
    ("message", "agent", "question"),
    [
        ("四级：这道题为什么选C？", "cet4", "这道题为什么选C？"),
        ("六级: 如何写主题句？", "cet6", "如何写主题句？"),
        ("考研 帮我检查作文结构", "postgrad", "帮我检查作文结构"),
        ("没有指定层级的问题", "cet4", "没有指定层级的问题"),
    ],
)
def test_wecom_selects_the_requested_level(message, agent, question):
    assert parse_wecom_question(message) == (agent, question)


def test_three_agents_have_independent_profiles():
    profiles = {key: default_profile() for key in AGENTS}
    profiles["cet4"]["reading_methods"].append("四级专属方法")
    assert "四级专属方法" not in profiles["cet6"]["reading_methods"]


def test_auto_transcription_prefers_local_without_api_key(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_PROVIDER", "auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("app.main.importlib.util.find_spec", lambda name: object())
    assert transcription_provider() == "local"


def test_transcription_can_fall_back_to_openai(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_PROVIDER", "auto")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr("app.main.importlib.util.find_spec", lambda name: None)
    assert transcription_provider() == "openai"


def test_background_job_progress_is_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_PATH", tmp_path / "jobs.json")
    job = {"id": "job_test", "status": "queued", "current": 0, "total": 2}
    main.create_job(job)
    main.update_job("job_test", status="running", current=1, success_count=1)
    saved = json.loads(main.JOBS_PATH.read_text(encoding="utf-8"))
    assert saved["items"][0]["status"] == "running"
    assert saved["items"][0]["current"] == 1


def test_auto_text_generation_prefers_running_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "ollama_ready", lambda: True)
    assert text_generation_provider() == "ollama"


def test_text_generation_falls_back_to_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(main, "ollama_ready", lambda: False)
    assert text_generation_provider() == "openai"


def test_compatible_cloud_provider_requires_complete_configuration(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "compatible")
    monkeypatch.setenv("COMPATIBLE_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("COMPATIBLE_API_KEY", "test-only")
    monkeypatch.setenv("COMPATIBLE_MODEL", "qwen-test")
    assert text_generation_provider() == "compatible"


def test_cloud_password_protects_api(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "long-test-password")
    monkeypatch.setenv("APP_ACCESS_USER", "student")
    with TestClient(main.app) as client:
        assert client.get("/api/library").status_code == 401
        login = client.post(
            "/login",
            data={"username": "student", "password": "long-test-password"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert client.get("/api/library").status_code == 200
