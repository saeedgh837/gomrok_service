"""test_estelam_hamta.py — test-haye vahed baraye estelam_hamta (bedun-e morurgar).

Ejra:
    python lab/tests/test_estelam_hamta.py
"""
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _ROOT)

# Keep the robot's logs out of the operator's live %LOCALAPPDATA%\irica when
# this module is run directly, the way its docstring says. Claimed only when
# nothing claimed it first: the suite runner points IRICA_DATA_DIR at its own
# scratch directory before importing any test module, and overwriting that
# value leaves these tests resolving one directory while panel's import-time
# constants hold another -- test_app_paths pins that invariant and fails. It
# has to happen before estelam_hamta is imported, because app_paths caches the
# resolved directory on its first call.
if not (os.environ.get("IRICA_DATA_DIR") or "").strip():
    os.environ["IRICA_DATA_DIR"] = tempfile.mkdtemp(
        prefix="irica_estelam_hamta_tests_")

import estelam_hamta as H  # noqa: E402


class TestClassify(unittest.TestCase):
    def test_good_real_live_text(self):
        # motn-e VAGHE'I ke dar ejra-ye live bargasht
        t = "شناسه دستگاه (IMEI) قانونی نیست و در شبکه تلفن همراه سرویس نخواهد گرفت"
        self.assertEqual(H.classify(t), "good")

    def test_captcha_wrong_real_live_text(self):
        # motn-e VAGHE'I az probe-ye captcha-ye ghalat
        self.assertEqual(H.classify("کد امنیتی را اشتباه وارد کرده اید"),
                         "captcha_wrong")

    def test_blocked_beats_good(self):
        # masdoodi bayad bar har marker-e digar olaviat dashte bashad
        t = "دستگاه مسدود است و سرویس نخواهد گرفت"
        self.assertEqual(H.classify(t), "blocked")

    def test_cancelled_real_live_text(self):
        """Motn-e VAGHE'I-ye masdoodi/laghv az karbar — bala-tarin olaviat."""
        t = ("ثبت این دستگاه به دلیل تخلف در فرآیندهای سامانه لغو شده است. "
             "از خرید این دستگاه خودداری نمایید.")
        self.assertEqual(H.classify(t), "blocked")

    def test_cancelled_beats_registered(self):
        # agar payam ham «قانونی ثبت شده» va ham «لغو شده» dasht, blocked barande ast
        t = ("جزء شناسه های قانونی ثبت شده است ... ثبت این دستگاه به دلیل تخلف "
             "در فرآیندهای سامانه لغو شده است. از خرید این دستگاه خودداری نمایید.")
        self.assertEqual(H.classify(t), "blocked")

    def test_blocked_variants(self):
        for t in ["شناسه مسدود شده است", "دستگاه غیرمجاز است",
                  "این دستگاه سرقتی اعلام شده", "دستگاه قاچاق است"]:
            self.assertEqual(H.classify(t), "blocked", t)

    def test_registered_real_live_text(self):
        """Natije-ye VAGHE'I-ye live (iPhone 17, ورود مسافری): dastgah SABT-shode ast.
        Matn shamel-e hoshdar-e sharti «... مسدود خواهد شد» ast ke NABAYAD blocked
        telaghi shavad."""
        t = ("شناسه دستگاه (IMEI) جزء شناسه های قانونی ثبت شده است.\n"
             "مشخصات دستگاه:\nبرند:\nApple\nمدل:\nApple iPhone 17\n"
             "وارد کننده:\nورود مسافری\n"
             "هشدار: این دستگاه جهت استفاده غیرتجاری وارد شده، فاقد هرگونه گارانتی "
             "رسمی بوده و در صورت احراز تخلف مسدود خواهد شد.\n"
             "لیست شناسه دستگاه (IMEI):\n359925155246015\n359925155271526 | تکمیل فرایند")
        self.assertEqual(H.classify(t), "registered")

    def test_conditional_block_warning_is_not_blocked(self):
        # «مسدود خواهد شد» sharti ast — nabayad blocked shavad
        self.assertNotEqual(
            H.classify("در صورت احراز تخلف مسدود خواهد شد"), "blocked")

    def test_registered_beats_conditional_warning(self):
        t = "جزء شناسه های قانونی ثبت شده است — در صورت تخلف مسدود خواهد شد"
        self.assertEqual(H.classify(t), "registered")

    def test_captcha_expired(self):
        self.assertEqual(H.classify("کد امنیتی منقضی شده است دوباره اقدام کنید."),
                         "captcha_expired")

    def test_transient(self):
        self.assertEqual(H.classify("برقراری ارتباط مقدور نمی باشد!"), "transient")

    def test_empty_is_error(self):
        self.assertEqual(H.classify(""), "error")
        self.assertEqual(H.classify(None), "error")

    def test_unknown_is_logged_category(self):
        self.assertEqual(H.classify("یک پیام کاملا تازه از سامانه"), "unknown")


