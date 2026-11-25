"""Exercise recoater controls through the integrated Tk notebook."""
from __future__ import annotations

import math
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recoater_views import chart_x_bounds
from suite_state import capture_inputs, restore_inputs
from suite_ui import SuiteApp


class RecoaterUiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest('A Tk display is required')
        self.errors = []
        self.settings = tempfile.TemporaryDirectory()
        self.addCleanup(self.settings.cleanup)
        self.addCleanup(self._shutdown)
        self.app = SuiteApp(self.root, test_mode=True, settings_path=Path(self.settings.name) / 'settings.json')
        self.root.report_callback_exception = lambda *error: self.errors.append(str(error[1]))
        self.root.geometry('1120x780')
        self.app.select_tool('recoater')
        self._wait(lambda: 'recoater' in self.app.controllers)
        self.tool = self.app.controllers['recoater']

    def _shutdown(self):
        try:
            if self.root.winfo_exists():
                self.app.request_close(confirm=False)
        except tk.TclError:
            pass
        self.assertFalse(self.errors)

    def _wait(self, predicate):
        deadline = time.monotonic() + 5
        self.root.update()
        while not predicate() and time.monotonic() < deadline:
            self.assertFalse(self.app.failures)
            time.sleep(.01)
            self.root.update()
        self.assertTrue(predicate())

    def test_compact_tab_can_seek_and_play_real_drawings(self):
        tool = self.tool
        self._wait(lambda: tool.scene.winfo_height() >= 110 and tool.chart.winfo_height() >= 80)
        self.assertTrue(tool.scene.winfo_viewable())
        self.assertGreater(len(tool.scene.find_all()), 20)
        self.assertGreater(len(tool.chart.find_all()), 12)
        self.assertFalse(tool.is_playing)
        self.assertAlmostEqual(tool.model.contact_x, 118.66044510913807)
        tool.contact_button.invoke()
        self.assertAlmostEqual(tool.distance, tool.model.contact_x)
        tool.distance_entry.event_generate('<FocusOut>')
        self.root.update()
        self.assertEqual(tool.distance, tool.model.contact_x)
        self.assertTrue(tool.model.at(tool.distance).contacted)

        left, right = chart_x_bounds(tool.chart.winfo_width())
        tool.chart.event_generate('<Button-1>', x=(left+right)//2, y=35)
        self.root.update()
        start = tool.distance
        self.assertAlmostEqual(start, 250 * (((left+right)//2-left)/(right-left)))
        tool.play_button.invoke()
        self._wait(lambda: tool.distance > start)
        self.app.stop_current()
        self.assertFalse(tool.is_playing)
        self.assertIsNone(tool._anim)
        tool.play_button.invoke()
        self.app.notebook.select(self.app.home)
        self.root.update()
        self.assertFalse(tool.is_playing)
        self.assertIsNone(tool._anim)
        self.app.select_tool('recoater')
        tool.fields['bed_length'].set('2.5')
        self._wait(lambda: tool.model is not None and tool.model.parameters.bed_length == 2.5)
        labels = {tool.chart.itemcget(item, 'text') for item in tool.chart.find_all()
                  if tool.chart.type(item) == 'text'}
        self.assertIn('2.5', labels)
        self.assertIn('1.5', labels)

    def test_edits_pause_and_disable_invalid_conditions(self):
        tool = self.tool
        tool.play_button.invoke()
        tool.fields['angle'].set('90')
        self.assertFalse(tool.is_playing)
        self._wait(lambda: tool.model is None)
        self.assertIn('안식각', tool.status.get())
        self.assertTrue(tool.play_button.instate(['disabled']))
        tool.fields['angle'].set('35')
        tool.spread_on.set(False)
        tool.fields['spacing'].set('')
        self._wait(lambda: tool.model is not None)
        self.assertIsNone(tool.model.at(100).spreader)
        self.assertTrue(tool._entries['spacing'].instate(['disabled']))
        tool.spread_on.set(True)
        self._wait(lambda: tool.model is None)
        self.assertIn('수평거리', tool.status.get())

    def test_restore_has_no_playback_or_position(self):
        tool = self.tool
        tool.fields['layer'].set('0.125')
        tool.speed.set('12.5')
        tool.view_buttons['full'].invoke()
        self._wait(lambda: tool.model.parameters.layer == .125)
        tool.seek(180)
        tool.play_button.invoke()
        state = capture_inputs('recoater', tool)
        self.assertEqual(set(state['inputs']), {'parameters', 'spread_on', 'speed', 'camera'})
        tool.fields['layer'].set('0.25')
        self.assertFalse(restore_inputs('recoater', tool, state))
        self.assertEqual(capture_inputs('recoater', tool), state)
        self.assertTrue(math.isclose(tool.model.parameters.layer, .125))
        self.assertEqual(tool.distance, 0)
        self.assertFalse(tool.is_playing)
        self.assertIsNone(tool._anim)


if __name__ == '__main__':
    unittest.main()
