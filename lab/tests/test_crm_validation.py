"""CRM store, validators, and stage machine tests (Phase A). Run:
  unset PYTHONPATH VIRTUAL_ENV
  "C:/Program Files/Python310/python.exe" -m unittest lab.tests.test_crm_validation -v
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)


class TempStoreCase(unittest.TestCase):
    """Each test gets a throwaway CRM store path."""

    def setUp(self):
        self._old = os.environ.get("IRICA_CRM_STORE")
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(path)
        os.environ["IRICA_CRM_STORE"] = path
        self.path = path
        import crm
        crm.reset_cache()
        self.crm = crm

    def tearDown(self):
        import crm
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


class TestNormalizeDigits(TempStoreCase):
    def test_persian_and_arabic_digits(self):
        self.assertEqual(self.crm.normalize_digits("۱۲۳۴۵۶۷۸۹۰"), "1234567890")
        self.assertEqual(self.crm.normalize_digits("٠١٢٣"), "0123")
        self.assertEqual(self.crm.normalize_digits("0912-123"), "0912-123")


class TestCustomerValidation(TempStoreCase):
    def test_full_name_required(self):
        errs = self.crm.validate_customer({"full_name": "  "})
        self.assertTrue(any("نام" in e for e in errs))

    def test_minimal_ok(self):
        self.assertEqual(self.crm.validate_customer({"full_name": "علی رضایی"}), [])

    def test_mobile_normalized(self):
        self.assertEqual(self.crm.validate_customer({"full_name": "x", "mobile": "۰۹۱۲۱۲۳۴۵۶۷"}), [])

    def test_mobile_invalid(self):
        self.assertTrue(self.crm.validate_customer({"full_name": "x", "mobile": "9121234567"}))

    def test_optional_fields_free(self):
        self.assertEqual(self.crm.validate_customer(
            {"full_name": "x", "address": "تهران", "telegram_id": "@ali", "instagram_id": "ali.i"}), [])


class TestOwnerValidation(TempStoreCase):
    def valid_owner(self, **over):
        base = {"first_name": "علی", "last_name": "رضایی",
                "national": "0012345679", "birth": "1375/04/29",
                "mobile": "09121234567", "card_no": "6219861012345678"}
        base.update(over)
        return base

    def test_valid_owner(self):
        self.assertEqual(self.crm.validate_owner(self.valid_owner()), [])

    def test_national_checksum(self):
        # 0012345679 passes mod-11; flipping the check digit must fail
        self.assertTrue(self.crm.validate_owner(self.valid_owner(national="0012345678")))

    def test_national_10_digits(self):
        self.assertTrue(self.crm.validate_owner(self.valid_owner(national="12345678")))

    def test_birth_formats(self):
        self.assertEqual(self.crm.validate_owner(self.valid_owner(birth="1375/4/29")), [])
        self.assertTrue(self.crm.validate_owner(self.valid_owner(birth="1375/13/01")))
        self.assertTrue(self.crm.validate_owner(self.valid_owner(birth="1375/00/15")))

    def test_age_under_15(self):
        # 15 at 1405 -> born 1390/9/16 exactly 15; 1391/01/01 -> 14 => reject
        self.assertTrue(self.crm.validate_owner(self.valid_owner(birth="1391/01/01")))

    def test_age_15_boundary_ok(self):
        self.assertEqual(self.crm.validate_owner(self.valid_owner(birth="1390/6/16")), [])

    def test_age_over_99(self):
        self.assertTrue(self.crm.validate_owner(self.valid_owner(birth="1305/01/01")))

    def test_age_99_boundary_ok(self):
        self.assertEqual(self.crm.validate_owner(self.valid_owner(birth="1306/6/16")), [])

    def test_mobile_rule(self):
        self.assertTrue(self.crm.validate_owner(self.valid_owner(mobile="08121234567")))

    def test_card_16_digits(self):
        self.assertTrue(self.crm.validate_owner(self.valid_owner(card_no="621986101234567")))
        self.assertTrue(self.crm.validate_owner(self.valid_owner(card_no="62198610123456780")))
        # dashed/spaced formatting is accepted after normalization
        self.assertEqual(self.crm.validate_owner(self.valid_owner(card_no="6219-8610-1234-5678")), [])

    def test_names_required(self):
        self.assertTrue(self.crm.validate_owner(self.valid_owner(first_name="")))
        self.assertTrue(self.crm.validate_owner(self.valid_owner(last_name="")))


class TestIdentityValidation(TempStoreCase):
    """Pool entries must obey exactly the estelam-person rules (F8)."""

    def test_same_rules_as_estelam_person(self):
        import panel
        ident = {"label": "خودم", "national": "0012345679",
                 "birth": "1375/4/29", "mobile": "09121234567"}
        self.assertEqual(self.crm.validate_identity(ident), panel.validate_estelam_person(ident))
        bad = dict(ident, national="123")
        self.assertEqual(self.crm.validate_identity(bad), panel.validate_estelam_person(bad))


class TestDeviceValidation(TempStoreCase):
    def test_name_required(self):
        errs = self.crm.validate_device({"name": "", "serial1": "356938035643809"})
        self.assertTrue(any("نام" in e for e in errs))

    def test_serial1_required_15_digits(self):
        self.assertTrue(self.crm.validate_device({"name": "iPhone", "serial1": "123"}))
        self.assertEqual(self.crm.validate_device({"name": "iPhone", "serial1": "356938035643809"}), [])

    def test_serial2_optional_but_15_if_present(self):
        self.assertEqual(self.crm.validate_device(
            {"name": "iPhone", "serial1": "356938035643809", "serial2": ""}), [])
        self.assertTrue(self.crm.validate_device(
            {"name": "iPhone", "serial1": "356938035643809", "serial2": "12345"}))

    def test_persian_digits_normalized(self):
        self.assertEqual(self.crm.validate_device(
            {"name": "گوشی", "serial1": "۳۵۶۹۳۸۰۳۵۶۴۳۸۰۹"}), [])


if __name__ == "__main__":
    unittest.main()
