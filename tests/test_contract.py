"""核对服务身份和领域样例。"""

import json
import unittest
from pathlib import Path

from service import SERVICE_ID, health_payload


class ContractTest(unittest.TestCase):
    def test_service_identity(self):
        self.assertEqual(health_payload()["service"], SERVICE_ID)

    def test_domain_sample(self):
        data = json.loads(Path("contracts/visit_day.json").read_text(encoding="utf-8"))
        self.assertEqual(data["service"], SERVICE_ID)
        self.assertTrue(data["sample"])

    def test_domain_sample_parses(self):
        """样例中的资源与团队申报都能通过领域校验。"""

        from model import parse_day, parse_group

        data = json.loads(Path("contracts/visit_day.json").read_text(encoding="utf-8"))
        day = parse_day(data["sample"])
        self.assertIn("storyteller-01", day.resources)
        for group in data["sample"]["groups"]:
            self.assertTrue(parse_group(group).requests)


if __name__ == "__main__":
    unittest.main()
