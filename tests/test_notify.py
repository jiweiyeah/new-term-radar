import json
import urllib.error
from types import SimpleNamespace

import pytest

from radar import __main__ as cli, notify


@pytest.fixture(autouse=True)
def no_real_notifications(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("notification tests must not access the network")
    monkeypatch.setattr(notify.urllib.request, "urlopen", blocked)
    monkeypatch.setattr(notify.time, "sleep", lambda seconds: None)


class Response:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data


def capture_requests(monkeypatch, result=b'{"code": 0}'):
    requests = []

    def open_request(req, timeout):
        requests.append((req, timeout))
        return Response(result)

    monkeypatch.setattr(notify.urllib.request, "urlopen", open_request)
    monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    return requests


def test_notification_env_loads_only_known_settings(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# optional notifications\n\nexport FEISHU_WEBHOOK='local-hook'\n"
        'RESEND_API_KEY="local-key"\nEMAIL_FROM = sender@example.com\n'
        "RADAR_EMAIL_TO=one@example.com,two@example.com\n"
        "RADAR_NOTIFY_TEST_OTHER=must-not-be-loaded\ninvalid-line\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("RADAR_NOTIFY_TEST_OTHER", raising=False)
    # Record environment modifications so this file's values cannot leak to later tests.
    for key in notify.ENV_KEYS:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.setenv("RESEND_API_KEY", "exported-key")
    monkeypatch.setenv("EMAIL_FROM", "")   # empty exported values disable a setting
    notify.load_env(path)
    assert notify.os.environ["FEISHU_WEBHOOK"] == "local-hook"
    assert notify.os.environ["RESEND_API_KEY"] == "exported-key"
    assert notify.os.environ["EMAIL_FROM"] == ""
    assert notify.os.environ["RADAR_EMAIL_TO"] == "one@example.com,two@example.com"
    assert "RADAR_NOTIFY_TEST_OTHER" not in notify.os.environ


def test_notification_env_missing_file_is_optional(tmp_path):
    notify.load_env(tmp_path / ".env")


@pytest.mark.parametrize("missing", notify.ENV_KEYS[1:])
def test_mail_skips_any_missing_setting(monkeypatch, missing):
    for key, value in (
        ("RESEND_API_KEY", "key"),
        ("EMAIL_FROM", "sender@example.com"),
        ("RADAR_EMAIL_TO", "recipient@example.com"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)
    assert notify.send("标题", "正文").startswith("mail skipped:")
    monkeypatch.setenv(missing, " \t ")
    assert notify.send("标题", "正文").startswith("mail skipped:")


def test_mail_complete_settings_still_send_html(monkeypatch):
    requests = capture_requests(monkeypatch)
    monkeypatch.setenv("RESEND_API_KEY", "key")
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("RADAR_EMAIL_TO", "one@example.com，two@example.com")
    assert notify.send("新词报告", "| a | b |\n|---|---|\n| 1 | 2 |") == "mail sent (200)"
    req, timeout = requests[0]
    data = json.loads(req.data)
    assert req.full_url == "https://api.resend.com/emails" and timeout == 30
    assert req.get_header("Authorization") == "Bearer key"
    assert data["to"] == ["one@example.com", "two@example.com"]
    assert data["subject"] == "新词报告" and "<table style=" in data["html"]


def test_feishu_skips_without_webhook(monkeypatch):
    assert notify.send_feishu("s", "x").startswith("feishu skipped:")
    monkeypatch.setenv("FEISHU_WEBHOOK", "  ")
    assert notify.send_feishu("s", "x").startswith("feishu skipped:")


@pytest.mark.parametrize("webhook", [
    "http://open.feishu.cn/open-apis/bot/v2/hook/test",
    "https://example.com/open-apis/bot/v2/hook/test",
    "https://open.feishu.cn/other",
    "https://open.feishu.cn/open-apis/bot/v2/hook/",
])
def test_feishu_rejects_invalid_endpoint_without_logging_it(monkeypatch, webhook):
    monkeypatch.setenv("FEISHU_WEBHOOK", webhook)
    result = notify.send_feishu("s", "x")
    assert result.startswith("feishu failed:") and webhook not in result


def test_feishu_sends_complete_chinese_report(monkeypatch):
    requests = capture_requests(monkeypatch)
    text = "## 新词雷达\n\n| 名字 | 来源 |\n|---|---|\n| 测试🚀 | https://example.com |\n"
    assert notify.send_feishu("日报", text) == "feishu sent (1 message(s))"
    req, timeout = requests[0]
    assert req.get_method() == "POST" and timeout == 30
    assert req.full_url == notify.os.environ["FEISHU_WEBHOOK"]
    assert req.get_header("Content-type") == "application/json; charset=utf-8"
    assert json.loads(req.data) == {"msg_type": "text", "content": {"text": "日报\n\n" + text}}


@pytest.mark.parametrize("text", [
    ("## 新词🚀\n|中文|数据|\n|---|---|\n" * 2000),
    ('🚀中文字"\\\t\n' * 9000),
    ("没有换行的超长中文🚀" * 5000),
])
def test_feishu_long_reports_preserve_every_character(text, monkeypatch):
    requests = capture_requests(monkeypatch)
    result = notify.send_feishu("报告", text)
    total = len(requests)
    assert total > 1 and result == f"feishu sent ({total} message(s))"
    chunks = []
    for i, (req, _) in enumerate(requests, 1):
        assert len(req.data) <= notify.FEISHU_MAX_BYTES
        message = json.loads(req.data)["content"]["text"]
        header = f"报告 ({i}/{total})\n\n"
        assert message.startswith(header)
        chunks.append(message[len(header):])
    assert "".join(chunks) == text


@pytest.mark.parametrize("answer", [
    b'{"code":0,"msg":"success"}',
    b'{"StatusCode":0,"StatusMessage":"success"}',
])
def test_feishu_supports_success_response_formats(monkeypatch, answer):
    capture_requests(monkeypatch, answer)
    assert notify.send_feishu("s", "x").startswith("feishu sent")


@pytest.mark.parametrize("answer", [
    b'{"code":19024,"msg":"private details"}',
    b'{"StatusCode":19021,"StatusMessage":"private details"}',
    b'{"code":null}',
    b'{"code":false}',
    b'{"code":"private details"}',
    b'{}',
    b'[]',
    b'<html>private details</html>',
    b'\xff',
])
def test_feishu_rejects_errors_and_invalid_responses(monkeypatch, answer):
    capture_requests(monkeypatch, answer)
    result = notify.send_feishu("s", "x")
    assert result.startswith("feishu failed (1/1):") and "private details" not in result
    assert notify.os.environ["FEISHU_WEBHOOK"] not in result


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("https://private-webhook", 429, "private details", {}, None),
    urllib.error.URLError("https://private-webhook private details"),
    TimeoutError("private details"),
])
def test_feishu_network_errors_are_safe(monkeypatch, error):
    capture_requests(monkeypatch)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(notify.urllib.request, "urlopen", fail)
    result = notify.send_feishu("s", "x")
    assert result.startswith("feishu failed")
    assert "private" not in result


def test_feishu_stops_after_failed_part(monkeypatch):
    requests = capture_requests(monkeypatch, b'{"code":19024}')
    assert notify.send_feishu("s", "中文🚀" * 15000).startswith("feishu failed (1/")
    assert len(requests) == 1


def test_oversized_feishu_title_fails_without_sending(monkeypatch):
    requests = capture_requests(monkeypatch)
    assert notify.send_feishu("标题" * 10000, "正文") == "feishu failed: report title is too long"
    assert not requests


def test_finish_pushes_feishu_without_mail_and_keeps_file(tmp_path, monkeypatch, capsys):
    requests = capture_requests(monkeypatch)
    out = tmp_path / "report.md"
    cli._finish(SimpleNamespace(out=str(out), mail=False, feishu=False), "报告正文", "日报")
    assert out.read_text(encoding="utf-8") == "报告正文"
    assert len(requests) == 1 and "feishu sent" in capsys.readouterr().out


def test_mail_missing_settings_does_not_block_feishu(tmp_path, monkeypatch, capsys):
    requests = capture_requests(monkeypatch)
    cli._finish(SimpleNamespace(out=str(tmp_path / "report.md"), mail=True, feishu=False), "正文", "日报")
    out = capsys.readouterr().out
    assert "mail skipped:" in out and "feishu sent" in out and len(requests) == 1


def test_mail_failure_does_not_block_feishu(tmp_path, monkeypatch, capsys):
    requests = capture_requests(monkeypatch)
    out = tmp_path / "report.md"

    def fail(*args):
        raise SystemExit("private details")

    monkeypatch.setattr(notify, "send", fail)
    cli._finish(SimpleNamespace(out=str(out), mail=True, feishu=False), "正文", "日报")
    captured = capsys.readouterr()
    assert "feishu sent" in captured.out and len(requests) == 1
    assert "mail failed:" in captured.err and "private details" not in captured.err
    assert out.read_text(encoding="utf-8") == "正文"


def test_explicit_feishu_flag_without_configuration_skips(capsys):
    cli._finish(SimpleNamespace(out=None, mail=False, feishu=True), "正文", "日报")
    assert "feishu skipped:" in capsys.readouterr().out


def test_daily_loads_webhook_beside_config_before_sending(tmp_path, monkeypatch):
    requests = capture_requests(monkeypatch)
    monkeypatch.delenv("FEISHU_WEBHOOK")
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/from-env\n", encoding="utf-8")

    def daily(args, cfg, state):
        cli._finish(args, "测试日报", "标题")
        return 0

    monkeypatch.setattr(cli, "cmd_daily", daily)
    assert cli.main(["daily", "--config", str(path), "--state", str(tmp_path / "state"), "--mail"]) == 0
    assert len(requests) == 1
    assert requests[0][0].full_url.endswith("/from-env")


def test_quiet_sources_does_not_send_to_configured_feishu(tmp_path, monkeypatch):
    requests = capture_requests(monkeypatch)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli.sources, "x_new", lambda cfg, state: {"posts": 0, "terms": []})
    monkeypatch.setattr(cli.sources, "hf_new", lambda cfg, state: {"models": 0, "terms": []})
    assert cli.main(["sources", "--config", str(config), "--state", str(tmp_path / "state"), "--feishu"]) == 0
    assert not requests
