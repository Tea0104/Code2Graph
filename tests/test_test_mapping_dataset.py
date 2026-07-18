from pathlib import Path
import tempfile
import unittest

from test_mapping.dataset import PairLayout, public_test_files
from test_mapping.models import LanguagePair


class PairLayoutTest(unittest.TestCase):
    def test_detects_team_subset_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pair_root = root / "Python_to_C++"
            (pair_root / "source_projects" / "demo").mkdir(parents=True)
            (pair_root / "target_projects" / "demo").mkdir(parents=True)
            layout = PairLayout.detect(root, LanguagePair("Python", "C++"))
            self.assertEqual(layout.layout, "team_subset")
            self.assertEqual(layout.project("demo").source_dir.name, "demo")

    def test_detects_raw_repotransbench_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source_projects" / "Python" / "demo").mkdir(parents=True)
            (root / "target_projects" / "Python" / "C++" / "demo").mkdir(parents=True)
            layout = PairLayout.detect(root, LanguagePair("Python", "C++"))
            self.assertEqual(layout.layout, "raw_repotransbench")
            self.assertEqual(len(layout.projects()), 1)

    def test_public_tests_can_be_marked_by_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "test" / "feature_public_test.cpp"
            path.parent.mkdir(parents=True)
            path.write_text("TEST(Demo, Works) {}", encoding="utf-8")
            self.assertEqual(public_test_files(root, "C++"), [path])


if __name__ == "__main__":
    unittest.main()
