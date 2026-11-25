"""Geometry, powder balance, and JavaScript reference checks for the recoater."""
from dataclasses import replace
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recoater_model import ROLLER_EXAMPLE, SHEET_EXAMPLE, build_curve, make_model


REFERENCE_SHEETS = replace(SHEET_EXAMPLE, angle=35)
REFERENCE_ROLLERS = replace(ROLLER_EXAMPLE, angle=35, compact_diameter=10, spread_diameter=6, spacing=9)


class RecoaterModelTests(unittest.TestCase):
    def assert_close(self, actual, expected):
        self.assertTrue(math.isclose(actual, expected, rel_tol=2e-12, abs_tol=1e-13),
                        f'{actual!r} != {expected!r}')

    def test_sheet_piles_follow_analytic_triangle_and_contact(self):
        p = REFERENCE_SHEETS
        model = make_model(p)
        slope = math.tan(math.radians(p.angle))
        feed = p.layer * p.feed_ratio
        balance = p.clearance - p.layer * p.bed_density / p.loose_density
        thickness_removed = feed - p.clearance
        self.assert_close(model.feed, feed)
        self.assert_close(model.base, p.clearance)
        self.assert_close(model.excess, balance)
        precontact = model.at(25)
        compact_rise = math.sqrt(2 * slope * 25 * balance)
        spread_rise = math.sqrt(2 * slope * (25 + p.spacing) * thickness_removed)
        self.assert_close(precontact.height, p.clearance + compact_rise)
        self.assert_close(precontact.toe_x, compact_rise / slope)
        self.assert_close(precontact.spreader.height, feed + spread_rise)
        self.assert_close(precontact.spreader.toe_x, spread_rise / slope)
        self.assertEqual(precontact.peak_x, 0)
        self.assertEqual(precontact.spreader.peak_x, 0)
        self.assert_close(model.capacity, p.spacing * p.spacing * slope / 2)
        self.assert_close(model.contact_x, model.capacity / balance)
        self.assert_close(model.at(model.contact_x).toe_x, p.spacing)

    def test_roller_preset_matches_original_javascript_outputs(self):
        # Values evaluated with the uploaded simulator's makeModel in Node.
        model = make_model(REFERENCE_ROLLERS)
        self.assert_close(model.contact_x, 118.66044510913807)
        self.assert_close(model.contact_height, 2.734935543259514)
        self.assert_close(model.capacity, 7.697315715632244)
        self.assert_close(model.tangent_x, 7.279270690946862)
        self.assert_close(model.tangent_z, 0.7225438671330247)
        self.assert_close(model.compact_wall(0.1 + 5), 5)
        self.assert_close(model.compact_wall(0.1 + 10), 5)
        self.assertEqual(model.spread_wall(0.18), 0)
        self.assert_close(model.spread_wall(0.18 + 3), 3)
        for distance, compact_height, spread_height in (
            (0, 0.18, 0.49754554850091004),
            (25, 1.1963318559839018, 0.8559353923368971),
            (model.contact_x, 2.734935543259514, 1.6471479559763051),
            (250, 2.734935543259514, 2.3789184634079157),
        ):
            with self.subTest(distance=distance):
                state = model.at(distance)
                self.assert_close(state.height, compact_height)
                self.assert_close(state.spreader.height, spread_height)
        contact = model.at(model.contact_x)
        self.assert_close(contact.height - model.slope * (model.tangent_x - contact.peak_x),
                          model.tangent_z)
        self.assert_close(model.end.diverted_area, 8.519789547525647)

    def test_contact_is_inclusive_and_spreader_holds_its_profile_after_exit(self):
        model = make_model(REFERENCE_ROLLERS)
        self.assertFalse(model.at(math.nextafter(model.contact_x, 0)).contacted)
        contact = model.at(model.contact_x)
        self.assertTrue(contact.contacted)
        self.assert_close(contact.diverted_area, 0)
        self.assertGreater(model.end.diverted_area, 0)
        self.assertEqual(model.end.height, contact.height)
        self.assertEqual(model.end.pile_area, contact.pile_area)
        self.assertEqual(model.at(0).spread_distance, 9)
        self.assertGreater(model.at(0).spreader.pile_area, 0)
        self.assertEqual(model.at(240).spread_distance, 249)
        self.assertEqual(model.at(241).spread_distance, 250)
        self.assertEqual(model.at(241).spreader, model.end.spreader)
        self.assertEqual(model.at(-10), model.at(0))
        self.assertEqual(model.at(300), model.end)
        self.assertIs(model.end, model.end)

    def test_contact_at_zero_at_endpoint_and_beyond_short_bed(self):
        immediate = make_model(replace(REFERENCE_SHEETS, spacing=0))
        self.assertEqual(immediate.contact_x, 0)
        self.assertTrue(immediate.at(0).contacted)
        self.assertEqual(immediate.end.pile_area, 0)
        self.assert_close(immediate.end.diverted_area, immediate.excess * REFERENCE_SHEETS.bed_length)

        contact_x = make_model(REFERENCE_ROLLERS).contact_x
        at_end = make_model(replace(REFERENCE_ROLLERS, bed_length=contact_x))
        self.assertEqual(at_end.contact_x, at_end.end.x)
        self.assertTrue(at_end.end.contacted)
        self.assertEqual(build_curve(at_end)[-1][0], at_end.end.x)

        short = make_model(replace(REFERENCE_ROLLERS, bed_length=4))
        self.assertGreater(short.contact_x, short.end.x)
        self.assertFalse(short.end.contacted)
        self.assertEqual(short.at(0).spreader, short.end.spreader)
        self.assertEqual(short.at(0).spread_distance, short.end.x)

    def test_mass_balance_with_and_without_spreader_and_with_insufficient_feed(self):
        for p in (REFERENCE_ROLLERS, replace(REFERENCE_ROLLERS, feed_ratio=0.5),
                  replace(REFERENCE_ROLLERS, clearance=0), replace(REFERENCE_ROLLERS, spread_on=False)):
            model = make_model(p)
            if model.balance < 0:
                self.assertLess(model.deposit, p.layer)
                self.assertEqual(model.end.pile_area, 0)
                self.assertTrue(math.isinf(model.contact_x))
            for distance in (0, 1, 60, 118.66044510913807, 241, p.bed_length):
                with self.subTest(parameters=p, distance=distance):
                    state = model.at(distance)
                    effective_length = state.spread_distance if p.spread_on else state.x
                    spread_area = state.spreader.pile_area if state.spreader is not None else 0
                    untouched_area = (effective_length - state.x) * model.base
                    loose_area = untouched_area + spread_area + state.pile_area + state.diverted_area
                    # One mm of roller width converts the area to mm³, then 1e-3 to cm³.
                    fed_mass = 1e-3 * p.loose_density * model.feed * effective_length
                    accounted_mass = 1e-3 * (p.loose_density * loose_area +
                                              p.bed_density * model.deposit * state.x)
                    self.assert_close(accounted_mass, fed_mass)

    def test_curve_contains_uniform_grid_contact_and_spreader_exit(self):
        model = make_model(REFERENCE_ROLLERS)
        curve = build_curve(model)
        distances = {250 * i / 240 for i in range(241)} | {model.contact_x, 241}
        self.assertEqual(tuple(x for x, _, _ in curve), tuple(sorted(distances)))
        for x, compact_height, spread_height in curve:
            state = model.at(x)
            self.assertEqual(compact_height, state.height)
            self.assertEqual(spread_height, state.spreader.height)

        no_spread = make_model(replace(REFERENCE_ROLLERS, spread_on=False))
        no_spread_curve = build_curve(no_spread)
        self.assertEqual(len(no_spread_curve), 241)
        self.assertTrue(all(height is None for _, _, height in no_spread_curve))

    def test_spreader_disabled_ignores_spreader_settings(self):
        p = replace(REFERENCE_ROLLERS, spread_on=False, spread_diameter=math.nan,
                    spacing=math.inf, clearance=-1)
        model = make_model(p)
        self.assertEqual(model.feed, model.base)
        self.assertEqual(model.swept_thickness, 0)
        self.assertEqual(model.spread_wall(p.layer), 0)
        self.assertEqual(model.rs, 0)
        self.assertTrue(math.isinf(model.contact_x))
        self.assertFalse(model.end.contacted)
        self.assertIsNone(model.end.spreader)
        self.assertEqual(model.end.spread_distance, 0)
        self.assertEqual(model.end.diverted_area, 0)

    def test_input_validation_and_unsupported_derived_scale(self):
        for field, value, label in (
            ('layer', 0, '레이어 두께'), ('loose_density', math.nan, '겉보기 밀도'),
            ('bed_density', -1, '베드밀도'), ('feed_ratio', -1, '피딩배수'),
            ('bed_length', math.inf, '베드 길이'), ('compact_diameter', -1, '컴팩팅롤러 지름'),
            ('spread_diameter', -1, '스프래딩롤러 지름'), ('spacing', -1, '롤러 축 간 수평거리'),
            ('clearance', -1, '스프래딩 하단 간극'), ('angle', 0, '안식각'), ('angle', 90, '안식각'),
        ):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, label):
                make_model(replace(REFERENCE_ROLLERS, **{field: value}))
        for p, label in (
            (replace(REFERENCE_ROLLERS, spacing=1), '외경이 겹칩니다'),
            (replace(REFERENCE_ROLLERS, compact_diameter=0, spacing=0), '스프래딩 접점'),
            (replace(REFERENCE_ROLLERS, angle=math.ulp(0.0)), '단위와 크기'),
            (replace(REFERENCE_ROLLERS, layer=2, feed_ratio=1e308), '단위와 크기'),
            (replace(REFERENCE_ROLLERS, layer=1e-300, bed_density=1e-100), '단위와 크기'),
        ):
            with self.subTest(parameters=p), self.assertRaisesRegex(ValueError, label):
                make_model(p)
        model = make_model(REFERENCE_ROLLERS)
        for distance in (math.nan, math.inf, -math.inf):
            with self.subTest(distance=distance), self.assertRaisesRegex(ValueError, '컴팩팅 진행거리'):
                model.at(distance)

    def test_no_feed_and_large_finite_bed_keep_actual_curve_endpoints(self):
        p = replace(REFERENCE_SHEETS, spread_on=False, bed_length=1e308, feed_ratio=0)
        model = make_model(p)
        self.assertEqual(model.deposit, 0)
        self.assertEqual(model.end.height, 0)
        curve = build_curve(model)
        self.assertEqual(len(curve), 241)
        self.assertEqual(curve[0], (0, 0, None))
        self.assertEqual(curve[-1], (p.bed_length, 0, None))
        self.assertTrue(all(math.isfinite(x) for x, _, _ in curve))


if __name__ == '__main__':
    unittest.main()
