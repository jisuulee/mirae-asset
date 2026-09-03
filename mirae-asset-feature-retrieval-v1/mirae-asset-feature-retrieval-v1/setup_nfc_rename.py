"""Normalize all file/directory names under a target dir to Unicode NFC.

Usage: python setup_nfc_rename.py [target_dir]
Defaults to <script_dir>/data. Renames only (content untouched). Bottom-up walk
so children are renamed before their parents. Name collisions are skipped and
reported.

Exclusion: the NFD alias layer (data/<NFD '3.공시'>/corpus/raw/...) exists on
purpose. analysis_results/document_manifest.csv stores NFD backslash paths and
its content may not be edited, so directory junctions bridge those NFD names to
the NFC-normalized real tree under data/raw. This script must not rename that
layer; entries whose NFC form equals the alias root, anything below it, and
reparse points (junctions) are skipped.
"""
import os
import stat
import sys
import unicodedata

target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
if not os.path.isdir(target):
    print(f"target not found: {target}")
    sys.exit(1)

ALIAS_ROOT_NFC = "3.공시"  # NFC '3.공시'


def is_alias_entry(name: str) -> bool:
    return unicodedata.normalize("NFC", name) == ALIAS_ROOT_NFC


def under_alias(dirpath: str) -> bool:
    rel = os.path.relpath(dirpath, target)
    if rel == ".":
        return False
    return is_alias_entry(rel.split(os.sep)[0])


def is_reparse(path: str) -> bool:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


changed = 0
skipped: list[str] = []
errors: list[str] = []

for dirpath, dirnames, filenames in os.walk(target, topdown=False):
    if under_alias(dirpath):
        continue
    names = filenames + dirnames
    if os.path.normcase(os.path.abspath(dirpath)) == os.path.normcase(os.path.abspath(target)):
        names = [n for n in names if not is_alias_entry(n)]
    for name in names:
        nfc = unicodedata.normalize("NFC", name)
        if nfc == name:
            continue
        src = os.path.join(dirpath, name)
        if is_reparse(src):
            continue
        dst = os.path.join(dirpath, nfc)
        if os.path.exists(dst):
            skipped.append(src)
            continue
        try:
            os.rename(src, dst)
            changed += 1
        except OSError as exc:
            errors.append(f"{src}: {exc}")

print(f"target: {target}")
print(f"renamed: {changed}")
print(f"skipped (name collision): {len(skipped)}")
for s in skipped[:50]:
    print(f"  SKIP {s}")
print(f"errors: {len(errors)}")
for e in errors[:50]:
    print(f"  ERR {e}")
sys.exit(2 if (skipped or errors) else 0)
