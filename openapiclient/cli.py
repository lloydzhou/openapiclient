"""oapi - a CLI for OpenAPI-defined APIs.

Register an API by name, then call its operations through dynamically
generated commands::

    oapi connect petstore https://petstore.example.com/openapi.json
    oapi petstore get-pet 42
    oapi petstore list-pets --status available --jq '.items[].name'

Design notes (borrowed from restish / gh / lark-cli):
- required path parameters become positional arguments, in URL-template order
- query/header parameters become ``--kebab-case`` flags (enum -> Choice)
- request body via ``--body`` (JSON / @file / - for stdin) or repeated
  ``-F/--field key=value`` pairs whose values are smart-parsed
- stdout carries data only, diagnostics go to stderr
- non-2xx responses exit with code 1
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import click

from .client import OpenAPIClient, extract_parameter_meta


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def registry_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(str(Path.home()), ".config")
    return Path(base) / "oapi" / "apis"


def registry_path(name):
    if not NAME_RE.match(name or ""):
        raise click.ClickException(
            f"invalid API name {name!r}: must match {NAME_RE.pattern}"
        )
    return registry_dir() / f"{name}.json"


def load_registry():
    entries = {}
    reg = registry_dir()
    if reg.is_dir():
        for f in sorted(reg.glob("*.json")):
            try:
                entries[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                click.echo(f"warning: skipping unreadable registry file {f}", err=True)
    return entries


def save_api(name, source, spec):
    reg = registry_dir()
    reg.mkdir(parents=True, exist_ok=True)
    entry = {
        "name": name,
        "source": source,
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "spec": spec,
    }
    registry_path(name).write_text(
        json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return entry


def load_api(name):
    entries = load_registry()
    if name not in entries:
        raise click.ClickException(
            f"unknown API {name!r}. Register it first: oapi connect {name} <spec-url-or-file> "
            f"(known: {', '.join(sorted(entries)) or 'none'})"
        )
    return entries[name]


# --------------------------------------------------------------------------
# spec loading
# --------------------------------------------------------------------------


def load_spec_source(source, timeout=30.0, insecure=False):
    """Fetch an OpenAPI document from a URL or local file and parse it."""
    if re.match(r"^https?://", source):
        import httpx

        try:
            resp = httpx.get(
                source, timeout=timeout, follow_redirects=True, verify=not insecure
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise click.ClickException(f"failed to fetch {source}: {e}")
        text = resp.text
    else:
        p = Path(source)
        if not p.is_file():
            raise click.ClickException(f"file not found: {source}")
        text = p.read_text(encoding="utf-8")

    try:
        spec = json.loads(text)
    except ValueError:
        import yaml

        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise click.ClickException(f"spec is neither JSON nor YAML: {e}")
    if not isinstance(spec, dict) or not (
        "openapi" in spec or "swagger" in spec or "paths" in spec
    ):
        raise click.ClickException("document does not look like an OpenAPI/Swagger spec")
    return spec


def spec_base_url(spec):
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        return servers[0]["url"].rstrip("/")
    return ""


# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------


def to_kebab(name):
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name or "")
    s = s.replace("_", "-").replace(".", "-").replace("/", "-")
    s = re.sub(r"[^0-9a-zA-Z-]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-").lower()


def operation_command_name(operation):
    op_id = operation.get("operationId")
    if op_id:
        return to_kebab(op_id)
    path = re.sub(r"\{([^}]+)\}", r"\1", operation.get("path", ""))
    return to_kebab(f"{operation.get('method', 'get')} {path}".replace(" ", "-"))


def operation_method_name(operation):
    """Key used for the method on the library-generated client."""
    return operation.get("operationId") or operation_command_name(operation)


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------


def jq_dotpath(data, expr):
    """Evaluate a dot-path subset of jq: ``a.b``, ``.items[0].id``, ``.items[].id``."""
    if not expr:
        return data
    e = expr.strip().lstrip(".")
    tokens = list(re.finditer(r"([A-Za-z_][\w-]*)|\[(\d+)\]|(\[\])", e))
    cur = data
    for i, m in enumerate(tokens):
        if m.group(1) is not None:
            if isinstance(cur, dict):
                cur = cur.get(m.group(1))
            else:
                return None
        elif m.group(2) is not None:
            idx = int(m.group(2))
            if isinstance(cur, (list, tuple)) and -len(cur) <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        else:  # [] spread: evaluate the remaining path for each item
            if isinstance(cur, (list, tuple)):
                rest = e[m.end():]
                return [jq_dotpath(item, rest) for item in cur]
            return None
    return cur


def smart_value(raw):
    """Parse a --field value: @file / - (stdin) first, then JSON literal, else string."""
    if raw == "-":
        raw = sys.stdin.read()
    elif raw.startswith("@"):
        p = Path(raw[1:])
        if not p.is_file():
            raise click.ClickException(f"file not found: {raw[1:]}")
        raw = p.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def parse_body_text(text):
    """--body accepts inline JSON, @file, or - for stdin. Returns a python object."""
    if text == "-":
        text = sys.stdin.read()
    elif text.startswith("@"):
        p = Path(text[1:])
        if not p.is_file():
            raise click.ClickException(f"file not found: {text[1:]}")
        text = p.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except ValueError as e:
        raise click.ClickException(f"--body is not valid JSON: {e}")


def emit(obj, output, jq):
    """Write payload to stdout; keep diagnostics on stderr."""
    if jq:
        obj = jq_dotpath(obj, jq)
        if obj is None:
            raise click.ClickException(f"--jq {jq!r} selected nothing")
    if output == "text":
        if isinstance(obj, str):
            click.echo(obj)
        else:
            click.echo(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    pretty = sys.stdout.isatty()
    click.echo(json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None))


def log(msg):
    click.echo(msg, err=True)


# --------------------------------------------------------------------------
# request execution
# --------------------------------------------------------------------------

TYPE_MAP = {"integer": int, "number": float, "boolean": bool}


def click_type(meta):
    if meta.get("enum"):
        return click.Choice([str(v) for v in meta["enum"]])
    return TYPE_MAP.get(meta.get("type"), str)


def build_request(operation, api_entry, kwargs, param_metas, has_body, global_opts):
    """Assemble (url, params, headers, json_body) from CLI kwargs."""
    base = spec_base_url(api_entry["spec"]) or api_entry["source"]
    url = operation["path"]
    params, headers = {}, dict(global_opts["headers"])
    body = None

    for meta in param_metas:
        name = meta["name"]
        # click normalizes declared names to lowercase with underscores
        value = kwargs.pop(name.lower().replace("-", "_"), None)
        if value is None:
            continue
        if meta["in"] == "path":
            url = url.replace("{" + name + "}", str(value))
        elif meta["in"] == "query":
            params[name] = ",".join(str(v) for v in value) if meta.get("is_array") else value
        elif meta["in"] == "header":
            headers[name] = str(value)
        else:
            log(f"warning: ignoring parameter {name!r} (in: {meta['in']})")

    if has_body:
        body_text = kwargs.pop("body", None)
        fields = kwargs.pop("field", None) or []
        if body_text is not None and fields:
            raise click.ClickException("use either --body or -F/--field, not both")
        if body_text is not None:
            body = parse_body_text(body_text)
        elif fields:
            body = {}
            for pair in fields:
                if "=" not in pair:
                    raise click.ClickException(f"--field expects key=value, got {pair!r}")
                k, v = pair.split("=", 1)
                body[k.replace("-", "_")] = smart_value(v)
    from urllib.parse import urljoin

    return urljoin(base + "/", url.lstrip("/")), params, headers, body


def execute(name, operation, api_entry, kwargs, param_metas, has_body, global_opts):
    import httpx

    url, params, headers, body = build_request(
        operation, api_entry, kwargs, param_metas, has_body, global_opts
    )
    method = operation["method"].upper()

    if global_opts["dry_run"]:
        preview = {
            "method": method,
            "url": url,
            "params": params,
            "headers": headers,
            "json": body,
        }
        click.echo(json.dumps(preview, ensure_ascii=False, indent=2))
        return None

    if NAME_RE.match(name) is None:
        raise click.ClickException(f"invalid API name {name!r}")

    started = time.time()
    try:
        resp = httpx.request(
            method,
            url,
            params=params or None,
            json=body,
            headers=headers or None,
            timeout=global_opts["timeout"],
            verify=not global_opts["insecure"],
        )
    except httpx.HTTPError as e:
        raise click.ClickException(f"request failed: {e}")

    if global_opts["verbose"]:
        log(f"{method} {resp.url} -> {resp.status_code} in {time.time() - started:.2f}s")

    if resp.status_code >= 400:
        snippet = resp.text[:400]
        log(f"HTTP {resp.status_code} {resp.reason_phrase} from {method} {url}")
        if snippet:
            log(snippet)
        sys.exit(1)

    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            return resp.json()
        except ValueError:
            return resp.text
    return resp.text


# --------------------------------------------------------------------------
# dynamic command tree
# --------------------------------------------------------------------------


def parse_operations(spec):
    """get_operations() over an in-memory spec (definition is normally loaded lazily)."""
    api = OpenAPIClient(spec)
    api.definition = spec
    return api.get_operations()


def collect_param_metas(operation, spec):
    metas = []
    for p in operation.get("parameters", []) or []:
        if isinstance(p, dict):
            meta = extract_parameter_meta(p, spec)
        else:
            continue
        meta["is_array"] = meta.get("type") == "array"
        if meta["is_array"] and not meta.get("enum"):
            meta.setdefault("item_type", "string")
        metas.append(meta)
    return metas


def build_operation_command(name, operation, api_entry):
    spec = api_entry["spec"]
    cmd_name = operation_command_name(operation)
    metas = collect_param_metas(operation, spec)
    path_metas = [m for m in metas if m["in"] == "path" and m["required"]]
    # order path params by their appearance in the URL template
    path_metas.sort(key=lambda m: operation["path"].find("{" + m["name"] + "}"))
    other_metas = [m for m in metas if m not in path_metas]
    has_body = bool(operation.get("requestBody"))

    params = []
    for m in path_metas:
        params.append(
            click.Argument([m["name"].replace("-", "_")], type=click_type(m), required=True)
        )
    for m in other_metas:
        decl = f'--{to_kebab(m["name"])}'
        if m["is_array"]:
            params.append(
                click.Option(
                    [decl],
                    type=click_type(m),
                    multiple=True,
                    required=m["required"],
                    default=m.get("default"),
                    help=(m["description"] or m["name"])[:120],
                )
            )
        else:
            params.append(
                click.Option(
                    [decl],
                    type=click_type(m),
                    required=m["required"],
                    default=m.get("default"),
                    help=(m["description"] or m["name"])[:120],
                )
            )
    if has_body:
        params.append(
            click.Option(
                ["--body"],
                type=str,
                default=None,
                help="request body: inline JSON, @file, or - for stdin",
            )
        )
        params.append(
            click.Option(
                ["-F", "--field"],
                type=str,
                multiple=True,
                help="request body field key=value (repeatable; value smart-parsed)",
            )
        )

    summary = operation.get("summary") or operation.get("description") or ""
    help_text = summary
    detail = operation.get("description") or ""
    if detail and detail != summary:
        help_text = f"{summary}\n\n{detail}"

    def callback(**kwargs):
        ctx = click.get_current_context()
        global_opts = ctx.obj or {}
        result = execute(
            name,
            operation,
            api_entry,
            kwargs,
            metas,
            has_body,
            global_opts,
        )
        if result is not None:
            emit(result, global_opts.get("output", "json"), global_opts.get("jq"))

    return click.Command(
        cmd_name,
        params=params,
        callback=callback,
        help=help_text[:300] or None,
        short_help=summary[:80] or None,
    )


class ApiGroup(click.Group):
    """Lazy command group for one registered API."""

    def __init__(self, name, api_entry):
        super().__init__(
            name=name,
            help=(api_entry["spec"].get("info", {}).get("description")
                  or f"Operations for {name} ({api_entry['source']})"),
        )
        self.api_entry = api_entry
        self.api_name = name
        self._ops = None
        self._by_operation_id = {}

    def operations(self):
        if self._ops is None:
            self._ops = parse_operations(self.api_entry["spec"])
        return self._ops

    def list_commands(self, ctx):
        return sorted({operation_command_name(o) for o in self.operations()})

    def get_command(self, ctx, cmd_name):
        for op in self.operations():
            if operation_command_name(op) == cmd_name:
                return build_operation_command(self.api_name, op, self.api_entry)
            op_id = op.get("operationId")
            if op_id and op_id not in self._by_operation_id:
                self._by_operation_id[op_id] = op
        # allow the raw operationId as an alias
        op = self._by_operation_id.get(cmd_name)
        if op is not None:
            return build_operation_command(self.api_name, op, self.api_entry)
        return None


BUILTIN_COMMANDS = ("connect", "sync", "ls", "rm", "schema", "api")


class OapiCLI(click.Group):
    def list_commands(self, ctx):
        return sorted(set(super().list_commands(ctx)) | set(load_registry()))

    def get_command(self, ctx, name):
        cmd = super().get_command(ctx, name)
        if cmd is not None:
            return cmd
        if name in BUILTIN_COMMANDS:
            return None
        try:
            entry = load_api(name)
        except click.ClickException:
            return None
        return ApiGroup(name, entry)


# --------------------------------------------------------------------------
# builtin commands
# --------------------------------------------------------------------------


@click.command()
@click.argument("name")
@click.argument("spec_source")
@click.option("--insecure", is_flag=True, help="skip TLS verification when fetching the spec")
@click.option("--force", is_flag=True, help="overwrite an existing registration")
def connect(name, spec_source, insecure, force):
    """Register an API: fetch its spec and cache it locally."""
    if registry_path(name).exists() and not force:
        raise click.ClickException(f"{name!r} already registered (use --force or `oapi rm {name}`)")
    spec = load_spec_source(spec_source, insecure=insecure)
    entry = save_api(name, spec_source, spec)
    n_ops = len(parse_operations(spec))
    click.echo(
        f"connected {name}: {n_ops} operations from {spec_source} "
        f"(spec: {entry['spec'].get('info', {}).get('title', '?')} "
        f"v{entry['spec'].get('info', {}).get('version', '?')})",
        err=True,
    )


@click.command()
@click.argument("name")
@click.option("--insecure", is_flag=True, help="skip TLS verification when re-fetching")
def sync(name, insecure):
    """Re-fetch the spec and refresh the local cache."""
    entry = load_api(name)
    source = entry["source"]
    if re.match(r"^https?://", source):
        spec = load_spec_source(source, insecure=insecure)
        save_api(name, source, spec)
        click.echo(f"synced {name} from {source}", err=True)
    else:
        spec = load_spec_source(source)
        save_api(name, source, spec)
        click.echo(f"reloaded {name} from {source}", err=True)


@click.command("ls")
def ls():
    """List registered APIs."""
    entries = load_registry()
    if not entries:
        click.echo("no APIs registered. start with: oapi connect <name> <spec-url-or-file>", err=True)
        return
    rows = []
    for name, e in sorted(entries.items()):
        try:
            n_ops = len(parse_operations(e["spec"]))
        except Exception:
            n_ops = -1
        rows.append((name, str(n_ops), e.get("registered_at", "?"), e.get("source", "?")))
    w = max(len(r[0]) for r in rows)
    for name, n, at, src in rows:
        click.echo(f"{name:<{w}}  {n:>3} ops  {at}  {src}")


@click.command()
@click.argument("name")
@click.confirmation_option(prompt="remove this API registration?")
def rm(name):
    """Remove an API registration."""
    p = registry_path(name)
    if not p.exists():
        raise click.ClickException(f"unknown API {name!r}")
    p.unlink()
    click.echo(f"removed {name}", err=True)


@click.command()
@click.argument("name")
@click.argument("operation", required=False)
def schema(name, operation):
    """Inspect operations: list them, or show one operation's parameter mapping."""
    entry = load_api(name)
    ops = parse_operations(entry["spec"])
    if not operation:
        for op in ops:
            click.echo(
                f"{operation_command_name(op):<32} {op['method'].upper():<7} {op['path']}"
                f"  {(op.get('summary') or '')[:60]}"
            )
        return
    target = next(
        (o for o in ops
         if operation_command_name(o) == operation or o.get("operationId") == operation),
        None,
    )
    if target is None:
        raise click.ClickException(f"no operation {operation!r} in {name!r}")
    info = {
        "command": operation_command_name(target),
        "operationId": target.get("operationId"),
        "method": target["method"].upper(),
        "path": target["path"],
        "summary": target.get("summary"),
        "parameters": collect_param_metas(target, entry["spec"]),
        "hasRequestBody": bool(target.get("requestBody")),
    }
    click.echo(json.dumps(info, ensure_ascii=False, indent=2))


