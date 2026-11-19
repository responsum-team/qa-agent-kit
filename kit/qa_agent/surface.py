"""A service's API surface, read from its OpenAPI document.

Each operation becomes one short record, so an agent can read a whole API
without loading the full document, and two snapshots can be compared to see
exactly what a change did to the contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .util import QAError, clip, git_head, http_get, is_git_repo, join_url, now_iso, read_json, write_json

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
MAX_DEPTH = 2


# ---------------------------------------------------------------- reading schemas


def _resolve(doc: dict, node, seen=()):
    """Follow local $ref pointers. Unknown or circular references resolve to nothing."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return {}
        seen = seen + (ref,)
        target = doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            target = target.get(part) if isinstance(target, dict) else None
            if target is None:
                return {}
        node = target
    return node if isinstance(node, dict) else {}


def _merged(doc: dict, schema: dict, hops: int = 0):
    """Properties and required names of an object schema, with allOf parts folded in."""
    schema = _resolve(doc, schema)
    props, required = dict(schema.get("properties") or {}), list(schema.get("required") or [])
    if hops < 6:  # schemas may refer to themselves
        for part in schema.get("allOf") or []:
            more_props, more_required = _merged(doc, part, hops + 1)
            props.update(more_props)
            required.extend(more_required)
    return props, required


def type_of(doc: dict, schema, hops: int = 0) -> str:
    schema = _resolve(doc, schema)
    if not schema or hops > 4:
        return "any"
    if "enum" in schema:
        values = [str(v) for v in schema["enum"]]
        shown = "|".join(values[:8]) + ("|…" if len(values) > 8 else "")
        return f"enum({shown})"
    for key in ("oneOf", "anyOf"):
        if schema.get(key):
            return "|".join(sorted({type_of(doc, part, hops + 1) for part in schema[key]}))
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = "|".join(sorted(str(k) for k in kind))
    if kind == "array":
        return type_of(doc, schema.get("items"), hops + 1) + "[]"
    if kind is None and (schema.get("properties") or schema.get("allOf")):
        kind = "object"
    text = kind or "any"
    if schema.get("format") and text in ("string", "integer", "number"):
        text += f"({schema['format']})"
    if schema.get("nullable"):
        text += "|null"
    return text


def _span(low, high, low_open: bool = False, high_open: bool = False) -> str:
    if low is None and high is None:
        return ""
    if low is not None and high is not None and not low_open and not high_open:
        return f"{low}..{high}"
    parts = []
    if low is not None:
        parts.append((">" if low_open else ">=") + str(low))
    if high is not None:
        parts.append(("<" if high_open else "<=") + str(high))
    return " ".join(parts)


def limits_of(doc: dict, schema, hops: int = 0) -> str:
    """The limits a schema puts on a value, in short: 'len 1..60', '>=1', 'pattern ^[a-z]+$', 'default 20'."""
    schema = _resolve(doc, schema)
    if not schema or hops > 2:
        return ""
    parts = []
    length = _span(schema.get("minLength"), schema.get("maxLength"))
    if length:
        parts.append("len " + length)
    low, high = schema.get("minimum"), schema.get("maximum")
    low_open, high_open = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
    if low_open is not None and not isinstance(low_open, bool):  # OpenAPI 3.1 gives the bound itself
        low, low_open = low_open, True
    if high_open is not None and not isinstance(high_open, bool):
        high, high_open = high_open, True
    bounds = _span(low, high, bool(low_open), bool(high_open))
    if bounds:
        parts.append(bounds)
    if schema.get("multipleOf") is not None:
        parts.append(f"multiple of {schema['multipleOf']}")
    if isinstance(schema.get("pattern"), str):
        parts.append("pattern " + clip(schema["pattern"], 60))
    if schema.get("type") == "array" or "items" in schema:
        count = _span(schema.get("minItems"), schema.get("maxItems"))
        if count:
            parts.append(count + " items")
        if schema.get("uniqueItems"):
            parts.append("unique")
        each = limits_of(doc, schema.get("items"), hops + 1)
        if each:
            parts.append("each " + each)
    if "default" in schema and not isinstance(schema["default"], (dict, list)):
        parts.append("default " + json.dumps(schema["default"]))
    return ", ".join(parts)


def described(doc: dict, schema, required: bool = False) -> str:
    """How a value is recorded: its type, `*` when required, then its limits in braces."""
    limits = limits_of(doc, schema)
    return type_of(doc, schema) + ("*" if required else "") + (f" {{{limits}}}" if limits else "")


