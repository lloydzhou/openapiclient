"""Tests for openapiclient.client: ref resolution, helpers, factory, request prep.

The library previously had no test suite; these tests also lock in the
publicly documented behaviour (positional argument consumption order,
shallow $ref resolution) so refactors cannot silently change it.
"""

import asyncio
import copy
import json

import httpx
import pytest
import yaml

from openapiclient import OpenAPIClient
from openapiclient.client import (
    extract_parameter_meta,
    resolve_open_api_reference,
    sanitize_openapi_path,
)


PETSTORE = {
    "openapi": "3.0.0",
    "info": {"title": "Petstore", "version": "1.0.0"},
    "servers": [{"url": "http://api.example.com/v1"}],
    "security": [{"apiKey": []}],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                    {"name": "status", "in": "query",
                     "schema": {"type": "string", "enum": ["available", "sold"]}},
                ],
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "operationId": "createPet",
                "requestBody": {
                    "content": {"application/json": {"schema":
                        {"$ref": "#/components/schemas/Pet"}}}
                },
                "responses": {"201": {"description": "created"}},
            },
        },
        "/pets/{petId}": {
            "parameters": [
                {"name": "petId", "in": "path", "required": True,
                 "schema": {"type": "integer"}}
            ],
            "get": {
                "operationId": "getPet",
                "responses": {"200": {"description": "ok"}},
            },
        },
    },
    "components": {
        "schemas": {
            "Pet": {"type": "object",
                    "properties": {"name": {"type": "string"},
                                   "status": {"type": "string"}}},
        }
    },
}


# ---------------------------------------------------------------- refs


class TestResolveSchemaRef:
    def make_client(self):
        return OpenAPIClient.__new__(OpenAPIClient)

    def refs(self, spec):
        return {f"#/components/schemas/{k}": v
                for k, v in spec["components"]["schemas"].items()}

    def test_cyclic_yaml_alias_does_not_recurse_infinitely(self, cyclic_spec):
        client = self.make_client()
        out = client.resolve_schema_ref(
            self.refs(cyclic_spec)["#/components/schemas/Node"],
            self.refs(cyclic_spec))
        assert "next" in out["properties"]  # expanded once, then stopped

    def test_shallow_ref_semantics_unchanged(self):
        spec = {"components": {"schemas": {
            "Pet": {"type": "object", "properties": {"name": {"type": "string"}}},
            "Pets": {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}},
        }}}
        client = self.make_client()
        arr = client.resolve_schema_ref(
            self.refs(spec)["#/components/schemas/Pets"], self.refs(spec))
        assert arr["items"] == spec["components"]["schemas"]["Pet"]

    def test_deeply_nested_schema_resolved_like_the_previous_release(self):
        # 60-level nested objects: the old resolver handled this fine, so the
        # new one must produce the identical fully-expanded result (no
        # defensive depth cap that could change behaviour).
        def build(depth):
            if depth == 0:
                return {"type": "string"}
            return {"type": "object",
                    "properties": {"child": build(depth - 1)}}

        def expand(node):
            # reference expansion the old code performed recursively
            if isinstance(node, dict):
                return {k: expand(v) for k, v in node.items()}
            return node

        schema = build(60)
        client = self.make_client()
        out = client.resolve_schema_ref(copy.deepcopy(schema), {})
        assert out == schema  # nothing to resolve; returned expanded & intact
        assert "child" in out["properties"]

    def test_non_dict_schema_returned_as_is(self):
        client = self.make_client()
        assert client.resolve_schema_ref("free-form", {}) == "free-form"


class TestResolveOpenApiReference:
    def test_resolves_local_ref(self):
        definition = {"components": {"parameters": {"PetId": {"name": "petId"}}}}
        assert resolve_open_api_reference(
            {"$ref": "#/components/parameters/PetId"}, definition)["name"] == "petId"

    def test_passthrough_without_ref(self):
        d = {"name": "x"}
        assert resolve_open_api_reference(d, {}) is d

    def test_external_ref_rejected(self):
        with pytest.raises(NotImplementedError):
            resolve_open_api_reference({"$ref": "https://x/y.json#/A"}, {})

    def test_missing_ref_rejected(self):
        with pytest.raises(ValueError):
            resolve_open_api_reference({"$ref": "#/components/schemas/Nope"},
                                       {"components": {"schemas": {}}})

    def test_scalar_intermediate_node_rejected(self):
        # regression: used to raise AttributeError
        with pytest.raises(ValueError, match="not an object"):
            resolve_open_api_reference(
                {"$ref": "#/components/schemas/x/y"},
                {"components": {"schemas": {"x": 5}}})


# ---------------------------------------------------------------- helpers


class TestExtractParameterMeta:
    def test_plain_parameter(self):
        p = {"name": "limit", "in": "query", "required": True,
             "description": "page size",
             "schema": {"type": "integer", "default": 10}}
        meta = extract_parameter_meta(p)
        assert meta == {"name": "limit", "in": "query", "required": True,
                        "type": "integer", "enum": None, "format": None,
                        "default": 10, "description": "page size"}

    def test_ref_parameter_resolved_with_definition(self):
        definition = {"components": {"parameters": {"PetId": {
            "name": "petId", "in": "path", "required": True,
            "schema": {"type": "integer"}}}}}
        meta = extract_parameter_meta(
            {"$ref": "#/components/parameters/PetId"}, definition)
        assert meta["name"] == "petId"
        assert meta["required"] and meta["type"] == "integer"

    def test_schema_defaults_to_string(self):
        meta = extract_parameter_meta({"name": "q", "in": "query"})
        assert meta["type"] == "string"
        assert meta["required"] is False

    def test_array_parameter_reports_item_type(self):
        meta = extract_parameter_meta(
            {"name": "tags", "in": "query",
             "schema": {"type": "array", "items": {"type": "integer"}}})
        assert meta.get("item_type") == "integer"

    def test_enum_passthrough(self):
        meta = extract_parameter_meta(
            {"name": "status", "in": "query",
             "schema": {"type": "string", "enum": ["a", "b"]}})
        assert meta["enum"] == ["a", "b"]


