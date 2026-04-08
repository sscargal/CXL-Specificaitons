# pdf2md

Convert PDF documents to Markdown using pluggable converter backends. Designed for complex technical documents with tables, figures, diagrams, and formulas.

## Features

- **Pluggable converters** — currently supports [Docling](https://github.com/docling-project/docling), extensible for LlamaParse, MarkItDown, etc.
- **Multiple OCR engines** — RapidOCR (default) or EasyOCR
- **Smart image handling** — extracts figures to a clean `figures/` directory, renames to `figure_001.png`, `figure_002.png`, etc., and uses reference-style markdown links with auto-extracted captions as alt text
- **Batch processing** — single file, multiple files, or entire directories
- **Skip existing** — won't re-process files unless `--force` is used
- **Auto thread detection** — uses all available CPUs minus one
- **Graceful Ctrl-C** — finishes the current file, skips the rest, prints summary
- **Detailed summary** — file sizes, line counts, headings, tables, figures, throughput

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd pdf2md

# Install core dependencies (uses RapidOCR)
uv sync

# Include EasyOCR support (optional)
uv sync --extra easyocr
```

PyTorch is installed as a CPU-only build automatically via the configured index URL in `pyproject.toml`.

## Usage

```bash
# Single file
python pdf2md.py document.pdf

# Multiple files
python pdf2md.py doc1.pdf doc2.pdf doc3.pdf

# All PDFs in a directory
python pdf2md.py raw/

# Custom output directory
python pdf2md.py raw/ --output-dir processed/

# Use EasyOCR instead of RapidOCR
python pdf2md.py raw/ --ocr-engine easyocr

# Force re-processing of already converted files
python pdf2md.py raw/ --force

# Save conversion stats to JSON
python pdf2md.py raw/ -o processed/ --save-stats stats.json

# Verbose output (info logging)
python pdf2md.py raw/ -v

# Debug output
python pdf2md.py raw/ -vv
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `-o, --output-dir` | `<input_dir>/processed/<stem>/` | Base output directory |
| `-c, --converter` | `docling` | Converter backend |
| `--ocr-engine` | `rapidocr` | OCR engine: `rapidocr`, `easyocr`, `tesserocr`, `tesseract`, `auto` |
| `--num-threads` | auto (CPUs - 1) | Number of processing threads |
| `--timeout` | none | Per-document timeout in seconds |
| `--force` | off | Re-process files even if output exists |
| `--save-stats FILE` | none | Save conversion statistics to JSON |
| `-v` | off | Verbose logging (`-vv` for debug) |

## Output Structure

For each PDF, the converter produces:

```
processed/
  Document_Name/
    Document Name.md       # Markdown with reference-style image links
    figures/
      figure_001.png       # Figures numbered by order of appearance
      figure_002.png
      ...
```

### Image Handling

Figures are extracted and organized with clean, sequential naming. The markdown uses reference-style images with auto-extracted captions as alt text:

```markdown
![Figure 4: Remote Far Memory Usage Model Example][fig004]

<!-- Figure reference definitions -->
[fig004]: figures/figure_004.png "Figure 4"
```

- **Alt text** carries the figure description (accessible, AI-readable)
- **Title attribute** holds the figure number (visible on hover)
- **Reference definitions** are collected at the end of the document
- Captions are auto-extracted from nearby `Figure N. Caption` lines in the source

## Adding a New Converter

The script uses an extensible converter architecture. To add a new backend:

1. Create a subclass of `BaseConverter` in `pdf2md.py`
2. Implement the `name` property and `convert_file()` method
3. Optionally implement `check_dependencies()` for pre-flight checks
4. Register it in the `CONVERTERS` dict

```python
class MyConverter(BaseConverter):
    @property
    def name(self) -> str:
        return "myconverter"

    def convert_file(self, pdf_path: Path, output_dir: Path) -> ConversionStats:
        # Your conversion logic here
        ...

CONVERTERS["myconverter"] = MyConverter
```

## Performance Notes

Benchmarked on a CPU-only system (8 threads) with the CXL 1.1 Specification (69 pages, 6.7 MB):

| OCR Engine | Time | Notes |
|------------|------|-------|
| RapidOCR | ~8.7 min | Slightly better detail in register tables |
| EasyOCR | ~12.4 min | Comparable quality, slower |

RapidOCR is recommended for natively-rendered PDFs (not scanned). For scanned documents, EasyOCR may produce better results — use `--ocr-engine easyocr`.
