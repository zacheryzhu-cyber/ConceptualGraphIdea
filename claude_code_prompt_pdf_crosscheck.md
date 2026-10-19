# Claude Code prompt: check DEXPI pipe links against the COMOS vector PDF of the same sheet

**Before pasting:**
1. Apply `claude_code_prompt_fix_islands.md` first, then `claude_code_prompt_graph_levels.md`.
2. Copy into the project folder:
   - `pdf_crosscheck.py`: the working prototype. It's the reference implementation.
   - the H4 PDF (`p1_h4.pdf`): the test file.

Paste everything below the line into Claude Code, in the PoC project folder.

---

I want the vector PDF that COMOS exports for each P&ID sheet to act as an **auditor** for the pipe links read from the DEXPI file of the same sheet.
- **DEXPI stays the backbone:** assets, identity, classes, tags, loops, signals and pipe links.
- **The PDF only checks pipe connectivity:** it confirms links, proposes missing ones, and flags undrawn ones.

`pdf_crosscheck.py` in this folder is a tested prototype of the method. On sheet =P1.H4.FB.001 it confirmed all 34 DEXPI pipe links and found 2 missing ones. Turn it into a module and wire it into the app as described below.

## Ground rule: generic code only
- No hard-coded tags, IDs, sheet names, line widths or coordinates.
- Everything is derived from the DEXPI file and the PDF itself.
- Expected results for my files go in `tests/expected_pdf_crosscheck_h4.json` and are checked with pytest, never in the module.

## 1. Module `pdf_crosscheck.py` → `crosscheck/pdf_vs_dexpi.py`
Keep the prototype's method and make each step a function:

1. **Coordinate mapping**
   - Scale = PDF page width ÷ DEXPI `Drawing/Extent/Max/X` (points per mm). Flip y: `y_pdf = page_height − y_mm × scale`.
   - **Sanity check before anything else:** for every DEXPI object with a TagName that also appears once as a word in the PDF text layer, the mapped object outline must lie within ~15 mm of that word.
   - If fewer than 50% of these tags match, stop and report "PDF does not match this DEXPI sheet (different sheet, revision or scale)". Don't produce results. Measured on my files: the correct pairing (H4 PDF + H4 DEXPI) scores 64%, the wrong pairing (H4 PDF + H3 DEXPI) scores 0%. Some tags legitimately sit away from their symbol, so don't raise the threshold above 50%.

2. **Objects**
   - Use Equipment, PipingComponent and PipeOffPageConnector elements with an `Extent`, converted to PDF boxes.
   - **Skip everything inside `<ShapeCatalogue>`.** Those are symbol templates drawn at the origin, not plant items.
   - Skip equipment nested inside other equipment.
   - Expand each equipment's box to cover its nozzles (`Nozzle` and COMOS `Nozzles`).

3. **Pipe line style, calibrated per file**
   - Group the solid (not dashed) PDF line segments by stroke width.
   - Pick the width whose segment endpoints best match the DEXPI pipe route coordinates (`PipingNetworkSegment//Coordinate`, within 1.5 pt).
   - Log the score per width, so it can be checked. On H4 it is 0.96 pt (116 matches); 1.42 pt is equipment outlines.
   - Accept **every** width scoring at least 25% of the best score, not only the best one. Other projects may draw main and minor lines in different widths. On H4 only 0.96 qualifies: 0.71 scores 26 and 1.42 scores 23, both under 25% of 116.

4. **Drawn connectivity**
   - Use **Shapely** (STRtree spatial index, `intersects` / `distance`) and **NetworkX** (connected components) rather than the prototype's hand-written pairwise loops. The prototype compares every pair of segments, which is fine for one sheet (about 200 segments) but slow on dense sheets with thousands.
   - Join pipe segments whose endpoints touch, or where an endpoint lies on another segment (a T-junction, tolerance 1.5 pt), ignoring junction points inside an object box.
   - Each joined run connects every object box it touches (2 pt margin).
   - Also connect two objects whose boxes touch directly (e.g. a reducer drawn against a pump), **except** when either has no ComponentClass. Untyped items such as COMOS motors are drawn on top of their equipment; that's composition, not piping.

