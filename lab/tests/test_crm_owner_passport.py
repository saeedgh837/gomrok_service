"""Optional owner passport + registration_chat_id default.

1. validate_owner accepts missing passports and rejects invalid provided values
   (1-20 latin alnum,
   mixed case accepted on input) while the other owner rules stay on.
2. add_owner/update_owner persist the passport trimmed + uppercased;
   modern-row updates omitting it keep the stored value; legacy rows
   without the key load unchanged and can be saved without a passport.
3. build_report never renders the passport (the step-4 Telegram message
   reads owner.passport straight from the store); DEFAULT_SETTINGS
   carries registration_chat_id as a default only (the panel domain
   owns validation and use).
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import crm  # noqa: E402

# Clearly synthetic latin alnum passport, 9 chars — never real data.
VALID_PASSPORT = "PP0000001"


def valid_owner_fields(**over):
    """Checksum-valid synthetic owner fields (national proven green in
    test_crm_validation)."""
    fields = {
        "first_name": "مسافر",
        "last_name": "آزمون",
        "national": "0012345679",
        "birth": "1375/04/29",
        "mobile": "09121234567",
        "card_no": "6219861012345678",
        "passport": VALID_PASSPORT,
    }
    fields.update(over)
    return fields


class TempStoreCase(unittest.TestCase):
    """Each test gets a throwaway CRM store path (test_crm_validation pattern)."""

    def setUp(self):
        self._old = os.environ.get("IRICA_CRM_STORE")
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        os.environ["IRICA_CRM_STORE"] = path
        self.path = path
        crm.reset_cache()

    def tearDown(self):
        crm.reset_cache()
        if self._old:
            os.environ["IRICA_CRM_STORE"] = self._old
        else:
            os.environ.pop("IRICA_CRM_STORE", None)
        for suffix in ("", ".bak"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def write_store(self, data):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)

    def stored_owner(self, owner_id):
        return next(o for o in crm.store_read()["owners"]
                    if o["id"] == owner_id)


class TestPassportValidation(TempStoreCase):
    def test_missing_passport_allowed(self):
        for variant in (None, "", "   ", "ABSENT"):
            fields = valid_owner_fields()
            if variant == "ABSENT":
                del fields["passport"]
            else:
                fields["passport"] = variant
            errors = crm.validate_owner(fields)
            self.assertEqual(errors, [], (variant, errors))

    def test_valid_boundaries_and_forms(self):
        for passport in ("A",                                  # 1 char
                         "abcd",                               # 4 chars
                         "abcdefghijklmnopqrst",               # 20 chars
                         "AbCdEf123",                          # mixed case
                         "123456789"):                         # digits only
            errors = crm.validate_owner(valid_owner_fields(passport=passport))
            self.assertEqual(errors, [], passport)

    def test_invalid_charset_rejected(self):
        for passport in ("آزمونتستتست",          # Persian letters
                         "۱۲۳۴۵۶۷۸۹",             # Persian digits
                         "ABCD-1234",                             # hyphen
                         "ABC DEF123",                            # inner space
                         "ABCD!1234"):                            # punctuation
            errors = crm.validate_owner(valid_owner_fields(passport=passport))
            self.assertTrue(
                any("گذرنامه" in e and "لاتین" in e for e in errors),
                (passport, errors))

    def test_length_out_of_range_rejected(self):
        for passport in ("abcdefghijklmnopqrstu",):              # 21 chars
            errors = crm.validate_owner(valid_owner_fields(passport=passport))
            self.assertTrue(
                any("گذرنامه" in e and "لاتین" in e for e in errors),
                (passport, errors))

    def test_other_rules_still_enforced_alongside(self):
        # Valid passport does not mask a broken national code.
        errors = crm.validate_owner(valid_owner_fields(national="0012345678"))
        self.assertTrue(any("کد ملی" in e for e in errors), errors)
        self.assertFalse(any("گذرنامه" in e for e in errors), errors)


class TestPassportPersistence(TempStoreCase):
    def test_add_owner_stores_trimmed_uppercase(self):
        record, errors = crm.add_owner(valid_owner_fields(passport=" ab1234567 "))
        self.assertEqual(errors, [], errors)
        self.assertEqual(self.stored_owner(record["id"])["passport"],
                         "AB1234567")

    def test_update_owner_stores_trimmed_uppercase(self):
        record, errors = crm.add_owner(valid_owner_fields())
        self.assertEqual(errors, [], errors)
        _updated, errors = crm.update_owner(record["id"],
                                            {"passport": " cd7654321 "})
        self.assertEqual(errors, [], errors)
        self.assertEqual(self.stored_owner(record["id"])["passport"],
                         "CD7654321")

    def test_update_omitting_passport_keeps_stored_value(self):
        record, errors = crm.add_owner(valid_owner_fields())
        self.assertEqual(errors, [], errors)
        _updated, errors = crm.update_owner(record["id"],
                                            {"first_name": "مسافر ویرایش‌شده"})
        self.assertEqual(errors, [], errors)
        self.assertEqual(self.stored_owner(record["id"])["passport"],
                         VALID_PASSPORT)

    def test_legacy_row_without_passport(self):
        # Raw legacy store: owner rows written before the field existed.
        legacy = {"id": "own-legacy-1", "first_name": "مسافر",
                  "last_name": "قدیمی", "national": "0012345679",
                  "birth": "1375/04/29", "mobile": "09121234567",
                  "card_no": "", "created": "2020-01-01 00:00:00"}
        self.write_store({"version": 1, "owners": [legacy]})
        # Loads unchanged, no crash.
        owner = crm.store_read()["owners"][0]
        self.assertNotIn("passport", owner)
        # Save omitting the optional passport succeeds and materializes it empty.
        _record, errors = crm.update_owner("own-legacy-1",
                                           {"first_name": "مسافر ویرایش‌شده"})
        self.assertEqual(errors, [], errors)
        self.assertEqual(self.stored_owner("own-legacy-1")["passport"], "")
        # Save providing a valid passport succeeds and persists uppercased.
        _record, errors = crm.update_owner("own-legacy-1",
                                           {"passport": " ab1234567 "})
        self.assertEqual(errors, [], errors)
        self.assertEqual(self.stored_owner("own-legacy-1")["passport"],
                         "AB1234567")


class TestReportAndSettings(TempStoreCase):
    def _report_state(self):
        owner = {"id": "own1", "first_name": "مسافر", "last_name": "آزمون",
                 "national": "0012345679", "birth": "1375/04/29",
                 "mobile": "09121234567", "card_no": "6219861012345678",
                 "passport": "AB1234567"}
        return {"devices": [{"id": "dev1", "name": "Device 1",
                             "serial1": "356938035640001", "serial2": "",
                             "customer_id": "", "owner_id": "own1",
                             "stage": "ready_owner"}],
                "customers": [], "owners": [owner]}

    def test_build_report_omits_passport(self):
        for kwargs in ({"full_data": True, "plain": False},
                       {"full_data": True, "plain": True},
                       {"full_data": False, "plain": False}):
            text = crm.build_report(self._report_state(), **kwargs)
            self.assertNotIn("گذرنامه", text, kwargs)
            self.assertNotIn("AB1234567", text, kwargs)

    def test_default_settings_registration_chat_id(self):
        # Empty store: the new default materializes on read.
        self.assertEqual(
            crm.store_read()["settings"]["registration_chat_id"], "")

    def test_store_registration_chat_id_preserved(self):
        self.write_store({"version": 1,
                          "settings": {"registration_chat_id": "123456789"}})
        settings = crm.store_read()["settings"]
        self.assertEqual(settings["registration_chat_id"], "123456789")
        # Other defaults still merge in alongside the stored value.
        self.assertEqual(settings["watch_interval_hours"], 2)


if __name__ == "__main__":
    unittest.main()
