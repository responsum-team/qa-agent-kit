import copy
import json

import pytest

from qa_agent import surface
from qa_agent.util import QAError
from conftest import project, write

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Orders", "version": "1.2.0"},
    "security": [{"bearer": []}],
    "paths": {
        "/orders": {
            "get": {
                "summary": "List orders", "tags": ["orders"],
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                "responses": {"200": {"content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Order"}}}}}},
            },
            "post": {
                "operationId": "createOrder",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewOrder"}}}},
                "responses": {
                    "201": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}},
                    "422": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Problem"}}}},
                },
            },
        },
        "/orders/{order_id}": {
            "parameters": [{"name": "order_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
            "get": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}}, "404": {}}},
        },
        "/healthz": {"get": {"security": [], "responses": {"200": {}}}},
    },
    "components": {"schemas": {
        "NewOrder": {"type": "object", "required": ["items"], "properties": {
            "items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}},
            "note": {"type": "string", "nullable": True},
        }},
        "Item": {"type": "object", "required": ["sku", "quantity"], "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer"}}},
        "Order": {"allOf": [{"$ref": "#/components/schemas/NewOrder"}, {"type": "object", "required": ["id", "status"], "properties": {
            "id": {"type": "string", "format": "uuid"},
            "status": {"enum": ["new", "paid", "shipped"]},
            "parent": {"$ref": "#/components/schemas/Order"},
        }}]},
        "Problem": {"type": "object", "properties": {"code": {"type": "string"}, "detail": {"type": "string"}}},
    }},
}


def ops(doc=OPENAPI):
    return {op["id"]: op for op in surface.operations(doc)}


def test_operations_are_short_records():
    found = ops()
    assert list(found) == ["GET /healthz", "GET /orders", "POST /orders", "GET /orders/{order_id}"]
    create = found["POST /orders"]
    assert create["auth"] is True and create["operationId"] == "createOrder"
    assert create["request"] == {
        "type": "application/json", "required": True,
        "fields": {"items": "object[]*", "items[].sku": "string*", "items[].quantity": "integer*", "note": "string|null"},
    }
    assert create["responses"]["201"]["fields"]["id"] == "string(uuid)*"
    assert create["responses"]["201"]["fields"]["status"] == "enum(new|paid|shipped)*"
    assert create["responses"]["422"]["fields"] == {"code": "string", "detail": "string"}
    assert found["GET /healthz"]["auth"] is False
    assert found["GET /orders"]["params"] == {"limit": "query integer"}
    assert found["GET /orders"]["responses"]["200"]["fields"]["[].id"] == "string(uuid)*"
    assert found["GET /orders/{order_id}"]["params"] == {"order_id": "path string(uuid)*"}
    assert found["GET /orders/{order_id}"]["key"] == "GET /orders/{}"


LIMITS = {
    "openapi": "3.0.3",
    "paths": {"/rooms": {
        "get": {
            "summary": "List rooms", "description": "Rooms in name order. `min_capacity` leaves out smaller rooms.",
            "parameters": [
                {"name": "min_capacity", "in": "query", "schema": {"type": "integer", "minimum": 1}},
                {"name": "limit", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
            ],
            "responses": {"200": {"description": "The rooms."}, "422": {"description": "INVALID_CAPACITY when min_capacity is not a whole number."}},
        },
        "post": {
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["title"], "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 60},
                "code": {"type": "string", "pattern": "^[a-z]+$"},
                "ratio": {"type": "number", "minimum": 0, "exclusiveMinimum": True, "maximum": 1},
                "score": {"type": "number", "exclusiveMaximum": 10},
                "tags": {"type": "array", "maxItems": 5, "uniqueItems": True, "items": {"type": "string", "maxLength": 20}},
                "step": {"type": "integer", "multipleOf": 15},
            }}}}},
            "responses": {"201": {"content": {"text/plain": {"schema": {"type": "string", "maxLength": 12}}}}},
        },
    }},
}


def test_limits_and_descriptions_are_kept():
    found = ops(LIMITS)
    listing = found["GET /rooms"]
    assert listing["params"] == {"min_capacity": "query integer {>=1}", "limit": "query integer* {1..100, default 20}"}
    assert listing["summary"] == "List rooms" and listing["description"].startswith("Rooms in name order.")
    assert listing["responses"] == {
        "200": {"description": "The rooms."},
        "422": {"description": "INVALID_CAPACITY when min_capacity is not a whole number."},
    }
    create = found["POST /rooms"]
    assert create["request"]["fields"] == {
        "title": "string* {len 1..60}",
        "code": "string {pattern ^[a-z]+$}",
        "ratio": "number {>0 <=1}",
        "score": "number {<10}",
        "tags": "string[] {<=5 items, unique, each len <=20}",
        "step": "integer {multiple of 15}",
    }
    assert create["responses"]["201"] == {"type": "text/plain", "schema": "string {len <=12}"}
    assert "description" not in create


