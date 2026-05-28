import re

def process_file(file_path, dry_run=False):
    """
    Removes blank lines inside Table 1-1. Terminology/Acronyms to ensure
    it renders as a single unified table.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    
    # Locate Table 1-1
    table_heading_idx = None
    for idx, line in enumerate(lines):
        if "Table 1-1. Terminology/Acronyms" in line:
            table_heading_idx = idx
            break

    if table_heading_idx is None:
        return 0

    # Locate where the actual table rows start
    table_start_idx = None
    for j in range(table_heading_idx + 1, len(lines)):
        if lines[j].strip().startswith("|"):
            table_start_idx = j
            break

    if table_start_idx is None:
        return 0

    # Scan and process lines in the table area
    new_lines = []
    # Add all lines before the table
    new_lines.extend(lines[:table_start_idx])

    removed_blank_count = 0
    in_table = True
    
    for j in range(table_start_idx, len(lines)):
        line = lines[j]
        stripped = line.strip()
        
        if in_table:
            if stripped.startswith("|"):
                new_lines.append(line)
            elif stripped == "":
                # Discard blank lines within the table block
                removed_blank_count += 1
            else:
                # We hit a non-table, non-blank line (end of table)
                in_table = False
                new_lines.append(line)
        else:
            new_lines.append(line)

    content_new = "\n".join(new_lines) + "\n"

    modified_count = 0
    if content != content_new:
        modified_count = removed_blank_count
        if not dry_run:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content_new)

    return modified_count
