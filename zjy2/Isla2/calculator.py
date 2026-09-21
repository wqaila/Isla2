"""简单计算器模块（「次元萌盒」原型中的示例代码）。

只提供最基础的四则运算，供单元测试演示使用。
"""

import math
from numbers import Real

# 判定「浮点近似零」的绝对容差：
# 像 0.1 + 0.2 - 0.3 这样的运算结果并不是精确的 0，而是 5.5e-17，
# 直接判 b == 0 会漏掉这种情况，导致返回一个巨大的错误结果。
_ZERO_TOLERANCE = 1e-12


class Calculator:
    """四则运算计算器。

    所有方法只接受实数（int / float），传入其他类型会抛出 TypeError；
    除法遇到 0（或绝对值小于 1e-12 的近似 0）时抛出 ValueError。
    """

    def __init__(self) -> None:
        """创建一个计算器实例（无内部状态）。"""

    @staticmethod
    def _ensure_number(value: Real, name: str) -> Real:
        """校验入参是实数，否则给出明确的错误信息"""
        if not isinstance(value, Real):
            raise TypeError(
                f"{name} 必须是实数（int 或 float），收到 {type(value).__name__}: {value!r}"
            )
        return value

    def add(self, a: Real, b: Real) -> Real:
        """返回 a + b"""
        return self._ensure_number(a, "a") + self._ensure_number(b, "b")

    def subtract(self, a: Real, b: Real) -> Real:
        """返回 a - b"""
        return self._ensure_number(a, "a") - self._ensure_number(b, "b")

    def multiply(self, a: Real, b: Real) -> Real:
        """返回 a * b"""
        return self._ensure_number(a, "a") * self._ensure_number(b, "b")

    def divide(self, a: Real, b: Real) -> Real:
        """返回 a / b

        除数为 0、或绝对值小于 1e-12（浮点运算产生的近似零）时抛出 ValueError。
        """
        a = self._ensure_number(a, "a")
        b = self._ensure_number(b, "b")
        if b == 0 or math.isclose(b, 0.0, abs_tol=_ZERO_TOLERANCE):
            raise ValueError("Cannot divide by zero")
        return a / b
