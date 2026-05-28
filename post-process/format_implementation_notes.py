import re

def process_file(file_path, dry_run=False):
    """
    Finds "## IMPLEMENTATION NOTE" headings and their corresponding subheadings
    and converts them to bold text formatting instead of markdown headers.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = r"##\s*IMPLEMENTATION NOTE\s*\r?\n\s*\r?\n##\s*([^\n]+)"
    
    # Count matches
    matches = re.findall(pattern, content)
    modified_count = len(matches)

    if modified_count > 0 and not dry_run:
        new_content = re.sub(
            pattern,
            lambda m: f"**IMPLEMENTATION NOTE**\n\n**{m.group(1).strip()}**",
            content
        )
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    return modified_count
