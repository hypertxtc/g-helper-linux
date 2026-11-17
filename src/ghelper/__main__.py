# SPDX-License-Identifier: GPL-3.0-or-later

"""``python -m ghelper`` and the ``ghelper-linux`` launcher."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import APP_NAME, APP_VERSION


def _check() -> int:
    """Print a JSON backend report; useful on new machines and in bug reports."""
    from .backend import asusd as asusd_mod
    from .backend import cardwire, display, hotkeys
    from .backend.system import Monitor, machine_name

    report: dict = {"machine": machine_name(), "version": APP_VERSION}
    client = asusd_mod.Asusd()
    try:
        client.connect()
        report["asusd"] = {
            "available": True,
            "version": client.version,
            "profile": client.platform_profile(),
            "profile_choices": client.platform_choices(),
            "charge_limit": client.charge_limit(),
            "fan_curves_balanced": client.fan_curves(asusd_mod.BALANCED),
            "armoury": {
                name: client.armoury(name)
                for name in (
                    asusd_mod.ATTR_DGPU_DISABLE,
                    asusd_mod.ATTR_GPU_MUX,
                    asusd_mod.ATTR_PANEL_OVERDRIVE,
                    asusd_mod.ATTR_PPT_APU,
                    asusd_mod.ATTR_PPT_PLATFORM,
                    asusd_mod.ATTR_PPT_FPPT,
                )
                if client.armoury_available(name)
            },
            "resolved_attributes": client.resolved_attributes(),
        }
    except Exception as exc:  # noqa: BLE001
        report["asusd"] = {"available": False, "error": str(exc)}

    try:
        report["cardwire"] = {"available": cardwire.available(), **cardwire.get_state()}
        report["cardwire"]["gpus"] = cardwire.list_gpus()
    except Exception as exc:  # noqa: BLE001
        report["cardwire"] = {"available": cardwire.available(), "error": str(exc)}

    try:
        panel = display.primary_panel()
        report["display"] = {
            "available": display.available(),
            "panel": panel["name"] if panel else None,
            "refresh_rates": display.refresh_rates(panel) if panel else [],
        }
    except Exception as exc:  # noqa: BLE001
        report["display"] = {"available": display.available(), "error": str(exc)}

    report["sensors"] = Monitor().snapshot()
    report["hotkeys"] = hotkeys.probe()
    print(json.dumps(report, indent=2, default=str))
    return 0


def _run_action(args) -> int:
    """One-shot actions, intended for hotkeys and scripts."""
    from .backend import asusd as asusd_mod
    from .backend import cardwire, display

    try:
        if args.profile or args.charge_limit is not None:
            client = asusd_mod.Asusd()
            client.connect()
        else:
            client = None

        if args.profile and client is not None:
            choices = set(client.platform_choices())
            preferred = {
                "quiet": (asusd_mod.QUIET, asusd_mod.LOW_POWER),
                "balanced": (asusd_mod.BALANCED,),
                "performance": (asusd_mod.PERFORMANCE,),
                "custom": (asusd_mod.CUSTOM,),
            }[args.profile]
            profile = next((p for p in preferred if p in choices), preferred[0])
            client.set_platform_profile(profile)
            print(f"profile: {asusd_mod.PROFILE_NAMES.get(profile, profile)}")

        if args.charge_limit is not None and client is not None:
            if not 20 <= args.charge_limit <= 100:
                raise ValueError("charge limit must be between 20 and 100")
            client.set_charge_limit(args.charge_limit)
            print(f"charge limit: {args.charge_limit} %")

        if args.gpu_mode:
            cardwire.set_mode(args.gpu_mode)
            print(f"GPU mode: {args.gpu_mode}")

        if args.refresh_rate:
            panel = display.primary_panel()
            if panel is None:
                raise RuntimeError("no active display found")
            mode = display.current_mode(panel)
            if mode is None:
                raise RuntimeError("could not read the current display mode")
            if args.refresh_rate == "max":
                rates = display.refresh_rates(panel, mode)
                if not rates:
                    raise RuntimeError("no refresh rates reported")
                rate = rates[0]
            else:
                rate = float(args.refresh_rate)
                rates = display.refresh_rates(panel, mode)
                if not any(abs(rate - candidate) < 0.3 for candidate in rates):
                    raise ValueError(
                        f"{rate:g} Hz is not available; choose from "
                        + ", ".join(f"{r:g}" for r in rates)
                    )
            display.set_refresh_rate(panel["name"], mode["width"], mode["height"], rate)
            print(f"refresh rate: {rate:g} Hz")
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ghelper-linux", description=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print a backend diagnostics report as JSON and exit",
    )
    parser.add_argument(
        "--page",
        choices=["main", "fans", "settings", "hotkeys"],
        help="open a specific window",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="start hidden in the tray (GUI mode)",
    )
    parser.add_argument(
        "--learn-fn",
        action="store_true",
        help="print ASUS hotkey codes until interrupted (Fn / ROG keys)",
    )
    actions = parser.add_argument_group("one-shot actions (no window)")
    actions.add_argument(
        "--profile",
        choices=["quiet", "balanced", "performance", "custom"],
        help="switch performance profile and exit",
    )
    actions.add_argument(
        "--gpu-mode",
        choices=["integrated", "hybrid", "smart", "manual"],
        help="set the cardwire GPU mode and exit",
    )
    actions.add_argument(
        "--charge-limit",
        type=int,
        metavar="PERCENT",
        help="set the battery charge limit (20-100) and exit",
    )
    actions.add_argument(
        "--refresh-rate",
        metavar="HZ|max",
        help="set the panel refresh rate for the current resolution and exit",
    )

    automation = parser.add_argument_group("power automation")
    automation.add_argument(
        "--daemon",
        action="store_true",
        help="run the AC/battery automation service loop",
    )
    automation.add_argument(
        "--once",
        action="store_true",
        help="apply automation once and exit (implies --daemon)",
    )
    automation.add_argument(
        "--dry-run",
        action="store_true",
        help="with --daemon/--once, log intended changes without applying them",
    )
    automation.add_argument(
        "--init-config",
        action="store_true",
        help="write the default automation config file and exit",
    )
    args, rest = parser.parse_known_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.check:
        return _check()

    if args.learn_fn:
        from .backend.hotkeys import run_learn

        return run_learn()

    if args.init_config:
        from .automation import write_default_config

        print(write_default_config())
        return 0

    if args.daemon or args.once:
        from .automation import run_daemon

        return run_daemon(once=args.once, dry_run=args.dry_run)

    if args.profile or args.gpu_mode or args.charge_limit is not None or args.refresh_rate:
        return _run_action(args)

    from .app import Application

    gui_args = [sys.argv[0]]
    if args.page:
        gui_args += ["--page", args.page]
    if args.minimized:
        gui_args.append("--minimized")
    gui_args += rest
    application = Application()
    return application.run(gui_args)


if __name__ == "__main__":
    raise SystemExit(main())
