import re

def process_file(file_path, dry_run=False):
    """
    Removes all occurrences of 'EVALUATION COPY' lines and '![EVALUATION COPY](figures/image_XXX.png)' images.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Remove standalone EVALUATION COPY lines (with or without markdown heading hashes)
    # Match lines containing only 'EVALUATION COPY' (possibly with leading/trailing spaces or markdown headers)
    pattern_standalone = r"^\s*#*\s*EVALUATION COPY\s*$"
    
    # 2. Remove EVALUATION COPY images
    pattern_image = r"^\s*!\[EVALUATION COPY\]\(figures/image_\d+\.png\)\s*$"

    # We will split into lines and process to accurately count and remove them, or use multiline regex.
    lines = content.splitlines()
    new_lines = []
    removed_count = 0

    for line in lines:
        stripped = line.strip()
        # Check standalone text
        if re.match(pattern_standalone, stripped):
            removed_count += 1
            continue
        # Check image markup
        if re.match(pattern_image, stripped):
            removed_count += 1
            continue
        new_lines.append(line)

    new_content = "\n".join(new_lines) + "\n"

    # Also check if there's any inline occurrences that got stuck in the middle of a sentence due to page splits
    # e.g., "EVALUATION COPY\n" in a paragraph, but let's be careful not to strip legitimate uses of "Evaluation Copy Agreement"
    # The prompt explicitly specifies: "remove all occurrences of `EVALUATION COPY` and `![EVALUATION COPY](figures/image_002.png)`."
    # Since we are removing standalone lines, that matches the typical header/footer layout.
    
    modified_count = 0
    if content != new_content:
        modified_count = removed_count
        if not dry_run:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

    return modified_count
