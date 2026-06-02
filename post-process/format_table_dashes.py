import re

def process_separator_cell(cell):
    cell_strip = cell.strip()
    if not cell_strip:
        return " --- "
    
    # Check for colons (alignment indicators)
    has_left_colon = cell_strip.startswith(":")
    has_right_colon = cell_strip.endswith(":")
    
    if has_left_colon and has_right_colon:
        return " :---: "
    elif has_left_colon:
        return " :--- "
    elif has_right_colon:
        return " ---: "
    else:
        return " --- "

def process_file(file_path, dry_run=False):
    """
    Finds table separator rows (containing only pipes, dashes, colons, and spaces)
    and reduces the number of dashes in each column to exactly three (e.g. ---, :---, ---:, :---:).
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    modified_count = 0

    # Pattern to match a table separator line
    # Must start with | and end with | and only contain dashes, colons, spaces, and pipes
    separator_pattern = re.compile(r"^\|\s*[: -]+\s*(?:\|\s*[: -]+\s*)*\|$")

    for line in lines:
        if separator_pattern.match(line):
            # Split the line by pipes, but skip the first and last empty elements from split
            parts = line.split("|")
            cells = parts[1:-1]
            
            new_cells = [process_separator_cell(cell) for cell in cells]
            new_line = "|" + "|".join(new_cells) + "|"
            
            if new_line != line:
                new_lines.append(new_line)
                modified_count += 1
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

    return modified_count
