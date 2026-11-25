"""AOR waveform, tray, and physical pass checks for the offline recoater."""
from dataclasses import replace
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recoater_aor import AOR_EXAMPLE, make_aor_feed
from recoater_model import ROLLER_EXAMPLE, build_curve, make_model


REFERENCE_AOR = replace(AOR_EXAMPLE, outlet_lead=24)
CALIBRATED = replace(REFERENCE_AOR, measured_period=0.15, entry_phase=104, period_variation=27,
                     run_rpm=22, run_outlet=1.1, feed_width=32)
LOW_RPM = replace(REFERENCE_AOR, run_rpm=0.25, entry_phase=31, period_variation=45)
AOR_ROLLERS = replace(ROLLER_EXAMPLE, compact_diameter=10, spread_diameter=6, spacing=9,
                      angle=45, aor=REFERENCE_AOR)


class RecoaterAorTests(unittest.TestCase):
    def assert_close(self, actual, expected, *, rel_tol=2e-12, abs_tol=2e-12):
        self.assertTrue(math.isclose(actual, expected, rel_tol=rel_tol, abs_tol=abs_tol),
                        f'{actual!r} != {expected!r}')

    def test_release_and_tray_match_supplied_javascript(self):
        # Node evaluation of makeAorFeed from the uploaded HTML, using the app defaults.
        model = make_model(replace(ROLLER_EXAMPLE, aor=AOR_EXAMPLE), two_pass=True)
        feed = model.aor
        self.assertEqual(model.parameters.angle, 45)
        self.assertEqual((model.parameters.compact_diameter, model.parameters.spread_diameter,
                          model.parameters.spacing, model.lead), (20, 20, 46, 61))
        self.assertEqual((model.turn_progress, model.spread_start, model.compact_start,
                          model.travel_end), (250, 265, 311, 561))
        self.assertTrue(math.isinf(model.contact_x))
        self.assert_close(model.capacity, 528.1893258798241)
        self.assert_close(model.end.height, 4.022494900464623)
        self.assert_close(model.end.spreader.height, 2.030709444171255)
        self.assertEqual(model.end.diverted_area, 0)
        self.assert_close(feed.test.period, 0.05893985122963921)
        self.assert_close(feed.run.period, 0.029469925614819606)
        self.assert_close(feed.run.pitch, 6.483383635260314)
        self.assert_close(feed.run.mean_mass, 8.0256)
        self.assert_close(feed.run.ideal_volume, 12063.715789784805)
        self.assert_close(feed.run.gain, 0.43767609350271225)
        self.assert_close(feed.run.low, 8.021700199547764)
        self.assert_close(feed.run.high, 8.029245167645746)
        self.assert_close(feed.run.contrast, 0.0004700637828724217)
        self.assert_close(feed.event_volume, 355.5168069632829)
        self.assert_close(feed.dwell, 0.4090909090909091)
        self.assert_close(feed.expected_mass, 1.933964437549877)
        self.assert_close(feed.covered_mean_mass, feed.expected_mass)
        self.assert_close(feed.pass_mass, 1.9339800889043757)
        self.assert_close(feed.run.wave.value(0.123), 0.9995473772568757)
        self.assert_close(feed.run.wave.integral(-0.3), -0.3000927601086858)

    def test_measured_clock_motif_and_partial_tray_match_supplied_javascript(self):
        feed = make_aor_feed(CALIBRATED, 0.1, 1.52, 2)
        self.assert_close(feed.test.period, CALIBRATED.measured_period)
        self.assert_close(feed.run.period, 0.371900826446281)
        self.assert_close(feed.run.pitch, 81.81818181818181)
        self.assert_close(feed.run.gain, 2.1702946785258455)
        self.assert_close(feed.covered_area, 2818.1124195391876)
        self.assert_close(feed.covered_mean_mass, 0.8567061755399131)
        self.assert_close(feed.pass_mass, 0.8513934953352623)
        wave = feed.run.wave
        self.assertEqual(wave.block, 4)
        self.assertEqual(wave.cycle_knots[0], 0)
        self.assertEqual(wave.cycle_knots[-1], 4)
        self.assert_close(wave.min, 0.22960497952669273)
        self.assert_close(wave.max, 1.7313441266008753)
        self.assert_close(wave.value(0.123), 1.5586076323623979)
        self.assert_close(wave.integral(-0.3), -0.24371737716616979)
        for cycles in (-4.03, -0.3, 0.123, 3.31):
            self.assert_close(wave.value(cycles), wave.value(cycles + wave.block))
            self.assert_close(wave.integral(cycles + wave.block) - wave.integral(cycles), wave.block)

    def test_inline_progress_reproduces_supplied_axis_and_delays(self):
        model = make_model(replace(AOR_ROLLERS, aor=CALIBRATED), two_pass=True,
                           feed_direction='front_to_rear')
        legacy = make_model(replace(AOR_ROLLERS, aor=CALIBRATED))
        self.assertIsNone(model.turn_progress)
        self.assertEqual((model.start_x, model.spread_start, model.compact_start,
                          model.feed_end, model.travel_end), (0, 15, 24, 250, 274))
        self.assert_close(model.contact_distance, 141.11352195906846)
        self.assert_close(model.contact_x, 165.11352195906846)
        self.assert_close(model.capacity, 8.550299724955405)
        self.assertEqual(len(model.history.positions), 1223)
        self.assert_close(model.end.spreader.height, 4.289047915035474)
        self.assert_close(model.end.diverted_area, 7.063293904497442)
        at = model.at(145)
        self.assert_close(at.height, 2.970367450276419)
        self.assert_close(at.spreader.height, 2.6692724921088393)
        self.assert_close(at.local.raw, 24.84534626447064)
        self.assert_close(at.local.formed, 13.930921052631557)
        self.assert_close(at.flux, 2.076723083836546)
        self.assert_close(model.end.elapsed, 274 / 220)
        for x in (0, 15, 24, 25, 145, model.contact_x, 250, 274):
            with self.subTest(progress=x):
                state = model.at(x)
                js_axis = legacy.at(x - model.lead)
                self.assertEqual(state.phase, 'combined')
                self.assertEqual(state.compact_started, x >= 24)
                self.assertEqual(state.feeding, x < 250)
                self.assertEqual(state.spreader is not None, x >= 15)
                self.assert_close(state.compact_world_x, 274 - x)
                self.assert_close(state.spread_world_x, 265 - x)
                self.assert_close(state.outlet_world_x, 250 - x)
                self.assert_close(state.height, js_axis.height)
                self.assert_close(state.fed_distance, js_axis.fed_distance)
                self.assert_close(state.flux, js_axis.flux)

    def test_separated_progress_reverses_frozen_feed_field(self):
        model = make_model(replace(AOR_ROLLERS, aor=CALIBRATED), two_pass=True)
        self.assertEqual((model.start_x, model.turn_progress, model.spread_start,
                          model.compact_start, model.travel_end), (0, 250, 265, 274, 524))
        outbound = model.at(137)
        self.assertEqual(outbound.phase, 'feed')
        self.assertEqual(outbound.outlet_world_x, 137)
        self.assertEqual(outbound.spread_world_x, 152)
        self.assertEqual(outbound.compact_world_x, 161)
        self.assertTrue(outbound.feeding)
        self.assertFalse(outbound.compact_started)
        self.assertIsNone(outbound.spreader)
        self.assert_close(outbound.flux, model.aor.run.flow_at(137 / 220))

        turn = model.at(model.turn_progress)
        self.assertEqual(turn.phase, 'compact')
        self.assertFalse(turn.feeding)
        self.assertEqual(turn.flux, 0)
        self.assertIsNone(turn.spreader)
        self.assertFalse(turn.compact_started)
        self.assertEqual((turn.outlet_world_x, turn.spread_world_x, turn.compact_world_x), (250, 265, 274))
        self.assertEqual(model.at(model.spread_start).spread_world_x, 250)
        self.assertEqual(model.at(model.compact_start).compact_world_x, 250)
        self.assertEqual(model.at(model.compact_start).spread_distance, 9)
        end = model.end
        self.assertEqual((end.fed_distance, end.spread_distance, end.compact_distance), (250, 250, 250))
        self.assertEqual((end.outlet_world_x, end.spread_world_x, end.compact_world_x), (-24, -9, 0))
        self.assert_close(end.feed_time, 250 / 220)
        self.assert_close(end.elapsed, 524 / 220)
        self.assertEqual(model.at(-10), model.at(0))
        self.assertEqual(model.at(600), end)

        # Reversed Node oracle keeps the original integrator and maps its incoming
        # area to A(L) - A(L - d), with the release-refinement knots reflected too.
        self.assert_close(model.contact_distance, 131.8098943400845)
        self.assert_close(model.contact_x, 405.8098943400845)
        self.assert_close(model.at(395).local.raw, 27.312698146211467)
        self.assert_close(model.at(395).spreader.height, 3.311630775404436)
        self.assert_close(model.end.local.raw, 54.63267550204429)
        self.assert_close(model.end.diverted_area, 7.666805538202615)
        self.assert_close(model.end.spreader.height, 4.1386913542502475)
        self.assert_close(model.max_spread.height, 4.289047915035474)
        d = 37
        derivative = (model.history.at(d + 1e-4).raw - model.history.at(d - 1e-4).raw) / 2e-4
        self.assert_close(derivative, model.layer_height_at(model.return_world(d), 'raw'), rel_tol=2e-8)

    def test_balance_with_both_schedules_and_with_or_without_spreader(self):
        for direction in ('rear_to_front', 'front_to_rear'):
            for spread_on in (True, False):
                p = replace(AOR_ROLLERS, aor=LOW_RPM, spread_on=spread_on)
                model = make_model(p, two_pass=True, feed_direction=direction)
                for x in (0, 14, 240, 250, model.compact_start + 20, model.travel_end - 0.5, model.travel_end):
                    with self.subTest(direction=direction, spread_on=spread_on, progress=x):
                        s = model.at(x)
                        delivered = model.aor.run.area_at(s.fed_distance)
                        leading = model.history.at(s.spread_distance if spread_on else s.compact_distance)
                        untouched = delivered - leading.raw
                        passing = leading.passed - s.local.passed
                        stored = leading.spread_pile + s.pile_area
                        self.assertGreaterEqual(untouched, -1e-12)
                        self.assertGreaterEqual(passing, -1e-12)
                        self.assert_close(untouched + passing + stored + s.local.formed + s.diverted_area,
                                          delivered)
                        if not spread_on:
                            self.assertIsNone(s.spread_world_x)
                            self.assertIsNone(s.spreader)
                            self.assertEqual(s.diverted_area, 0)

    def test_low_rpm_resolves_peak_and_layer_shortage(self):
        p = replace(AOR_ROLLERS, aor=LOW_RPM)
        inline = make_model(p, two_pass=True, feed_direction='front_to_rear')
        reverse = make_model(p, two_pass=True)
        self.assertGreater(inline.aor.run.wave.max, 35)
        self.assertGreater(inline.aor.run.pitch, 1500)
        self.assertGreater(len(inline.history.positions), 1025)
        self.assert_close(inline.max_compact.height, 0.8747676337479793)
        self.assert_close(inline.at(24 + 37).local.formed_height, 0.03825763626216545)
        self.assert_close(reverse.max_compact.height, 0.8101615843735999)
        self.assert_close(reverse.at(274 + 1).local.formed_height, 0.005211866609349214)
        self.assert_close(reverse.end.local.formed_height, p.layer)
        self.assert_close(inline.end.local.raw, reverse.end.local.raw)

    def test_flat_matches_continuous_and_stopped_or_closed_stays_empty(self):
        p = replace(AOR_ROLLERS, aor=replace(REFERENCE_AOR, baseline_pct=100, feed_width=32))
        model = make_model(p, two_pass=True)
        reference = make_model(replace(p, aor=None), continuous_lead=model.lead, two_pass=True)
        self.assertEqual(model.aor.pass_mass, model.aor.covered_mean_mass)
        self.assertEqual(model.contact_x, reference.contact_x)
        self.assertEqual(model.aor.run.wave.min, 1)
        self.assertEqual(model.aor.run.wave.max, 1)
        for x in (0, 250, 265, 274, 295, model.contact_x, model.travel_end):
            actual, expected = model.at(x), reference.at(x)
            self.assertEqual(actual.height, expected.height)
            self.assertEqual(actual.spreader, expected.spreader)
            self.assertEqual(actual.diverted_area, expected.diverted_area)
            self.assert_close(actual.local.formed_height, p.layer)

        for settings, ratio, stopped in ((replace(p.aor, run_rpm=0), 2, True),
                                         (replace(p.aor, run_outlet=0), 2, True), (p.aor, 0, False)):
            empty = make_model(replace(p, aor=settings, feed_ratio=ratio), two_pass=True)
            self.assertEqual(empty.aor.run.stopped, stopped)
            self.assertEqual(math.isinf(empty.aor.run.period), stopped)
            self.assertEqual(empty.aor.run.gain, None if stopped else 0)
            self.assertEqual(empty.aor.run.height_at(100), 0)
            self.assertEqual(empty.aor.run.area_at(100), 0)
            self.assertEqual(empty.aor.run.flow_at(0.2), 0)
            self.assertFalse(empty.at(12).feeding)
            self.assertEqual(empty.end.local.formed_height, 0)
            self.assertEqual(empty.end.pile_area, 0)

    def test_curve_bounds_and_events_for_short_and_normal_beds(self):
        for length in (5, 250):
            for direction in ('rear_to_front', 'front_to_rear'):
                model = make_model(replace(AOR_ROLLERS, bed_length=length), two_pass=True,
                                   feed_direction=direction)
                curve = build_curve(model)
                by_progress = {x: (compact, spread) for x, compact, spread in curve}
                self.assertEqual(curve[0][0], 0)
                self.assertEqual(curve[-1][0], model.travel_end)
                for event in (model.compact_start, model.spread_start, model.feed_end,
                              model.spread_start + length):
                    self.assertIn(event, by_progress)
                if model.turn_progress is not None:
                    self.assertIn(model.turn_progress, by_progress)
                self.assertEqual(by_progress[model.spread_start][0], None)
                self.assertEqual(by_progress[model.compact_start][0], model.base)
                self.assertEqual(by_progress[model.travel_end][1], model.end.spreader.height)

    def test_input_validation_and_finite_derived_phase(self):
        settings = (('tray_diameter', 0, '트레이 지름'), ('test_speed', -2, '시험 이동속도'),
                    ('run_rpm', -0.1, '운전 RPM'), ('start_angle', 34, '사면 시작각'),
                    ('stop_angle', -1, '사면 시작각'), ('baseline_pct', math.nan, '상시 흐름'),
                    ('entry_phase', 361, '베드 진입 위상'), ('period_variation', 91, '주기 편차'),
                    ('outlet_lead', 8, '선행 거리'), ('active_length', 1e308, '단위와 크기'),
                    ('release_tau', 1e-310, '아발란체 주기'))
        for field, value, label in settings:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, label):
                make_model(replace(AOR_ROLLERS, aor=replace(AOR_EXAMPLE, **{field: value})))
        with self.assertRaisesRegex(ValueError, '시험 RPM과 토출 두께'):
            make_aor_feed(replace(CALIBRATED, test_outlet=0), 0.1, 1.52, 2)
        with self.assertRaisesRegex(ValueError, '피딩 방향'):
            make_model(AOR_ROLLERS, two_pass=True, feed_direction='north')


if __name__ == '__main__':
    unittest.main()
