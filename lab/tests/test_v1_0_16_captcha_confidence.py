"""v1.0.16: captcha confidence gate + post-reload image-stability wait.

Root cause evidence (operator logs, %LOCALAPPDATA%/irica):
  * 36 wrong-captcha submissions (~29% of submits); 12/16 retry events came
    right after a dirty load (page timeout / empty captcha / F5).
  * forced_decode fabricates a confident-looking 5-char answer from partial
    CTC runs with no confidence gate anywhere in the pipeline.

Fixes under test:
  1. solver: decode returns (text, mean-peak-confidence); OnnxSolver and
     IricaSolver both expose read_array_conf/read_pil_conf.
  2. captcha_collector.wait_captcha_stable: double-fingerprint stability wait
     used after reload/F5 before OCR.
  3. estelam_gomrok.solve_confidently: refresh-and-reread (bounded) when
     confidence < IRICA_CAPTCHA_MIN_CONF (default 0.6).
"""
import ast
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

ONNX = os.path.join(ROOT, "solver", "onnx_solve.py")
PREDICT = os.path.join(ROOT, "solver", "predict.py")
CC = os.path.join(ROOT, "captcha_collector.py")
EST = os.path.join(ROOT, "estelam_gomrok.py")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestSolverConfidenceAPI(unittest.TestCase):
    def test_forced_decode_with_conf_exists(self):
        src = read(ONNX)
        self.assertIn("def _forced_decode_conf", src)

    def test_onnx_read_conf_methods(self):
        src = read(ONNX)
        self.assertIn("def read_array_conf", src)
        self.assertIn("def read_pil_conf", src)

    def test_torch_solver_has_conf_methods(self):
        src = read(PREDICT)
        self.assertIn("def read_array_conf", src)
        self.assertIn("def read_pil_conf", src)

    def test_conf_is_mean_peak_of_final_runs(self):
        # Import the numpy decoder directly and feed a synthetic log_probs
        # tensor: 3 blank steps, then 5 distinct strong runs -> conf ~1.0.
        sys.path.insert(0, os.path.join(ROOT, "solver"))
        import importlib
        mod = importlib.import_module("onnx_solve")
        import numpy as np
        from vocab import VOCAB
        C = len(VOCAB)
        T = 20
        lp = np.full((T, 1, C), -12.0, dtype=np.float32)
        # strong runs for chars a b c d e at steps 2,6,10,14,18 (peak ~1.0)
        for t, ch in ((2, "a"), (6, "b"), (10, "c"), (14, "d"), (18, "e")):
            lp[t, 0, VOCAB.stoi[ch]] = 0.0
        text, conf = mod._forced_decode_conf(lp)
        self.assertEqual(text, "abcde")
        self.assertGreater(conf, 0.99)
        # weak peaks -> lower conf
        lp2 = np.full((T, 1, C), -1.2, dtype=np.float32)
        for t, ch in ((2, "a"), (6, "b"), (10, "c"), (14, "d"), (18, "e")):
            lp2[t, 0, VOCAB.stoi[ch]] = -0.2
        _, conf2 = mod._forced_decode_conf(lp2)
        self.assertLess(conf2, conf)

    def test_fabricated_answer_flags_low_conf(self):
        # greedy gives 3 runs; forced path duplicates -> conf below the strong case
        sys.path.insert(0, os.path.join(ROOT, "solver"))
        import importlib
        mod = importlib.import_module("onnx_solve")
        import numpy as np
        from vocab import VOCAB
        C = len(VOCAB)
        T = 20
        # only 2 runs (a b) with strong peaks -> forced must duplicate to 5
        lp = np.full((T, 1, C), -12.0, dtype=np.float32)
        for t, ch in ((2, "a"), (12, "b")):
            lp[t, 0, VOCAB.stoi[ch]] = 0.0
        text, conf = mod._forced_decode_conf(lp)
        self.assertEqual(len(text), 5)
        self.assertLess(conf, 0.5)


