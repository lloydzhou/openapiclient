"""Shared pytest fixtures: isolated registry, canned HTTP responses."""

import json
import os
import pathlib

import httpx
import pytest
import yaml

from openapiclient import cli

HERE = pathlib.Path(__file__).parent
SPEC_FILE = HERE / "petstore.json"


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Point the CLI registry at a throwaway XDG config dir for every test."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


@pytest.fixture
def spec_path():
    return str(SPEC_FILE)


@pytest.fixture
def petstore(spec_path):
    """CliRunner with petstore connected."""
    from click.testing import CliRunner

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli.main, ["connect", "petstore", spec_path])
    assert result.exit_code == 0, result.output + str(result.stderr)
    return runner


class FakeHTTP:
    """Records httpx.request calls; serves canned responses."""

    def __init__(self, responses):
        # responses: list of (status, payload) consumed in order, or a callable
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": str(url), **kwargs})
        item = self.responses.pop(0) if self.responses else None
        if callable(item):
            result = item(self.calls[-1])
            if isinstance(result, httpx.Response):
                return result
            status, payload = result
        else:
            status, payload = item if item else (200, {})
        request = httpx.Request(method, url)
        return httpx.Response(
            status,
            json=payload,
            headers={"content-type": "application/json"},
            request=request,
        )


@pytest.fixture
def fake_http(monkeypatch):
    def install(*responses):
        fake = FakeHTTP(responses or [(200, {})])
        monkeypatch.setattr(httpx, "request", fake)
        return fake

    return install


@pytest.fixture
def cyclic_spec():
    """YAML-alias self-referential schema: crashes the old resolver."""
    text = """
components:
  schemas:
    Node:
      type: object
      properties:
        value: {type: string}
        next: &node
          type: object
          properties:
            value: {type: integer}
            next: *node
"""
    return yaml.safe_load(text)