def _parts(text: str):
    """The recorded text of a value -> (type, required, limits)."""
    kind, _, limits = text.partition(" {")
    return kind.rstrip("*"), kind.endswith("*"), limits[:-1] if limits.endswith("}") else limits


def fields_of(doc: dict, schema, prefix: str = "", depth: int = 0, hops: int = 0) -> dict:
    """Flatten an object or array-of-objects schema to `name: type`, `*` marking required fields."""
    schema = _resolve(doc, schema)
    if not schema or hops > 3:
        return {}
    if schema.get("type") == "array" or "items" in schema:
        return fields_of(doc, schema.get("items"), prefix + "[].", depth, hops + 1)
    props, required = _merged(doc, schema)
    out: dict = {}
    for name, sub in props.items():
        sub = _resolve(doc, sub)
        out[prefix + name] = described(doc, sub, name in required)
        nested_object = sub.get("properties") or sub.get("allOf")
        nested_items = _resolve(doc, sub.get("items")) if sub.get("type") == "array" else {}
        if depth + 1 < MAX_DEPTH:
            if nested_object:
                out.update(fields_of(doc, sub, prefix + name + ".", depth + 1))
            elif nested_items.get("properties") or nested_items.get("allOf"):
                out.update(fields_of(doc, nested_items, prefix + name + "[].", depth + 1))
    return out


def _body(doc: dict, content) -> dict:
    """Summarise a request or response body from its content map."""
    if not isinstance(content, dict) or not content:
        return {}
    media = "application/json" if "application/json" in content else sorted(content)[0]
    schema = (content.get(media) or {}).get("schema")
    summary = {"type": media}
    fields = fields_of(doc, schema)
    if fields:
        summary["fields"] = fields
    elif schema:
        summary["schema"] = described(doc, schema)
    return summary


# ---------------------------------------------------------------- operations


def path_key(method: str, path: str) -> str:
    """Identity of an operation: renaming a path parameter does not make a new one."""
    return f"{method.upper()} {re.sub(r'{[^}]*}', '{}', path)}"


def _auth(doc: dict, operation: dict) -> bool:
    security = operation["security"] if "security" in operation else doc.get("security")
    if not security:
        return False
    return not any(requirement == {} for requirement in security)


def operations(doc: dict) -> list:
    """One record per operation of an OpenAPI 3.x (or Swagger 2.0) document."""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
        raise QAError("The document has no 'paths': it is not an OpenAPI description")
    swagger2 = str(doc.get("swagger", "")).startswith("2")
    found = []
    for path, item in doc["paths"].items():
        item = _resolve(doc, item)
        shared = item.get("parameters") or []
        for method in METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            params: dict = {}
            request: dict = {}
            form: dict = {}
            for raw in list(shared) + list(operation.get("parameters") or []):
                param = _resolve(doc, raw)
                where = param.get("in")
                if swagger2 and where == "body":
                    request = {"type": "application/json", "required": bool(param.get("required"))}
                    fields = fields_of(doc, param.get("schema"))
                    if fields:
                        request["fields"] = fields
                    continue
                kind = described(doc, param.get("schema") if "schema" in param else param, bool(param.get("required")))
                if swagger2 and where == "formData":
                    form[param.get("name", "?")] = kind
                    continue
                params[param.get("name", "?")] = f"{where} {kind}"
            if form:
                request = {"type": "form", "required": True, "fields": form}
            body = _resolve(doc, operation.get("requestBody"))
            if body:
                request = _body(doc, body.get("content"))
                request["required"] = bool(body.get("required"))
            responses: dict = {}
            for code, raw in (operation.get("responses") or {}).items():
                response = _resolve(doc, raw)
                if swagger2:
                    schema = response.get("schema")
                    fields = fields_of(doc, schema)
                    answer = {"fields": fields} if fields else ({"schema": described(doc, schema)} if schema else {})
                else:
                    answer = _body(doc, response.get("content"))
                if isinstance(response.get("description"), str) and response["description"].strip():
                    answer = {"description": clip(response["description"], 300), **answer}
                responses[str(code)] = answer
            record = {
                "id": f"{method.upper()} {path}",
                "key": path_key(method, path),
                "summary": clip(operation.get("summary") or operation.get("description") or "", 120),
                "tags": operation.get("tags") or [],
                "auth": _auth(doc, operation),
                "params": params,
                "request": request or None,
                "responses": responses,
            }
            if operation.get("summary") and isinstance(operation.get("description"), str) and operation["description"].strip():
                record["description"] = clip(operation["description"], 800)
            if operation.get("operationId"):
                record["operationId"] = operation["operationId"]
            if operation.get("deprecated"):
                record["deprecated"] = True
            found.append(record)
    found.sort(key=lambda r: (r["key"].split(" ", 1)[1], r["key"]))
    return found


