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
    from .calibrate import backfill_geometry, calibrate

    if args.geometry_only:
        return backfill_geometry(args.form)
    return calibrate(args.form)


def cmd_bind(args: argparse.Namespace) -> int:
    from .loop import run_loop

    return run_loop(
        args.form, args.estate,
        from_draft=args.from_draft,
        max_rounds=args.max_rounds,
        label=args.label,
        naive=args.naive,
    )


def cmd_fill(args: argparse.Namespace) -> int:
    from .fill import run_fill

    return run_fill(
        args.form, args.estate, via=args.via, binding_version=args.binding_version
    )


def cmd_propose(args: argparse.Namespace) -> int:
    from .bind import run_propose

    return run_propose(
        args.form, args.estate,
        whole_form_first=args.whole_form,
        from_draft=args.from_draft,
    )


def cmd_reuse_proof(args: argparse.Namespace) -> int:
    from .reuse import run_reuse_proof

    return run_reuse_proof(binding_version=args.binding_version, use_draft=args.draft)


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo import build_all

    return build_all()


def cmd_anvil_register(args: argparse.Namespace) -> int:
    import hashlib
    import json

    from . import anvil
    from .fill import load_approved
    from .registry import BINDINGS_DIR, rel

    if args.binding_version is not None:
        artifact, path = load_approved(args.form, args.binding_version)
    else:
        path = BINDINGS_DIR / f"{args.form}.json"
        if not path.exists():
            print(f"no draft binding at {rel(path)}; run forge bind first", file=sys.stderr)
            return 1
        artifact = json.loads(path.read_text(encoding="utf-8"))

    transport = anvil.HttpTransport()
    cast = anvil.register_cast(args.form, artifact, transport)
    drift = anvil.reconcile(artifact, cast)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    # an approved artifact is immutable (chmod 0444), so the cast eid goes beside it
    reg = anvil.save_cast_registry(args.form, cast, sha, rel(path))
    print(f"cast {cast['eid']} registered and published from {rel(path)}")
    print(f"wrote {rel(reg)}")
    print(f"drift: {json.dumps(drift)}")
    return 0 if not drift["boundButMissingFromCast"] else 1


def cmd_review(args: argparse.Namespace) -> int:
    from .review import serve

    return serve(port=args.port, form=args.form, estate=args.estate)


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
    p.add_argument(
        "--geometry-only", action="store_true",
        help="no model: add measured page boxes and per-widget rectangles to an existing "
        "calibration, leaving every label untouched",
    )
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("bind", help="synthesise binding; run the convergence loop")
    p.add_argument("form")
    p.add_argument("--estate", required=True)
    p.add_argument(
        "--from-draft", action="store_true",
        help="skip proposal; stress the existing draft binding against this estate",
    )
    p.add_argument("--max-rounds", type=int, default=6)
    p.add_argument(
        "--label",
        help="isolate this run's outputs (renders under out/renders/<form>/<label>/, draft "
        "to out/reports/) so it cannot clobber the draft under review",
    )
    p.add_argument(
        "--naive", action="store_true",
        help="propose with the pre-lessons binding language, so the loop has real mistakes "
        "to correct rather than marking its own homework",
    )
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser(
        "propose",
        help="one-pass binding synthesis: propose, write the draft, fill and rasterise once "
        "(no critique, no rounds — see forge bind for the convergence loop)",
    )
    p.add_argument("form")
    p.add_argument("--estate", required=True)
    p.add_argument(
        "--whole-form", action="store_true",
        help="try one whole-form call before falling back to per-page (known to time out on f56)",
    )
    p.add_argument(
        "--from-draft", action="store_true",
        help="no model: re-validate, re-fill and re-render the existing draft "
        "(use after editing rows in forge review)",
    )
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("fill", help="fill using an approved binding; asserts zero model calls")
    p.add_argument("form")
    p.add_argument("--estate", required=True)
    p.add_argument("--via", choices=("local", "anvil"), default="local")
    p.add_argument(
        "--binding-version", type=int, default=None,
        help="pin an exact approved version instead of the highest (reproducibility)",
    )
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser(
        "anvil-register",
        help="create/refresh the Anvil cast for a form's DRAFT binding (before approval "
        "— approved artifacts are immutable, and anvilCastEid is part of the compiled output)",
    )
    p.add_argument("form")
    p.add_argument(
        "--binding-version", type=int, default=None,
        help="register from this approved version instead of the draft",
    )
    p.set_defaults(func=cmd_anvil_register)

    p = sub.add_parser("review", help="serve the approval UI on localhost")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--form", default="irs-f56", help="form the printed URL opens on")
    p.add_argument("--estate", default="estate-05-in-formal-probate")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("bench", help="run every applicable (estate, form) pair; write report")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser(
        "reuse-proof",
        help="fill every estate from ONE binding and build out/demo/reuse.md plus the "
        "Section A comparison strip; no model, ever",
    )
    p.add_argument("--binding-version", type=int, default=1)
    p.add_argument("--draft", action="store_true", help="use the draft instead of an approved version")
    p.set_defaults(func=cmd_reuse_proof)

    p = sub.add_parser("demo", help="assemble demo assets under out/demo/")
    p.set_defaults(func=cmd_demo)

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
