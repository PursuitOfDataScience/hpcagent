import grp
import os
import pwd
import stat
import subprocess
from datetime import datetime


def check_path_info(path: str) -> str:
    path = os.path.expanduser(path)
    path = os.path.normpath(path)
    result_lines = [f"=== Path Information for {path} ===\n"]
    try:
        stat_info = os.stat(path)
        try:
            owner = pwd.getpwuid(stat_info.st_uid).pw_name
        except KeyError:
            owner = str(stat_info.st_uid)
        try:
            group = grp.getgrgid(stat_info.st_gid).gr_name
        except KeyError:
            group = str(stat_info.st_gid)
        mode = stat_info.st_mode
        perms = stat.filemode(mode)
        size = stat_info.st_size
        if size >= 1024 ** 3:
            size_str = f"{size / 1024**3:.2f} GB"
        elif size >= 1024 ** 2:
            size_str = f"{size / 1024**2:.2f} MB"
        elif size >= 1024:
            size_str = f"{size / 1024:.2f} KB"
        else:
            size_str = f"{size} bytes"
        mtime = datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        atime = datetime.fromtimestamp(stat_info.st_atime).strftime('%Y-%m-%d %H:%M:%S')
        path_type = "Directory" if os.path.isdir(path) else "File"
        result_lines.append(f"Type:        {path_type}")
        result_lines.append(f"Owner:       {owner}")
        result_lines.append(f"Group:       {group}")
        result_lines.append(f"Permissions: {perms}")
        result_lines.append(f"Size:        {size_str}")
        result_lines.append(f"Modified:    {mtime}")
        result_lines.append(f"Accessed:    {atime}")
        if os.path.isdir(path):
            try:
                contents = os.listdir(path)
                result_lines.append(f"Contents:    {len(contents)} items")
            except PermissionError:
                result_lines.append("Contents:    Unable to list (permission denied)")
        return '\n'.join(result_lines)
    except PermissionError:
        result_lines.append(f"Direct access to '{path}' denied (Permission denied)")
        result_lines.append("\nAttempting to get info from parent directory...\n")
    except FileNotFoundError:
        return f"Error: Path '{path}' does not exist."
    except Exception as e:
        result_lines.append(f"Error accessing path directly: {str(e)}")
        result_lines.append("\nAttempting to get info from parent directory...\n")

    parent_dir = os.path.dirname(path)
    target_name = os.path.basename(path)
    if not parent_dir:
        parent_dir = "/"
    try:
        cmd = f"ls -la {parent_dir} 2>/dev/null | grep -E '\\s{target_name}$'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            ls_output = result.stdout.strip()
            result_lines.append(f"Information from parent directory ({parent_dir}):\n")
            result_lines.append(f"  {ls_output}\n")
            parts = ls_output.split()
            if len(parts) >= 9:
                perms = parts[0]
                owner = parts[2]
                group = parts[3]
                size = parts[4]
                result_lines.append("Parsed information:")
                result_lines.append(f"  Permissions: {perms}")
                result_lines.append(f"  Owner:       {owner}")
                result_lines.append(f"  Group:       {group}")
                result_lines.append(f"  Size:        {size}")
                if perms.startswith('d'):
                    result_lines.append("  Type:        Directory")
                elif perms.startswith('l'):
                    result_lines.append("  Type:        Symbolic link")
                elif perms.startswith('-'):
                    result_lines.append("  Type:        Regular file")
                else:
                    result_lines.append("  Type:        Special file")
                if '+' in perms:
                    result_lines.append("  Note:        Has ACL (Access Control List)")
        else:
            cmd = f"ls -la {parent_dir} 2>/dev/null"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if target_name in line and line.strip().endswith(target_name):
                        result_lines.append(f"Information from parent directory ({parent_dir}):\n")
                        result_lines.append(f"  {line}\n")
                        parts = line.split()
                        if len(parts) >= 9:
                            result_lines.append("Parsed information:")
                            result_lines.append(f"  Permissions: {parts[0]}")
                            result_lines.append(f"  Owner:       {parts[2]}")
                            result_lines.append(f"  Group:       {parts[3]}")
                            result_lines.append(f"  Size:        {parts[4]}")
                        break
                else:
                    result_lines.append(f"Could not find '{target_name}' in parent directory listing.")
                    result_lines.append(f"\nParent directory contents:\n{result.stdout}")
            else:
                result_lines.append(f"Could not access parent directory '{parent_dir}' either.")
                if result.stderr:
                    result_lines.append(f"Error: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        result_lines.append("Timeout while trying to get info from parent directory.")
    except Exception as e:
        result_lines.append(f"Error getting info from parent directory: {str(e)}")
    return '\n'.join(result_lines)


def manage_file_permissions(path: str, group: str, permissions: str = "rX") -> str:
    path = os.path.expanduser(path)
    path = os.path.normpath(path)
    result_lines = [f"=== File Permission Management for {path} ===\n"]
    if not os.path.exists(path):
        return f"Error: Path '{path}' does not exist."
    result_lines.append(f"Step 1: Changing group ownership to '{group}' ...")
    try:
        cmd = f"chgrp -R {group} {path} 2>&1"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            result_lines.append(f"  chgrp failed: {res.stdout.strip()} {res.stderr.strip()}")
            return '\n'.join(result_lines)
        result_lines.append(f"  chgrp -R {group} {path}")
    except subprocess.TimeoutExpired:
        return "Error: chgrp command timed out."
    except Exception as e:
        return f"Error running chgrp: {str(e)}"

    result_lines.append(f"\nStep 2: Setting group permissions to 'g={permissions}' ...")
    try:
        cmd = f"chmod -R g={permissions} {path} 2>&1"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            result_lines.append(f"  chmod failed: {res.stdout.strip()} {res.stderr.strip()}")
            return '\n'.join(result_lines)
        result_lines.append(f"  chmod -R g={permissions} {path}")
    except subprocess.TimeoutExpired:
        return "Error: chmod command timed out."
    except Exception as e:
        return f"Error running chmod: {str(e)}"

    result_lines.append("\nStep 3: Verifying parent directory traversal ...")
    parents = []
    current = path
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            break
        parents.append(parent)
        current = parent
    parents.reverse()
    fixes_applied = []
    for parent in parents:
        try:
            cmd = f"ls -ld {parent} 2>&1"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                result_lines.append(f"  Could not inspect {parent}: {res.stdout.strip()} {res.stderr.strip()}")
                continue
            ls_line = res.stdout.strip()
            parts = ls_line.split()
            if len(parts) < 4:
                continue
            perm_str = parts[0]
            parent_group = parts[3]
            if parent_group == group:
                group_exec = perm_str[6] if len(perm_str) > 6 else '-'
                if group_exec not in ('x', 's', 'S', 't', 'T'):
                    fix_cmd = f"chmod g+x {parent} 2>&1"
                    fix_res = subprocess.run(fix_cmd, shell=True, capture_output=True, text=True, timeout=10)
                    if fix_res.returncode == 0:
                        fixes_applied.append(f"  Added g+x to {parent} (group '{parent_group}' matches target group)")
                    else:
                        fixes_applied.append(f"  Failed to add g+x to {parent}: {fix_res.stdout.strip()} {fix_res.stderr.strip()}")
            else:
                other_exec = perm_str[9] if len(perm_str) > 9 else '-'
                if other_exec not in ('x', 's', 'S', 't', 'T'):
                    fix_cmd = f"chmod o+x {parent} 2>&1"
                    fix_res = subprocess.run(fix_cmd, shell=True, capture_output=True, text=True, timeout=10)
                    if fix_res.returncode == 0:
                        fixes_applied.append(f"  Added o+x to {parent} (group '{parent_group}' != target group '{group}')")
                    else:
                        fixes_applied.append(f"  Failed to add o+x to {parent}: {fix_res.stdout.strip()} {fix_res.stderr.strip()}")
        except Exception as e:
            result_lines.append(f"  Error checking {parent}: {str(e)}")

    if fixes_applied:
        result_lines.append("  Parent directory fixes applied:")
        result_lines.extend(fixes_applied)
    else:
        result_lines.append("  All parent directories already have correct traversal permissions.")

    result_lines.append(f"\n{'=' * 60}")
    result_lines.append("FINAL PERMISSIONS VERIFICATION")
    result_lines.append('=' * 60)
    all_paths = parents + [path]
    ls_paths = ' '.join(all_paths)
    try:
        cmd = f"ls -ld {ls_paths} 2>&1"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            result_lines.append(res.stdout.strip())
        else:
            result_lines.append(f"  Could not list final permissions: {res.stdout.strip()} {res.stderr.strip()}")
    except Exception as e:
        result_lines.append(f"  Error listing final permissions: {str(e)}")

    result_lines.append(f"\n{'=' * 60}")
    result_lines.append("SUMMARY")
    result_lines.append('=' * 60)
    result_lines.append(f"  Target path:   {path}")
    result_lines.append(f"  Group:         {group}")
    result_lines.append(f"  Permissions:   g={permissions}")
    result_lines.append(f"  Parent fixes:  {len(fixes_applied)}")
    return '\n'.join(result_lines)
