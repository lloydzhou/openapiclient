"""Tests for the oapi CLI: registry, dynamic commands, body handling, output contract."""

import json

import pytest

from openapiclient import cli


# ---------------------------------------------------------------- registry


def test_connect_ls_rm(petstore, isolated_registry):
    result = petstore.invoke(cli.main, ["ls"])
    assert result.exit_code == 0
    assert "petstore" in result.output
    assert "4 ops" in result.output

    result = petstore.invoke(cli.main, ["rm", "petstore", "--yes"])
    assert result.exit_code == 0

    result = petstore.invoke(cli.main, ["ls"])
    assert "petstore" not in result.output


def test_connect_rejects_path_traversal_name(petstore, spec_path):
    result = petstore.invoke(cli.main, ["connect", "../evil", spec_path])
    assert result.exit_code != 0
    assert "invalid API name" in result.stderr


def test_connect_rejects_non_openapi_document(petstore, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"hello": "world"}))
    result = petstore.invoke(cli.main, ["connect", "bad", str(bad)])
    assert result.exit_code != 0
    assert "does not look like" in result.stderr


def test_connect_twice_requires_force(petstore, spec_path):
    result = petstore.invoke(cli.main, ["connect", "petstore", spec_path])
    assert result.exit_code != 0
    assert "--force" in result.stderr
    result = petstore.invoke(cli.main, ["connect", "petstore", spec_path, "--force"])
    assert result.exit_code == 0


def test_unknown_api_error(petstore):
    result = petstore.invoke(cli.main, ["nosuchapi", "get-pet", "1"])
    assert result.exit_code == 2  # click usage error for unknown commands
    assert "No such command" in result.stderr


# ---------------------------------------------------------------- commands


def test_schema_lists_operations(petstore):
    result = petstore.invoke(cli.main, ["schema", "petstore"])
    assert result.exit_code == 0
    assert "list-pets" in result.output
    assert "get-pet" in result.output
    # fallback name for the operation without operationId
    assert "delete-pets-pet-id" in result.output


def test_schema_single_operation(petstore):
    result = petstore.invoke(cli.main, ["schema", "petstore", "get-pet"])
    assert result.exit_code == 0
    info = json.loads(result.output)
    assert info["command"] == "get-pet"
    assert info["method"] == "GET"
    pet_id = next(p for p in info["parameters"] if p["name"] == "petId")
    assert pet_id["in"] == "path" and pet_id["required"] and pet_id["type"] == "integer"


def test_help_shows_positional_and_choices(petstore):
    result = petstore.invoke(cli.main, ["petstore", "get-pet", "--help"])
    assert result.exit_code == 0
    assert "PETID" in result.output  # positional argument

    result = petstore.invoke(cli.main, ["petstore", "list-pets", "--help"])
    assert "[available|sold]" in result.output  # enum -> Choice
    assert "--limit INTEGER" in result.output


def test_operation_id_alias_accepted(petstore, fake_http):
    fake_http((200, {"id": 1, "name": "Rex"}))
    result = petstore.invoke(cli.main, ["petstore", "getPet", "1"])
    assert result.exit_code == 0
    assert json.loads(result.output)["name"] == "Rex"


# ---------------------------------------------------------------- requests


def test_get_request_renders_path_and_query(petstore, fake_http):
    fake = fake_http((200, [{"id": 1}, {"id": 2}]))
    result = petstore.invoke(
        cli.main, ["petstore", "list-pets", "--limit", "5", "--status", "available",
                   "--tags", "dog", "--tags", "cat"])
    assert result.exit_code == 0, result.stderr
    (call,) = fake.calls
    assert call["method"] == "GET"
    assert call["url"] == "http://api.example.com/v1/pets"
    assert call["params"] == {"limit": 5, "status": "available", "tags": "dog,cat"}
    assert json.loads(result.output) == [{"id": 1}, {"id": 2}]


def test_ref_path_parameter_becomes_positional(petstore, fake_http):
    fake = fake_http((200, {"id": 42}))
    result = petstore.invoke(cli.main, ["petstore", "get-pet", "42"])
    assert result.exit_code == 0
    assert fake.calls[0]["url"] == "http://api.example.com/v1/p1".replace("p1", "pets/42")


def test_enum_rejects_unknown_value(petstore):
    result = petstore.invoke(cli.main, ["petstore", "list-pets", "--status", "bogus"])
    assert result.exit_code == 2  # click usage error
    assert "available" in (result.stderr or result.output)


def test_missing_required_positional(petstore):
    result = petstore.invoke(cli.main, ["petstore", "get-pet"])
    assert result.exit_code == 2


