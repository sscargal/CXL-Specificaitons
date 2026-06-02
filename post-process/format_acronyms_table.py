import re

def process_file(file_path, dry_run=False):
    """
    Combines all sheets within the Terminology/Acronyms section into a single table
    and reduces the number of dashes to three in the header separator.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    
    first_sheet_idx = None
    last_row_idx = None
    all_rows = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        if "Table 1-1. Terminology/Acronyms (Sheet" in line:
            if first_sheet_idx is None:
                first_sheet_idx = i
            
            # Skip the heading line
            i += 1
            # Skip blank lines
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            
            # Skip table header row
            if i < len(lines) and lines[i].strip().startswith("|"):
                i += 1
            # Skip table separator row
            if i < len(lines) and lines[i].strip().startswith("|"):
                i += 1
            
            # Collect data rows
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                # Ensure we don't accidentally collect headers/separators
                if "Term/Acronym" not in row and not all(c in '|- ' for c in row):
                    all_rows.append(lines[i])
                i += 1
                
            last_row_idx = i
            continue
            
        i += 1
        
    if first_sheet_idx is None or not all_rows:
        return 0

    # Construct the combined table with three dashes in the header
    combined_table_lines = [
        "#### Table 1-1. Terminology/Acronyms",
        "",
        "| Term/Acronym | Definition |",
        "| --- | --- |"
    ] + all_rows
    
    # Replace the entire span from the start of Sheet 1 to the end of Sheet 11
    new_lines = lines[:first_sheet_idx] + combined_table_lines + lines[last_row_idx:]
    new_content = "\n".join(new_lines) + "\n"
    
    modified_count = 0
    if content != new_content:
        modified_count = len(all_rows)
        if not dry_run:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
                
    return modified_count
