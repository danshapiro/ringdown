"""Pytest entry-point for the live call CLI."""

from click.testing import CliRunner
import pytest

from tests import live_test_call

pytestmark = pytest.mark.live


@pytest.mark.live
def test_live_call_via_cli():
    runner = CliRunner()
    result = runner.invoke(live_test_call.main, [], catch_exceptions=False)
    assert result.exit_code == 0, result.output
