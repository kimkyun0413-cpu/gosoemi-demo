"""scripts/backup_storage.py의 목록 읽기 검사(네트워크 없음). 실행: python3 -m unittest discover -s tests"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backup_storage as b  # noqa: E402


class FakeBucket:
    def __init__(self, files):
        self.files = files

    def list(self, prefix, opts):
        depth = len(prefix.split("/")) if prefix else 0
        names = {}
        for path in self.files:
            parts = path.split("/")
            if prefix and not path.startswith(prefix + "/"):
                continue
            name = parts[depth]
            names[name] = len(parts) == depth + 1
        rows = [{"name": n, "id": ("id-" + n) if is_file else None} for n, is_file in sorted(names.items())]
        return rows[opts["offset"]: opts["offset"] + opts["limit"]]


class ListAllTest(unittest.TestCase):
    def test_walks_user_and_case_folders(self):
        files = ["u1/c1/a.jpg", "u1/c1/b.pdf", "u1/c2/c.mp3", "u2/c9/d.png"]
        self.assertEqual(sorted(b.list_all(FakeBucket(files))), sorted(files))

    def test_pages_through_more_than_100_files(self):
        files = [f"u1/c1/f{i:03d}.jpg" for i in range(250)]
        self.assertEqual(len(b.list_all(FakeBucket(files))), 250)


if __name__ == "__main__":
    unittest.main()
