import re

def process_file(file_path, dry_run=False):
    """
    Finds "## IMPLEMENTATION NOTE" headings and their corresponding subheadings
    and converts them to bold text formatting instead of markdown headers.
    Also replaces any remaining standalone or labeled "## IMPLEMENTATION NOTE" with "**IMPLEMENTATION NOTE**".
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    modified_count = 0

    # Pattern 1: Heading followed by subheading (e.g. ## IMPLEMENTATION NOTE \n\n ## Subheading)
    pattern1 = r"##\s*IMPLEMENTATION NOTE\s*\r?\n\s*\r?\n##\s*([^\n]+)"
    matches1 = re.findall(pattern1, content)
    modified_count += len(matches1)

    if len(matches1) > 0 and not dry_run:
        content = re.sub(
            pattern1,
            lambda m: f"**IMPLEMENTATION NOTE**\n\n**{m.group(1).strip()}**",
            content
        )

    # Pattern 2: Any remaining line starting with ## IMPLEMENTATION NOTE
    pattern2 = r"(?m)^##\s*IMPLEMENTATION NOTE\b"
    matches2 = re.findall(pattern2, content)
    modified_count += len(matches2)

    if len(matches2) > 0 and not dry_run:
        content = re.sub(
            pattern2,
            "**IMPLEMENTATION NOTE**",
            content
        )

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    return modified_count
