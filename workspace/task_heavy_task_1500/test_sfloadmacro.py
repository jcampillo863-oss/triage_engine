import unittest
from sfloadmacro import sfloadmacro_process

class TestSFLoadMacro(unittest.TestCase):
    def test_instrumentation_coverage(self):
        sample_data = [1, 2, None, 4]
        result = sfloadmacro_process(sample_data, instrumentation=True)
        self.assertEqual(result, [2, 4, 8])

if __name__ == "__main__":
    unittest.main()