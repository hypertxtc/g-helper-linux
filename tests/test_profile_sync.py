# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for profile synchronization, D-Bus signal watch and unplug race guards."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper import window as win_mod  # noqa: E402
from ghelper.backend import asusd as asusd_mod  # noqa: E402
from ghelper.ui import main_view as mv_mod  # noqa: E402


def make_window(initial_profile: int = asusd_mod.BALANCED):
    win = win_mod.MainWindow.__new__(win_mod.MainWindow)
    win.asusd_available = True
    win._profile = initial_profile
    win._profile_seq = 1
    win._profile_writes_inflight = 0
    win._expected_profile = None
    win._power_settle_until = 0.0
    win._power_confirm_ids = []
    win._profile_watch = None
    win._power_watch = None
    win._sync_pending = False
    win._fans_window = None
    win._settings_window = None
    win._tray = mock.Mock(active=True)
    win._last_snapshot = {}
    win._last_ac = True
    win.profiles = [asusd_mod.QUIET, asusd_mod.BALANCED, asusd_mod.PERFORMANCE]
    win.tasks = mock.Mock()
    win.asusd = mock.Mock()
    win.show_mode_overlay = mock.Mock()
    win.toast = mock.Mock()
    win.show_error = mock.Mock()
    win.get_visible = mock.Mock(return_value=True)

    mv = SimpleNamespace(
        _profile=initial_profile,
        gpu_quick_state=lambda: "standard",
        pending_reboot=lambda: False,
    )

    def set_profile(value: int) -> None:
        mv._profile = value

    mv.set_profile = mock.Mock(side_effect=set_profile)
    win.main_view = mv
    return win


