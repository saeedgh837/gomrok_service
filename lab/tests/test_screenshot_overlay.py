import os
import tempfile
import unittest

import gomrok


class FakeDriver:
    def __init__(self):
        self.scripts = []
        self.captured = False

    def execute_script(self, script):
        self.scripts.append(script)

    def execute_cdp_cmd(self, name, payload):
        if name == "Page.captureScreenshot":
            self.captured = True
            return {"data": ""}
        raise AssertionError(name)


class ScreenshotOverlayTests(unittest.TestCase):
    def test_full_screenshot_temporarily_hides_only_marked_overlays(self):
        driver = FakeDriver()
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

        self.assertTrue(gomrok._full_screenshot(driver, path))

        scripts = "\n".join(driver.scripts)
        self.assertIn("data-hermes-hide-in-screenshot", scripts)
        self.assertIn("el.style.visibility = 'hidden'", scripts)
        self.assertIn("style.visibility", scripts)
        self.assertTrue(driver.captured)


if __name__ == "__main__":
    unittest.main()
