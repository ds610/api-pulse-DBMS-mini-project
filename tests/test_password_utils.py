
import unittest

from password_utils import hash_password, verify_password

class TestPasswordHashing(unittest.TestCase):

    def test_password_is_hashed(self):
        password = "TestPassword123!"
        hashed = hash_password(password)

        self.assertNotEqual(password, hashed)
        self.assertTrue(hashed.startswith("$argon2id$"))

    def test_correct_password_verifies(self):
        password = "TestPassword123!"
        hashed = hash_password(password)

        self.assertTrue(verify_password(password, hashed))

    def test_incorrect_password_fails(self):
        hashed = hash_password("TestPassword123!")

        self.assertFalse(
            verify_password("WrongPassword123!", hashed)
        )

    def test_same_password_produces_different_hashes(self):
        password = "TestPassword123!"

        first_hash = hash_password(password)
        second_hash = hash_password(password)

        self.assertNotEqual(first_hash, second_hash)
        self.assertTrue(verify_password(password, first_hash))
        self.assertTrue(verify_password(password, second_hash))


if __name__ == "__main__":
    unittest.main()
