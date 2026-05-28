import os
import re
import shutil

def process_file(file_path, dry_run=False):
    """
    Standardizes figure/table nomenclature, renames corresponding files in the
    figures/ directory, and updates image link references across the document.
    """
    dir_name = os.path.dirname(file_path)
    figures_dir = os.path.join(dir_name, "figures")
    
    if not os.path.exists(file_path):
        return 0

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    figure_title_re = re.compile(r'^(?:#+\s+)?Figure\s+([A-Za-z0-9]+-[A-Za-z0-9]+)\.\s*(.*)$')
    table_title_re = re.compile(r'^(?:#+\s+)?Table\s+([A-Za-z0-9]+-[A-Za-z0-9]+)\.\s*(.*)$')
    image_tag_re = re.compile(r'!\[(.*?)\]\((figures/((?:image|figure|table)_\w+\.png))\)')

    renames = {}
    new_lines = list(lines)
    modified_count = 0

    for i, line in enumerate(lines):
        match = image_tag_re.search(line)
        if match:
            alt_text, full_path, filename = match.groups()
            
            found_title = None
            is_figure = True
            num = None
            desc = None
            
            # Look backwards up to 5 lines for a preceding Title
            for j in range(i - 1, max(-1, i - 6), -1):
                prev = lines[j].strip()
                fig_m = figure_title_re.match(prev)
                if fig_m:
                    found_title = prev
                    is_figure = True
                    num, desc = fig_m.groups()
                    break
                tab_m = table_title_re.match(prev)
                if tab_m:
                    found_title = prev
                    is_figure = False
                    num, desc = tab_m.groups()
                    break
            
            if found_title:
                prefix = "figure" if is_figure else "table"
                new_filename = f"{prefix}_{num}.png"
                new_path = f"figures/{new_filename}"
                new_alt = f"{prefix.capitalize()} {num}. {desc}"
                
                new_tag = f"![{new_alt}]({new_path})"
                new_lines[i] = image_tag_re.sub(new_tag, line)
                
                if filename != new_filename:
                    renames[filename] = new_filename
                    modified_count += 1

    if modified_count > 0:
        content = "".join(new_lines)
        for old_fn, new_fn in renames.items():
            content = content.replace(f"figures/{old_fn}", f"figures/{new_fn}")
            content = content.replace(old_fn, new_fn)
        
        if not dry_run:
            # Write updated specification markdown
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            # Perform actual file renames in the filesystem
            if os.path.exists(figures_dir):
                for old_fn, new_fn in renames.items():
                    old_full = os.path.join(figures_dir, old_fn)
                    new_full = os.path.join(figures_dir, new_fn)
                    if os.path.exists(old_full):
                        try:
                            shutil.move(old_full, new_full)
                        except Exception as e:
                            print(f"Warning: Could not move {old_full} to {new_full}: {e}")

    return modified_count