class TestStabilityWait(unittest.TestCase):
    def test_wait_captcha_stable_exists(self):
        src = read(CC)
        self.assertIn("def wait_captcha_stable", src)

    def test_stable_true_when_fingerprint_unchanged(self):
        sys.path.insert(0, ROOT)
        import captcha_collector as cc
        driver = mock.Mock()
        el = mock.Mock()
        with mock.patch.object(cc, "_captcha_fingerprint",
                               side_effect=["fp1"] * 10):
            self.assertTrue(cc.wait_captcha_stable(driver, el, settle=0.0))

    def test_unstable_false_when_fingerprint_changes(self):
        sys.path.insert(0, ROOT)
        import captcha_collector as cc
        driver = mock.Mock()
        el = mock.Mock()
        # alternating fingerprints never settle -> bounded timeout returns False
        with mock.patch.object(cc, "_captcha_fingerprint",
                               side_effect=["fp1", "fp2"] * 100):
            self.assertFalse(cc.wait_captcha_stable(driver, el, settle=0.0))


class TestConfidenceGate(unittest.TestCase):
    def test_min_conf_default(self):
        sys.path.insert(0, ROOT)
        import gomrok as G
        self.assertAlmostEqual(G.MIN_CONFIDENCE, 0.6)

    def test_estelam_reuses_shared_solver_gate(self):
        sys.path.insert(0, ROOT)
        import estelam_gomrok as est
        import gomrok as G
        self.assertIs(est.solve_confidently, G.solve_confidently)

    def test_solve_confidently_submits_high_conf(self):
        sys.path.insert(0, ROOT)
        import gomrok as G
        solver = mock.Mock()
        solver.read_pil_conf.return_value = ("ab3de", 0.95)
        crop = mock.Mock()
        wait = mock.Mock()
        with mock.patch.object(G.cc, "crop_captcha_with_padding",
                               return_value=crop), \
             mock.patch.object(G.cc, "click_refresh", return_value=True) as cr:
            text, conf = G.solve_confidently(mock.Mock(), mock.Mock(), solver,
                                             wait)
            self.assertEqual(text, "ab3de")
            self.assertGreaterEqual(conf, G.MIN_CONFIDENCE)
            cr.assert_not_called()

    def test_solve_confidently_refreshes_low_conf(self):
        sys.path.insert(0, ROOT)
        import gomrok as G
        solver = mock.Mock()
        solver.read_pil_conf.side_effect = [("xxxxx", 0.2), ("ab3de", 0.9)]
        crop = mock.Mock()
        wait = mock.Mock()
        with mock.patch.object(G.cc, "crop_captcha_with_padding",
                               return_value=crop), \
             mock.patch.object(G.cc, "click_refresh", return_value=True) as cr:
            text, conf = G.solve_confidently(mock.Mock(), mock.Mock(), solver,
                                             wait)
            self.assertEqual(text, "ab3de")
            cr.assert_called_once()

    def test_solve_confidently_gives_up_after_bounds(self):
        sys.path.insert(0, ROOT)
        import gomrok as G
        solver = mock.Mock()
        solver.read_pil_conf.return_value = ("xxxxx", 0.1)
        crop = mock.Mock()
        with mock.patch.object(G.cc, "crop_captcha_with_padding",
                               return_value=crop), \
             mock.patch.object(G.cc, "click_refresh", return_value=True):
            text, conf = G.solve_confidently(mock.Mock(), mock.Mock(), solver,
                                             mock.Mock(), max_rereads=2)
            # returns the last (still low-conf) answer rather than crashing
            self.assertEqual(conf, 0.1)

    def test_retry_loop_accepts_read_conf(self):
        src = read(os.path.join(ROOT, "gomrok.py"))
        self.assertIn("read_conf", src)
        est = read(EST)
        self.assertIn("read_conf=solve_confidently", est)

    def test_registration_flow_uses_confident_solve(self):
        src = read(os.path.join(ROOT, "gomrok.py"))
        self.assertIn("answer, _conf = solve_confidently(driver, captcha, "
                      "solver, wait)", src)
        self.assertIn("read_conf=solve_confidently)", src)


class TestSyntax(unittest.TestCase):
    def test_all_edit_files_parse(self):
        for p in (ONNX, PREDICT, CC, EST,
                  os.path.join(ROOT, "gomrok.py")):
            ast.parse(read(p))


if __name__ == "__main__":
    unittest.main()
