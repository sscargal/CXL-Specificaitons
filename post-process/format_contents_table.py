import re
import os
import shutil
import sys
import argparse

def make_anchor(heading_text):
    """Generates a standard GitHub-style markdown anchor from heading text."""
    text = heading_text.lower()
    text = re.sub(r'[^a-z0-9\s\-]', '', text)
    text = re.sub(r'\s+', '-', text)
    return '#' + text.strip('-')

def collect_headings_from_body(lines):
    """
    Scans the document lines (from line 1000 onwards) to collect all valid
    section headings starting with # that represent numbered or appendix sections.
    Returns a list of dictionaries with section details in order.
    """
    headings = []
    # We can scan starting after the Contents section. Since Contents ends before line 1500,
    # scanning from line 1500 onwards is very safe.
    for idx, line in enumerate(lines[1500:]):
        stripped = line.strip()
        if not stripped.startswith('#'):
            continue
        h_match = re.match(r'^(#+)\s*(.*?)$', stripped)
        if not h_match:
            continue
        
        level = len(h_match.group(1))
        title = h_match.group(2).strip()
        
        # Match standard numbered headings or Appendix titles/sections
        # Group 1: section number, Group 2: title
        sec_match = re.match(r'^((?:\d+(?:\.\d+)+)|(?:\d+\.0)|(?:Appendix\s+[A-Z])|(?:[A-Z]\.\d+(?:\.\d+)*))\s+(.*?)$', title)
        if sec_match:
            sec_num = sec_match.group(1).strip()
            sec_title = sec_match.group(2).strip()
            headings.append({
                'line_num': 1500 + idx + 1,
                'level': level,
                'sec_num': sec_num,
                'title': sec_title,
                'full_heading': stripped
            })
            
    return headings

def parse_existing_toc(lines):
    """
    Locates the ## Contents section and parses the existing ToC table rows
    up to ### List of Figures.
    Returns (start_idx, end_idx, list of parsed rows).
    """
    table_start_idx = None
    table_end_idx = None
    
    for idx, line in enumerate(lines):
        if line.strip().startswith("## Contents"):
            for j in range(idx + 1, len(lines)):
                if lines[j].strip().startswith("|"):
                    table_start_idx = j
                    break
            break
            
    if table_start_idx is not None:
        for j in range(table_start_idx, len(lines)):
            if lines[j].strip().startswith("### List of Figures") or (not lines[j].strip().startswith("|") and lines[j].strip() != ""):
                table_end_idx = j
                break
        if table_end_idx is None:
            table_end_idx = len(lines)
            
    if table_start_idx is None or table_end_idx is None:
        return None, None, []
        
    toc_lines = lines[table_start_idx:table_end_idx]
    rows = []
    
    for idx, line in enumerate(toc_lines):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4:
            col_sec = parts[1]
            col_title = parts[2]
            col_page = parts[3]
            
            if col_sec == "Section" or col_sec.startswith("---"):
                continue
                
            # Extract clean section number and title from markdown links if they exist
            clean_sec = col_sec
            sec_link_match = re.match(r'^\[(.*?)\]\(.*?\)$', col_sec)
            if sec_link_match:
                clean_sec = sec_link_match.group(1).strip()
                
            clean_title = col_title
            title_link_match = re.match(r'^\[(.*?)\]\(.*?\)$', col_title)
            if title_link_match:
                clean_title = title_link_match.group(1).strip()
                
            rows.append({
                'line_offset': idx,
                'sec_num': clean_sec,
                'title': clean_title,
                'page': col_page,
                'original': line
            })
            
    return table_start_idx, table_end_idx, rows

