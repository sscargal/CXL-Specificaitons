def is_inside_code_block(lines):
    inside = [False] * len(lines)
    state = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            state = not state
            inside[i] = True
        else:
            inside[i] = state
    return inside

def process_file(file_path, dry_run=False):
    """
    Detects and rejoins paragraphs that were split across pages (creating
    unnecessary blank lines/newlines inside a single continuous sentence).
    """
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    inside_cb = is_inside_code_block(lines)
    new_lines = list(lines)
    fixed_indices = set()
    modified_count = 0

    # Iterate backwards up to len(lines) - 3
    for i in range(len(lines) - 3, -1, -1):
        if inside_cb[i] or inside_cb[i+1] or inside_cb[i+2]:
            continue
            
        line_curr = lines[i].rstrip('\r\n')
        line_next = lines[i+1].rstrip('\r\n')
        line_after = lines[i+2].rstrip('\r\n')
        
        if line_curr and not line_next and line_after:
            stripped_after = line_after.strip()
            stripped_curr = line_curr.strip()
            if not stripped_after or not stripped_curr:
                continue
                
            # Ignore markdown structures
            markdown_prefixes = ('#', '-', '*', '+', '|', '!', '[', '>', '1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.', '0.')
            if stripped_after.startswith(markdown_prefixes) or stripped_curr.startswith(markdown_prefixes):
                continue
                
            first_char = stripped_after[0]
            last_char = stripped_curr[-1]
            
            is_split = False
            
            if first_char.islower():
                is_split = True
            elif last_char in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,;-":
                words = stripped_curr.split()
                last_word = words[-1].lower() if words else ""
                conjunctions = ["the", "a", "an", "and", "or", "to", "of", "in", "on", "at", "by", "with", "from", "for", "as", "is", "are", "was", "were", "be", "been", "that", "this", "these", "those", "which", "who", "whom", "whose"]
                if last_word in conjunctions or last_char in ",-":
                    is_split = True
                    
            if is_split:
                # Special case for "Note:" issue
                if "LinkError, LinkReset," in line_curr and "Note:" in line_after:
                    new_lines[i] = line_curr + " " + lines[i+4].lstrip()
                    new_lines[i+1] = ""
                    new_lines[i+2] = ""
                    new_lines[i+3] = ""
                    new_lines[i+4] = ""
                    fixed_indices.add(i)
                    modified_count += 1
                    continue
                    
                if i in fixed_indices or (i+1) in fixed_indices or (i+2) in fixed_indices:
                    continue
                    
                if stripped_after.startswith("where "):
                    new_lines[i+1] = ""
                else:
                    new_lines[i] = line_curr + " " + line_after + "\n"
                    new_lines[i+1] = ""
                    new_lines[i+2] = ""
                    
                fixed_indices.add(i)
                modified_count += 1

    final_lines = []
    for line in new_lines:
        if line == "":
            continue
        final_lines.append(line)

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(final_lines)

    return modified_count
