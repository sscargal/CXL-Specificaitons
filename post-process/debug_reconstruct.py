import shutil
import sys
import os

sys.path.append('post-process')
import format_figures_table

# Copy to temporary file inside workspace
temp_spec = 'CXL_3.0/temp_spec.md'
shutil.copy('CXL_3.0/CXL 3.0 Specification.md', temp_spec)

try:
    with open(temp_spec, 'r') as f:
        orig = len(f.read())
        
    print("Original character size:", orig)
    format_figures_table.process_file(temp_spec, dry_run=False)
    
    with open(temp_spec, 'r') as f:
        new_content = f.read()
        new_sz = len(new_content)
        
    print("New character size:", new_sz)
    
    # Check the lines around 2-8 in the temp file
    lines = new_content.splitlines()
    for idx, l in enumerate(lines):
        if '### List of Figures' in l:
            print("Found List of Figures at line:", idx)
            for j in range(idx + 1, idx + 30):
                if '2-7' in lines[j] or '2-8' in lines[j] or 'Head-to' in lines[j]:
                    print(j, repr(lines[j]))
            break
finally:
    if os.path.exists(temp_spec):
        os.remove(temp_spec)
