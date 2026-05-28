import re

def process_file(file_path, dry_run=False):
    """
    Finds Table and Figure headings/captions and updates their heading levels (hashes)
    so they nest correctly as child headings under their preceding enumerated parent headings.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    parent_heading = None
    parent_level = 0
    modified_count = 0

    for idx, line in enumerate(lines):
        stripped = line.strip()
        
        # 1. Update parent heading level
        heading_match = re.match(r'^(#+)\s+(.*)$', stripped)
        if heading_match:
            level_str, content = heading_match.groups()
            level = len(level_str)
            content_stripped = content.strip()
            if not (content_stripped.startswith("Figure") or content_stripped.startswith("Table")):
                parent_heading = content_stripped
                parent_level = level
                
        # 2. Check if the line is a Figure or Table caption
        fig_tbl_match = re.match(r'^(#*)\s*(Figure|Table)\s+(\d+[-.]\d+)([\.:])\s*(.*)$', stripped)
        if fig_tbl_match:
            hashes, prefix, num, sep, rest = fig_tbl_match.groups()
            expected_level = min(parent_level + 1, 6)
            
            new_line = f"{'#' * expected_level} {prefix} {num}{sep} {rest}\n"
            
            if stripped != new_line.strip():
                modified_count += 1
                new_lines.append(new_line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    return modified_count
