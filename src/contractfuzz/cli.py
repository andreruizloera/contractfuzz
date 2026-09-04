"""Command-line interface for contractfuzz."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from contractfuzz import __version__
from contractfuzz.errors import ContractfuzzError
from contractfuzz.generator import TargetResult, generate_for_target, write_fixtures
from contractfuzz.ranking import render_summary
from contractfuzz.spec import find_targets, list_endpoints, load_spec


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contractfuzz",
        description=(
            "Find undocumented assumptions in API clients by generating"
            " valid-but-unusual payloads from an OpenAPI 3 contract."
        ),
    )
    parser.add_argument("--version", action="version", version=f"contractfuzz {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate valid edge-case fixtures for one endpoint")
    gen.add_argument("spec", help="path to an OpenAPI 3 YAML or JSON document")
    gen.add_argument("--endpoint", required=True, help="path template, e.g. /users/{id}")
    gen.add_argument("--method", help="restrict to one HTTP method (default: all on the endpoint)")
    gen.add_argument(
        "--out", default="fixtures", help="output directory for fixtures (default: fixtures)"
    )
    gen.add_argument(
        "--responses-only",
        action="store_true",
        help="skip request-body schemas, generate response fixtures only",
    )

    ls = sub.add_parser("list", help="list the endpoints and methods in a spec")
    ls.add_argument("spec", help="path to an OpenAPI 3 YAML or JSON document")

    proxy = sub.add_parser(
        "proxy", help="run a mutating proxy that applies schema-valid mutations to JSON responses"
    )
    proxy.add_argument("spec", help="path to an OpenAPI 3 YAML or JSON document")
    proxy.add_argument(
        "--upstream", required=True, help="upstream base URL, e.g. http://localhost:8080"
    )
    proxy.add_argument(
        "--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)"
    )
    proxy.add_argument("--port", type=int, default=8642, help="port to listen on (default: 8642)")
    return parser


def _cmd_generate(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    targets = find_targets(spec, args.endpoint, args.method)
    if args.responses_only:
        targets = [t for t in targets if t.kind == "response"]
        if not targets:
            print("error: no response schemas found for this endpoint", file=sys.stderr)
            return 2

    results: list[TargetResult] = []
    for target in targets:
        results.append(generate_for_target(target))

    out_dir = Path(args.out)
    write_fixtures(out_dir, args.spec, args.endpoint, results)

    total = sum(len(r.variants) for r in results)
    rejected = sum(r.rejected for r in results)
    print(f"Generated {total} valid edge-case fixtures for {args.endpoint} -> {out_dir}/")
    print("Every fixture validates against the OpenAPI contract.")
    print()
    for result in results:
        print(render_summary(result.target.display, result.variants))
        print()
    print(f"Machine-readable mutation records: {out_dir}/manifest.json")
    if rejected:
        print(
            f"note: {rejected} candidate mutations were discarded because they"
            " failed contract validation",
            file=sys.stderr,
        )
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    for path, methods in list_endpoints(spec):
        print(f"{path}  [{', '.join(methods)}]")
    return 0


def _cmd_proxy(args: argparse.Namespace) -> int:
    from contractfuzz.proxy import run_proxy  # deferred: not needed for generate

    spec = load_spec(args.spec)
    return run_proxy(spec, args.upstream, args.host, args.port)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            return _cmd_generate(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "proxy":
            return _cmd_proxy(args)
    except ContractfuzzError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
