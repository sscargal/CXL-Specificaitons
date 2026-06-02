#!/usr/bin/env python3
import os
import sys
import argparse
import hashlib

# Import rule modules
import remove_evaluation_copy
import merge_split_tables
import format_table_whitespace
import format_table_newlines
import format_table_dashes
import format_figure_table_headings
import format_numbered_headings
import format_implementation_notes
import format_command_sections
import format_test_elements
import fix_paragraph_page_splits
import rename_figure_nomenclature
import format_table_lists
import format_figures_table
import format_tables_table
import format_revision_history
import format_acronyms_table
import format_contents_table

# Define execution order
RULES_ORDER = [
    ("remove_evaluation_copy", remove_evaluation_copy, "Remove Evaluation Copy Watermarks"),
    ("merge_split_tables", merge_split_tables, "Merge Split Tables"),
    ("format_table_whitespace", format_table_whitespace, "Format Table Cell Whitespace"),
    ("format_table_newlines", format_table_newlines, "Format Broken Table Newlines"),
    ("format_table_dashes", format_table_dashes, "Reduce Table Separator Dashes to 3"),
    ("format_figure_table_headings", format_figure_table_headings, "Format Figure and Table Headings"),
    ("format_numbered_headings", format_numbered_headings, "Format Numbered Headings"),
    ("format_implementation_notes", format_implementation_notes, "Format Implementation Notes"),
    ("format_command_sections", format_command_sections, "Format Command Return Codes and Effects"),
    ("format_test_elements", format_test_elements, "Format Test Elements (Fail/Pass/Steps/Prereqs)"),
    ("fix_paragraph_page_splits", fix_paragraph_page_splits, "Fix Paragraph Page Split Newlines"),
    ("rename_figure_nomenclature", rename_figure_nomenclature, "Rename Figure Files and References"),
    ("table_lists", format_table_lists, "Format Table Cell Lists (bullets/dashes)"),
    ("format_figures_table", format_figures_table, "Format Figures Table"),
    ("format_tables_table", format_tables_table, "Format Tables Table"),
    ("format_revision_history", format_revision_history, "Format Revision History Table"),
    ("format_acronyms_table", format_acronyms_table, "Format Acronyms Table"),
    ("format_contents_table", format_contents_table, "Format Table of Contents Table")
]

def get_markdown_files(path):
    """Recursively retrieves all markdown files under the given path."""
    if os.path.isfile(path):
        if path.endswith(".md"):
            return [path]
        return []
    
    md_files = []
    for root, _, files in os.walk(path):
        for file in files:
            if file.endswith(".md"):
                md_files.append(os.path.join(root, file))
    return sorted(md_files)

def calculate_checksum(file_path):
    """Computes SHA-256 checksum of a file to verify modifications."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def validate_integrity(before_content, after_content):
    """
    Validates before-and-after document integrity to verify no untoward
    corruption (such as losing significant content or breaking block structures).
    """
    # 1. Content size sanity check (must not lose more than 5% of text size in paragraph joining/merges)
    # Note: We completely strip dashes "-" from the non-whitespace strings to prevent false positives from dash reduction rules.
    before_non_ws = len("".join(before_content.split()).replace("-", ""))
    after_non_ws = len("".join(after_content.split()).replace("-", ""))
    if after_non_ws < before_non_ws * 0.95:
        return False, f"Integrity check failed: Significant content loss detected (Before: {before_non_ws} non-ws chars, After: {after_non_ws} non-ws chars)"

    # 2. Balanced Markdown code block markers check
    before_code_blocks = before_content.count("```")
    after_code_blocks = after_content.count("```")
    if before_code_blocks != after_code_blocks:
        return False, f"Integrity check failed: Code block fences '```' became unbalanced (Before: {before_code_blocks}, After: {after_code_blocks})"

    # 3. Critical heading check (the document should still have headings)
    if not any(line.startswith("#") for line in after_content.splitlines()):
        return False, "Integrity check failed: Markdown headings are completely missing from the output document"

    return True, "Success"

def main():
    parser = argparse.ArgumentParser(
        description="Run post-processing rules and fixes on CXL Specification markdown files."
    )
    parser.add_argument(
        "target",
        help="Path to a markdown file or a directory containing markdown files to process."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the formatting and print what would change without modifying files."
    )
    parser.add_argument(
        "--rules",
        nargs="+",
        choices=[rule[0] for rule in RULES_ORDER],
        default=[rule[0] for rule in RULES_ORDER],
        help="Specify which rules/scripts to run (default: runs all in logical order)."
    )

    args = parser.parse_args()
    
    if not os.path.exists(args.target):
        print(f"\033[91mError: Target path '{args.target}' does not exist.\033[0m", file=sys.stderr)
        sys.exit(1)

    files = get_markdown_files(args.target)
    if not files:
        print("No markdown (.md) files found in target.")
        sys.exit(0)

    print(f"\033[96m============================================================\033[0m")
    print(f"\033[96m              CXL Specification Post-Processor             \033[0m")
    print(f"\033[96m============================================================\033[0m")
    print(f"Found {len(files)} markdown file(s) to process.")
    if args.dry_run:
        print("\033[93m[DRY-RUN MODE] No files or directories will be modified.\033[0m\n")

    for file_path in files:
        print(f"\n\033[97mProcessing File: {os.path.basename(file_path)}\033[0m")
        print(f"Path: {file_path}")
        
        # Load content for integrity validation
        with open(file_path, "r", encoding="utf-8") as f:
            before_content = f.read()
        before_hash = calculate_checksum(file_path)

        any_updates = False
        
        # Run registered rules in sequential order
        for rule_id, rule_module, rule_name in RULES_ORDER:
            if rule_id in args.rules:
                print(f"  \033[90mRunning: {rule_name}...\033[0m", end="\r")
                try:
                    count = rule_module.process_file(file_path, dry_run=args.dry_run)
                    if count > 0:
                        any_updates = True
                        action_word = "\033[93mWould update\033[0m" if args.dry_run else "\033[92mUpdated\033[0m"
                        print(f"  - {rule_name:<35}: {action_word} {count} line(s)/item(s)")
                    else:
                        print(f"  - {rule_name:<35}: \033[90mNo changes needed\033[0m")
                except Exception as e:
                    print(f"  \033[91m- {rule_name:<35}: FAILED with error: {e}\033[0m")

        # Perform Validation if any updates occurred (and not dry-run)
        if any_updates and not args.dry_run:
            with open(file_path, "r", encoding="utf-8") as f:
                after_content = f.read()
            after_hash = calculate_checksum(file_path)
            
            valid, msg = validate_integrity(before_content, after_content)
            if valid:
                print(f"\033[92m  ✓ Integrity Check Passed! Checksum updated: {before_hash[:8]} -> {after_hash[:8]}\033[0m")
            else:
                print(f"\033[91m  ✗ {msg}\033[0m")
                # Restore original file in case of failure
                print("\033[93m  [SAFETY] Restoring original file from memory backup...\033[0m")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(before_content)
                print("\033[92m  ✓ Original file successfully restored.\033[0m")
        elif not any_updates:
            print("  \033[92m✓ File is already fully optimized. No changes needed.\033[0m")

    print(f"\n\033[96m============================================================\033[0m")
    print(f"\033[92mProcessing Completed Successfully!\033[0m")
    print(f"\033[96m============================================================\033[0m")

if __name__ == "__main__":
    main()
