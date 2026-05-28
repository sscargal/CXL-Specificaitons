import re

SPLIT_MAPPINGS = {
    "3-21 3-22": [
        ("3-21", "BISnp Early Conflict", "139"),
        ("3-22", "BISnp Late Conflict", "140")
    ],
    "4-29 4-30": [
        ("4-29", "G1 - D2H Req + 2 D2H Rsp", "178"),
        ("4-30", "G2 - D2H Req + D2H Data Header + D2H Rsp", "178")
    ],
    "5-8 5-9": [
        ("5-8", "PM Abort before Downstream Port PM Acceptance", "239"),
        ("5-9", "Example of a PMNAK Flow", "240")
    ],
    "5-12 5-13": [
        ("5-12", "Both Upstream Port and Downstream Port Hide Recovery Transitions from ARB/MUX", "244"),
        ("5-13", "Both Upstream Port and Downstream Port Notify ARB/MUX of Recovery Transitions", "245")
    ],
    "7-8 7-9": [
        ("7-8", "Example of Simultaneous Boot after Binding", "292"),
        ("7-9", "Example of Binding and Unbinding of an SLD Port", "292")
    ],
    "7-13": [
        ("7-13", "Example of a CXL Switch after Binding of LD-ID 1 within Pooled Device", "297"),
        ("7-14", "Example of a CXL Switch after Binding of LD-IDs 0 and 1 within Pooled Device", "298")
    ],
    "7-21 7-22": [
        ("7-21", "Example of MLD Management Requiring Tunneling", "314"),
        ("7-22", "Tunneling Commands to an LD in an MLD", "327")
    ],
    "9-12 9-13": [
        ("9-12", "Physical Topology - Example", "603"),
        ("9-13", "Software View", "604")
    ],
    "9-15 9-16": [
        ("9-15", "CXL Link/Protocol Registers in a CXL Switch", "608"),
        ("9-16", "Example", "608")
    ],
    "9-22 9-23": [
        ("9-22", "Extent List Example (No Sharing)", "619"),
        ("9-23", "Shared Extent List Example", "619")
    ],
    "11-16 11-17": [
        ("11-16", "Inclusion of the PCRC Mechanism in the AES-GCM Advanced Decryption Function..", "674"),
        ("11-17", "MAC Epochs and MAC Transmission in Case of Back-to-Back Traffic (a) Earliest MAC Header Transmit (b) Latest MAC Header Transmit in the Presence of Multi-Data Header", "677")
    ],
    "11-21 11-22": [
        ("11-21", "Link Idle Case after Transmission of Aggregation Flit Count Number of Flits", "681"),
        ("11-22", "Various Interface Standards that are Referenced by this Specification and their Lineage", "686")
    ],
    "14-4 14-5": [
        ("14-4", "Example SHSW-FM Topology", "723"),
        ("14-5", "Example DHSW-FM Topology", "723")
    ]
}

def collect_headings_from_body(lines):
    """
    Scans the document lines (from line 2000 onwards) to collect all valid Figure headings starting with #.
    Returns a dictionary mapping fig_id -> fig_title.
    """
    headings_map = {}
    for line in lines[2000:]:
        stripped = line.strip()
        # Matches headings starting with one or more hashes, then Figure X-Y, then optional punctuation and title
        match = re.match(r"^#+\s*Figure\s+([A-Za-z\d]+-\d+)(?:[\.:\s]+)\s*(.*?)$", stripped)
        if match:
            fig_id = match.group(1).strip()
            fig_title = match.group(2).strip()
            
            # Skip if it looks like an embedded image link
            if fig_title.startswith("![") or fig_title.endswith(")") or "figures/" in fig_title:
                continue
                
            fig_title = fig_title.rstrip(".* ")
            if fig_id not in headings_map:
                headings_map[fig_id] = fig_title
                
    return headings_map

