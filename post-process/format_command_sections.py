import re

def process_file(file_path, dry_run=False):
    """
    Replaces "## Possible Command Return Codes:" with "Possible Command Return Codes:"
    and "## Command Effects:" with "Command Effects:".
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Pattern 1: ^## Possible Command Return Codes:
    pattern1 = r"(?m)^##\s*Possible Command Return Codes:"
    matches1 = re.findall(pattern1, content)

    # Pattern 2: ^## Command Effects:
    pattern2 = r"(?m)^##\s*Command Effects:"
    matches2 = re.findall(pattern2, content)

    # Pattern 3: ^## Possible Error Response, Error Codes:
    pattern3 = r"(?m)^##\s*Possible Error Response,\s*Error Codes:"
    matches3 = re.findall(pattern3, content)

    modified_count = len(matches1) + len(matches2) + len(matches3)

    if modified_count > 0 and not dry_run:
        content = re.sub(pattern1, "Possible Command Return Codes:", content)
        content = re.sub(pattern2, "Command Effects:", content)
        content = re.sub(pattern3, "Possible Error Response, Error Codes:", content)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

    return modified_count
