import re

def format_cell(cell):
    # 1. Format bullets '•'
    def bullet_repl(match):
        start = match.start()
        if cell[:start].strip() == "":
            return match.group(0)
        preceding = cell[:start].rstrip()
        if preceding.endswith("<br/>"):
            return match.group(0)
        return "<br/>•"

    cell = re.sub(r'\s*•', bullet_repl, cell)
    
    # 2. Format list dashes '-'
    replacements = [
        (r'(\b\w+\s*)-\s*Internal\b', r'\1<br/>-Internal'),
        (r'(\b\w+\s*)-\s*An error\b', r'\1<br/>- An error'),
        (r'(\s*:\s*)-\s*([0-9])', r'\1<br/>- \2'),
        (r'(\s+)-\s*([0-9]\s*=)', r'<br/>- \2'),
    ]
    
    for pattern, repl in replacements:
        cell = re.sub(pattern, repl, cell)
        
    return cell

def process_file(file_path, dry_run=False):
    """Processes a markdown file to format lists within tables."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    new_lines = []
    modified_count = 0

    for idx, line in enumerate(lines):
        if line.strip().startswith("|") and not line.strip().startswith("| ---"):
            parts = line.split("|")
            cells = parts[1:-1]
            new_cells = []
            changed = False
            for cell in cells:
                new_cell = format_cell(cell)
                if new_cell != cell:
                    changed = True
                new_cells.append(new_cell)
            
            if changed:
                new_line = "|" + "|".join(new_cells) + "|"
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
