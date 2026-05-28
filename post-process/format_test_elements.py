import re

def adjust_list_indentations(content):
    """
    Checks and adjusts the indentation of unordered lists or sublists
    (e.g., - a., - b. or - i., - ii.) under their parent list items.
    """
    lines = content.splitlines()
    new_lines = []
    
    # stack keeps track of active list types and their actual target indentation.
    # Elements are: (list_type, indent)
    # list_type can be 'ordered', 'bullet', 'lettered', 'roman'
    stack = []
    
    # Hierarchy levels for determining nesting
    levels = {
        'ordered': 0,
        'bullet': 1,
        'lettered': 2,
        'roman': 3
    }
    
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            new_lines.append(line)
            continue
            
        if stripped.startswith('#'):
            stack = []
            new_lines.append(line)
            continue
            
        m_ordered = re.match(r"^(\s*)(\d+)\.\s", line)
        m_roman = re.match(r"^(\s*)-\s+([ivxlcdm]+)\.\s", line)
        m_lettered = re.match(r"^(\s*)-\s+([a-z])\.\s", line)
        m_bullet = re.match(r"^(\s*)-\s", line)
        
        is_list = False
        l_type = None
        orig_indent = len(line) - len(stripped)
        
        if m_ordered:
            is_list = True
            l_type = 'ordered'
        elif m_roman and len(m_roman.group(2)) > 1:
            is_list = True
            l_type = 'roman'
        elif m_lettered:
            is_list = True
            letter = m_lettered.group(2)
            if letter in ['i', 'v', 'x'] and not any(t == 'lettered' for t, _ in stack):
                l_type = 'roman'
            else:
                l_type = 'lettered'
        elif m_bullet:
            is_list = True
            l_type = 'bullet'
            
        if is_list:
            # Pop elements from stack that are of equal or lower hierarchy (higher or equal level index)
            while stack and levels[stack[-1][0]] >= levels[l_type]:
                stack.pop()
                
            if not stack:
                # Top level list item
                target_indent = 0
                stack.append((l_type, target_indent))
            else:
                parent_type, parent_indent = stack[-1]
                target_indent = parent_indent + 4
                stack.append((l_type, target_indent))
                
            new_line = " " * target_indent + stripped
            new_lines.append(new_line)
        else:
            if orig_indent == 0:
                stack = []
            new_lines.append(line)
            
    return "\n".join(new_lines) + ("\n" if content.endswith("\n") else "")

def process_file(file_path, dry_run=False):
    """
    Standardizes test headings by converting various markdown subheading formats
    (like ## Fail Conditions:, ## Pass Criteria:, ## Test Steps:, ##Prerequisites:)
    into standard bold-like text elements without the markdown headings hashes.
    It also checks and corrects the indentation of nested list items.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # List of replacement pairs (regex pattern, replacement string)
    replacements = [
        (r"^##\s*Fail\s+Conditions\s*:", "Fail Conditions:"),
        (r"^##\s*Pass\s+Criteria\s*:", "Pass Criteria:"),
        (r"^##\s*Test\s+Steps\s*:", "Test Steps:"),
        # Matches formats like "## Test Steps (256B Flit Mode):"
        (r"^##\s*Test\s+Steps\s*\((.*?)\)\s*:", r"Test Steps (\1):"),
        (r"^##\s*Prerequisites\s*:", "Prerequisites:"),
        (r"^##Prerequisites:", "Prerequisites:"),
        # Matches "## Prerequisites" without trailing colon
        (r"^##\s*Prerequisites\s*$", "Prerequisites:"),
        (r"^##\s*For\s+an\s+MLD\s*:", "**For an MLD:**"),
        (r"^##\s*For\s+an\s+SLD\s*:", "**For an SLD:**"),
        (r"^##\s*Test\s+Equipment\s*:", "**Test Equipment:**"),
        (r"^##\s*Test\s+Equipment\s*$", "**Test Equipment**"),
        (r"^##\s*Device\s+Test\s+Steps\s*:", "**Device Test Steps:**"),
        (r"^##\s*Host\s+Test\s+Steps\s*:", "**Host Test Steps:**"),
        (r"^##\s*Topologies\s*:", "**Topologies:**"),
        (r"^##\s*Open\s*:", "Open:")
    ]
    
    modified_count = 0
    new_content = content
    
    for pattern, repl in replacements:
        matches = re.findall(pattern, new_content, flags=re.MULTILINE)
        if matches:
            modified_count += len(matches)
            if not dry_run:
                new_content = re.sub(pattern, repl, new_content, flags=re.MULTILINE)

    # Check and adjust list indentation
    indented_content = adjust_list_indentations(new_content)
    if indented_content != new_content:
        orig_lines = new_content.splitlines()
        ind_lines = indented_content.splitlines()
        for old_line, new_line in zip(orig_lines, ind_lines):
            if old_line != new_line:
                modified_count += 1
        if len(ind_lines) > len(orig_lines):
            modified_count += len(ind_lines) - len(orig_lines)
            
        if not dry_run:
            new_content = indented_content

    if modified_count > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    return modified_count