5. **DEXPI connectivity to compare:** chain `FromID → inline items → ToID` per PipingNetworkSegment, with nozzles resolved to their owner. Use the same rule as fix 5 in the island prompt; reuse that function rather than duplicating it.

6. **Result:**
   - three lists: `confirmed`, `drawn_only`, `dexpi_only`
   - the calibration log
   - the tag-match score from step 1

## 2. Writing results to the graph
For a sheet with a cross-check:
- **`confirmed`:** set `r.pdfCheck = 'confirmed'` on the existing PIPE relationship.
- **`drawn_only`:** create `PIPE {source: 'drawing', status: 'candidate', sheet, pdfFile}` between the two existing assets.
  - Candidates are **visible in the graph only when approved**, or when the Graph tab option "Show drawing candidates" is ticked. Draw them dashed orange.
- **`dexpi_only`:** set `r.pdfCheck = 'not_drawn'`.
- **Removing a sheet** removes its candidates and pdfCheck properties.
- **Re-running the check** replaces the previous results for that sheet. It must never duplicate links.
- **Detail levels (Process / Conceptual)** use approved candidates like any other PIPE, keeping `source: 'drawing'` in the link's `via`/tooltip.

## 3. App changes
- **Tab "1 · Load DEXPI":** an optional "Matching PDF (same sheet)" upload next to each DEXPI file.
  - Pair files automatically when the PDF's file name contains the sheet's drawing number or label. Otherwise let me pick the pairing in a dropdown.
  - After loading the DEXPI sheet, run the cross-check and show a one-line summary: "PDF check: 34 confirmed · 2 drawn only · 0 not drawn".
- **Tab "6 · Validation", new section "Drawing check":**
  - Per sheet: tag-match score, chosen pipe line width, and the three counts.
  - A table of `drawn_only` candidates (both asset names, classes, FullLabels) with **Approve** / **Reject** buttons.
    - Approve sets `status: 'approved'`.
    - Reject deletes the link and remembers the rejection on the Sheet node, so a re-run doesn't propose it again.
  - A table of `dexpi_only` links ("in DEXPI but not drawn").
  - The overlay picture: the PDF page rendered with confirmed links in green, candidates in red and object boxes outlined, as in the prototype.
- **Tab "3 · Plant":** per sheet, show whether it has a PDF check, and the number of open candidates.
- **Adjacency JSON export:** include `source`, `status` and `pdfCheck` on each relationship.

## 4. Acceptance test (expected results on my H4 files; check with pytest, never hard-code)
Reset. Load `=P1.H3.FB.001` (DEXPI only) and `=P1.H4.FB.001` (DEXPI + `p1_h4.pdf`). Expected:
- **Sanity check passes:** tag-match score of about 64% (at least 50%).
- **Pipe width chosen:** 0.96 pt.
- **Result:** 34 confirmed, 2 drawn only, 0 not drawn.
- **The 2 candidates** are pump `=P1.H4.T1P002` with reducer `=P1.H4.T1.P008.001R002`, and the same pump with reducer `=P1.H4.T1.P009.001R001`.
- **After approving both,** `check_islands.py` on the whole-plant export shows the pipe network of H3 + H4 as **one** connected group (before approval, the H4 column section is a separate group). The Conceptual view gains F001 → P002 (P1.H4.T1) → k001.
- **Wrong pairing rejected:** loading `p1_h4.pdf` as the PDF for the H3 sheet must fail the sanity check with the "does not match" message.
- **Generality:** H3 loaded without a PDF behaves exactly as before.

Show me the Validation tab with the overlay and the candidate table, and the Conceptual view before and after approving.
