import grp
import os
import pwd
import re
import stat
import subprocess
from datetime import datetime


def _validate_path(path: str) -> str:
    expanded = os.path.expanduser(path)
    resolved = os.path.normpath(expanded)
    if re.search(r'[;&|`$(){}]', resolved):
        raise ValueError(f"Path contains shell metacharacters: {path}")
    return resolved


def _validate_group(group_name: str) -> str:
    try:
        grp.getgrnam(group_name)
    except KeyError:
        valid = [g.gr_name for g in grp.getgrall()]
        raise ValueError(f"Group '{group_name}' does not exist. Valid groups: {', '.join(valid[:10])}...")
    return group_name


_PERM_RE = re.compile(r'^[ugoa]*[-+=][rwxXst]+$')


def _validate_permissions(perm: str) -> str:
    if not _PERM_RE.match(perm):
        raise ValueError(f"Invalid permissions string: '{perm}'. Expected format like 'rX', 'rwx', 'g=rX', 'o+x'")
    return perm


def _format_stat_info(path: str, stat_info, result_lines: list) -> str:
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

    if stat.S_ISDIR(mode):
        path_type = "Directory"
    elif stat.S_ISLNK(mode):
        path_type = "Symlink"
    else:
        path_type = "File"

    result_lines.append(f"Type:        {path_type}")
    result_lines.append(f"Owner:       {owner}")
    result_lines.append(f"Group:       {group}")
    result_lines.append(f"Permissions: {perms}")
    result_lines.append(f"Size:        {size_str}")
    result_lines.append(f"Modified:    {mtime}")
    result_lines.append(f"Accessed:    {atime}")
    if stat.S_ISDIR(mode):
        try:
            contents = os.listdir(path)
            result_lines.append(f"Contents:    {len(contents)} items")
        except PermissionError:
            result_lines.append("Contents:    Unable to list (permission denied)")
    return '\n'.join(result_lines)


def check_path_info(path: str) -> str:
    path = _validate_path(path)
    result_lines = [f"=== Path Information for {path} ===\n"]
    try:
        stat_info = os.lstat(path)
        return _format_stat_info(path, stat_info, result_lines)
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
        parent_dir = "."
    try:
        found = False
        with os.scandir(parent_dir) as it:
            for entry in it:
                if entry.name == target_name:
                    found = True
                    result_lines.append(f"Information from parent directory ({parent_dir}):\n")
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        _format_stat_info(entry.path, entry_stat, result_lines)
                    except Exception as stat_err:
                        result_lines.append(f"  Could not stat entry: {stat_err}")
                    break
        if not found:
            result_lines.append(f"Could not find '{target_name}' in parent directory listing.")
    except Exception as e:
        result_lines.append(f"Could not access parent directory '{parent_dir}': {e}")
    return '\n'.join(result_lines)


