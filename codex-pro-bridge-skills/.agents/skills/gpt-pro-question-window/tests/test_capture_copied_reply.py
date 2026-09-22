import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture_copied_reply.py"
SPEC = importlib.util.spec_from_file_location("capture_copied_reply", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CopiedReplyMetricsTests(unittest.TestCase):
    def test_preserves_structured_markdown_contract(self):
        reply = """# Result

Inline \\(a_e\\) and [source](sandbox:/mnt/data/source.txt).

$$
A_e=\\frac{x}{y}
$$

| Name | Value |
| --- | ---: |
| a | 1 |

```cpp
phase.phi();
```
"""
        metrics = MODULE.markdown_metrics(reply)
        self.assertEqual(metrics["headings"], 1)
        self.assertEqual(metrics["inline_math"], 1)
        self.assertEqual(metrics["display_math"], 1)
        self.assertEqual(metrics["tables"], 1)
        self.assertEqual(metrics["code_blocks"], 1)
        self.assertEqual(metrics["links"], 1)


if __name__ == "__main__":
    unittest.main()
