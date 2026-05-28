def process_file(file_path, dry_run=False):
    """
    Removes leading and trailing whitespace from every cell in all markdown tables.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    modified_count = 0

    for idx, line in enumerate(lines):
        if line.strip().startswith("|"):
            # Check if it is a separator row e.g., | --- | --- |
            if "---" in line:
                # Normalize separator row spacing for clean aesthetics
                parts = line.split("|")
                new_parts = [parts[0]]
                for part in parts[1:-1]:
                    new_parts.append(" " + part.strip() + " ")
                new_parts.append(parts[-1])
                new_line = "|".join(new_parts)
            else:
                parts = line.split("|")
                new_parts = [parts[0]]
                for part in parts[1:-1]:
                    new_parts.append(" " + part.strip() + " ")
                new_parts.append(parts[-1])
                new_line = "|".join(new_parts)
            
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
