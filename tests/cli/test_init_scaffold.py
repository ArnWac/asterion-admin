"""asterion init scaffold sanity (PR-11 hotfix #1).

The scaffold previously wrote ``ASTERION_SECRET_KEY=change-me-in-production``
as the example value. That literal string is the one
``CoreAdminConfig.validate()`` actively rejects, so a newcomer running
``asterion init`` then ``asterion doctor`` got a confusing
"insecure default" error rather than the clearer "missing env var"
message.
"""

from __future__ import annotations

from typer.testing import CliRunner

from asterion.cli.main import app as cli_app


def test_init_writes_env_example(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli_app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "app.py").exists()
    assert (tmp_path / ".env.example").exists()


def test_init_env_example_does_not_use_rejected_default(tmp_path):
    runner = CliRunner()
    runner.invoke(cli_app, ["init", str(tmp_path)])

    body = (tmp_path / ".env.example").read_text(encoding="utf-8")

    # The validator hard-rejects this exact string. The scaffold must not
    # ship it as the example value.
    assert "ASTERION_SECRET_KEY=change-me-in-production" not in body


def test_init_env_example_prompts_for_real_secret(tmp_path):
    runner = CliRunner()
    runner.invoke(cli_app, ["init", str(tmp_path)])

    body = (tmp_path / ".env.example").read_text(encoding="utf-8")
    # Empty value triggers the "missing env var" message which is a much
    # clearer error than "insecure default".
    assert "ASTERION_SECRET_KEY=" in body
    # Helpful comment so users know how to fill it in.
    assert "openssl rand -hex 32" in body
