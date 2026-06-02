import re

def process_file(file_path, dry_run=False):
    """
    Combines the "Revision History" section into a single table.
    If the same "Version" (Revision) spans multiple tables, combines them.
    Keeps all content, removes duplicated headings, and reduces the number of
    '---' characters in the separator header to the minimal of 3.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find the Revision History section
    section_match = re.search(r"(## Revision History\s*\n+)(.*?)(?=\n+## |\Z)", content, re.DOTALL)
    if not section_match:
        return 0

    section_header = section_match.group(1)
    section_body = section_match.group(2)

    # Parse all rows across all tables in this section
    rows = []
    # Find all table rows (lines starting with |)
    for line in section_body.splitlines():
        line_stripped = line.strip()
        if not line_stripped.startswith("|"):
            continue
        
        parts = [p.strip() for p in line_stripped.split("|")]
        # A valid row must have at least the columns (Revision, Description, Date)
        if len(parts) < 4:
            continue
        
        col_rev = parts[1]
        col_desc = parts[2]
        col_date = parts[3]
        
        # Skip header rows and separator rows
        if col_rev.lower() == "revision" or all(c == '-' or c == ' ' for c in col_rev):
            continue
            
        rows.append({
            "revision": col_rev,
            "description": col_desc,
            "date": col_date
        })

    if not rows:
        return 0

    # Combine rows with the same Revision/Version
    combined_rows = []
    seen_revisions = {} # maps revision -> index in combined_rows

    for r in rows:
        rev = r["revision"]
        desc = r["description"]
        date = r["date"]
        
        if rev in seen_revisions:
            idx = seen_revisions[rev]
            existing = combined_rows[idx]
            # Combine description with <br/> if not empty
            if existing["description"] and desc:
                existing["description"] += "<br/>" + desc
            elif desc:
                existing["description"] = desc
            
            # Prefer non-empty date, or keep existing
            if not existing["date"] and date:
                existing["date"] = date
        else:
            seen_revisions[rev] = len(combined_rows)
            combined_rows.append({
                "revision": rev,
                "description": desc,
                "date": date
            })

    # Format dashed items in descriptions
    for r in combined_rows:
        desc = r["description"]
        def dashed_repl(match):
            full_match = match.group(0)
            start_idx = match.start()
            preceding = desc[:start_idx].rstrip()
            following = desc[start_idx + len(full_match):].lstrip()
            
            # Skip if it is a link state range like L1.1 - L1.4
            if re.search(r'L\d+(\.\d+)?$', preceding) and re.match(r'^L\d+(\.\d+)?\b', following):
                return full_match
                
            if preceding.endswith("<br/>") or preceding.endswith("<br />") or preceding.endswith("<br>") or preceding == "":
                return full_match
            return "<br/>" + full_match.lstrip()
        r["description"] = re.sub(r'\s*-\s+', dashed_repl, desc)

    # Construct the single new table
    new_table_lines = [
        "| Revision | Description | Date |",
        "| --- | --- | --- |"
    ]
    for r in combined_rows:
        new_table_lines.append(f"| {r['revision']} | {r['description']} | {r['date']} |")

    new_section_body = "\n".join(new_table_lines) + "\n"
    new_section_content = section_header + new_section_body

    # Replace the old section in the content
    new_content = content.replace(section_match.group(0), new_section_content)

    modified_count = 0
    if content != new_content:
        modified_count = 1
        if not dry_run:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

    return modified_count
