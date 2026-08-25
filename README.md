# Team BOM Workspace

A shared Flask app for uploading team BOMs (Bill of Materials), auto-detecting whether
each upload is a **Bot BOM** or a **General Supplies & Projects BOM**, combining matching
items across every team's submission into one purchasing list, and breaking that list
down store-by-store for ordering.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** in a browser. No sign-in is required — this is a
single shared workspace, so everyone sees the same uploaded BOMs and combined order.

Data is stored in `instance/bom.db` (SQLite), which is created automatically on first
run and persists across restarts.

## How it works

### 1. Upload
One upload box accepts either template as a `.csv` (Google Sheets export) or `.xlsx`.
The app auto-detects the template by checking the sheet's structure:
- **General BOM**: the header row (row 2) must have `Comments` in column L, plus a
  strong match on the rest of the expected column headers.
- **Bot BOM**: the header row (row 3) must have `Comments` in column O, plus a strong
  match on the rest of the expected column headers.

If neither matches, the app shows exactly which headers were expected vs. found, so the
correct template can be downloaded and re-filled.

### 2. Bot BOM order eligibility
Every row from a Bot BOM is kept, but only rows where **"Provided by Railside?" is
checked** count toward the combined order, store totals, and exports. Unchecked rows
are preserved on a separate **Tracked Only** page — visible for reference, but they
never add to quantities or costs.

### 3. Review before saving
After upload, you get a preview of every row (with format warnings flagged) and a form
to attach a team name, project name, and optional notes before saving to the shared
workspace.

### 4. Combined ordering
Items are automatically combined when their **item name + ordering specs + vendor**
all match (kept separate otherwise). The Combined Order page supports search and
filtering by item, team, project, category, vendor, and BOM type, and can be exported
as CSV.

### 5. Manual matching
The **Manual Matching** page surfaces items that share a name but weren't
auto-combined (different specs/vendor/wording). Select rows that are actually the same
purchase and combine them by hand — the match persists across refreshes and can be
undone at any time.

### 6. Store / vendor view
The **By Store/Vendor** page groups the combined, orderable items by vendor, shows a
subtotal per store, and lets you export one CSV per store or one combined file for
everything.

### Uploaded BOM history
Every submission is listed with team, project, template type, upload date, item count,
and estimated cost. BOMs can be removed from combined ordering (and restored) without
losing the underlying data.

## Notes / open questions carried over from planning
- This build assumes **one shared workspace** (no sign-in / private team spaces).
- Both `.csv` (Google Sheets export) and `.xlsx` uploads are supported.
- Automatic Bot BOM matching combines on item name + ordering specs + vendor (not a
  separate part-number field, since the template doesn't include one) — use Manual
  Matching for any exceptions.
