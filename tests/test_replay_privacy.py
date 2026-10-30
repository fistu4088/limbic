import unittest

from limbic.replay import _owner_alias


class ReplayPrivacyTests(unittest.TestCase):
    def test_owner_alias_does_not_contain_private_owner_names(self):
        self.assertEqual(_owner_alias("Morgan", "Aria"), "Morgan")
        self.assertEqual(_owner_alias("Casey", "Aria"), "Casey")

    def test_generic_owner_aliases_still_resolve_to_configured_owner(self):
        self.assertEqual(_owner_alias("the user", "Aria"), "Aria")
        self.assertEqual(_owner_alias("felhasználó", "Aria"), "Aria")


if __name__ == "__main__":
    unittest.main()