class TestSelectorsScoped(unittest.TestCase):
    """Safhe-ye login KHODESH #CaptchaInputText va img.captcha-image darad; hame-ye
    selector-haye modal bayad scope-shode bashand (vagarna captcha-ye eshtebahi)."""

    def test_captcha_img_scoped(self):
        self.assertIn("#device-inquiry", H.SEL_M_CAPTCHA_IMG)

    def test_captcha_input_scoped_by_class(self):
        self.assertIn("deviceInquiryCaptcha", H.SEL_M_CAPTCHA_IN)

    def test_imei_scoped_to_modal(self):
        self.assertIn("swal2-modal", H.SEL_M_IMEI)

    def test_refresh_scoped(self):
        self.assertIn("#device-inquiry", H.SEL_M_REFRESH)


class TestTelegramMessage(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._orig = H._telegram
        H._telegram = lambda text, enabled=True: self.sent.append((text, enabled))

    def tearDown(self):
        H._telegram = self._orig

    def test_good_message_shape(self):
        H._telegram_result("350358333315545", "good", "شناسه دستگاه (IMEI) قانونی نیست",
                           enabled=True)
        text, enabled = self.sent[0]
        self.assertTrue(enabled)
        self.assertIn("استعلام اصالت", text)
        self.assertIn("350358333315545", text)
        self.assertIn("زمان استعلام", text)
        self.assertIn("پیام سامانه", text)

    def test_blocked_message_is_danger(self):
        H._telegram_result("123456789012345", "blocked", "دستگاه مسدود است")
        text, _ = self.sent[0]
        self.assertIn("⛔️", text)
        self.assertIn("مسدود", text)

    def test_no_telegram_flag_respected(self):
        H._telegram_result("1", "good", "x", enabled=False)
        # v1.9.1: nothing is sent at all — neither the photo nor the text.
        self.assertEqual(self.sent, [])


class TestConfidenceGate(unittest.TestCase):
    def test_gate_below_live_success_value(self):
        # dar live, conf=0.593 va 0.743 dorost budand -> gate nabayad an-ha ra rad konad
        self.assertLess(H.MIN_CONFIDENCE_TO_SUBMIT, 0.593)

    def test_gate_positive(self):
        self.assertGreater(H.MIN_CONFIDENCE_TO_SUBMIT, 0.0)


class TestLogFile(unittest.TestCase):
    """Log ra be file-e movaghat mibarad ta hamta_logs.jsonl-e VAGHE'I aloode nashavad."""

    def setUp(self):
        import tempfile
        import panel
        self._orig = H._LOG_PATH
        self._orig_panel_log = panel.ESTELAM_LOGS_PATH
        self._tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self._tmp.close()
        H._LOG_PATH = self._tmp.name
        # _elog() mirrors every record into panel.estelam_log, which writes
        # panel.ESTELAM_LOGS_PATH -- a path fixed at panel's import. Redirect
        # that too, or the mirror lands in a real log even though H._LOG_PATH
        # does not.
        panel.ESTELAM_LOGS_PATH = self._tmp.name

    def tearDown(self):
        import panel
        H._LOG_PATH = self._orig
        panel.ESTELAM_LOGS_PATH = self._orig_panel_log
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_elog_writes_jsonl(self):
        import json
        import time
        marker = f"unit-test-{time.time()}"
        H._elog("unknown", marker, serial="000000000000000")
        with open(H._LOG_PATH, encoding="utf-8") as f:
            lines = [json.loads(x) for x in f if x.strip()]
        self.assertTrue(any(r.get("message") == marker for r in lines))
        last = [r for r in lines if r.get("message") == marker][-1]
        self.assertEqual(last["serial"], "000000000000000")
        self.assertIn("ts", last)


class TestSolverBridge(unittest.TestCase):
    def test_solver_dir_present(self):
        d = os.path.join(_ROOT, "Hamta-solver")
        for f in ["live_solve.py", "solve.py", "model.py", "preprocess.py",
                  "vocab.py", "number_decode.py", "utils.py", "config.py"]:
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)
        # Model weights are generated/hydrated build artifacts and are
        # intentionally excluded from Git. If a developer checkout has the
        # torch checkpoint, still catch a zero-byte/corrupt placeholder here;
        # clean checkouts are valid without it. Release verification separately
        # requires the exported ONNX artifact and forbids shipping .pt files.
        checkpoint = os.path.join(d, "checkpoints", "best.pt")
        if os.path.exists(checkpoint):
            self.assertGreater(os.path.getsize(checkpoint), 0, checkpoint)

    def test_read_pil_on_synthetic_captcha_inference_fixture(self):
        # Exercise the real bridge, preprocessing and decoding without opening
        # recon/customer screenshots or installed weights. Model accuracy is a
        # separate artifact check; only inference is deterministic here.
        import shutil
        from types import ModuleType, SimpleNamespace
        from unittest.mock import Mock, patch
        import numpy as np
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory(prefix="hamta-inference-fixture-") as directory:
            for name in ("live_solve", "onnx_solve", "config", "preprocess", "vocab", "utils", "number_decode"):
                shutil.copyfile(os.path.join(_ROOT, "Hamta-solver", name + ".py"),
                                os.path.join(directory, name + ".py"))
            checkpoint = os.path.join(directory, "checkpoints")
            os.makedirs(checkpoint)
            model = os.path.join(checkpoint, "best.onnx")
            with open(model, "wb") as handle:
                handle.write(b"synthetic inference fixture, never parsed")
            image = Image.new("RGB", (160, 60), "white")
            ImageDraw.Draw(image).rectangle((20, 12, 140, 45), fill="black")
            feeds = []

            class InferenceFixture:
                def __init__(self, *_args, **_kwargs):
                    vocab = sys.modules["vocab"].VOCAB
                    expected = vocab.normalize(sys.modules["number_decode"].spell(1234))
                    self.log_probs = np.full((2 * len(expected) + 1, 1, len(vocab)), -12.0,
                                             dtype=np.float32)
                    self.log_probs[:, 0, vocab.blank_idx] = 0.0
                    for index, char in enumerate(expected):
                        self.log_probs[2 * index + 1, 0, vocab.blank_idx] = -12.0
                        self.log_probs[2 * index + 1, 0, vocab.stoi[char]] = 0.0

                def get_inputs(self):
                    return [SimpleNamespace(name="synthetic-input")]

                def get_outputs(self):
                    return [SimpleNamespace(name="synthetic-output")]

                def run(self, outputs, inputs):
                    feeds.append((outputs, inputs))
                    return [self.log_probs]

            # Stub only the model-inference dependency before its package-level
            # platform probes can spawn external utilities. Selection/bridge and
            # read_pil/preprocessing/decoder code remain real trusted sources.
            runtime = ModuleType("onnxruntime")
            inference = Mock(side_effect=InferenceFixture)
            runtime.InferenceSession = inference
            absent = object()
            previous = sys.modules.get("onnxruntime", absent)
            sys.modules["onnxruntime"] = runtime
            try:
                with patch.object(H, "_SOLVER_DIR", directory):
                    solver = H.load_solver()
                    result = solver.read_pil(image)
            finally:
                if previous is absent:
                    sys.modules.pop("onnxruntime", None)
                else:
                    sys.modules["onnxruntime"] = previous
            inference.assert_called_once_with(model, providers=["CPUExecutionProvider"])
            self.assertEqual(result["number"], 1234)
            self.assertGreaterEqual(result["confidence"], 0.99)
            self.assertEqual(result["snap_distance"], 0)
            self.assertEqual(feeds[0][0], ["synthetic-output"])
            tensor = feeds[0][1]["synthetic-input"]
            self.assertEqual(tensor.shape, (1, 1, 48, 512))
            self.assertEqual(tensor.dtype, np.float32)


if __name__ == "__main__":
    unittest.main(verbosity=2)
