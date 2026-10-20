"""Create a clean source archive without secrets, databases, or cache files."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/"cognito-project.zip"
INCLUDED=("app","tests","scripts")
ROOT_FILES=("README.md","requirements.txt",".env.example",".gitignore")
OMIT_DIRS={"__pycache__",".venv","node_modules",".git"}
OMIT_NAMES={".env","cognito-project.zip"}
OMIT_SUFFIXES={".pyc",".sqlite3",".sqlite3-shm",".sqlite3-wal",".log"}


def main():
    files=[]
    for folder in INCLUDED:
        base=ROOT/folder
        if not base.exists(): continue
        for path in base.rglob("*"):
            if not path.is_file() or any(part in OMIT_DIRS for part in path.parts): continue
            if path.name in OMIT_NAMES or path.suffix in OMIT_SUFFIXES: continue
            files.append(path)
    files.extend(ROOT/name for name in ROOT_FILES if (ROOT/name).is_file())
    with ZipFile(ARCHIVE,"w",ZIP_DEFLATED) as archive:
        for path in sorted(files): archive.write(path,path.relative_to(ROOT))
    print(f"Created {ARCHIVE.name} with {len(files)} source and data files.")


if __name__=="__main__": main()