def spec_origin(spec):
    """scheme://netloc of the first server (raw `api` command base)."""
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        parsed = urlparse(servers[0]["url"])
        if parsed.scheme:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


@click.command("api")
@click.argument("name")
@click.argument("http_method")
@click.argument("path")
@click.option("--params", help="query parameters as JSON (inline / @file / -)")
@click.option("--data", help="request body as JSON (inline / @file / -)")
@click.pass_context
def api_command(ctx, name, http_method, path, params, data):
    """Raw escape hatch: call any endpoint by METHOD + full path.

    PATH is the absolute path including any prefix (e.g. /v1/pets).
    """
    entry = load_api(name)
    query = parse_body_text(params) if params else None
    body = parse_body_text(data) if data else None
    from urllib.parse import urljoin

    base = spec_origin(entry["spec"]) or entry["source"]
    url = urljoin(base + "/", path.lstrip("/"))
    global_opts = ctx.obj or {}

    def run():
        import httpx

        if global_opts.get("dry_run"):
            click.echo(json.dumps(
                {"method": http_method.upper(), "url": url, "params": query, "json": body},
                ensure_ascii=False, indent=2))
            return None
        resp = httpx.request(
            http_method.upper(), url,
            params=query or None, json=body,
            timeout=global_opts.get("timeout", 30.0),
            verify=not global_opts.get("insecure", False),
        )
        if global_opts.get("verbose"):
            log(f"{http_method.upper()} {resp.url} -> {resp.status_code}")
        if resp.status_code >= 400:
            log(f"HTTP {resp.status_code} {resp.reason_phrase} from {http_method.upper()} {url}")
            snippet = resp.text[:400]
            if snippet:
                log(snippet)
            sys.exit(1)
        if "application/json" in resp.headers.get("content-type", ""):
            try:
                return resp.json()
            except ValueError:
                return resp.text
        return resp.text

    emit(run(), global_opts.get("output", "json"), global_opts.get("jq"))


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


