import re

# Dictionary mapping placeholder "Table X" entries to their correct Table IDs, Titles, and Pages
PLACEHOLDER_MAPPINGS = {
    "Table 1": ("14-58", "Enable Flow Control Injection", "883"),
    "Table 2": ("14-59", "Flow Control Injection Response", "884"),
    "Table 3": ("14-68", "Inject ALMP Request", "886"),
    "Table 4": ("14-69", "Inject ALMP Response", "886"),
    "Table 5": ("14-70", "Ignore Received ALMP Request", "887"),
    "Table 6": ("14-71", "Ignore Received ALMP Response", "887"),
    "Table 7": ("14-72", "Inject Bit Error in Flit Request", "887"),
    "Table 8": ("14-73", "Inject Bit Error in Flit Response", "888"),
    "Table 9": ("14-74", "Memory Device Media Poison Injection Request", "888"),
    "Table 10": ("14-75", "Memory Device Media Poison Injection Response", "888"),
    "Table 11": ("14-76", "Memory Device LSA Poison Injection Request", "889"),
    "Table 12": ("14-77", "Memory Device LSA Poison Injection Response", "889"),
    "Table 13": ("14-78", "Inject Memory Device Health Enable Memory Device Health Injection", "889"),
    "Table 14": ("14-79", "Device Health Injection Response", "891"),
    "Table 15": ("A-1", "Accelerator Usage Taxonomy", "892"),
    "Table 16": ("C-1", "Field Encoding Abbreviations", "900"),
    "Table 17": ("C-2", "HDM-D/HDM-DB Memory Request", "902"),
    "Table 18": ("C-3", "HDM-D Request Forward Sub-table", "905"),
    "Table 19": ("C-4", "HDM-DB BISnp Flow", "907"),
    "Table 20": ("C-5", "HDM-H Memory Request", "909"),
    "Table 21": ("C-6", "HDM-D/HDM-DB Memory RwD", "910"),
    "Table 22": ("C-7", "HDM-H Memory RwD", "911")
}

SPLIT_MAPPINGS = {
    "7-65 7-66": [
        ("7-65", "Virtual CXL Switch Event Record Format", "348"),
        ("7-66", "MLD Port Event Records Payload", "349")
    ],
    "7-68 7-69": [
        ("7-68", "Fabric Segment Size Table", "358"),
        ("7-69", "Segment Table Intlv[3:0] Field Encoding", "358")
    ],
    "8-6": [
        ("8-6", "CXL Extensions DVSEC for Ports - Header", "388"),
        ("8-7", "GPF DVSEC for CXL Port - Header", "393")
    ],
    "8-7 8-8": [
        ("8-8", "GPF DVSEC for CXL Device - Header", "394")
    ]
}

def collect_headings_from_body(lines):
    """
    Scans the document lines (from line 1580 onwards) to collect all valid Table headings/entries.
    Returns a dictionary mapping table_id -> table_title.
    """
    headings_map = {}
    # Track which IDs were added via actual hash headings (#)
    hash_headings = set()
    
    for line in lines[1580:]:
        stripped = line.strip()
        is_hash = stripped.startswith("#")
        
        if is_hash:
            match = re.match(r"^#+\s*Table\s+([A-Za-z\d]+-[A-Za-z\d]+)(?:[\.:\s]+)\s*(.*?)$", stripped)
        else:
            # Require period or colon after the table ID for non-hash headings to ignore raw text references
            match = re.match(r"^Table\s+([A-Za-z\d]+-[A-Za-z\d]+)[\.:]\s*(.*?)$", stripped)
            
        if match:
            t_id = match.group(1).strip()
            t_title = match.group(2).strip()
            
            # Skip if it looks like an embedded image link or register details
            if t_title.startswith("![") or t_title.endswith(")") or "figures/" in t_title:
                continue
                
            t_title = re.sub(r"\s+", " ", t_title).strip()
            # Clean sheet suffixes
            t_title = re.sub(r"\s*\(\s*Sheet\s+\d+\s+of\s+\d+\s*\)", "", t_title, flags=re.IGNORECASE).strip()
            t_title = t_title.rstrip(".* ")
            
            # Priority: actual hash headings (#) override non-hash entries
            if is_hash:
                headings_map[t_id] = t_title
                hash_headings.add(t_id)
            else:
                if t_id not in headings_map or t_id not in hash_headings:
                    headings_map[t_id] = t_title
                    
    return headings_map

def table_sort_key(r):
    """
    Returns a sorting key to naturally order table rows:
    Chapters 1 to 14 first, followed by Appendices A, B, C.
    """
    tab = r['tab']
    parts = tab.split('-')
    if len(parts) == 2:
        ch = parts[0]
        num_str = parts[1]
        
        # Parse chapter prefix (digit or letter)
        if ch.isdigit():
            ch_val = int(ch)
        else:
            ch_val = 100 + (ord(ch[0].upper()) - ord('A'))
            
        # Parse table number
        try:
            num_val = int(num_str)
        except ValueError:
            num_val = 0
            
        return (ch_val, num_val)
    else:
        return (999, 0)

