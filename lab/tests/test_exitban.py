import unittest
from unittest.mock import patch

import exitban


class ExitbanIdentityTests(unittest.TestCase):
    def test_load_identity_uses_explicit_profile_id_not_active_profile(self):
        store = {
            "active_id": "p1",
            "profiles": [
                {"id": "p1", "first_name": "فعال"},
                {"id": "p2", "first_name": "هدف", "last_name": "خانواده",
                 "father_name": "پدر", "birth": "1370/01/01",
                 "birth_certificate_no": "22", "national": "0012345678"},
            ],
        }
        with patch.object(exitban.panel, "load_store", return_value=store):
            identity = exitban.load_identity("p2")
        self.assertEqual(identity["first_name"], "هدف")
        self.assertEqual(identity["national"], "0012345678")

    def test_load_identity_falls_back_to_active_id(self):
        store = {"active_id": "p1", "profiles": [{"id": "p1", "first_name": "فعال"}]}
        with patch.object(exitban.panel, "load_store", return_value=store):
            self.assertEqual(exitban.load_identity()["first_name"], "فعال")

    def test_missing_fields_are_reported_by_semantic_name(self):
        identity = {key: "ok" for key in exitban.IDENTITY_FIELDS}
        identity["father_name"] = ""
        missing = exitban.missing_identity_fields(identity)
        self.assertEqual(missing, ["father_name"])

    def test_form_mapping_contains_exact_site_names(self):
        self.assertEqual(exitban.FIELD_MAP["national"], "NationlityCode")
        self.assertEqual(set(exitban.FIELD_MAP), set(exitban.IDENTITY_FIELDS))

    def test_incomplete_identity_blocks_submit(self):
        identity = {key: "ok" for key in exitban.IDENTITY_FIELDS}
        identity["national"] = ""
        with self.assertRaises(exitban.IncompleteIdentityError):
            exitban.ensure_complete(identity)

    def test_dry_run_fills_all_fields_without_submit(self):
        identity = {key: key for key in exitban.IDENTITY_FIELDS}

        class Element:
            def __init__(self):
                self.value = ""
            def is_enabled(self):
                return True
            def clear(self):
                self.value = ""
            def send_keys(self, value):
                self.value = value
            def get_attribute(self, name):
                return self.value if name == "value" else ""

        class Driver:
            def __init__(self):
                self.elements = {name: Element() for name in exitban.FIELD_MAP.values()}
                self.submit_count = 0
            def find_element(self, by, value):
                if value == "button[type='submit']":
                    self.submit_count += 1
                    raise AssertionError("submit must not be located in dry-run")
                return self.elements[value]

        driver = Driver()
        exitban.fill_and_verify(driver, identity, wait_factory=lambda _driver, _timeout: _driver)
        self.assertEqual(driver.submit_count, 0)
        self.assertEqual(
            {name: element.value for name, element in driver.elements.items()},
            {exitban.FIELD_MAP[key]: value for key, value in identity.items()},
        )


if __name__ == "__main__":
    unittest.main()
