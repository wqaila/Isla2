import sys
import unittest
from pathlib import Path

# 保证无论从哪个目录运行测试，都能导入同目录下的 calculator 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from calculator import Calculator


class TestCalculator(unittest.TestCase):
    def setUp(self):
        self.calculator = Calculator()

    def test_add(self):
        self.assertEqual(self.calculator.add(2, 3), 5)

    def test_subtract(self):
        self.assertEqual(self.calculator.subtract(5, 3), 2)

    def test_multiply(self):
        self.assertEqual(self.calculator.multiply(2, 3), 6)

    def test_divide(self):
        self.assertEqual(self.calculator.divide(6, 3), 2)
        with self.assertRaises(ValueError):
            self.calculator.divide(5, 0)

    def test_divide_by_approximate_zero(self):
        # 0.1 + 0.2 - 0.3 的结果是 5.5e-17 而不是精确的 0，也应视为除零
        near_zero = 0.1 + 0.2 - 0.3
        with self.assertRaises(ValueError):
            self.calculator.divide(1, near_zero)

    def test_non_numeric_input(self):
        with self.assertRaises(TypeError):
            self.calculator.add("1", 2)


if __name__ == '__main__':
    unittest.main()