def test_body_inline_json(petstore, fake_http):
    fake = fake_http((201, {"ok": True}))
    result = petstore.invoke(cli.main, ["petstore", "create-pet", "--body", '{"name": "Rex"}'])
    assert result.exit_code == 0
    assert fake.calls[0]["json"] == {"name": "Rex"}


def test_body_from_file(petstore, fake_http, tmp_path):
    f = tmp_path / "body.json"
    f.write_text('{"name": "FromDisk"}')
    fake = fake_http((201, {"ok": True}))
    result = petstore.invoke(cli.main, ["petstore", "create-pet", "--body", f"@{f}"])
    assert result.exit_code == 0
    assert fake.calls[0]["json"] == {"name": "FromDisk"}


def test_body_from_stdin(petstore, fake_http):
    fake = fake_http((201, {"ok": True}))
    result = petstore.invoke(cli.main, ["petstore", "create-pet", "--body", "-"],
                             input='{"name": "Piped"}')
    assert result.exit_code == 0
    assert fake.calls[0]["json"] == {"name": "Piped"}


def test_body_and_field_are_mutually_exclusive(petstore):
    result = petstore.invoke(
        cli.main, ["petstore", "create-pet", "--body", "{}", "-F", "a=1"])
    assert result.exit_code != 0
    assert "not both" in result.stderr


def test_field_smart_types(petstore, fake_http):
    fake = fake_http((201, {"ok": True}))
    result = petstore.invoke(
        cli.main,
        ["petstore", "create-pet", "-F", "name=Rex", "-F", "vaccinated=true",
         "-F", "weight=12.5", "-F", "note=plain text"])
    assert result.exit_code == 0
    body = fake.calls[0]["json"]
    assert body["vaccinated"] is True
    assert body["weight"] == 12.5
    assert body["note"] == "plain text"


def test_non_2xx_exits_1_and_keeps_stdout_clean(petstore, fake_http):
    fake_http((404, {"error": "pet not found"}))
    result = petstore.invoke(cli.main, ["petstore", "get-pet", "999"])
    assert result.exit_code == 1
    assert result.output == ""          # data only on stdout
    assert "404" in result.stderr       # diagnostics on stderr


def test_dry_run_does_not_send(petstore, fake_http):
    fake = fake_http((200, {}))
    result = petstore.invoke(cli.main, ["--dry-run", "petstore", "get-pet", "7"])
    assert result.exit_code == 0
    assert fake.calls == []
    preview = json.loads(result.output)
    assert preview["url"].endswith("/pets/7")


def test_extra_headers_sent(petstore, fake_http):
    fake = fake_http((200, {}))
    result = petstore.invoke(
        cli.main, ["--header", "X-Trace: abc", "petstore", "list-pets"])
    assert result.exit_code == 0
    assert fake.calls[0]["headers"]["X-Trace"] == "abc"


# ---------------------------------------------------------------- output


def test_jq_filters_output(petstore, fake_http):
    fake_http((200, {"items": [{"id": 1}, {"id": 2}]}))
    result = petstore.invoke(cli.main, ["--jq", "items[].id", "petstore", "list-pets"])
    assert json.loads(result.output) == [1, 2]

    fake_http((200, {"body": {"name": "Rex"}}))
    result = petstore.invoke(cli.main, ["--jq", ".body.name", "petstore", "get-pet", "1"])
    assert result.output.strip() == '"Rex"'


def test_jq_miss_reports_error(petstore, fake_http):
    fake_http((200, {"a": 1}))
    result = petstore.invoke(cli.main, ["--jq", "nope", "petstore", "list-pets"])
    assert result.exit_code != 0
    assert "selected nothing" in result.stderr


def test_output_text_mode(petstore, fake_http):
    fake_http((200, "just-a-string"))
    result = petstore.invoke(cli.main, ["-o", "text", "petstore", "list-pets"])
    assert result.output == "just-a-string\n"


def test_non_json_response_returned_as_text(petstore, fake_http):
    import httpx

    def plain(call):
        return httpx.Response(200, text="hello",
                              request=httpx.Request(call["method"], call["url"]))

    fake_http(plain)
    result = petstore.invoke(cli.main, ["petstore", "list-pets"])
    assert result.output == '"hello"\n'


# ---------------------------------------------------------------- raw api


def test_raw_api_escape_hatch(petstore, fake_http):
    fake = fake_http((200, {"ok": 1}))
    result = petstore.invoke(
        cli.main, ["api", "petstore", "GET", "/v1/pets", "--params", '{"limit": "3"}'])
    assert result.exit_code == 0
    (call,) = fake.calls
    assert call["method"] == "GET"
    assert call["url"] == "http://api.example.com/v1/pets"
    assert call["params"] == {"limit": "3"}
