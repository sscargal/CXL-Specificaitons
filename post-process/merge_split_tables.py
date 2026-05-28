import re

def process_file(file_path, dry_run=False):
    """
    Merges markdown tables that are split across multiple sections/pages,
    indicated by "(Sheet X of Y)" or similar notation in the headings.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    
    # We will parse the file and find sequences where we have a table,
    # followed by a heading containing "(Sheet X of Y)", followed by another table.
    # If the second table has the same columns (or is a continuation), we merge it.
    
    # Let's write a robust state-machine parser to merge them.
    i = 0
    modified_count = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Look for sheet headers like "Table 8-43 ... (Sheet 1 of 2)"
        sheet_match = re.search(r'^(#+)\s*(Table\s+\d+[-.]\d+.*?)\s*\(Sheet\s+(\d+)\s+of\s+(\d+)\)', line, re.IGNORECASE)
        if sheet_match:
            hashes, table_title, sheet_num, total_sheets = sheet_match.groups()
            sheet_num = int(sheet_num)
            
            if sheet_num == 1:
                # Keep the heading but strip the "(Sheet 1 of Y)" suffix
                clean_heading = f"{hashes} {table_title.strip()}"
                new_lines.append(clean_heading)
                i += 1
                modified_count += 1
                continue
            else:
                # This is a continuation sheet (e.g., Sheet 2 of 2).
                # We want to skip this heading and any intermediate blank lines/separators,
                # and skip the subsequent table header row + separator row,
                # appending only the data rows of this table to the previous table!
                
                # Let's skip blank lines to reach the table start
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("|"):
                    i += 1
                
                # We should be at the table header row: e.g. "| Col1 | Col2 |"
                if i < len(lines) and lines[i].strip().startswith("|"):
                    # Skip the header row
                    i += 1
                # Skip the separator row: e.g. "| --- | --- |"
                if i < len(lines) and lines[i].strip().startswith("|"):
                    # Skip the separator row
                    i += 1
                
                # Now we append the subsequent data rows
                while i < len(lines) and lines[i].strip().startswith("|"):
                    new_lines.append(lines[i])
                    i += 1
                
                modified_count += 1
                continue
        
        new_lines.append(line)
        i += 1

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

    return modified_count
