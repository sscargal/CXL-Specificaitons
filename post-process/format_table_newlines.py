import re

def process_file(file_path, dry_run=False):
    """
    Finds broken tables with a blank newline between rows and removes the newline
    to merge the rows into a single table.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Pattern to find a table row followed by a blank line and another table row
    pattern = r"(\|[^\n]*\|)\s*\r?\n\s*\r?\n\s*(\|)"
    
    newline = "\r\n" if "\r\n" in content else "\n"
    replacement = rf"\1{newline}\2"

    modified_count = 0
    temp_content = content
    
    # Run in a loop to catch consecutive broken rows that might overlap in a single pass
    while True:
        matches = re.findall(pattern, temp_content)
        if not matches:
            break
        modified_count += len(matches)
        temp_content = re.sub(pattern, replacement, temp_content)

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(temp_content)

    return modified_count