def process_file(file_path, dry_run=False):
    """
    Main post-processing entry point. Standardizes the Table of Contents table.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    lines = content.splitlines()
    
    # 1. Parse existing ToC
    start_idx, end_idx, toc_rows = parse_existing_toc(lines)
    if start_idx is None or end_idx is None:
        print(f"\033[91m[Error] Could not find '## Contents' table in {file_path}\033[0m")
        return 0
        
    # 2. Collect headings from body
    headings = collect_headings_from_body(lines)
    if not headings:
        print(f"\033[91m[Error] No valid headings found in {file_path}\033[0m")
        return 0
        
    # Build maps for lookup
    # Map section number -> page number
    sec_to_page = {}
    sec_to_title = {}
    for r in toc_rows:
        sec = r['sec_num']
        if sec:
            sec_to_page[sec] = r['page']
            sec_to_title[sec] = r['title']
            
    # 3. Align and reconstruct ToC entries matching document headings order
    new_toc_rows = []
    skipped_count = 0
    missing_pages = []
    human_review = []
    non_sequential = []
    
    # Let's keep track of section numbers we've seen to check sequence
    last_sec_parts = []
    
    for h in headings:
        sec_num = h['sec_num']
        title = h['title']
        
        # Determine the page number from existing ToC
        page = sec_to_page.get(sec_num, "")
        if not page:
            missing_pages.append(f"Heading {sec_num} '{title}' (line {h['line_num']}) lacks a page number.")
            
        anchor = make_anchor(f"{sec_num} {title}")
        
        # Section and Title columns should be clickable
        sec_col = f"[{sec_num}]({anchor})"
        title_col = f"[{title}]({anchor})"
        
        # Check if we should skip already valid entries
        # A valid entry is one that already matches exactly
        existing_match = None
        for r in toc_rows:
            if r['sec_num'] == sec_num:
                existing_match = r
                break
                
        is_already_valid = False
        if existing_match:
            # Check if original line was already formatted perfectly
            expected_line = f"| {sec_col} | {title_col} | {page} |"
            if existing_match['original'].strip() == expected_line:
                is_already_valid = True
                skipped_count += 1
                
        new_toc_rows.append({
            'sec_num': sec_num,
            'sec_col': sec_col,
            'title_col': title_col,
            'page': page
        })
        
        # Sequence tracking
        # Convert e.g., "1.4.1" -> [1, 4, 1] for sequence comparison where possible
        match_digits = re.match(r'^(\d+(?:\.\d+)*)$', sec_num)
        if match_digits:
            parts = [int(x) for x in match_digits.group(1).split('.')]
            if last_sec_parts:
                # Compare prefixes or numbers to see if it went backwards
                # E.g. [1, 5] should not be followed by [1, 4]
                common_len = min(len(parts), len(last_sec_parts))
                if last_sec_parts[:common_len] == parts[:common_len]:
                    if len(parts) == len(last_sec_parts):
                        if parts[-1] < last_sec_parts[-1]:
                            non_sequential.append(f"Non-sequential: {sec_num} follows {'.'.join(str(x) for x in last_sec_parts)}")
                elif last_sec_parts[0] > parts[0]:
                    non_sequential.append(f"Non-sequential: {sec_num} follows {'.'.join(str(x) for x in last_sec_parts)}")
            last_sec_parts = parts

    # 4. Report issues
    # Report any entries in the original ToC that could not be matched
    body_sec_nums = {h['sec_num'] for h in headings}
    for r in toc_rows:
        if r['sec_num'] and r['sec_num'] not in body_sec_nums:
            human_review.append(f"Original ToC section '{r['sec_num']}' (Title: '{r['title']}', Page: '{r['page']}') could not be matched to any heading in the document.")

    if missing_pages or non_sequential or human_review:
        print("\n\033[93m[REPORT] Table of Contents Integrity Report:\033[0m")
        if non_sequential:
            print("\033[93m  Detected Non-sequential Headings:\033[0m")
            for item in non_sequential:
                print(f"    - {item}")
        if missing_pages:
            print("\033[93m  Detected Gaps / Headings with Missing Page Numbers:\033[0m")
            for item in missing_pages:
                print(f"    - {item}")
        if human_review:
            print("\033[93m  Items requiring Human Review / Cannot Fix:\033[0m")
            for item in human_review:
                print(f"    - {item}")
        print("")
        
    # 5. Construct the final ToC table
    new_table_lines = [
        "| Section | Title | PDF Page |",
        "| --------- | ------- | ---------- |"
    ]
    for r in new_toc_rows:
        new_table_lines.append(f"| {r['sec_col']} | {r['title_col']} | {r['page']} |")
        
    content_new = "\n".join(lines[:start_idx]) + "\n" + "\n".join(new_table_lines) + "\n" + "\n".join(lines[end_idx:]) + "\n"
    
    modified_count = 0
    if content != content_new:
        modified_count = len(new_toc_rows) - skipped_count
        if not dry_run:
            # Create a backup file (.bak) as the backout option before modifying
            backup_path = file_path + ".bak"
            shutil.copy(file_path, backup_path)
            print(f"\033[92m  ✓ Backup copy created: {os.path.basename(backup_path)}\033[0m")
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content_new)
                
    return modified_count

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify and fix Table of Contents.")
    parser.add_argument("file", help="Path to markdown file")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    parser.add_argument("--revert", action="store_true", help="Restore the file from its .bak backup")
    
    args = parser.parse_args()
    
    if args.revert:
        backup = args.file + ".bak"
        if os.path.exists(backup):
            shutil.copy(backup, args.file)
            print(f"\033[92m✓ Successfully reverted {args.file} from {backup}\033[0m")
            sys.exit(0)
        else:
            print(f"\033[91mError: Backup file {backup} does not exist.\033[0m", file=sys.stderr)
            sys.exit(1)
            
    count = process_file(args.file, dry_run=args.dry_run)
    print(f"Processed. Modified items: {count}")