def process_file(file_path, dry_run=False):
    """
    Standardizes the "List of Tables" table by:
    1. Parsing all existing Table IDs, Titles, and Pages from the current table.
    2. Re-mapping placeholders and fixing split/duplicate rows.
    3. Scanning the entire document body for correct Table headings/labels.
    4. Sorting rows naturally according to document flow.
    5. Reconstructing the List of Tables table.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    
    # Locate boundaries of the List of Tables table
    table_start_idx = None
    table_end_idx = None
    
    for idx, line in enumerate(lines):
        if line.strip().startswith("### List of Tables"):
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
            col_tab = parts[1]
            col_title = parts[2]
            col_page = parts[3]
            if col_tab == "Table" or col_tab.startswith("---"):
                continue
            rows.append({
                'tab': col_tab,
                'title': col_title,
                'page': col_page
            })

    cleaned_rows = []
    for r in rows:
        col_tab = r['tab']
        col_title = r['title']
        col_page = r['page']
        
        # Step 1: Handle Placeholders "Table X"
        if col_tab in PLACEHOLDER_MAPPINGS:
            t_id, t_title, t_page = PLACEHOLDER_MAPPINGS[col_tab]
            col_tab = t_id
            col_title = t_title
            col_page = t_page
        # Step 2: Extract Table ID from Title if Table is empty (includes A-1, 1-1, etc.)
        elif not col_tab:
            tab_match = re.match(r"^((?:[A-Za-z\d]+-[A-Za-z0-9]+)(?:\s+[A-Za-z\d]+-[A-Za-z0-9]+)*)\b\s*(.*)$", col_title)
            if tab_match:
                col_tab = re.sub(r"\s+", " ", tab_match.group(1).strip())
                col_title = tab_match.group(2).strip()
        else:
            # Handle edge case where a misplaced pipe put part of the title into the Table column
            tab_match = re.match(r"^([A-Za-z\d]+-[A-Za-z0-9]+)\s+(.+)$", col_tab)
            if tab_match:
                real_tab = tab_match.group(1)
                extra_title = tab_match.group(2)
                if col_title.startswith("-"):
                    col_title = extra_title + col_title
                else:
                    col_title = extra_title + " " + col_title
                col_title = re.sub(r"\s+", " ", col_title).strip()
                col_tab = real_tab
                
        # Step 3: Extract trailing page numbers from Title
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
            'tab': col_tab,
            'title': col_title,
            'page': col_page
        })

    # Step 4: Handle Split Mappings and Deduplicate
    final_rows = []
    SKIP_TITLES = {
        "GPF DVSEC for CXL Port - Header",
        "GPF DVSEC for CXL Device - Header",
        "Segment Table Gran[3:0] Field Encoding"
    }

    for r in cleaned_rows:
        tab = r['tab']
        title = r['title']
        page = r['page']
        
        if tab in SPLIT_MAPPINGS:
            for t_id, t_title, t_page in SPLIT_MAPPINGS[tab]:
                final_rows.append({
                    'tab': t_id,
                    'title': t_title,
                    'page': t_page
                })
        else:
            if title in SKIP_TITLES:
                continue
            final_rows.append(r)

    # Scan document body to get all correct Table headings/captions
    headings_map = collect_headings_from_body(lines)
    
    # Cross reference the parsed rows against body table titles
    for r in final_rows:
        tab_id = r['tab']
        if tab_id in headings_map:
            # Replace the title with the correct verified heading title from the document body!
            r['title'] = headings_map[tab_id]

    # Sort rows naturally based on order of appearance (Chapters 1-14, then Appendices A, B, C)
    final_rows = sorted(final_rows, key=table_sort_key)

    # Check for gaps and non-sequential ordering for reporting
    chapters = {}
    for r in final_rows:
        tab_id = r['tab']
        match = re.match(r"^([A-Za-z\d]+)-(\d+)$", tab_id)
        if match:
            ch = match.group(1)
            num = int(match.group(2))
            if ch not in chapters:
                chapters[ch] = []
            chapters[ch].append((num, tab_id))
            
    gaps_detected = []
    non_seq_detected = []
    
    for ch, tab_list in chapters.items():
        for i in range(len(tab_list) - 1):
            if tab_list[i][0] >= tab_list[i+1][0]:
                non_seq_detected.append(f"Non-sequential order in Chapter {ch}: {tab_list[i][1]} appears before {tab_list[i+1][1]}")
        if tab_list:
            nums = [item[0] for item in tab_list]
            min_num = min(nums)
            max_num = max(nums)
            all_expected = set(range(min_num, max_num + 1))
            missing = sorted(list(all_expected - set(nums)))
            if missing:
                for m in missing:
                    gaps_detected.append(f"Missing Table: {ch}-{m}")
                    
    if gaps_detected or non_seq_detected:
        print("\n\033[93m[WARNING] Table List Integrity Report:\033[0m")
        if gaps_detected:
            print("\033[93m  Detected Gaps (Missing Tables):\033[0m")
            for gap in gaps_detected:
                print(f"    - {gap}")
        if non_seq_detected:
            print("\033[93m  Detected Non-sequential Orderings:\033[0m")
            for non_seq in non_seq_detected:
                print(f"    - {non_seq}")
        print("")

    # Construct the final markdown table string
    table_lines_new = [
        "| Table | Title | PDF Page |",
        "| --- | --- | --- |"
    ]
    for r in final_rows:
        table_lines_new.append(f"| {r['tab']} | {r['title']} | {r['page']} |")

    # Combine back into the file content
    content_new = "\n".join(lines[:table_start_idx]) + "\n" + "\n".join(table_lines_new) + "\n" + "\n".join(lines[table_end_idx:]) + "\n"
    
    modified_count = 0
    if content != content_new:
        modified_count = 1
        if not dry_run:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content_new)
                
    return modified_count
