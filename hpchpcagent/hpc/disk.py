import os, re, subprocess


def parse_size_to_bytes(size_str: str) -> int:
    size_str = size_str.strip().upper()
    match = re.match(r'^([\d.]+)\s*([KMGTP]?)B?$', size_str)
    if not match:
        try:
            return int(float(size_str))
        except ValueError:
            return 0
    number = float(match.group(1))
    unit = match.group(2)
    multipliers = {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4, 'P': 1024 ** 5}
    return int(number * multipliers.get(unit, 1))


def analyze_disk_usage(directory: str = None, max_depth: int = 1) -> str:
    if not directory:
        directory = os.path.expanduser("~")
    directory = os.path.expanduser(directory)
    if not os.path.exists(directory):
        return f"Error: Directory '{directory}' does not exist."
    if not os.path.isdir(directory):
        return f"Error: '{directory}' is not a directory."
    max_depth = min(max_depth, 3)
    cmd = ["du", directory, f"--max-depth={max_depth}", "-h"]
    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=60)
        lines = output.strip().split('\n')
        parsed_items = []
        for line in lines:
            parts = line.split('\t')
            if len(parts) == 2:
                size_str = parts[0].strip()
                path = parts[1].strip()
                size_bytes = parse_size_to_bytes(size_str)
                parsed_items.append({'size_str': size_str, 'size_bytes': size_bytes, 'path': path})
        parsed_items.sort(key=lambda x: x['size_bytes'], reverse=True)
        result_lines = [
            f"=== Disk Usage Analysis for {directory} ===\n",
            "Size      Path",
            "-" * 60
        ]
        large_items = []
        total_size = 0
        for item in parsed_items:
            result_lines.append(f"{item['size_str']:<10}{item['path']}")
            if item['path'] != directory:
                total_size += item['size_bytes']
                if item['size_bytes'] >= 100 * 1024 * 1024:
                    large_items.append(item)
        result_lines.append("\n" + "=" * 60)
        result_lines.append("RECOMMENDATIONS (read-only analysis - no automatic deletion)")
        result_lines.append("=" * 60)
        if large_items:
            result_lines.append(f"\nFound {len(large_items)} large item(s) (>100MB) that may be worth reviewing:\n")
            for item in large_items[:10]:
                path_lower = item['path'].lower()
                result_lines.append(f"  * {item['size_str']:<8} {item['path']}")
                if '.cache' in path_lower or 'cache' in path_lower:
                    result_lines.append(f"    Suggestion: cache directory - consider clearing old cache files")
                elif '.npm' in path_lower:
                    result_lines.append(f"    Suggestion: npm cache - run 'npm cache clean --force' to clear")
                elif '.conda' in path_lower or 'anaconda' in path_lower or 'miniconda' in path_lower:
                    result_lines.append(f"    Suggestion: conda environment - remove unused environments with 'conda env remove -n ENV_NAME'")
                elif '.local' in path_lower:
                    result_lines.append(f"    Suggestion: local packages - review and uninstall unused ones")
                elif 'tmp' in path_lower or '.tmp' in path_lower:
                    result_lines.append(f"    Suggestion: temporary files - review and manually delete old temp files")
                elif '__pycache__' in path_lower:
                    result_lines.append(f"    Suggestion: python cache - safe to delete")
                elif '.git' in path_lower:
                    result_lines.append(f"    Suggestion: git repository data - run 'git gc' to optimize")
                elif 'node_modules' in path_lower:
                    result_lines.append(f"    Suggestion: node modules - consider removing unused project dependencies")
        else:
            result_lines.append("\nNo unusually large items found (>100MB).")
        result_lines.append("\n" + "-" * 60)
        result_lines.append("IMPORTANT: This tool only provides recommendations.")
        result_lines.append("No files can be automatically deleted by this assistant.")
        result_lines.append("You must manually review and delete files if needed.")
        result_lines.append("-" * 60)
        return '\n'.join(result_lines)
    except subprocess.TimeoutExpired:
        return f"Error: Disk usage analysis timed out. The directory may be too large. Try a smaller directory or reduce max_depth."
    except subprocess.CalledProcessError as e:
        return f"Error analyzing disk usage: {e.output if hasattr(e, 'output') else str(e)}"
