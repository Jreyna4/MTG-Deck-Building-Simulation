"""A small learning model in pure Python: ridge linear regression.

It learns how much each feature of an opening hand (land count, ramp, cheap plays, colors...)
adds to the goldfish score, from thousands of simulated games. Pure Python keeps it portable;
it can be swapped for scikit-learn or a neural network later without changing the callers.
"""
from __future__ import annotations

import json
from pathlib import Path


def _solve(a: list, b: list) -> list:
    """Solve a·x = b with Gaussian elimination (a is small and square)."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                if f:
                    m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


class LinearModel:
    def __init__(self, feature_names: list):
        self.names = list(feature_names)
        self.mean = [0.0] * len(self.names)
        self.std = [1.0] * len(self.names)
        self.coef = [0.0] * len(self.names)
        self.intercept = 0.0

    def _z(self, x: list) -> list:
        return [(v - m) / s for v, m, s in zip(x, self.mean, self.std)]

    def fit(self, rows: list, targets: list, l2: float = 1.0) -> "LinearModel":
        n, k = len(rows), len(self.names)
        self.mean = [sum(r[j] for r in rows) / n for j in range(k)]
        self.std = []
        for j in range(k):
            var = sum((r[j] - self.mean[j]) ** 2 for r in rows) / n
            self.std.append(var ** 0.5 if var > 1e-12 else 1.0)
        z = [self._z(r) for r in rows]
        self.intercept = sum(targets) / n
        yc = [t - self.intercept for t in targets]
        xtx = [[sum(z[i][a] * z[i][b] for i in range(n)) + (l2 if a == b else 0.0) for b in range(k)]
               for a in range(k)]
        xty = [sum(z[i][a] * yc[i] for i in range(n)) for a in range(k)]
        self.coef = _solve(xtx, xty)
        return self

    def predict(self, x: list) -> float:
        return self.intercept + sum(c * v for c, v in zip(self.coef, self._z(x)))

    def contributions(self, x: list) -> list:
        """(feature, effect) pairs: how far each feature moves this prediction from average."""
        return sorted(((n, c * v) for n, c, v in zip(self.names, self.coef, self._z(x))),
                      key=lambda p: abs(p[1]), reverse=True)

    def r2(self, rows: list, targets: list) -> float:
        mean = sum(targets) / len(targets)
        ss_tot = sum((t - mean) ** 2 for t in targets)
        ss_res = sum((t - self.predict(r)) ** 2 for r, t in zip(rows, targets))
        return 1 - ss_res / ss_tot if ss_tot else 0.0

    def to_dict(self) -> dict:
        return {"names": self.names, "mean": self.mean, "std": self.std, "coef": self.coef,
                "intercept": self.intercept}

    @classmethod
    def from_dict(cls, d: dict) -> "LinearModel":
        m = cls(d["names"])
        m.mean, m.std, m.coef, m.intercept = d["mean"], d["std"], d["coef"], d["intercept"]
        return m

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "LinearModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
