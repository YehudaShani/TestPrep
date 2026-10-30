import pytest
import json
import re
from pathlib import Path

from pdf_splitter.auth_config import auth_config


def test_auth_defaults_to_guest(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    assert auth_config() == {}


@pytest.mark.parametrize("key", ["", "sb_secret_private", "legacy.jwt.key"])
def test_incomplete_or_private_configuration_is_rejected(monkeypatch, key):
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", key)
    with pytest.raises(ValueError):
        auth_config()


def test_public_configuration(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co/")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_example")
    assert auth_config() == {
        "url": "https://project.supabase.co",
        "publishableKey": "sb_publishable_example",
    }


def test_saved_public_configuration_is_validated_and_environment_overrides_it(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    saved = {"url": "https://saved.supabase.co", "publishableKey": "sb_publishable_saved"}
    assert auth_config(saved) == saved
    with pytest.raises(ValueError):
        auth_config({**saved, "publishableKey": "sb_secret_private"})
    monkeypatch.setenv("SUPABASE_URL", "https://override.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_override")
    assert auth_config(saved)["url"] == "https://override.supabase.co"


def test_static_rebuild_keeps_saved_sign_in_without_environment(monkeypatch, tmp_path):
    from pdf_splitter.build_site import build

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    saved = {"url": "https://saved.supabase.co", "publishableKey": "sb_publishable_saved"}
    (tmp_path / "site-auth.json").write_text(json.dumps(saved), encoding="utf-8")
    out = tmp_path / "site"
    build([], out, 30, base=tmp_path)
    build([], out, 30, base=tmp_path)
    config = json.loads(re.search(r"window.STUDY_CONFIG = (.*);", (out / "index.html").read_text(encoding="utf-8"))[1])
    assert config["auth"] == saved


def test_local_page_embeds_auth_at_the_top_level(monkeypatch, tmp_path):
    from pdf_splitter.courses import Course
    from pdf_splitter.study_server import ServedCourse, StudyHandler

    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_example")
    handler = object.__new__(StudyHandler)
    handler.courses = {"test": ServedCourse(Course("test", "Test", "test", "old"), tmp_path)}
    sent = []
    handler._send = lambda *args: sent.append(args)
    handler._serve_page()
    page = sent[0][2].decode()
    config = json.loads(re.search(r"window.STUDY_CONFIG = (.*);", page)[1])
    assert config["auth"]["publishableKey"] == "sb_publishable_example"
    assert config["courses"][0]["id"] == "test"


def test_static_build_embeds_public_auth_and_keeps_exam_metadata_clean(monkeypatch, tmp_path):
    import shutil
    from pdf_splitter.build_site import build
    from pdf_splitter.courses import Course

    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_example")
    exam = tmp_path / "input" / "exam"
    exam.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parent / "fixtures" / "sample.pdf", exam / "question_01.pdf")
    out = tmp_path / "site"
    build([Course("test", "Test", "input", "old")], out, 30, base=tmp_path)
    page = (out / "index.html").read_text(encoding="utf-8")
    config = json.loads(re.search(r"window.STUDY_CONFIG = (.*);", page)[1])
    assert config["auth"]["url"] == "https://project.supabase.co"
    assert "window.STATIC_SITE = true;" in page
    exams = json.loads((out / "test" / "exams.json").read_text(encoding="utf-8"))
    assert "auth" not in exams[0]
    assert (out / "test" / "img" / "exam" / "question_01.webp").is_file()
