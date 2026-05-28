import shutil
import sys
import os

# Copy clean backup spec
temp_spec = 'CXL_3.0/temp_spec_seq.md'
shutil.copy('CXL_3.0/CXL 3.0 Specification.md', temp_spec)

sys.path.append('post-process')
import merge_split_tables
import format_table_whitespace
import format_figure_table_headings
import format_numbered_headings
import format_implementation_notes
import format_test_elements
import fix_paragraph_page_splits
import rename_figure_nomenclature
import format_table_lists
import format_figures_table

try:
    # Run all rules in sequence
    merge_split_tables.process_file(temp_spec)
    format_table_whitespace.process_file(temp_spec)
    format_figure_table_headings.process_file(temp_spec)
    format_numbered_headings.process_file(temp_spec)
    format_implementation_notes.process_file(temp_spec)
    format_test_elements.process_file(temp_spec)
    fix_paragraph_page_splits.process_file(temp_spec)
    rename_figure_nomenclature.process_file(temp_spec)
    format_table_lists.process_file(temp_spec)
    
    # Now run format_figures_table and print debug info
    with open(temp_spec, 'r') as f:
        content = f.read()
    lines = content.splitlines()
    
    table_start_idx = None
    table_end_idx = None
    for idx, line in enumerate(lines):
        if line.strip().startswith('### List of Figures'):
            for j in range(idx + 1, len(lines)):
                if lines[j].strip().startswith('|'):
                    table_start_idx = j
                    break
            break
            
    if table_start_idx is not None:
        for j in range(table_start_idx, len(lines)):
            # Print last 5 lines around the end of the table to see why it broke or didn't
            if not lines[j].strip().startswith('|') and lines[j].strip() != '':
                table_end_idx = j
                break
                
    print("Sequential Start:", table_start_idx, "End:", table_end_idx, "Total lines:", len(lines))
    if table_end_idx is not None:
        print("Line at End:", repr(lines[table_end_idx]))
        print("Line before End:", repr(lines[table_end_idx-1]))
    else:
        print("End is None!")
        # Print the last 10 lines of the file
        for k in range(len(lines)-10, len(lines)):
            print(k, repr(lines[k]))
            
finally:
    if os.path.exists(temp_spec):
        os.remove(temp_spec)
