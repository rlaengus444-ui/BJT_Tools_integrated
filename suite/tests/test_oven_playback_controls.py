"""Real integrated Tk geometry and playback regressions for both 3D tabs."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tkinter as tk
import types
import unittest

from suite_loader import BASE_DIR, compile_tool, tool_specs
from suite_ui import SuiteApp, create_root


def fully_visible(widget):
    """Mapping alone is insufficient: check every ancestor's clipping bounds."""
    if not widget.winfo_ismapped() or min(widget.winfo_width(), widget.winfo_height()) <= 1:
        return False
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    right, bottom = x + widget.winfo_width(), y + widget.winfo_height()
    parent = widget.master
    while parent is not None:
        if not parent.winfo_ismapped():
            return False
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        # A one-pixel native border can overlap its parent's allocation.
        if x < px - 1 or y < py - 1 or right > px + parent.winfo_width() + 1 or bottom > py + parent.winfo_height() + 1:
            return False
        parent = parent.master
    return True


def mount_thermal_for_test(suite):
    """Use the actual suite adapter without stale development manifest hashes."""
    spec = tool_specs()["thermal"]
    path = BASE_DIR / spec["source"]
    name = "_oven_playback_regression_thermal"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module._suite_root_base = suite._base_factory("thermal")
    sys.modules[name] = module
    code, _ = compile_tool(spec, path.read_bytes())
    exec(code, module.__dict__)
    controller = suite.mount_tool("thermal", module)
    suite.select_tool("thermal")
    return module, controller


class OvenPlaybackControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            root = tk.Tk()
            root.withdraw()
            root.destroy()
        except tk.TclError as exc:
            raise unittest.SkipTest("Tk display unavailable: " + str(exc))

    def setUp(self):
        self.root = create_root()
        self.suite = SuiteApp(self.root, test_mode=True)
        self.module, self.app = mount_thermal_for_test(self.suite)
        inputs = replace(self.app._collect_inputs(), nx=3, ny=3, nz=3,
                         oven_profile_min_c=[(0.0, 25.0), (0.02, 30.0)],
                         max_dt_s=0.1, record_interval_s=0.1, snapshot_interval_s=0.1)
        self.result = self.module.simulate(inputs, progress_cb=None, stop_event=None)
        self.app.result = self.result
        self.app._initialize_3d_field(self.result)
        self.app._initialize_o2_3d_field(self.result)
        self.root.geometry("1660x860+0+0")
        self.root.update()

    def tearDown(self):
        try:
            self.suite.request_close(confirm=False)
        except tk.TclError:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def _controls(self, oxygen=False):
        prefix = "o2_field" if oxygen else "field"
        return [getattr(self.app, prefix + suffix) for suffix in
                ("_prev_btn", "_play_btn", "_pause_btn", "_stop_btn", "_next_btn", "_slider", "_time_label")]

    def test_controls_stay_visible_in_both_tabs_at_supported_window_sizes(self):
        for width, height in ((1660, 860), (1120, 780)):
            self.root.geometry(f"{width}x{height}+0+0")
            for language in ("ko", "en"):
                self.app.language_var.set(language)
                for oxygen, tab_index in ((False, 1), (True, 3)):
                    with self.subTest(size=(width, height), language=language, oxygen=oxygen):
                        self.app.right_notebook.select(tab_index)
                        self.root.update()
                        controls = self._controls(oxygen)
                        for widget in controls:
                            self.assertTrue(fully_visible(widget), str(widget))
                        self.assertGreater(controls[-2].winfo_width(), 40)
                        self.assertLess(controls[0].winfo_rooty(), controls[-1].winfo_rooty())
                        expected = ("재생", "일시정지", "정지") if language == "ko" else ("Play", "Pause", "Stop playback")
                        self.assertEqual(tuple(w.cget("text") for w in controls[1:4]), expected)

    def test_play_pause_and_stop_operate_on_saved_frames_without_changing_solver(self):
        original_worker = self.app.worker
        original_stop = self.app.stop_event.is_set()
        temperatures = self.result.snapshots_c.copy()
        oxygen_values = self.result.o2_snapshots_percent.copy()
        for oxygen, tab_index in ((False, 1), (True, 3)):
            with self.subTest(oxygen=oxygen):
                self.app.right_notebook.select(tab_index)
                self.root.update()
                prefix = "o2_field" if oxygen else "field"
                frame = getattr(self.app, prefix + "_slider_var")
                play = getattr(self.app, prefix + "_play_btn")
                pause = getattr(self.app, prefix + "_pause_btn")
                stop = getattr(self.app, prefix + "_stop_btn")
                self.assertEqual(frame.get(), 0)
                play.invoke()
                self.assertEqual(frame.get(), 1)
                self.assertTrue(getattr(self.app, prefix + "_playing"))
                self.assertIsNotNone(getattr(self.app, prefix + "_after_id"))
                self.assertEqual(str(play.cget("state")), "disabled")
                self.assertEqual(str(pause.cget("state")), "normal")
                self.root.after(240, lambda: (pause.invoke(), self.root.quit()))
                self.root.mainloop()
                self.assertGreater(frame.get(), 1)
                held = frame.get()
                self.assertFalse(getattr(self.app, prefix + "_playing"))
                self.assertIsNone(getattr(self.app, prefix + "_after_id"))
                self.root.after(220, self.root.quit)
                self.root.mainloop()
                self.assertEqual(frame.get(), held)
                play.invoke()
                self.assertGreater(frame.get(), held)
                stop.invoke()
                self.assertEqual(frame.get(), 0)
                self.assertFalse(getattr(self.app, prefix + "_playing"))
                self.assertIsNone(getattr(self.app, prefix + "_after_id"))
                self.assertEqual(self.app.stop_event.is_set(), original_stop)
                self.assertIs(self.app.worker, original_worker)
                self.assertIs(self.app.result, self.result)
                self.assertTrue(self.module.np.array_equal(self.result.snapshots_c, temperatures))
                self.assertTrue(self.module.np.array_equal(self.result.o2_snapshots_percent, oxygen_values))

    def test_legacy_toggle_api_keeps_working(self):
        for toggle, prefix in ((self.app._toggle_3d_play, "field"), (self.app._toggle_o2_3d_play, "o2_field")):
            toggle()
            self.assertTrue(getattr(self.app, prefix + "_playing"))
            toggle()
            self.assertFalse(getattr(self.app, prefix + "_playing"))
            self.assertIsNone(getattr(self.app, prefix + "_after_id"))

    def test_zero_and_single_frame_control_availability(self):
        for count in (1, 0):
            result = replace(self.result,
                             snapshot_time_min=self.result.snapshot_time_min[:count].copy(),
                             snapshots_c=self.result.snapshots_c[:count].copy(),
                             o2_snapshots_percent=self.result.o2_snapshots_percent[:count].copy())
            self.app.result = result
            self.app._initialize_3d_field(result)
            self.app._initialize_o2_3d_field(result)
            for prefix in ("field", "o2_field"):
                for suffix in ("_prev_btn", "_play_btn", "_pause_btn", "_next_btn"):
                    self.assertEqual(str(getattr(self.app, prefix + suffix).cget("state")), "disabled")
                state = "normal" if count else "disabled"
                self.assertEqual(str(getattr(self.app, prefix + "_stop_btn").cget("state")), state)
                self.assertEqual(str(getattr(self.app, prefix + "_slider").cget("state")), state)
                getattr(self.app, prefix + "_stop_btn").invoke()
                self.assertEqual(getattr(self.app, prefix + "_slider_var").get(), 0)

    def test_closing_cancels_both_playback_timers(self):
        self.app._play_3d_animation()
        self.app._play_o2_3d_animation()
        timers = (self.app.field_after_id, self.app.o2_field_after_id)
        self.assertTrue(all(timers))
        self.app._on_close()
        for timer in timers:
            with self.assertRaises(tk.TclError):
                self.root.tk.call("after", "info", timer)


if __name__ == "__main__":
    unittest.main()
