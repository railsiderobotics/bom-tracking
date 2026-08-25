import io
import re
import pandas as pd

GENERAL_HEADERS = [
    "Qty", "Item", "Ordering Specs", "Link", "Vendor/Source", "Other Vendor",
    "Event/Category", "If Other Category", "Unit Cost", "Total Cost",
    "Physical/Not Physical", "Comments",
]

BOT_HEADERS = [
    "Category", "Qty", "Item", "Ordering Specs", "Link", "Vendor/Source",
    "Other Vendor", "Package Cost", "Total Cost", "Provided by Railside?",
    "Subsystem", "Units per Package", "Units per Bot", "Unit/Bot Cost", "Comments",
]


def _norm(s):
    if s is None:
        return ""
    s_clean = re.sub(r"[^\w\s]", "", str(s)).lower()
    return re.sub(r"\s+", " ", s_clean).strip()


def _cell(grid, r, c):
    if r < len(grid) and c < len(grid[r]):
        val = grid[r][c]
        return "" if val is None else str(val)
    return ""


def load_grid(filename, file_bytes):
    lower = filename.lower()
    if lower.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=str, keep_default_na=False)
    elif lower.endswith(".xlsx") or lower.endswith(".xls"):
        df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str, engine=None)
        df = df.fillna("")
    else:
        raise ValueError("Unsupported file type. Please upload a .csv or .xlsx export of the template.")
    grid = df.astype(str).values.tolist()
    return [["" if str(v).strip().lower() == "nan" else v for v in row] for row in grid]


def detect_template(grid):
    details = {"general_missing": [], "bot_missing": []}
    
    best_match_type = None
    best_header_row = -1
    max_matches = -1

    for r_idx in range(min(5, len(grid))):
        row_norm = [_norm(_cell(grid, r_idx, c)) for c in range(len(grid[r_idx]))]
        
        bot_matches = sum(1 for h in BOT_HEADERS if _norm(h) in row_norm)
        gen_matches = sum(1 for h in GENERAL_HEADERS if _norm(h) in row_norm)
        
        if bot_matches > max_matches:
            max_matches = bot_matches
            best_match_type = "bot"
            best_header_row = r_idx
            
        if gen_matches > max_matches:
            max_matches = gen_matches
            best_match_type = "general"
            best_header_row = r_idx

    if max_matches >= 3:
        return best_match_type, {"header_row": best_header_row}

    return None, details


def _parse_money(v):
    if v is None:
        return 0.0, False
    s = str(v).replace("$", "").replace(",", "").strip()
    if s == "":
        return 0.0, False
    try:
        return float(s), True
    except ValueError:
        return 0.0, False


def _parse_qty(v):
    s = str(v).replace(",", "").strip()
    if s == "":
        return 0, False
    try:
        f = int(float(s))
        return f, f > 0
    except ValueError:
        return 0, False


def _truthy(v):
    return str(v).strip().upper() in ("TRUE", "1", "YES", "Y")


def make_match_key(item, specs, vendor):
    item_clean = _norm(item)
    specs_clean = _norm(specs)
    vendor_clean = _norm(vendor)
    return f"{item_clean}||{specs_clean}||{vendor_clean}"


def find_col_index(grid, header_row, target_name):
    target_norm = _norm(target_name)
    for c in range(len(grid[header_row])):
        if _norm(_cell(grid, header_row, c)) == target_norm:
            return c
    return -1


def parse_general_rows(grid):
    _, details = detect_template(grid)
    header_row = details.get("header_row", 1)
    
    items = []
    for r in range(header_row + 1, len(grid)):
        item_name = _norm(_cell(grid, r, 1))
        if not item_name:
            continue  
        qty, qty_ok = _parse_qty(_cell(grid, r, 0))
        specs = _norm(_cell(grid, r, 2))
        link = _norm(_cell(grid, r, 3))
        vendor = _norm(_cell(grid, r, 4))
        other_vendor = _norm(_cell(grid, r, 5))
        event_cat = _norm(_cell(grid, r, 6))
        other_cat = _norm(_cell(grid, r, 7))
        unit_cost, unit_cost_ok = _parse_money(_cell(grid, r, 8))
        total_cost, total_cost_ok = _parse_money(_cell(grid, r, 9))
        physical = _cell(grid, r, 10)
        comments = _norm(_cell(grid, r, 11))

        resolved_vendor = other_vendor if vendor.lower() == "other" and other_vendor else vendor
        vendor_lower = resolved_vendor.lower()
        category = other_cat if event_cat.lower() == "other" and other_cat else event_cat
        cat_lower = category.lower()
        item_lower = item_name.lower()
        
        is_railside = "railside" in vendor_lower
        is_filament = "filament" in item_lower or "filament" in cat_lower

        orderable = 1
        flags = []

        if not qty_ok:
            flags.append("Missing or invalid quantity")
        if vendor.lower() == "other" and not other_vendor:
            flags.append("Vendor set to 'Other' but no vendor name given")
        if not total_cost_ok and unit_cost_ok:
            total_cost = unit_cost * qty
        if not total_cost_ok and not unit_cost_ok:
            flags.append("Missing cost information")

        if is_filament:
            orderable = 0
        elif is_railside:
            orderable = 1
        else:
            if not link:
                flags.append("Missing link")

        items.append({
            "row_index": r + 1,
            "category": category,
            "qty": qty,
            "item": item_name,
            "specs": specs,
            "link": link,
            "vendor": vendor,
            "other_vendor": other_vendor,
            "resolved_vendor": resolved_vendor if resolved_vendor else ("Railside Stock" if is_railside else "Unspecified"),
            "event_category": event_cat,
            "other_category": other_cat,
            "unit_cost": unit_cost,
            "total_cost": total_cost,
            "physical": "Physical" if _truthy(physical) else "Not Physical",
            "subsystem": "N/A",
            "units_per_package": None,
            "units_per_bot": None,
            "comments": comments,
            "orderable": orderable,
            "match_key": make_match_key(item_name, specs, resolved_vendor),
            "flagged": "" if is_filament else "; ".join(flags),
        })
    return items


