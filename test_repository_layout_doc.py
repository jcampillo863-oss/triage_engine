"""Offline layout-document checks; no application imports or runtime writes."""
from pathlib import Path, PurePosixPath
import re
import unittest
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DOCUMENT = ROOT / "docs" / "REPOSITORY_LAYOUT.md"
HEADING = "# Repository source layout"


def validate_document(data, document=DOCUMENT, root=ROOT):
    text = data.decode("utf-8", errors="strict")
    if text.startswith("\ufeff") or not text.endswith("\n"):
        raise ValueError("Document must be UTF-8 without BOM and end with a newline")
    if text.splitlines()[0] != HEADING:
        raise ValueError("Descriptive heading does not match")
    targets = re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", text)
    if not targets:
        raise ValueError("Document must reference repository paths")
    for target in targets:
        url = urlsplit(target)
        if (url.scheme or url.netloc or url.query or url.fragment
                or "\\" in target or PurePosixPath(target).is_absolute()):
            raise ValueError("Reference must be a relative repository path")
        resolved = (document.parent / target).resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
            raise ValueError("Reference must exist inside this repository")
    return targets


class RepositoryLayoutDocTests(unittest.TestCase):
    def test_delivered_document(self):
        targets = validate_document(DOCUMENT.read_bytes())
        self.assertGreaterEqual(len(targets), 5)

    def test_invalid_utf8_is_rejected(self):
        with self.assertRaises(UnicodeDecodeError):
            validate_document(b"\xff")

    def test_missing_final_newline_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_document(DOCUMENT.read_bytes().rstrip(b"\n"))

    def test_wrong_heading_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_document(b"# Other heading\n[Source](../db.py)\n")

    def test_bom_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_document(b"\xef\xbb\xbf" + DOCUMENT.read_bytes())

    def test_invalid_reference_is_rejected(self):
        for target in ("https://example.invalid/file", "/db.py", "C:/db.py",
                       "..\\db.py", "../../outside-repository", "../missing-layout-source",
                       "../db.py?query=1", "../db.py#fragment"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_document((HEADING + "\n[Source](" + target + ")\n").encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
