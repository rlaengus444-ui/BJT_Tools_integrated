"""Language changes preserve canonical inputs and numerical results."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from oven_i18n import OvenTranslator, LocalizedDialogs, MODEL_INFO_EN


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self.language = "ko"
        self.tr = OvenTranslator(lambda: self.language)

    def test_explicit_static_and_template_translations(self):
        self.assertEqual(self.tr("Run simulation"), "시뮬레이션 실행")
        self.assertEqual(self.tr("k_eff mode: PSD estimate"), "유효 열전도율 계산 방식: 입도 기반 추정")
        self.assertEqual(self.tr("Solid fraction: 0.530   |   Porosity: 0.470"),
                         "고체 부피 비율: 0.530   |   기공률: 0.470")
        self.assertEqual(self.tr("3D powder temperature field — 0.125 h"),
                         "3차원 분말 온도 분포 — 0.125 h")

    def test_unknown_values_and_machine_identifiers_are_not_rewritten(self):
        for original in ("o2_center_volpct", "time_min,center_C", "SUS304", "3.14159", "nan",
                         r"C:\Oven air\Manual.csv", "/tmp/PSD estimate.csv"):
            self.assertEqual(self.tr(original), original)

    def test_path_and_numeric_values_survive_full_dialog_translation(self):
        path = r"C:\Users\한국어 사용자\Oven air\Manual.csv"
        self.assertEqual(self.tr("Saved:\n" + path), "저장했습니다.\n" + path)
        self.assertEqual(self.tr("Ambient O₂: 20.9500 vol%"), "외부 산소 농도: 20.9500 vol%")

    def test_multi_line_summaries_translate_known_lines_only(self):
        summary = "=== Simulation summary ===\nPeak center temperature: 123.45 °C\n\nunknown_key"
        self.assertEqual(self.tr(summary), "=== 시뮬레이션 결과 ===\n최고 중심 온도: 123.45 °C\n\nunknown_key")

    def test_switch_back_uses_original_english(self):
        korean = self.tr("Run simulation")
        self.language = "en"
        self.assertEqual(self.tr(self.tr.canonical(korean)), "Run simulation")

    def test_nested_display_note_and_canonical_choice(self):
        self.assertEqual(self.tr("1/2  t=0.001 h  Center=20.950 vol%  Range=20.950–20.950 vol%  |  27 cells"),
                         "1/2  경과=0.001 h  중심=20.950 vol%  범위=20.950–20.950 vol%  |  격자 27개")

    def test_model_explanation_is_natural_multiline_text(self):
        korean = self.tr(MODEL_INFO_EN)
        self.assertIn("입도 기반 추정\n", korean)
        self.assertIn("실제 충전 밀도", korean)
        self.assertNotIn("\\n", korean)
        self.language = "en"
        self.assertEqual(self.tr(MODEL_INFO_EN), MODEL_INFO_EN)

    def test_dialogs_translate_only_presentation(self):
        dialogs = LocalizedDialogs(self.tr)
        with patch("oven_i18n.messagebox.showinfo") as info:
            dialogs.showinfo("Export", "Run a simulation first.", parent="unchanged-parent")
        info.assert_called_once_with("CSV 저장", "시뮬레이션을 먼저 실행해 주세요.", parent="unchanged-parent")
        with patch("oven_i18n.filedialog.askopenfilename", return_value="/tmp/a.csv") as dialog:
            self.assertEqual(dialogs.askopenfilename(title="Load center thermocouple CSV",
                             filetypes=[("CSV files", "*.csv")]), "/tmp/a.csv")
        dialog.assert_called_once_with(title="중심 열전대 CSV 불러오기", filetypes=[("CSV 파일", "*.csv")])


def _thermal_module():
    path = Path(__file__).resolve().parents[1] / "sources" / "sic_buildbox_thermal_gui_v5.py"
    name = "_oven_i18n_test_thermal"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class OvenLanguageGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as exc:
            raise unittest.SkipTest("Tk display unavailable: " + str(exc))
        cls.module = _thermal_module()

    def setUp(self):
        self.patches = [patch.object(self.module.ThermalGUI, "_load_saved_settings", return_value=False),
                        patch.object(self.module.ThermalGUI, "_save_settings", return_value=True)]
        for item in self.patches:
            item.start()
        self.app = self.module.ThermalGUI()
        self.app.withdraw()

    def tearDown(self):
        self.app._on_close()
        for item in reversed(self.patches):
            item.stop()

    def test_language_changes_existing_widgets_without_touching_inputs(self):
        app = self.app
        self.assertEqual(app.language_var.get(), "ko")
        self.assertEqual(app.run_btn.cget("text"), "시뮬레이션 실행")
        app.lx.set("410.123")
        app.profile_tree.insert("", "end", values=(1200, 60))
        before = app._collect_inputs()
        profile = [app.profile_tree.item(i, "values") for i in app.profile_tree.get_children()]
        widget_ids = (id(app.profile_tree), id(app.run_btn), id(app.lx))
        for language, caption in (("en", "Run simulation"), ("ko", "시뮬레이션 실행"), ("en", "Run simulation")):
            app.language_var.set(language)
            app.update_idletasks()
            self.assertEqual(app.run_btn.cget("text"), caption)
            self.assertEqual(app._collect_inputs(), before)
            self.assertEqual([app.profile_tree.item(i, "values") for i in app.profile_tree.get_children()], profile)
            self.assertEqual((id(app.profile_tree), id(app.run_btn), id(app.lx)), widget_ids)

    def test_displayed_combo_labels_keep_canonical_values(self):
        app = self.app
        app.k_mode.set("Manual")
        app.box_mat.set("Custom")
        self.assertEqual(app.k_mode.get(), "Manual")
        self.assertEqual(ttk.Combobox.get(app.k_mode), "직접 입력")
        self.assertEqual(app.box_mat.get(), "Custom")
        self.assertEqual(ttk.Combobox.get(app.box_mat), "사용자 지정")
        app.language_var.set("en")
        self.assertEqual(app.k_mode.get(), "Manual")
        self.assertEqual(ttk.Combobox.get(app.k_mode), "Manual")
        app.language_var.set("ko")
        self.assertEqual(app.k_mode.get(), "Manual")
        self.assertEqual(ttk.Combobox.get(app.k_mode), "직접 입력")

    def test_language_selector_and_setting_use_stable_ids(self):
        app = self.app
        app.language_combo.set("en")
        app.language_combo.event_generate("<<ComboboxSelected>>")
        app.update_idletasks()
        self.assertEqual(app.language_var.get(), "en")
        self.assertEqual(app.run_btn.cget("text"), "Run simulation")
        self.assertIs(app._settings_variables()["language"], app.language_var)
        self.assertEqual(app._capture_settings()["variables"]["language"], "en")

    def test_result_language_changes_without_recomputing_or_replacing_results(self):
        app = self.app
        inputs = replace(app._collect_inputs(), nx=3, ny=3, nz=3,
                         oven_profile_min_c=[(0.0, 25.0), (0.02, 30.0)],
                         max_dt_s=0.1, record_interval_s=0.1, snapshot_interval_s=0.1)
        result = self.module.simulate(inputs, progress_cb=None, stop_event=None)
        app.result = result
        app._plot_result(result)
        app._plot_o2_result(result)
        app._show_summary(result)
        app._show_o2_summary(result)
        self.assertEqual(app.ax.get_xlabel(), "경과 시간 [h]")
        self.assertEqual(app.ax.lines[0].get_label(), "오븐 공기")
        self.assertIn("시뮬레이션 결과", app.summary.get("1.0", "end"))
        before_center = result.center_c.copy()
        app.language_var.set("en")
        self.assertIs(app.result, result)
        self.assertTrue(self.module.np.array_equal(before_center, result.center_c))
        self.assertEqual(app.ax.get_xlabel(), "Elapsed time [h]")
        self.assertEqual(app.ax.lines[0].get_label(), "Oven air")
        self.assertIn("Simulation summary", app.summary.get("1.0", "end"))
        app.language_var.set("ko")
        self.assertEqual(app.ax.lines[0].get_label(), "오븐 공기")
        self.assertIn("산소 확산 결과", app.o2_summary.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