@click.command(cls=OapiCLI)
@click.option("-o", "--output", type=click.Choice(["json", "text"]), default="json",
              help="output format for response payloads")
@click.option("--jq", default=None, help="filter output with a dot-path (a.b[0].c, items[].id)")
@click.option("--header", "headers", multiple=True, help="extra header 'Name: Value' (repeatable)")
@click.option("--timeout", type=float, default=30.0, show_default=True, help="request timeout in seconds")
@click.option("--insecure", is_flag=True, help="skip TLS certificate verification")
@click.option("--dry-run", is_flag=True, help="print the request instead of sending it")
@click.option("-v", "--verbose", is_flag=True, help="log request/response summary to stderr")
@click.pass_context
def main(ctx, output, jq, headers, timeout, insecure, dry_run, verbose):
    """oapi - call OpenAPI-defined APIs from the command line.

    Register once with `connect`, then use `oapi <name> <operation>`.

    \b
    Examples:
      oapi connect petstore ./petstore.json
      oapi petstore get-pet 42
      oapi petstore list-pets --status available --jq 'items[].name'
      oapi schema petstore get-pet
      oapi api petstore GET /v1/pets --params '{"limit": 10}'
    """
    hdrs = {}
    for h in headers:
        if ":" not in h:
            raise click.ClickException(f"--header expects 'Name: Value', got {h!r}")
        k, v = h.split(":", 1)
        hdrs[k.strip()] = v.strip()
    ctx.obj = {
        "output": output,
        "jq": jq,
        "headers": hdrs,
        "timeout": timeout,
        "insecure": insecure,
        "dry_run": dry_run,
        "verbose": verbose,
    }


main.add_command(connect)
main.add_command(sync)
main.add_command(ls)
main.add_command(rm)
main.add_command(schema)
main.add_command(api_command)


if __name__ == "__main__":
    main()
