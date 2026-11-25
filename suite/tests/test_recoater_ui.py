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
        self._wait(lambda: self.tool.model is not None)

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
            self.assertFalse(self.errors)
            time.sleep(.01)
            self.root.update()
        self.assertTrue(predicate())

    def test_compact_tab_can_seek_and_play_real_drawings(self):
        tool = self.tool
        tool.plots.select(tool.plot_pages['height'])
        self.root.update()
        self._wait(lambda: tool.scene.winfo_height() >= 110 and tool.chart.winfo_height() >= 80)
        self.assertTrue(tool.scene.winfo_viewable())
        self.assertGreater(len(tool.scene.find_all()), 20)
        self.assertGreater(len(tool.chart.find_all()), 12)
        self.assertFalse(tool.is_playing)
        self.assertEqual(tool.fields['angle'].get(), '45')
        for name in ('test_speed', 'run_speed'):
            self.assertEqual(tool.aor_panel.fields[name].get(), '220')
        for name in ('test_outlet', 'run_outlet'):
            self.assertEqual(tool.aor_panel.fields[name].get(), '2')
        self.assertEqual([tool.fields[k].get() for k in ('spread_diameter', 'compact_diameter', 'spacing')], ['20','20','46'])
        self.assertEqual(tool.aor_panel.fields['outlet_lead'].get(), '61')
        self.assertTrue(tool.contact_button.instate(['disabled']))
        # A narrower test fixture makes contact within this bed.
        for key, value in (('compact_diameter', '10'), ('spread_diameter', '6'), ('spacing', '9')):
            tool.fields[key].set(value)
        self._wait(lambda: tool.model is not None and tool.model.contact_x <= tool.model.travel_end)
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
        self.assertAlmostEqual(start, tool.model.travel_end * (((left+right)//2-left)/(right-left)))
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
        self.assertAlmostEqual(tool.model.travel_end, 2*2.5 + tool.model.aor.lead)
        left, right = chart_x_bounds(tool.chart.winfo_width())
        tool.chart.event_generate('<Button-1>', x=right, y=40)
        self.root.update()
        self.assertAlmostEqual(tool.distance, tool.model.travel_end)

    def test_edits_pause_and_disable_invalid_conditions(self):
        tool = self.tool
        tool.play_button.invoke()
        tool.fields['angle'].set('90')
        self.assertFalse(tool.is_playing)
        self._wait(lambda: tool.model is None and tool._model_future is None and tool._edit is None)
        self.assertIn('안식각', tool.status.get())
        self.assertTrue(tool.play_button.instate(['disabled']))
        tool.fields['angle'].set('35')
        tool.spread_on.set(False)
        tool.fields['spacing'].set('')
        self._wait(lambda: tool.model is not None and not tool.model.parameters.spread_on)
        self.assertIsNone(tool.model.at(tool.model.travel_end).spreader)
        self.assertTrue(tool._entries['spacing'].instate(['disabled']))
        tool.spread_on.set(True)
        self._wait(lambda: tool.model is None and tool._model_future is None and tool._edit is None)
        self.assertIn('수평거리', tool.status.get())

    def test_restore_has_no_playback_or_position(self):
        tool = self.tool
        tool.fields['layer'].set('0.125')
        tool.speed.set('12.5')
        tool.view_buttons['full'].invoke()
        self._wait(lambda: tool.model is not None and tool.model.parameters.layer == .125)
        tool.seek(180)
        tool.play_button.invoke()
        state = capture_inputs('recoater', tool)
        self.assertEqual(state['inputs']['feed_mode'], 'aor')
        self.assertNotIn('distance', state['inputs'])
        self.assertNotIn('playing', state['inputs'])
        tool.fields['layer'].set('0.25')
        self.assertFalse(restore_inputs('recoater', tool, state))
        self.assertEqual(capture_inputs('recoater', tool), state)
        self._wait(lambda: tool.model is not None and math.isclose(tool.model.parameters.layer, .125))
        self.assertEqual(tool.distance, 0)
        self.assertFalse(tool.is_playing)
        self.assertIsNone(tool._anim)

    def test_sequence_selection_and_three_cursor_readouts(self):
        tool = self.tool
        self.assertEqual(tool.direction.get(), 'rear_to_front')
        separate = tool.model
        tool.seek(separate.turn_progress)
        self.assertEqual(tool.model.at(tool.distance).fed_distance, separate.parameters.bed_length)
        self.assertFalse(tool.model.at(tool.distance).compact_started)
        tool.direction.set('front_to_rear')
        self._wait(lambda: tool.model is not None and tool.model is not separate)
        model = tool.model
        self.assertEqual(tool.distance, 0)
        self.assertIsNone(model.turn_progress)
        tool.seek(model.compact_start + 20)
        at = model.at(tool.distance)
        self.assertEqual(at.phase, 'combined')
        self.assertTrue(at.feeding and at.compact_started)
        for key, unit in (('flow', 'g/s'), ('height', 'mm'), ('compare', '%')):
            tool.plots.select(tool.plot_pages[key])
            canvas = tool.canvases[key]
            self._wait(lambda: getattr(canvas, '_recoater_hover_plot', None) is not None)
            axes = canvas._recoater_hover_plot.bounds
            x, y = int((axes.left + axes.right) / 2), int((axes.top + axes.bottom) / 2)
            cursor_position = tool.distance
            canvas.event_generate('<Motion>', x=x, y=y)
            self.root.update()
            text = ' '.join(canvas.itemcget(i, 'text') for i in canvas.find_withtag('recoater-hover')
                            if canvas.type(i) == 'text')
            self.assertIn(unit, text)
            self.assertEqual(tool.distance, cursor_position)
            canvas.event_generate('<Leave>')
            self.root.update()
            self.assertFalse(canvas.find_withtag('recoater-hover'))
        tool.seek(model.feed_end)
        self.assertFalse(model.at(tool.distance).feeding)
        self.assertFalse(tool.scene.find_withtag('aor-stream'))
        tool.seek(model.travel_end)
        self.assertAlmostEqual(model.at(tool.distance).compact_world_x, 0.)

    def test_old_uniform_settings_and_independent_angles(self):
        tool = self.tool
        tool.restore_input_state({'parameters': {'angle': '38.5', 'layer': '.15'},
                                  'speed': '22', 'camera': 'full', 'spread_on': True})
        self._wait(lambda: tool.model is not None and tool.model.aor is None)
        self.assertEqual(tool.feed_mode.get(), 'uniform')
        self.assertEqual(tool.fields['angle'].get(), '38.5')
        self.assertEqual(tool.speed.get(), '22')
        tool.feed_mode.set('aor')
        self._wait(lambda: tool.model is not None and tool.model.aor is not None)
        self.assertEqual(tool.fields['angle'].get(), '45')
        tool.fields['angle'].set('46.5')
        tool.direction.set('front_to_rear')
        tool.aor_panel.fields['run_outlet'].set('1.75')
        tool.aor_panel.fields['run_rpm'].set('0')
        self._wait(lambda: tool.model is not None and tool.model.aor.run.stopped)
        tool.seek(50)
        self.assertFalse(tool.scene.find_withtag('aor-stream'))
        state = tool.input_state()
        tool.restore_input_state(state)
        self._wait(lambda: tool.model is not None)
        self.assertEqual(tool.input_state(), state)
        self.assertEqual(tool.distance, 0.)
        tool.feed_mode.set('uniform')
        self.assertEqual(tool.fields['angle'].get(), '38.5')


if __name__ == '__main__':
    unittest.main()
