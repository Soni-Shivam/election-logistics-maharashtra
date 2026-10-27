# Election logistics technical report

The standalone LaTeX source is `election_logistics_technical_report.tex`.
The final PDF is at `output/pdf/election_logistics_technical_report.pdf`
relative to the project root.

The report reconstructs the project's progression, methods and saved results.
It distinguishes superseded experiments, current models, saved runs and
remaining work. It includes the complete map, location/routing, queue,
base-seat, legacy booth-MIP and apportionment result registers.

## Regenerate the document

From the project root:

```bash
python3 docs/technical_report/verify_evidence.py
python3 docs/technical_report/build_report.py
mkdir -p tmp/pdfs output/pdf
pdflatex -interaction=nonstopmode -halt-on-error \
  -output-directory=tmp/pdfs \
  docs/technical_report/election_logistics_technical_report.tex
```

Repeat the LaTeX command until table widths and references stabilize, normally
three passes on a fresh build. Copy the final PDF from `tmp/pdfs/` to `output/pdf/`.
Required LaTeX packages include Latin Modern, geometry, microtype, amsmath,
booktabs, longtable, TikZ, pgfplots, fancyhdr, titlesec, enumitem and hyperref.
All maps and charts are embedded vector drawings; no external image or
bibliography files are needed to compile the `.tex` file.

The report builder uses Python's standard library and reads only the frozen
`evidence/` directory. It does not rerun or change any election model. The
verification script also checks whether detailed site/assignment files are
missing from the original workspace; that observation can change if those files
are restored later.

## Provenance

- `evidence/`: 267 frozen source/result artifacts, including the original
  presentations and notebook, selected code, summary data and eight map files.
- `source_manifest.json`: SHA-256 hashes and the repository state when captured.
- `report_audit.json`: base-scenario solver/coverage and count diagnostics.
- `verification_results.json`: independent aggregate and PC-feasibility checks.

The evidence snapshot is revision `a0dfaa0` on 27 October 2026. Raw downloads,
network binaries, licence files and credentials are not included. Source data
retains its original licensing; the project identifies SHRUG-derived data as
CC BY-NC-SA 4.0.

The built-in editor compiler failed to launch in this session because its
sandbox executable was missing. Local TeX Live successfully compiles the source.