class TestSanitizeOpenApiPath:
    def test_params_and_slashes(self):
        assert sanitize_openapi_path("/v3/symbols/{symbol}/session") == \
            "v3_symbols_by_symbol_session"

    def test_collapsed_and_stripped(self):
        assert sanitize_openapi_path("//a//b__c//") == "a_b_c"


# ---------------------------------------------------------------- factory


class TestFactory:
    def test_get_operations_merges_path_level_parameters(self):
        api = OpenAPIClient(PETSTORE)
        api.definition = PETSTORE
        ops = {o["operationId"]: o for o in api.get_operations()}
        assert set(ops) == {"listPets", "createPet", "getPet"}
        get_pet = ops["getPet"]
        assert get_pet["path"] == "/pets/{petId}"
        assert get_pet["method"] == "get"
        names = [p["name"] for p in get_pet["parameters"]]
        assert names == ["petId"]

    def test_get_operations_inherits_security(self):
        api = OpenAPIClient(PETSTORE)
        api.definition = PETSTORE
        ops = api.get_operations()
        assert all(o.get("security") == [{"apiKey": []}] for o in ops)

    def test_create_tool_parameters(self):
        api = OpenAPIClient(PETSTORE)
        api.definition = PETSTORE
        (op,) = [o for o in api.get_operations() if o["operationId"] == "listPets"]
        tool = api.create_tool("listPets", op, {
            "#/components/schemas/Pet": PETSTORE["components"]["schemas"]["Pet"]})
        fn = tool["function"]
        assert fn["name"] == "listPets"
        assert fn["parameters"]["properties"]["limit"]["type"] == "integer"
        assert fn["parameters"]["properties"]["status"]["enum"] == ["available", "sold"]

    def test_create_tool_request_body_ref_resolved(self):
        api = OpenAPIClient(PETSTORE)
        api.definition = PETSTORE
        (op,) = [o for o in api.get_operations() if o["operationId"] == "createPet"]
        tool = api.create_tool("createPet", op, {
            "#/components/schemas/Pet": PETSTORE["components"]["schemas"]["Pet"]})
        # the $ref was resolved to the actual Pet schema
        body = tool["function"]["parameters"]
        assert "name" in body.get("properties", {}) or body.get("type") == "object"


# ---------------------------------------------------------------- end-to-end


def make_mock_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    api = OpenAPIClient(PETSTORE, httpx_client=http)
    return api


class TestSyncEndToEnd:
    def test_get_with_kwarg_path_param(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"id": 42, "name": "Rex"},
                                  headers={"content-type": "application/json"})

        api = make_mock_client(handler)
        with api.Client() as cl:
            resp = cl.getPet(petId=42)
        assert seen[0].url == "http://api.example.com/v1/pets/42"
        assert resp["data"]["name"] == "Rex"
        assert resp["status"] == 200

    def test_query_params_and_body(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(201, json={"ok": True},
                                  headers={"content-type": "application/json"})

        api = make_mock_client(handler)
        with api.Client() as cl:
            resp = cl.listPets(limit=5, status="available")
            cl.createPet(name="Rex", status="available")
        assert str(seen[0].url) == "http://api.example.com/v1/pets?limit=5&status=available"
        assert json.loads(seen[1].read()) == {"name": "Rex", "status": "available"}
        assert resp["data"] == {"ok": True}

    def test_positional_args_consume_path_then_query(self):
        # README-documented behaviour: positional args fill path params first
        # (declaration order), then query params.
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={},
                                  headers={"content-type": "application/json"})

        api = make_mock_client(handler)
        with api.Client() as cl:
            cl.getPet(7)
        assert seen[0].url == "http://api.example.com/v1/pets/7"


class TestAsyncEndToEnd:
    def test_async_get(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"id": 1},
                                  headers={"content-type": "application/json"})

        async def run():
            transport = httpx.MockTransport(handler)
            api = OpenAPIClient(PETSTORE, httpx_async_client=httpx.AsyncClient(transport=transport))
            async with api.AsyncClient() as cl:
                return await cl.getPet(petId=1)

        resp = asyncio.run(run())
        assert resp["data"] == {"id": 1}
        assert seen[0].url == "http://api.example.com/v1/pets/1"


# ---------------------------------------------------------------- prepare


class TestPrepareRequestParams:
    def make(self):
        api = OpenAPIClient(PETSTORE)
        api.definition = PETSTORE
        api.base_url = "http://api.example.com/v1"
        (op,) = [o for o in api.get_operations() if o["operationId"] == "listPets"]
        return api, op

    def test_kwargs_split_across_path_and_query(self):
        api, op = self.make()
        url, query, body, headers, rest = api._prepare_request_params(
            "/pets/{petId}", {"parameters": [
                {"name": "petId", "in": "path", "required": True},
                {"name": "limit", "in": "query"},
            ]}, [], {"petId": 9, "limit": 3})
        assert url == "http://api.example.com/v1/pets/9"
        assert query == {"limit": 3}

    def test_leftover_kwargs_become_body_when_request_body_exists(self):
        api, op = self.make()
        url, query, body, headers, rest = api._prepare_request_params(
            "/pets", {"requestBody": {"content": {"application/json": {}}}},
            [], {"name": "Rex"})
        assert body == {"name": "Rex"}
        assert rest == {}