def parse_bot_rows(grid):
    _, details = detect_template(grid)
    header_row = details.get("header_row", 2)

    c_cat = find_col_index(grid, header_row, "Category")
    c_qty = find_col_index(grid, header_row, "Qty")
    c_item = find_col_index(grid, header_row, "Item")
    c_specs = find_col_index(grid, header_row, "Ordering Specs")
    c_link = find_col_index(grid, header_row, "Link")
    c_vendor = find_col_index(grid, header_row, "Vendor/Source")
    c_oth_vendor = find_col_index(grid, header_row, "Other Vendor")
    c_pkg_cost = find_col_index(grid, header_row, "Package Cost")
    c_tot_cost = find_col_index(grid, header_row, "Total Cost")
    c_railside = find_col_index(grid, header_row, "Provided by Railside?")
    c_subsystem = find_col_index(grid, header_row, "Subsystem")
    c_upp = find_col_index(grid, header_row, "Units per Package")
    c_upb = find_col_index(grid, header_row, "Units per Bot")
    c_comments = find_col_index(grid, header_row, "Comments")

    items = []
    for r in range(header_row + 1, len(grid)):
        item_name = _norm(_cell(grid, r, c_item if c_item != -1 else 2))
        if not item_name:
            continue
        
        qty, qty_ok = _parse_qty(_cell(grid, r, c_qty if c_qty != -1 else 1))
        specs = _norm(_cell(grid, r, c_specs if c_specs != -1 else 3))
        link = _norm(_cell(grid, r, c_link if c_link != -1 else 4))
        vendor = _norm(_cell(grid, r, c_vendor if c_vendor != -1 else 5))
        other_vendor = _norm(_cell(grid, r, c_oth_vendor if c_oth_vendor != -1 else 6))
        package_cost, package_cost_ok = _parse_money(_cell(grid, r, c_pkg_cost if c_pkg_cost != -1 else 7))
        total_cost, total_cost_ok = _parse_money(_cell(grid, r, c_tot_cost if c_tot_cost != -1 else 8))
        railside = _cell(grid, r, c_railside if c_railside != -1 else 9)
        subsystem = _norm(_cell(grid, r, c_subsystem if c_subsystem != -1 else 10)) or "N/A"
        units_per_package, _ = _parse_qty(_cell(grid, r, c_upp if c_upp != -1 else 11))
        units_per_bot, _ = _parse_qty(_cell(grid, r, c_upb if c_upb != -1 else 12))
        comments = _norm(_cell(grid, r, c_comments if c_comments != -1 else 14))

        resolved_vendor = other_vendor if vendor.lower() == "other" and other_vendor else vendor
        
        vendor_lower = resolved_vendor.lower()
        item_lower = item_name.lower()
        is_railside = "railside" in vendor_lower or _truthy(railside)
        is_filament = "filament" in item_lower

        orderable = 1 if _truthy(railside) else 0
        if is_filament:
            orderable = 0

        flags = []
        if not qty_ok:
            flags.append("Missing or invalid quantity")
        if orderable and not resolved_vendor:
            flags.append("Missing vendor/source")
        if vendor.lower() == "other" and not other_vendor:
            flags.append("Vendor set to 'Other' but no vendor name given")
        if not total_cost_ok and package_cost_ok:
            total_cost = package_cost * qty
        if orderable and not total_cost_ok and not package_cost_ok:
            flags.append("Missing cost information")
        if not is_railside and not link and orderable and not is_filament:
            flags.append("Missing link")

        items.append({
            "row_index": r + 1,
            "category": _norm(_cell(grid, r, c_cat if c_cat != -1 else 0)),
            "qty": qty,
            "item": item_name,
            "specs": specs,
            "link": link,
            "vendor": vendor,
            "other_vendor": other_vendor,
            "resolved_vendor": resolved_vendor,
            "event_category": "",
            "other_category": "",
            "unit_cost": package_cost,
            "total_cost": total_cost,
            "physical": "",
            "subsystem": subsystem,
            "units_per_package": units_per_package,
            "units_per_bot": units_per_bot,
            "comments": comments,
            "orderable": orderable,
            "match_key": make_match_key(item_name, specs, resolved_vendor),
            "flagged": "" if is_filament else "; ".join(flags),
        })
    return items