# ---------------------------------------------------------------- comparing


def _field_changes(label: str, old: dict, new: dict, request_side: bool) -> list:
    changes = []
    for name in sorted(set(old) | set(new)):
        if name not in new:
            level = "info" if request_side else "breaking"
            changes.append({"level": level, "what": f"{label}: field '{name}' removed"})
            continue
        if name not in old:
            required = _parts(new[name])[1]
            level = "breaking" if request_side and required else "info"
            changes.append({"level": level, "what": f"{label}: field '{name}' added" + (" (required)" if required else "")})
            continue
        (old_kind, old_required, old_limits), (new_kind, now_required, new_limits) = _parts(old[name]), _parts(new[name])
        if old_kind != new_kind:
            changes.append({"level": "breaking", "what": f"{label}: field '{name}' is now {new_kind}, was {old_kind}"})
            continue
        if old_required != now_required:
            if request_side:
                level = "breaking" if now_required else "info"
            else:
                level = "info" if now_required else "risky"
            changes.append({"level": level, "what": f"{label}: field '{name}' is now {'required' if now_required else 'optional'}"})
        if old_limits != new_limits:
            changes.append({
                "level": "risky",
                "what": f"{label}: limits of '{name}' are now {new_limits or 'none'}, were {old_limits or 'none'}",
            })
    return changes


def compare(old_ops: list, new_ops: list) -> dict:
    """What a caller could notice between two snapshots of the same API."""
    old = {op["key"]: op for op in old_ops}
    new = {op["key"]: op for op in new_ops}
    added = [new[k]["id"] for k in new if k not in old]
    removed = [old[k]["id"] for k in old if k not in new]
    changed = []
    for key in sorted(set(old) & set(new)):
        before, after = old[key], new[key]
        notes = []
        if before.get("auth") != after.get("auth"):
            if after.get("auth"):
                notes.append({"level": "breaking", "what": "authentication is now required"})
            else:
                notes.append({"level": "security", "what": "authentication is no longer required"})
        if (before.get("summary"), before.get("description")) != (after.get("summary"), after.get("description")):
            notes.append({"level": "info", "what": "the operation's description was reworded"})
        notes += _field_changes("parameter", before.get("params") or {}, after.get("params") or {}, True)
        old_request, new_request = before.get("request") or {}, after.get("request") or {}
        if bool(old_request) != bool(new_request):
            notes.append({
                "level": "breaking" if new_request.get("required") else "info",
                "what": "request body added" if new_request else "request body removed",
            })
        else:
            notes += _field_changes("request", old_request.get("fields") or {}, new_request.get("fields") or {}, True)
            if old_request.get("type") != new_request.get("type"):
                notes.append({"level": "breaking", "what": f"request type is now {new_request.get('type')}, was {old_request.get('type')}"})
        old_codes, new_codes = before.get("responses") or {}, after.get("responses") or {}
        for code in sorted(set(old_codes) | set(new_codes)):
            if code not in new_codes:
                notes.append({"level": "risky", "what": f"response {code} removed"})
            elif code not in old_codes:
                level = "risky" if code.startswith("2") else "info"
                notes.append({"level": level, "what": f"response {code} added"})
            else:
                notes += _field_changes(
                    f"response {code}", old_codes[code].get("fields") or {}, new_codes[code].get("fields") or {}, False
                )
                if old_codes[code].get("description") != new_codes[code].get("description"):
                    notes.append({"level": "info", "what": f"response {code}: its description was reworded"})
        if notes:
            changed.append({"id": after["id"], "changes": notes})
    return {"added": added, "removed": removed, "changed": changed}


def worst_level(result: dict) -> str:
    order = ["none", "info", "risky", "breaking", "security"]
    worst = "breaking" if result["removed"] else ("info" if result["added"] else "none")
    for item in result["changed"]:
        for note in item["changes"]:
            if order.index(note["level"]) > order.index(worst):
                worst = note["level"]
    return worst


# ---------------------------------------------------------------- fetching and storing


def snapshot_path(paths, service_name: str) -> Path:
    return paths.surface / f"{service_name}.json"


