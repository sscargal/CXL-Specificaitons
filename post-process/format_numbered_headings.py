import re

def process_file(file_path, dry_run=False):
    """
    Checks and formats numbered headings to ensure:
    1. There is a single space between the numerical identifier and the heading text.
    2. The heading level (number of hashes) matches its nesting level in the hierarchy,
       up to a maximum of H6.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    modified_count = 0

    for line in lines:
        # Match lines starting with optional whitespace, one or more hashes, 
        # optional whitespace, a numbered identifier (e.g., 14.11.3.11.3), optional whitespace, and text.
        match = re.match(r"^(\s*)(#+)\s*(\d+(?:\.\d+)+)(.*)$", line)
        if match:
            indent, hashes, num_id, rest = match.groups()
            cleaned_rest = rest.strip()
            
            # Skip if there is no actual text after the numbered identifier
            if not cleaned_rest:
                new_lines.append(line)
                continue
                
            # Determine correct heading level
            components = num_id.strip('.').split('.')
            if num_id.endswith('.0'):
                expected_level = 2
            else:
                expected_level = min(len(components) + 1, 6)
            
            expected_hashes = "#" * expected_level
            new_line = f"{indent}{expected_hashes} {num_id} {cleaned_rest}"
            
            if line != new_line:
                modified_count += 1
                if not dry_run:
                    new_lines.append(new_line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + ("\n" if content.endswith("\n") else ""))

    return modified_count
