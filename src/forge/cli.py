"""The `forge` entry point.

Every subcommand prints machine-checkable output and exits non-zero on failure.
Phases 1-5 replace the NotImplemented stubs; the surface is fixed here so the other
half of the system can depend on it.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from . import __version__

PHASE_OF = {
    "calibrate": "phase 1",
    "bind": "phase 2",
    "review": "phase 3",
    "fill": "phase 3",
    "bench": "phase 5",
}


def _not_built(name: str) -> int:
    phase = PHASE_OF.get(name, "a later phase")
    print(
        f"forge {name}: not built yet — {phase} of docs/04-BUILD-PLAN.md.\n"
        "Phases are gated: the previous gate must print PASS first.",
        file=sys.stderr,
    )
    return 2


def cmd_inspect(args: argparse.Namespace) -> int:
    from .inspect import inspect_all, inspect_one

    if args.all:
        return inspect_all()
    if not args.form:
        print("forge inspect: give a formId or --all", file=sys.stderr)
        return 2
    return inspect_one(args.form, as_json=args.json)


def cmd_mock_workorder(args: argparse.Namespace) -> int:
    from .registry import ESTATES_DIR
    from .warrant_mock import write_work_order

    estate_ids = (
        [p.stem for p in sorted(ESTATES_DIR.glob("*.json"))] if args.all else [args.estate]
    )
    if not estate_ids or estate_ids == [None]:
        print("forge mock-workorder: give --estate <id> or --all", file=sys.stderr)
        return 2
    for estate_id in estate_ids:
        out = write_work_order(estate_id)
        print(f"wrote {out}")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibrate import calibrate

    return calibrate(args.form)


def cmd_bind(args: argparse.Namespace) -> int:
    from .loop import run_loop

    return run_loop(args.form, args.estate, from_draft=args.from_draft)


def cmd_fill(args: argparse.Namespace) -> int:
    from .fill import run_fill

    return run_fill(args.form, args.estate, via=args.via)


def cmd_anvil_register(args: argparse.Namespace) -> int:
    import json

    from . import anvil
    from .registry import BINDINGS_DIR, rel

    draft_path = BINDINGS_DIR / f"{args.form}.json"
    if not draft_path.exists():
        print(f"no draft binding at {rel(draft_path)}; run forge bind first", file=sys.stderr)
        return 1
    artifact = json.loads(draft_path.read_text(encoding="utf-8"))
    transport = anvil.HttpTransport()
    cast = anvil.register_cast(args.form, artifact, transport)
    drift = anvil.reconcile(artifact, cast)
    artifact["anvilCastEid"] = cast["eid"]
    draft_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"cast {cast['eid']} registered; drift: {json.dumps(drift)}")
    return 0 if not drift["boundButMissingFromCast"] else 1


def cmd_review(args: argparse.Namespace) -> int:
    from .review import serve

    return serve(port=args.port)


def cmd_bench(args: argparse.Namespace) -> int:
    from .bench import run_bench

    return run_bench()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", description="A compiler for government forms.")
    parser.add_argument("--version", action="version", version=f"forge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="enumerate fields; print counts and types")
    p.add_argument("form", nargs="?", help="formId, e.g. irs-f56")
    p.add_argument("--all", action="store_true", help="every registered form; the phase 0 gate")
    p.add_argument("--json", action="store_true", help="dump the full field table as JSON")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("mock-workorder", help="stand in for Warrant; write a work order")
    p.add_argument("--estate", help="estateId, e.g. estate-05-in-formal-probate")
    p.add_argument("--all", action="store_true", help="every estate under inputs/estates/")
    p.set_defaults(func=cmd_mock_workorder)

    p = sub.add_parser("calibrate", help="sentinel pass; write artifacts/calibration/<form>.json")
    p.add_argument("form")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("bind", help="synthesise binding; run the convergence loop")
    p.add_argument("form")
    p.add_argument("--estate", required=True)
    p.add_argument(
        "--from-draft", action="store_true",
        help="skip proposal; stress the existing draft binding against this estate",
    )
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("fill", help="fill using an approved binding; asserts zero model calls")
    p.add_argument("form")
    p.add_argument("--estate", required=True)
    p.add_argument("--via", choices=("local", "anvil"), default="local")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser(
        "anvil-register",
        help="create/refresh the Anvil cast for a form's DRAFT binding (before approval "
        "— approved artifacts are immutable, and anvilCastEid is part of the compiled output)",
    )
    p.add_argument("form")
    p.set_defaults(func=cmd_anvil_register)

    p = sub.add_parser("review", help="serve the approval UI on localhost")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("bench", help="run every applicable (estate, form) pair; write report")
    p.set_defaults(func=cmd_bench)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    try:
        return func(args)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        print(f"forge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
