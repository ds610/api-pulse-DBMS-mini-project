import unittest
from pydantic import ValidationError

from auth_schemas import LoginPayload


class TestLoginPayload(unittest.TestCase):

    def test_valid_credentials(self):
        payload = LoginPayload(
            email="test@example.com",
            password="hello123"
        )
        self.assertEqual(payload.email, "test@example.com")

    def test_empty_password_rejected(self):
        with self.assertRaises(ValidationError):
            LoginPayload(
                email="test@example.com",
                password=""
            )

    def test_missing_password_rejected(self):
        with self.assertRaises(ValidationError):
            LoginPayload(email="test@example.com")

    def test_empty_email_rejected(self):
        with self.assertRaises(ValidationError):
            LoginPayload(email="", password="hello123")


if __name__ == "__main__":
    unittest.main()