class ProfileSyncTests(unittest.TestCase):
    def test_stale_poll_sequence_is_discarded(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        win._begin_power_ui(asusd_mod.QUIET, ac=False, seq=1)
        # Newer sequence (2) advances past the in-flight poll captured at seq=1.
        win._profile_seq = 2
        win._sync_pending = True

        win._sync_profile_done(asusd_mod.BALANCED, force=False, seq=1)

        self.assertFalse(win._sync_pending)
        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win.main_view._profile, asusd_mod.QUIET)

    def test_power_settle_window_blocks_premature_revert_then_reconciles(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        with mock.patch.object(win, "_schedule_power_confirms"):
            win._begin_power_ui(asusd_mod.QUIET, ac=False, seq=1)
            win._finish_power_switch(asusd_mod.QUIET, ac=False, switched=True, seq=1)

        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win._expected_profile, asusd_mod.QUIET)

        # Premature poll while asusd/tuned-ppd settle.
        win._sync_profile_done(asusd_mod.BALANCED, force=True, seq=1)
        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win.main_view._profile, asusd_mod.QUIET)

        # Once the settle window expires without switching, reconcile.
        win._power_settle_until = time.monotonic() - 0.1
        win._sync_profile_done(asusd_mod.BALANCED, force=False, seq=1)
        self.assertIsNone(win._expected_profile)
        self.assertEqual(win._profile, asusd_mod.BALANCED)
        self.assertEqual(win.main_view._profile, asusd_mod.BALANCED)

    def test_matching_expected_profile_clears_settle_window(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        with mock.patch.object(win, "_schedule_power_confirms"):
            win._begin_power_ui(asusd_mod.QUIET, ac=False, seq=1)
            win._finish_power_switch(asusd_mod.QUIET, ac=False, switched=True, seq=1)

        win._sync_profile_done(asusd_mod.QUIET, force=False, seq=1)
        self.assertIsNone(win._expected_profile)
        self.assertEqual(win._power_settle_until, 0.0)
        self.assertEqual(win._profile, asusd_mod.QUIET)

    def test_sync_self_heals_main_view_drift(self) -> None:
        win = make_window(asusd_mod.QUIET)
        win.main_view._profile = asusd_mod.BALANCED

        win._sync_profile_done(asusd_mod.QUIET, force=False, seq=1)
        self.assertEqual(win.main_view._profile, asusd_mod.QUIET)
        win.main_view.set_profile.assert_called_with(asusd_mod.QUIET)

    def test_transient_disagreeing_signal_ignored_inflight_and_during_settle(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        win._profile_seq = 2
        win._profile_writes_inflight = 1
        win._expected_profile = asusd_mod.QUIET
        win._power_settle_until = time.monotonic() + 10.0
        win._apply_profile_ui(asusd_mod.QUIET)

        # Intermediate TuneD rollback signal while transition is in flight.
        win._on_profile_signal(asusd_mod.BALANCED)
        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win._profile_seq, 2)
        self.assertEqual(win._expected_profile, asusd_mod.QUIET)

        # Rollback signal after worker completes, still inside the settle window.
        win._profile_writes_inflight = 0
        win._on_profile_signal(asusd_mod.BALANCED)
        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win._profile_seq, 2)
        self.assertEqual(win._expected_profile, asusd_mod.QUIET)

        # External switch after settle window expires is honoured.
        win._power_settle_until = time.monotonic() - 0.1
        win._on_profile_signal(asusd_mod.BALANCED)
        self.assertEqual(win._profile, asusd_mod.BALANCED)
        self.assertEqual(win._profile_seq, 3)
        self.assertIsNone(win._expected_profile)

    def test_matching_signal_during_inflight_updates_ui_without_clearing_guard(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        win._profile_seq = 2
        win._profile_writes_inflight = 1
        win._expected_profile = asusd_mod.QUIET
        win._power_settle_until = time.monotonic() + 10.0

        win._on_profile_signal(asusd_mod.QUIET)

        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win.main_view._profile, asusd_mod.QUIET)
        self.assertEqual(win._profile_seq, 2)
        self.assertEqual(win._expected_profile, asusd_mod.QUIET)
        win._tray.set_profile.assert_called_with(asusd_mod.QUIET)

    def test_profile_signal_outside_settle_updates_ui(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        win._on_profile_signal(asusd_mod.QUIET)

        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win.main_view._profile, asusd_mod.QUIET)
        self.assertEqual(win._profile_seq, 2)
        win._tray.set_profile.assert_called_with(asusd_mod.QUIET)

    def test_announce_power_syncs_ppd_before_asusd_and_begins_ui(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        order: list[tuple[str, int]] = []
        idle_calls: list[tuple] = []

        win.asusd.set_platform_profile.side_effect = (
            lambda prof: order.append(("asusd", prof))
        )

        def fake_submit(work, *, done=None, error=None):
            try:
                res = work()
            except Exception as exc:  # noqa: BLE001
                if error is not None:
                    error(exc)
            else:
                if done is not None:
                    done(res)

        win.tasks.submit.side_effect = fake_submit

        def fake_idle(fn, *args):
            idle_calls.append((fn, args))
            fn(*args)
            return 1

        with (
            mock.patch.object(
                win_mod.power_memory, "target", return_value=asusd_mod.QUIET
            ),
            mock.patch.object(
                win_mod.ppd,
                "sync_profile",
                side_effect=lambda prof: order.append(("ppd", prof)) or True,
            ),
            mock.patch.object(win_mod.GLib, "idle_add", side_effect=fake_idle),
            mock.patch.object(win, "_schedule_power_confirms") as confirms,
        ):
            win._announce_power(ac=False, seq=1)

        self.assertEqual(
            order, [("ppd", asusd_mod.QUIET), ("asusd", asusd_mod.QUIET)]
        )
        self.assertEqual(len(idle_calls), 1)
        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win._expected_profile, asusd_mod.QUIET)
        self.assertEqual(win._profile_writes_inflight, 0)
        win.show_mode_overlay.assert_called_once_with(asusd_mod.QUIET, False)
        confirms.assert_called_once()

    def test_apply_profile_orders_ppd_asusd_remember_and_reverts_on_failure(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        order: list[tuple[str, int]] = []

        win.asusd.set_platform_profile.side_effect = (
            lambda prof: order.append(("asusd", prof))
        )

        def submit_and_capture(work, *, done=None, error=None):
            if work == win.asusd.platform_profile:
                return
            try:
                res = work()
            except Exception as exc:  # noqa: BLE001
                if error is not None:
                    error(exc)
            else:
                if done is not None:
                    done(res)

        win.tasks.submit.side_effect = submit_and_capture

        # Optimistic UI happens before background worker runs; verify mid-write
        # rollback signal is ignored while inflight > 0.
        submitted: list[tuple] = []

        def defer_submit(work, *, done=None, error=None):
            submitted.append((work, done, error))

        win.tasks.submit.side_effect = defer_submit
        with mock.patch.object(win, "_cancel_power_confirms"):
            win.apply_profile(asusd_mod.QUIET)

        self.assertEqual(win._profile, asusd_mod.QUIET)
        self.assertEqual(win.main_view._profile, asusd_mod.QUIET)
        self.assertEqual(win._profile_writes_inflight, 1)

        win._on_profile_signal(asusd_mod.BALANCED)
        self.assertEqual(win._profile, asusd_mod.QUIET)

        # Now execute deferred work and check ordering.
        work, done, _err = submitted[0]
        with (
            mock.patch.object(
                win_mod.ppd,
                "sync_profile",
                side_effect=lambda prof: order.append(("ppd", prof)) or True,
            ),
            mock.patch.object(
                win_mod.power_memory,
                "remember",
                side_effect=lambda _a, prof: order.append(("remember", prof)),
            ),
            mock.patch.object(win, "_sync_profile") as sync_mock,
        ):
            work()
            done(None)

        self.assertEqual(
            order,
            [
                ("ppd", asusd_mod.QUIET),
                ("asusd", asusd_mod.QUIET),
                ("remember", asusd_mod.QUIET),
            ],
        )
        self.assertEqual(win._profile_writes_inflight, 0)
        sync_mock.assert_called_with(force=True)

        # Failure path calls show_error and forces sync to revert UI.
        win.asusd.set_platform_profile.side_effect = RuntimeError("write failed")
        win.tasks.submit.side_effect = submit_and_capture
        with (
            mock.patch.object(win_mod.ppd, "sync_profile", return_value=True),
            mock.patch.object(win, "_cancel_power_confirms"),
            mock.patch.object(win, "_sync_profile") as sync_mock,
        ):
            win.apply_profile(asusd_mod.PERFORMANCE)

        self.assertEqual(win._profile_writes_inflight, 0)
        win.show_error.assert_called_once()
        sync_mock.assert_called_with(force=True)

    def test_upower_and_poll_deduplicate_via_last_ac(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        win._last_ac = True
        with (
            mock.patch.object(win, "_announce_power") as announce,
            mock.patch.object(win_mod, "ac_online", return_value=False),
        ):
            win._on_upower_ac(False)
            win._poll_power()

        announce.assert_called_once_with(False, 2)
        self.assertFalse(win._last_ac)

    def test_superseded_announce_power_seq_is_ignored_on_completion(self) -> None:
        win = make_window(asusd_mod.BALANCED)
        win._profile_seq = 2
        win._profile_writes_inflight = 1

        with mock.patch.object(win, "_schedule_power_confirms") as confirms:
            win._finish_power_switch(asusd_mod.QUIET, ac=False, switched=True, seq=1)

        self.assertEqual(win._profile_writes_inflight, 0)
        self.assertEqual(win._profile, asusd_mod.BALANCED)
        win.show_mode_overlay.assert_not_called()
        confirms.assert_not_called()

    def test_watch_platform_profile_filters_interface_and_property(self) -> None:
        client = asusd_mod.Asusd.__new__(asusd_mod.Asusd)
        bus = mock.Mock()
        client._bus = bus
        seen: list[int] = []

        client.watch_platform_profile(seen.append)
        handler = bus.add_signal_receiver.call_args.args[0]

        handler("xyz.ljones.FanCurves", {"PlatformProfile": 2}, [])
        handler("xyz.ljones.Platform", {"OtherProp": 1}, [])
        handler("xyz.ljones.Platform", {"PlatformProfile": "bad"}, [])
        handler("xyz.ljones.Platform", {"PlatformProfile": 2}, [])

        self.assertEqual(seen, [asusd_mod.QUIET])

    def test_main_view_collect_and_apply_do_not_own_profile(self) -> None:
        view = mv_mod.MainView.__new__(mv_mod.MainView)
        view.asusd = mock.Mock()
        view.asusd.pending_reboot.return_value = False
        view.asusd.armoury.side_effect = asusd_mod.AsusdError("absent")
        view.asusd.charge_limit.return_value = 80
        view.aura = SimpleNamespace(available=False)
        view.slash = SimpleNamespace(available=False)
        view.anime = SimpleNamespace(available=False)

        with (
            mock.patch.object(mv_mod.cw, "available", return_value=False),
            mock.patch.object(mv_mod.display, "available", return_value=False),
            mock.patch.object(mv_mod.display, "unavailable_reason", return_value="off"),
        ):
            data = view._collect()

        self.assertNotIn("profile", data)
        view.asusd.platform_profile.assert_not_called()

        view.set_profile = mock.Mock()
        view._apply_gpu = mock.Mock()
        view._apply_screen = mock.Mock()
        view._apply_keyboard = mock.Mock()
        view._apply_slash = mock.Mock()
        view._apply_anime = mock.Mock()
        view._apply_battery = mock.Mock()
        view._startup = mock.Mock()
        with mock.patch.object(mv_mod.autostart, "enabled", return_value=False):
            view._apply({"profile": asusd_mod.PERFORMANCE})
        view.set_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
