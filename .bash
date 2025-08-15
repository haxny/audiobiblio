#System deps
#	•	ffmpeg (yt-dlp)
#	•	exiftool (tag-fixer)

#usage#
audioloader --help
tag-fixer --help

**`LICENSE`**  
#to be done#

**`Makefile`** (optional)
```makefile
.PHONY: venv install build clean

venv:
\tpython3 -m venv .venv

install:
\t. .venv/bin/activate; pip install -U pip build
\t. .venv/bin/activate; pip install -e .

build:
\tpython -m build

clean:
\trm -rf build dist *.egg-info __pycache__