def manage_file_permissions(path: str, group: str, permissions: str = "rX", dry_run: bool = False) -> str:
    path = _validate_path(path)
    group = _validate_group(group)
    permissions = _validate_permissions(permissions)

    if not os.path.exists(path):
        return f"Error: Path '{path}' does not exist."

    result_lines = []
    if dry_run:
        result_lines.append(f"=== DRY RUN: Planned File Permission changes for {path} ===")
    else:
        result_lines.append(f"=== File Permission Management for {path} ===")

    chgrp_cmd = ["chgrp", "-R", group, path]
    if dry_run:
        result_lines.append(f"  [Plan] Run command: {' '.join(chgrp_cmd)}")
    else:
        result_lines.append(f"Step 1: Changing group ownership to '{group}' ...")
        try:
            res = subprocess.run(chgrp_cmd, capture_output=True, text=True, timeout=60)
            if res.returncode != 0:
                result_lines.append(f"  chgrp failed: {res.stderr.strip() or res.stdout.strip()}")
                return '\n'.join(result_lines)
            result_lines.append(f"  Successfully changed group ownership to '{group}'.")
        except subprocess.TimeoutExpired:
            return "Error: chgrp command timed out."
        except Exception as e:
            return f"Error running chgrp: {str(e)}"

    chmod_cmd = ["chmod", "-R", f"g={permissions}", path]
    if dry_run:
        result_lines.append(f"  [Plan] Run command: {' '.join(chmod_cmd)}")
    else:
        result_lines.append(f"\nStep 2: Setting group permissions to 'g={permissions}' ...")
        try:
            res = subprocess.run(chmod_cmd, capture_output=True, text=True, timeout=60)
            if res.returncode != 0:
                result_lines.append(f"  chmod failed: {res.stderr.strip() or res.stdout.strip()}")
                return '\n'.join(result_lines)
            result_lines.append(f"  Successfully set group permissions to 'g={permissions}'.")
        except subprocess.TimeoutExpired:
            return "Error: chmod command timed out."
        except Exception as e:
            return f"Error running chmod: {str(e)}"

    if dry_run:
        result_lines.append("\n  [Plan] Check and fix parent directories traversal:")
    else:
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
            parent_stat = os.lstat(parent)
            mode = parent_stat.st_mode
            parent_perms = stat.filemode(mode)

            try:
                parent_group = grp.getgrgid(parent_stat.st_gid).gr_name
            except KeyError:
                parent_group = str(parent_stat.st_gid)

            if parent_group == group:
                group_exec = parent_perms[6]
                if group_exec not in ('x', 's', 'S', 't', 'T'):
                    if dry_run:
                        fixes_applied.append(f"  [Plan] Add g+x to parent: {parent} (group '{parent_group}' matches target group)")
                    else:
                        try:
                            os.chmod(parent, mode | stat.S_IXGRP)
                            fixes_applied.append(f"  Added g+x to {parent} (group '{parent_group}' matches target group)")
                        except Exception as e:
                            fixes_applied.append(f"  Failed to add g+x to {parent}: {e}")
            else:
                other_exec = parent_perms[9]
                if other_exec not in ('x', 's', 'S', 't', 'T'):
                    if dry_run:
                        fixes_applied.append(f"  [Plan] Add o+x to parent: {parent} (group '{parent_group}' != target group '{group}')")
                    else:
                        try:
                            os.chmod(parent, mode | stat.S_IXOTH)
                            fixes_applied.append(f"  Added o+x to {parent} (group '{parent_group}' != target group '{group}')")
                        except Exception as e:
                            fixes_applied.append(f"  Failed to add o+x to {parent}: {e}")
        except Exception as e:
            if not dry_run:
                result_lines.append(f"  Error checking {parent}: {str(e)}")

    if fixes_applied:
        if dry_run:
            result_lines.extend(fixes_applied)
        else:
            result_lines.append("  Parent directory fixes applied:")
            result_lines.extend(fixes_applied)
    else:
        result_lines.append("  All parent directories already have correct traversal permissions.")

    if not dry_run:
        result_lines.append(f"\n{'=' * 60}")
        result_lines.append("FINAL PERMISSIONS VERIFICATION")
        result_lines.append('=' * 60)
        for p in parents + [path]:
            try:
                p_stat = os.lstat(p)
                p_mode = p_stat.st_mode
                p_perms = stat.filemode(p_mode)
                try:
                    p_owner = pwd.getpwuid(p_stat.st_uid).pw_name
                except KeyError:
                    p_owner = str(p_stat.st_uid)
                try:
                    p_group = grp.getgrgid(p_stat.st_gid).gr_name
                except KeyError:
                    p_group = str(p_stat.st_gid)
                result_lines.append(f"  {p_perms}  {p_owner}  {p_group}  {p}")
            except Exception as e:
                result_lines.append(f"  Could not verify {p}: {e}")

        result_lines.append(f"\n{'=' * 60}")
        result_lines.append("SUMMARY")
        result_lines.append('=' * 60)
        result_lines.append(f"  Target path:   {path}")
        result_lines.append(f"  Group:         {group}")
        result_lines.append(f"  Permissions:   g={permissions}")
        result_lines.append(f"  Parent fixes:  {len(fixes_applied)}")

    return '\n'.join(result_lines)
