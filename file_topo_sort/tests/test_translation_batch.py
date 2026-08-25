from pathlib import Path
import tempfile
import unittest

from file_topo_sort import plan_translation_batch


class TranslationBatchPlanTest(unittest.TestCase):
    def make_project(self, *, with_test: bool = True) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "config.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "model.py").write_text(
            "from config import VALUE\n\nclass Model: pass\n",
            encoding="utf-8",
        )
        (root / "service.py").write_text(
            "from model import Model\n\ndef run(): return Model()\n",
            encoding="utf-8",
        )
        (root / "main.py").write_text(
            "from service import run\n\ndef main(): return run()\n",
            encoding="utf-8",
        )
        if with_test:
            (root / "tests").mkdir()
            (root / "tests" / "test_main.py").write_text(
                "from main import main\n\ndef test_main(): assert main()\n",
                encoding="utf-8",
            )
        return root

    def test_expands_requested_count_to_complete_chain(self):
        root = self.make_project()

        result = plan_translation_batch(
            root,
            "python",
            translated_files=["config.py"],
            requested_count=1,
        )

        self.assertEqual(
            result["recommended_files"], ["model.py", "service.py", "main.py"]
        )
        self.assertTrue(result["expanded"])
        self.assertEqual(result["expansion_count"], 2)
        self.assertEqual(result["status"], "ready_for_realtime_test")
        self.assertTrue(result["realtime_test_ready"])
        self.assertEqual(result["verification_tests"][0]["file"], "tests/test_main.py")

    def test_continues_after_translated_prefix(self):
        root = self.make_project()

        result = plan_translation_batch(
            root,
            "python",
            translated_files=["config.py", "model.py", "service.py"],
            requested_count=1,
        )

        self.assertEqual(result["recommended_files"], ["main.py"])
        self.assertFalse(result["expanded"])
        self.assertEqual(result["selected_chains"][0]["already_translated"], [
            "config.py", "model.py", "service.py",
        ])

    def test_reports_missing_static_verification_test(self):
        root = self.make_project(with_test=False)

        result = plan_translation_batch(
            root,
            "python",
            translated_files=["config.py"],
            requested_count=1,
        )

        self.assertEqual(result["status"], "no_static_verification_test")
        self.assertFalse(result["realtime_test_ready"])
        self.assertEqual(result["verification_tests"], [])

    def test_accepts_unique_relative_suffix_and_rejects_unknown_file(self):
        root = self.make_project()

        result = plan_translation_batch(
            root,
            "python",
            translated_files=[Path("config.py")],
            requested_count=1,
        )
        self.assertEqual(result["translated_files"], ["config.py"])

        with self.assertRaises(ValueError):
            plan_translation_batch(
                root,
                "python",
                translated_files=["does_not_exist.py"],
                requested_count=1,
            )


if __name__ == "__main__":
    unittest.main()