def load_document(config, service: dict, source=None):
    """Read the service's OpenAPI document from its running instance or from a file."""
    spec = source or service.get("openapi")
    if not spec:
        raise QAError(
            f"Service '{service['name']}' has no 'openapi' in the project map. Set it to the document's "
            "URL path (for example /openapi.json) or to a JSON file in its repository."
        )
    if spec.startswith("http://") or spec.startswith("https://") or spec.startswith("/"):
        if spec.startswith("/"):
            base = config.url(service)
            if not base:
                raise QAError(f"Service '{service['name']}' has no URL to fetch {spec} from")
            spec = join_url(base, spec)
        status, body, error = http_get(spec, timeout=20)
        if status is None:
            raise QAError(f"{spec} did not answer: {error}")
        if status >= 400:
            raise QAError(f"{spec} answered HTTP {status}")
        text, origin = body.decode("utf-8", errors="replace"), spec
    else:
        candidates = []
        checkout = config.repo_path(service)
        if checkout:
            candidates.append(checkout / spec)
        candidates.append(config.paths.root / spec)
        target = next((c for c in candidates if c.is_file()), None)
        if target is None:
            raise QAError(f"OpenAPI file '{spec}' was not found in the service's repository or in this one")
        text, origin = target.read_text(encoding="utf-8", errors="replace"), str(target)
    try:
        return json.loads(text), origin
    except json.JSONDecodeError as exc:
        if re.search(r"^(openapi|swagger)\s*:", text, re.M):
            raise QAError(f"{origin} is YAML. This kit reads JSON: point 'openapi' at a JSON copy of the document.") from exc
        raise QAError(f"{origin} is not JSON: {exc}") from exc


def build_snapshot(config, service: dict, source=None) -> dict:
    doc, origin = load_document(config, service, source)
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    checkout = config.repo_path(service)
    return {
        "service": service["name"],
        "fetched_at": now_iso(),
        "source": origin,
        "commit": git_head(checkout) if checkout and is_git_repo(checkout) else None,
        "title": info.get("title"),
        "api_version": info.get("version"),
        "operations": operations(doc),
    }


def fetch(paths, config, service: dict, source=None, save: bool = True) -> dict:
    """Take a fresh snapshot and say what differs from the stored one."""
    fresh = build_snapshot(config, service, source)
    stored = read_json(snapshot_path(paths, service["name"]))
    result = {"service": service["name"], "operations": len(fresh["operations"]), "source": fresh["source"]}
    if stored:
        difference = compare(stored["operations"], fresh["operations"])
        result["since"] = stored.get("fetched_at")
        result["worst"] = worst_level(difference)
        result.update(difference)
    else:
        result["note"] = "first snapshot; nothing to compare with"
    if save:
        write_json(snapshot_path(paths, service["name"]), fresh)
        result["saved"] = paths.rel(snapshot_path(paths, service["name"]))
    return result


_OPERATION = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s,;]*)", re.I)


def coverage(paths, service_name: str, entries: list) -> dict:
    """Which operations of the stored surface have behaviours recorded for them, and which have none."""
    stored = read_json(snapshot_path(paths, service_name))
    if not stored:
        raise QAError(f"No stored surface for '{service_name}'. Run: qa.py surface fetch --service {service_name}")
    counts: dict = {}
    for entry in entries:
        if entry.get("status") == "retired" or entry.get("service") not in (service_name, "system"):
            continue
        for method, path in _OPERATION.findall(entry.get("surface") or ""):
            key = path_key(method, path.rstrip("/") or "/")
            counts[key] = counts.get(key, 0) + 1
    known = {op["key"]: op["id"] for op in stored["operations"]}
    without = [op_id for key, op_id in known.items() if key not in counts]
    thin = [op_id for key, op_id in known.items() if counts.get(key) == 1]
    unknown = sorted(key for key in counts if key not in known)
    result = {
        "service": service_name,
        "operations": len(known),
        "with_behaviours": len(known) - len(without),
        "without_any": without,
        "with_only_one": thin,
    }
    if unknown:
        result["behaviours_naming_operations_not_in_the_document"] = unknown
    return result


def show(paths, service_name: str, text=None, op=None, full: bool = False) -> list:
    stored = read_json(snapshot_path(paths, service_name))
    if not stored:
        raise QAError(f"No stored surface for '{service_name}'. Run: qa.py surface fetch --service {service_name}")
    rows = []
    for record in stored["operations"]:
        if op and op not in (record["id"], record["key"], record.get("operationId")):
            continue
        if text and text.lower() not in json.dumps(record).lower():
            continue
        if full or op:
            rows.append(record)
        else:
            rows.append({
                "id": record["id"],
                "auth": record["auth"],
                "responses": sorted(record["responses"]),
                "summary": record["summary"],
            })
    return rows