def test_answers_kept_once_under_components_are_followed():
    doc = {
        "openapi": "3.0.3",
        "paths": {"/rooms": {"get": {"responses": {
            "200": {"description": "The rooms."},
            "401": {"$ref": "#/components/responses/Unauthenticated"},
        }}}},
        "components": {
            "responses": {"Unauthenticated": {
                "description": "UNAUTHENTICATED: no token, or one the service does not know.",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Refusal"}}},
            }},
            "schemas": {"Refusal": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}, "message": {"type": "string"}}}},
        },
    }
    assert surface.operations(doc)[0]["responses"]["401"] == {
        "description": "UNAUTHENTICATED: no token, or one the service does not know.",
        "type": "application/json",
        "fields": {"code": "string*", "message": "string"},
    }


def test_a_changed_limit_or_wording_is_reported():
    doc = copy.deepcopy(LIMITS)
    rooms = doc["paths"]["/rooms"]
    rooms["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["title"]["maxLength"] = 80
    rooms["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["code"].pop("pattern")
    rooms["get"]["parameters"][1]["schema"]["maximum"] = 50
    rooms["get"]["parameters"][1]["required"] = False
    rooms["get"]["responses"]["422"]["description"] = "INVALID_CAPACITY when min_capacity is below 1."
    rooms["get"]["description"] = "Rooms in name order."
    result = surface.compare(surface.operations(LIMITS), surface.operations(doc))
    notes = {(item["id"], note["what"]): note["level"] for item in result["changed"] for note in item["changes"]}
    assert notes == {
        ("POST /rooms", "request: limits of 'title' are now len 1..80, were len 1..60"): "risky",
        ("POST /rooms", "request: limits of 'code' are now none, were pattern ^[a-z]+$"): "risky",
        ("GET /rooms", "parameter: field 'limit' is now optional"): "info",
        ("GET /rooms", "parameter: limits of 'limit' are now 1..50, default 20, were 1..100, default 20"): "risky",
        ("GET /rooms", "response 422: its description was reworded"): "info",
        ("GET /rooms", "the operation's description was reworded"): "info",
    }
    assert surface.worst_level(result) == "risky"


def test_a_schema_that_refers_to_itself_does_not_loop():
    doc = {"paths": {"/tree": {"get": {"responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}}}}},
           "components": {"schemas": {"Node": {"type": "object", "properties": {
               "name": {"type": "string"}, "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
               "self": {"allOf": [{"$ref": "#/components/schemas/Node"}]}}}}}}
    fields = surface.operations(doc)[0]["responses"]["200"]["fields"]
    assert fields["name"] == "string" and fields["children"] == "object[]"


def test_swagger_2_documents_are_read_too():
    doc = {"swagger": "2.0", "paths": {"/orders": {"post": {
        "parameters": [{"in": "body", "name": "body", "required": True, "schema": {"type": "object", "required": ["sku"], "properties": {"sku": {"type": "string"}}}},
                       {"in": "query", "name": "dry", "type": "boolean"}],
        "responses": {"201": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}},
    }}}}
    op = surface.operations(doc)[0]
    assert op["request"] == {"type": "application/json", "required": True, "fields": {"sku": "string*"}}
    assert op["params"] == {"dry": "query boolean"}
    assert op["responses"]["201"] == {"fields": {"id": "integer"}}


def test_not_an_openapi_document():
    with pytest.raises(QAError, match="no 'paths'"):
        surface.operations({"hello": "world"})


def changed(mutate):
    doc = copy.deepcopy(OPENAPI)
    mutate(doc)
    result = surface.compare(surface.operations(OPENAPI), surface.operations(doc))
    notes = {(item["id"], note["what"]): note["level"] for item in result["changed"] for note in item["changes"]}
    return result, notes


def test_compare_says_what_a_caller_would_notice():
    def mutate(doc):
        schemas = doc["components"]["schemas"]
        schemas["NewOrder"]["required"].append("note")                       # request field becomes required
        schemas["NewOrder"]["properties"]["coupon"] = {"type": "string"}     # new optional request field
        schemas["Problem"]["properties"].pop("detail")                       # response field removed
        schemas["Item"]["properties"]["quantity"] = {"type": "string"}       # type changed
        doc["paths"]["/orders"]["get"]["security"] = []                      # no longer needs sign-in
        doc["paths"]["/healthz"]["get"].pop("security")                      # now needs sign-in
        doc["paths"]["/orders"]["post"]["responses"]["409"] = {}
        doc["paths"]["/orders/{order_id}"]["get"]["responses"].pop("404")
        doc["paths"]["/orders/{id}"] = doc["paths"].pop("/orders/{order_id}")  # renaming a path parameter is no change
        doc["paths"]["/orders/{id}"]["parameters"][0]["name"] = "id"
        doc["paths"]["/refunds"] = {"post": {"responses": {"201": {}}}}

    result, notes = changed(mutate)
    assert result["added"] == ["POST /refunds"] and result["removed"] == []
    assert notes[("POST /orders", "request: field 'note' is now required")] == "breaking"
    assert notes[("POST /orders", "request: field 'coupon' added")] == "info"
    assert notes[("POST /orders", "response 422: field 'detail' removed")] == "breaking"
    assert notes[("POST /orders", "request: field 'items[].quantity' is now string, was integer")] == "breaking"
    assert notes[("GET /orders", "authentication is no longer required")] == "security"
    assert notes[("GET /healthz", "authentication is now required")] == "breaking"
    assert notes[("POST /orders", "response 409 added")] == "info"
    assert notes[("GET /orders/{id}", "response 404 removed")] == "risky"
    assert surface.worst_level(result) == "security"


def test_compare_of_identical_documents_is_empty():
    result = surface.compare(surface.operations(OPENAPI), surface.operations(copy.deepcopy(OPENAPI)))
    assert result == {"added": [], "removed": [], "changed": []}
    assert surface.worst_level(result) == "none"


def test_a_removed_operation_is_breaking():
    result, _ = changed(lambda doc: doc["paths"].pop("/healthz"))
    assert result["removed"] == ["GET /healthz"] and surface.worst_level(result) == "breaking"


def test_fetch_stores_a_snapshot_and_reports_the_difference(host, orders):
    write(orders, "openapi.json", json.dumps(OPENAPI))
    config = host.configure(project(services=[{"name": "orders", "repo": "../orders", "openapi": "openapi.json"}]))
    service = config.service("orders")
    first = surface.fetch(host.paths, config, service)
    assert first["operations"] == 4 and "first snapshot" in first["note"]
    assert first["saved"] == ".qa-agent/state/surface/orders.json"

    doc = copy.deepcopy(OPENAPI)
    doc["paths"].pop("/healthz")
    write(orders, "openapi.json", json.dumps(doc))
    peek = surface.fetch(host.paths, config, service, save=False)
    assert peek["removed"] == ["GET /healthz"] and peek["worst"] == "breaking" and "saved" not in peek
    assert surface.fetch(host.paths, config, service)["removed"] == ["GET /healthz"]     # still the stored one
    assert surface.fetch(host.paths, config, service)["removed"] == []                   # now it is saved

    rows = surface.show(host.paths, "orders", text="createorder")
    assert [row["id"] for row in rows] == ["POST /orders"] and "request" not in rows[0]
    assert surface.show(host.paths, "orders", op="POST /orders")[0]["request"]["required"] is True


def test_coverage_lists_operations_no_behaviour_names(host, orders):
    write(orders, "openapi.json", json.dumps(OPENAPI))
    config = host.configure(project(services=[
        {"name": "orders", "repo": "../orders", "openapi": "openapi.json"}, {"name": "web"},
    ]))
    with pytest.raises(QAError, match="No stored surface"):
        surface.coverage(host.paths, "orders", [])
    surface.fetch(host.paths, config, config.service("orders"))
    entries = [
        {"service": "orders", "surface": "POST /orders", "status": "passing"},
        {"service": "orders", "surface": "post /orders/", "status": "planned"},              # same operation, written loosely
        {"service": "orders", "surface": "GET /orders/{id}", "status": "planned"},           # another parameter name
        {"service": "system", "surface": "web -> orders: GET /orders", "status": "planned"},
        {"service": "orders", "surface": "GET /healthz", "status": "retired"},               # retired: no longer counts
        {"service": "orders", "surface": "DELETE /orders/{id}", "status": "planned"},        # not in the document
        {"service": "web", "surface": "GET /healthz", "status": "planned"},                  # another service's
    ]
    assert surface.coverage(host.paths, "orders", entries) == {
        "service": "orders", "operations": 4, "with_behaviours": 3,
        "without_any": ["GET /healthz"],
        "with_only_one": ["GET /orders", "GET /orders/{order_id}"],
        "behaviours_naming_operations_not_in_the_document": ["DELETE /orders/{}"],
    }


def test_fetch_explains_what_it_cannot_read(host, orders):
    config = host.configure(project(services=[{"name": "orders", "repo": "../orders"}]))
    with pytest.raises(QAError, match="has no 'openapi'"):
        surface.fetch(host.paths, config, config.service("orders"))
    write(orders, "openapi.yaml", "openapi: 3.0.0\npaths: {}\n")
    with pytest.raises(QAError, match="is YAML"):
        surface.fetch(host.paths, config, config.service("orders"), source="openapi.yaml")
    with pytest.raises(QAError, match="was not found"):
        surface.fetch(host.paths, config, config.service("orders"), source="missing.json")
    with pytest.raises(QAError, match="No stored surface"):
        surface.show(host.paths, "orders")
