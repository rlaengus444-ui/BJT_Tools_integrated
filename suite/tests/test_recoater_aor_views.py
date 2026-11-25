"""Exercise calculated recoater plots and the rendered process sequence."""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import sys
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recoater_aor import AOR_EXAMPLE
from recoater_charts import (
    bind_chart_hover, draw_comparison_chart, draw_discharge_chart, prepare_comparison,
    waveform_samples,
)
from recoater_model import ROLLER_EXAMPLE, build_curve, make_model
from recoater_views import draw_height_chart, draw_section, empty, stream_bands


def _models(direction='rear_to_front', *, ratio=2., **aor_changes):
    inputs = replace(AOR_EXAMPLE, **aor_changes)
    parameters = replace(ROLLER_EXAMPLE, feed_ratio=ratio, aor=inputs)
    model = make_model(parameters, two_pass=True, feed_direction=direction)
    reference = make_model(replace(parameters, aor=None), continuous_lead=model.lead,
                           two_pass=True, feed_direction=direction)
    return model, reference


class QuantitativeChartTests(unittest.TestCase):
    def test_stationary_and_zero_reference_comparisons(self):
        model, reference = _models(run_rpm=0.)
        data = prepare_comparison(model, reference, 'flow')
        before = data.at_state(model.at(0.))
        self.assertEqual(before.actual, 0.)
        self.assertGreater(before.reference, 0.)
        self.assertEqual(before.percent, -100.)
        self.assertEqual(data.percent.rms, 100.)
        self.assertIsNone(data.at_state(model.at(model.turn_progress)))

        model, reference = _models(ratio=0.)
        data = prepare_comparison(model, reference, 'feed')
        self.assertEqual(data.difference.rms, 0.)
        self.assertIsNone(data.percent.minimum)
        self.assertIsNone(data.at_state(model.at(0.)).percent)

    def test_both_feed_directions_use_the_same_local_comparison_coordinates(self):
        for direction in ('rear_to_front', 'front_to_rear'):
            with self.subTest(direction=direction):
                model, reference = _models(direction)
                state = model.at(model.compact_start + 32.5)
                feed_state = model.at(83.75)
                self.assertEqual(prepare_comparison(model, reference, 'flow').at_state(feed_state).actual,
                                 feed_state.flux)
                for metric in ('spread', 'compact', 'formed'):
                    data = prepare_comparison(model, reference, metric)
                    self.assertEqual(data.span, model.parameters.bed_length)
                    self.assertIsNone(data.at_state(model.at(0.)))
                    point = data.at_state(state)
                    if metric == 'spread':
                        self.assertEqual(point.x, state.spread_distance)
                        self.assertAlmostEqual(point.actual, state.spreader.height)
                    elif metric == 'compact':
                        self.assertEqual(point.x, state.compact_distance)
                        self.assertAlmostEqual(point.actual, state.height)
                    else:
                        self.assertEqual(point.x, state.compact_distance)
                        self.assertAlmostEqual(point.actual, state.local.formed_height)
                    self.assertIsNotNone(data.difference.rms)

    def test_steady_aor_matches_ideal_continuous_feed_for_all_metrics(self):
        model, reference = _models(baseline_pct=100.)
        for metric in ('flow', 'feed', 'spread', 'compact', 'formed'):
            with self.subTest(metric=metric):
                data = prepare_comparison(model, reference, metric)
                self.assertEqual(data.difference.minimum, 0.)
                self.assertEqual(data.difference.maximum, 0.)
                self.assertEqual(data.difference.rms, 0.)
                self.assertEqual(data.percent.rms, 0.)

    def test_narrow_pulses_survive_the_display_and_stream_uses_release_times(self):
        model, _ = _models(measured_period=1., run_rpm=AOR_EXAMPLE.test_rpm, release_tau=.004)
        run = model.aor.run
        samples, dense = waveform_samples(run, run.flow_at, run.period, 0., run.period * 2, 16, run.mean_mass)
        self.assertFalse(dense)
        self.assertLessEqual(len(samples), 16 * 3 + 1)
        self.assertAlmostEqual(max(rate for _, rate in samples), run.high)
        self.assertAlmostEqual(min(rate for _, rate in samples), run.low)
        _, dense = waveform_samples(run, run.flow_at, run.period, 0., run.period * 100, 16, run.mean_mass)
        self.assertTrue(dense)

        feed_time = .15
        bands = stream_bands(run, feed_time)
        self.assertGreater(max(rate for _, rate in bands), 30 * min(rate for _, rate in bands))
        for depth, rate in bands:
            self.assertLessEqual(depth, 1.)
            self.assertAlmostEqual(rate, run.flow_at(max(0., feed_time - depth * .18)), places=9)
        steady, _ = _models(baseline_pct=100.)
        steady_run = steady.aor.run
        steady_bands = stream_bands(steady_run, .3)
        self.assertGreater(len(steady_bands), 10)
        self.assertTrue(all(rate == steady_run.mean_mass for _, rate in steady_bands))
        later_bands = stream_bands(steady_run, .34545)
        self.assertNotEqual(steady_bands, later_bands)
        # The packet emitted at .264 s is deeper in the later frame.
        for time, packets in ((.3, steady_bands), (.34545, later_bands)):
            self.assertTrue(any(math.isclose(time - depth * .18, .264) for depth, _ in packets))
        stopped, _ = _models(run_outlet=0.)
        self.assertEqual(stream_bands(stopped.aor.run, feed_time), ())


class RecoaterCanvasTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest('A Tk display is required')
        self.addCleanup(self.root.destroy)
        self.canvas = tk.Canvas(self.root, width=620, height=230, highlightthickness=0)
        self.canvas.pack()
        self.root.update()

    def _items(self, tag, kind):
        return tuple(item for item in self.canvas.find_withtag(tag) if self.canvas.type(item) == kind)

    def _hover(self, x, y):
        self.canvas.event_generate('<Motion>', x=x, y=y)
        self.root.update()
        labels = self._items('recoater-hover', 'text')
        self.assertEqual(len(labels), 1)
        return self.canvas.itemcget(labels[0], 'text')

    def test_graphs_hover_model_values_and_leave_and_seek_bindings_coexist(self):
        bind_chart_hover(self.canvas)
        clicks = []
        self.canvas.bind('<Button-1>', lambda event: clicks.append(event.x))
        model, reference = _models('front_to_rear')
        state = model.at(90.)

        draw_height_chart(self.canvas, model, state, build_curve(model))
        axes = self.canvas._recoater_hover_plot.bounds
        x, y = int((axes.left + axes.right) / 2), 60
        progress = axes.x_at(x)
        height = model.at(progress).height
        label = self._hover(x, y)
        self.assertIn(f'전체 주행거리  {progress:.5g} mm', label)
        self.assertIn(f'커서 y  {axes.y_at(y):.5g} mm', label)
        self.assertIn(f'컴팩팅 더미 높이  {height:.5g} mm', label)
        lower_label = self._hover(x, y + 20)
        self.assertIn(f'커서 y  {axes.y_at(y + 20):.5g} mm', lower_label)
        self.assertNotEqual(axes.y_at(y), axes.y_at(y + 20))
        self.assertEqual([line for line in label.splitlines() if not line.startswith('커서 y')],
                         [line for line in lower_label.splitlines() if not line.startswith('커서 y')])
        self.assertIn([axes.left, y + 20, axes.right, y + 20],
                      [self.canvas.coords(item) for item in self._items('recoater-hover', 'line')])
        self.canvas.event_generate('<Button-1>', x=x, y=y)
        self.assertEqual(clicks, [x])
        self.canvas.event_generate('<Motion>', x=2, y=y)
        self.assertFalse(self.canvas.find_withtag('recoater-hover'))

        for view in ('time', 'distance'):
            draw_discharge_chart(self.canvas, model, state, view)
            axes = self.canvas._recoater_hover_plot.bounds
            pos = axes.x_at(x)
            label = self._hover(x, y)
            if view == 'time':
                self.assertIn(f'피딩 시간  {pos:.5g} s', label)
                self.assertIn(f'커서 y  {axes.y_at(y):.5g} g/s', label)
                self.assertIn(f'토출 질량유량  {model.aor.run.flow_at(pos):.5g} g/s', label)
            else:
                self.assertIn(f'토출점 이동거리  {pos:.5g} mm', label)
                self.assertIn(f'커서 y  {axes.y_at(y):.5g} mm', label)
                self.assertIn(f'피딩 직후 두께  {model.aor.run.height_at(pos):.5g} mm', label)
                self.assertIn(f'베드 좌표 (후면=0)  {model.feed_world(pos):.5g} mm', label)

        data = prepare_comparison(model, reference, 'flow')
        for view, key, series, unit in (('overlay', 'actual', 'AOR', 'g/s'),
                                        ('difference', 'difference', '편차', 'g/s'),
                                        ('percent', 'percent', '편차율', '%')):
            draw_comparison_chart(self.canvas, data, data.at_state(state), view)
            axes = self.canvas._recoater_hover_plot.bounds
            pos = axes.x_at(x)
            current = data.value_at(pos)
            label = self._hover(x, y)
            self.assertIn(f'피딩 시간  {pos:.5g} s', label)
            self.assertIn(f'커서 y  {axes.y_at(y):.5g} {unit}', label)
            self.assertIn(f'{series}  {getattr(current, key):.5g} {unit}', label)
            if view == 'overlay':
                self.assertIn(f'연속피딩  {current.reference:.5g} g/s', label)
            self.canvas.event_generate('<Leave>')
            self.assertFalse(self.canvas.find_withtag('recoater-hover'))
        self._hover(x, y)
        empty(self.canvas, '')
        self.assertFalse(self.canvas.find_withtag('recoater-hover'))

    def test_compact_tooltips_retain_coordinates_and_all_series_inside_canvas(self):
        bind_chart_hover(self.canvas)
        model, reference = _models('front_to_rear')
        state, curve = model.at(150.), build_curve(model)
        comparison = prepare_comparison(model, reference, 'flow')
        charts = (
            ('height', lambda: draw_height_chart(self.canvas, model, state, curve)),
            ('time', lambda: draw_discharge_chart(self.canvas, model, state, 'time')),
            ('distance', lambda: draw_discharge_chart(self.canvas, model, state, 'distance')),
            *((view, lambda view=view: draw_comparison_chart(self.canvas, comparison, comparison.at_state(state), view))
              for view in ('overlay', 'percent', 'difference')),
        )
        for width, height in ((150, 75), (180, 85), (190, 90)):
            self.canvas.configure(width=width, height=height)
            self.root.update()
            for mode, draw in charts:
                with self.subTest(width=width, height=height, chart=mode):
                    draw()
                    plot = self.canvas._recoater_hover_plot
                    axes = plot.bounds
                    for x in (int((axes.left + axes.right) / 2), int(axes.right) - 1):
                        y = int(axes.top) + 1
                        text = self._hover(x, y)
                        pos = axes.x_at(x)
                        self.assertIn(f'{pos:.5g} {plot.x_unit}', text)
                        self.assertIn(f'y  {axes.y_at(y):.5g} {plot.y_unit}', text)
                        for _name, unit, value, color in plot.values_at(pos):
                            if color:
                                self.assertIn(f'{value:.5g} {unit}', text)
                        a, b, c, d = self.canvas.bbox('recoater-hover')
                        self.assertTrue(0 <= a < c <= width, (a, c))
                        self.assertTrue(0 <= b < d <= height, (b, d))

    def test_sections_keep_fixed_world_edges_and_show_stream_only_with_active_feed(self):
        for direction in ('rear_to_front', 'front_to_rear'):
            with self.subTest(direction=direction):
                model, _ = _models(direction, measured_period=1., run_rpm=AOR_EXAMPLE.test_rpm, release_tau=.004)
                length = model.parameters.bed_length
                run = model.aor.run
                peak = max(run.wave.cycle_knots, key=run.wave.value) * run.period
                draw_section(self.canvas, model, model.at(peak * run.speed), False)
                thick_stream = len(self.canvas.find_withtag('aor-stream-grain'))
                self.assertGreater(thick_stream, 0)
                draw_section(self.canvas, model, model.at(.70 * run.period * run.speed), False)
                trough_stream = len(self.canvas.find_withtag('aor-stream-grain'))
                # Compare the current band width, independently of retained earlier particles.
                peak_state = model.at(peak * run.speed)
                draw_section(self.canvas, model, peak_state, True)
                peak_band = self.canvas.coords(self._items('aor-stream-band', 'line')[0])
                draw_section(self.canvas, model, model.at(.70 * run.period * run.speed), True)
                trough_band = self.canvas.coords(self._items('aor-stream-band', 'line')[0])
                self.assertGreater(peak_band[2] - peak_band[0], trough_band[2] - trough_band[0])
                self.assertGreater(trough_stream, 0)

                draw_section(self.canvas, model, model.at(length / 3), False)
                rear = self.canvas.coords(self._items('recoater-bed-rear', 'line')[0])[0]
                front = self.canvas.coords(self._items('recoater-bed-front', 'line')[0])[0]
                raw = self.canvas.coords(self._items('aor-layer-raw', 'polygon')[0])[::2]
                if direction == 'rear_to_front':
                    self.assertAlmostEqual(min(raw), rear)
                    self.assertLess(max(raw), front)
                    self.assertFalse(self.canvas.find_withtag('recoater-compactor'))
                else:
                    self.assertLess(max(raw), front)  # Leading roller leaves a passed region at the front.
                    self.assertGreater(min(raw), rear)
                    self.assertTrue(self.canvas.find_withtag('recoater-compactor'))

                draw_section(self.canvas, model, model.at(length), False)
                self.assertFalse(self.canvas.find_withtag('aor-stream'))
                self.assertEqual(rear, self.canvas.coords(self._items('recoater-bed-rear', 'line')[0])[0])
                self.assertEqual(front, self.canvas.coords(self._items('recoater-bed-front', 'line')[0])[0])
                if direction == 'rear_to_front':
                    self.assertFalse(self.canvas.find_withtag('aor-feeder'))
                draw_section(self.canvas, model, model.end, True)
                self.assertTrue(self.canvas.find_withtag('aor-layer-formed'))
                self.assertFalse(self.canvas.find_withtag('aor-layer-raw'))
                self.assertTrue(all(math.isfinite(x) for item in self.canvas.find_all()
                                    for x in self.canvas.coords(item)))

        stopped, _ = _models(run_rpm=0.)
        draw_section(self.canvas, stopped, stopped.at(10.), True)
        self.assertFalse(self.canvas.find_withtag('aor-stream'))
        self.assertFalse(self.canvas.find_withtag('aor-layer-raw'))
        self.assertTrue(self.canvas.find_withtag('aor-feeder'))


if __name__ == '__main__':
    unittest.main()