def process_file(file_path, dry_run=False):
    """
    Standardizes the "List of Figures" table by:
    1. Parsing all existing Figure IDs, Titles, and Pages from the current table.
    2. Scanning the entire document body for correct Figure headings starting with #.
    3. Reconstructing the List of Figures table, replacing the title of each entry with its
       correct heading from the document body if found, otherwise keeping the existing title.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    
    # We will locate the boundaries of the List of Figures table
    table_start_idx = None
    table_end_idx = None
    
    for idx, line in enumerate(lines):
        if line.strip().startswith("### List of Figures"):
            # The table starts a couple lines after this heading
            for j in range(idx + 1, len(lines)):
                if lines[j].strip().startswith("|"):
                    table_start_idx = j
                    break
            break
            
    if table_start_idx is not None:
        for j in range(table_start_idx, len(lines)):
            if not lines[j].strip().startswith("|") and lines[j].strip() != "":
                table_end_idx = j
                break
        if table_end_idx is None:
            table_end_idx = len(lines)
            
    if table_start_idx is None or table_end_idx is None:
        return 0

    # Parse and clean the table rows
    table_lines = lines[table_start_idx:table_end_idx]
    rows = []
    
    for line in table_lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4:
            col_fig = parts[1]
            col_title = parts[2]
            col_page = parts[3]
            if col_fig == "Figure" or col_fig.startswith("---"):
                continue
            rows.append({
                'fig': col_fig,
                'title': col_title,
                'page': col_page
            })

    cleaned_rows = []
    for r in rows:
        col_fig = r['fig']
        col_title = r['title']
        col_page = r['page']
        
        # Step 1: Extract Figure ID from Title if Figure is empty (includes A-1, B-2 etc.)
        if not col_fig:
            fig_match = re.match(r"^((?:[A-Za-z\d]+-[A-Za-z0-9]+)(?:\s+[A-Za-z\d]+-[A-Za-z0-9]+)*)\b\s*(.*)$", col_title)
            if fig_match:
                col_fig = re.sub(r"\s+", " ", fig_match.group(1).strip())
                col_title = fig_match.group(2).strip()
        else:
            # Handle edge case where a misplaced pipe put part of the title into the Figure column
            # (e.g. "2-8 Head-to | -LD Mapping...")
            fig_match = re.match(r"^([A-Za-z\d]+-[A-Za-z0-9]+)\s+(.+)$", col_fig)
            if fig_match:
                real_fig = fig_match.group(1)
                extra_title = fig_match.group(2)
                if col_title.startswith("-"):
                    col_title = extra_title + col_title
                else:
                    col_title = extra_title + " " + col_title
                col_title = re.sub(r"\s+", " ", col_title).strip()
                col_fig = real_fig
                
        # Step 2: Extract page numbers from Title
        page_match = re.search(r"(\d+(?:\s+\d+)*)\s*$", col_title)
        if page_match:
            extracted_pages = page_match.group(1).split()
            col_title_cleaned = re.sub(r"\s*\d+(?:\s+\d+)*\s*$", "", col_title)
            col_title_cleaned = re.sub(r"(\D+)\d+$", r"\1", col_title_cleaned).strip()
            
            is_valid_page = True
            for term in ["Type", "x", "Slot", "L", "Channel", "Revision", "Phase", "Algorithm"]:
                if col_title_cleaned.endswith(term):
                    is_valid_page = False
                    break
            
            if is_valid_page:
                col_title = col_title_cleaned
                existing_pages = col_page.split() if col_page else []
                all_pages = []
                for p in extracted_pages:
                    if p not in all_pages:
                        all_pages.append(p)
                for p in existing_pages:
                    if p not in all_pages:
                        all_pages.append(p)
                try:
                    all_pages = sorted(list(set(all_pages)), key=int)
                except ValueError:
                    all_pages = list(set(all_pages))
                
                if len(all_pages) == 1:
                    col_page = all_pages[0]
                elif len(all_pages) > 1:
                    try:
                        ints = [int(p) for p in all_pages]
                        if all(ints[i] + 1 == ints[i+1] for i in range(len(ints)-1)):
                            col_page = f"{ints[0]}-{ints[-1]}"
                        else:
                            col_page = ", ".join(all_pages)
                    except ValueError:
                        col_page = ", ".join(all_pages)
                        
        cleaned_rows.append({
            'fig': col_fig,
            'title': col_title,
            'page': col_page
        })

    # Step 3: Handle the split mappings and avoid duplicate rows
    final_rows = []
    
    # Exact titles of continuation lines to skip to avoid duplicates after splits
    SKIP_TITLES = {
        "MAC Epochs and MAC Transmission in Case of Back-to-Back Traffic (a) Earliest MAC Header Transmit (b) Latest MAC Header Transmit in the Presence of Multi-Data Header",
        "Various Interface Standards that are Referenced by this Specification and their 686 681 Lineage",
        "One Links"
    }

    for r in cleaned_rows:
        fig = r['fig']
        title = r['title']
        page = r['page']
        
        if fig in SPLIT_MAPPINGS:
            for f_id, f_title, f_page in SPLIT_MAPPINGS[fig]:
                final_rows.append({
                    'fig': f_id,
                    'title': f_title,
                    'page': f_page
                })
        else:
            # Skip if this is a known continuation/duplicate line
            if title in SKIP_TITLES:
                continue
            final_rows.append(r)

    # Scan document body to get all correct Figure headings starting with #
    headings_map = collect_headings_from_body(lines)
    
    # Cross reference the parsed rows against heading titles
    for r in final_rows:
        fig_id = r['fig']
        if fig_id in headings_map:
            # Replace the title with the correct verified heading title from the document body!
            r['title'] = headings_map[fig_id]

    # Check for gaps and non-sequential ordering for reporting
    chapters = {}
    for r in final_rows:
        fig_id = r['fig']
        match = re.match(r"^([A-Za-z\d]+)-(\d+)$", fig_id)
        if match:
            ch = match.group(1)
            num = int(match.group(2))
            if ch not in chapters:
                chapters[ch] = []
            chapters[ch].append((num, fig_id))
            
    gaps_detected = []
    non_seq_detected = []
    
    for ch, fig_list in chapters.items():
        for i in range(len(fig_list) - 1):
            if fig_list[i][0] >= fig_list[i+1][0]:
                non_seq_detected.append(f"Non-sequential order in Chapter {ch}: {fig_list[i][1]} appears before {fig_list[i+1][1]}")
        if fig_list:
            nums = [item[0] for item in fig_list]
            min_num = min(nums)
            max_num = max(nums)
            all_expected = set(range(min_num, max_num + 1))
            missing = sorted(list(all_expected - set(nums)))
            if missing:
                for m in missing:
                    gaps_detected.append(f"Missing Figure: {ch}-{m}")
                    
    if gaps_detected or non_seq_detected:
        print("\n\033[93m[WARNING] Figure Table Integrity Report:\033[0m")
        if gaps_detected:
            print("\033[93m  Detected Gaps (Missing Figures):\033[0m")
            for gap in gaps_detected:
                print(f"    - {gap}")
        if non_seq_detected:
            print("\033[93m  Detected Non-sequential Orderings:\033[0m")
            for non_seq in non_seq_detected:
                print(f"    - {non_seq}")
        print("")

    # Construct the final markdown table string
    table_lines_new = [
        "| Figure | Title | PDF Page |",
        "| --- | --- | --- |"
    ]
    for r in final_rows:
        table_lines_new.append(f"| {r['fig']} | {r['title']} | {r['page']} |")

    # Combine back into the file content
    content_new = "\n".join(lines[:table_start_idx]) + "\n" + "\n".join(table_lines_new) + "\n" + "\n".join(lines[table_end_idx:]) + "\n"
    
    modified_count = 0
    if content != content_new:
        modified_count = 1
        if not dry_run:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content_new)
                
    return modified_count
