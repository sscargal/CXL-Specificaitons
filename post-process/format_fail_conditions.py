import re

def process_file(file_path, dry_run=False):
    """
    Finds lines containing '## Fail Conditions:' and replaces them with 'Fail Conditions:'
    (removing the heading hashes).
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Search for ## Fail Conditions: at the start of a line
    pattern = r"^##\s*Fail\s+Conditions\s*:"
    
    # We will search and replace line-by-line or with multi-line regex
    matches = re.findall(pattern, content, flags=re.MULTILINE)
    modified_count = len(matches)

    if modified_count > 0 and not dry_run:
        new_content = re.sub(pattern, "Fail Conditions:", content, flags=re.MULTILINE)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    return modified_count
