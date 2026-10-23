from pathlib import Path
import unittest


class ImplementationContract(unittest.TestCase):
    def test_gateway_entrypoint_exists(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "gateway" / "app.py").is_file(), "尚未实现独立网关")


if __name__ == "__main__":
    unittest.main()
