#!/usr/bin/env python3
"""
Log Files Purge Utility

This script purges log files from the logs directory.
You can purge all logs or filter by specific patterns/names.

Usage:
    # Purge all log files:
    python -m src.utils.purge_logs
    
    # Purge specific log files by pattern:
    python -m src.utils.purge_logs --pattern "CLMS*.log"
    
    # Purge specific log files by name:
    python -m src.utils.purge_logs --files CLMS_agent_log.txt advanced_graph_builder_20260511.log
    
    # Purge all logs older than 7 days:
    python -m src.utils.purge_logs --older-than 7
    
    # Purge from custom directory:
    python -m src.utils.purge_logs --logs-dir custom_logs
"""

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


def purge_logs(
    logs_dir: str = "logs",
    file_patterns: list[str] | None = None,
    specific_files: list[str] | None = None,
    older_than_days: int | None = None,
) -> dict:
    """Purge log files from the logs directory.
    
    Args:
        logs_dir: Path to the logs directory (default: logs)
        file_patterns: List of glob patterns to match (e.g., ["CLMS*.log", "*.txt"])
        specific_files: List of specific file names to delete
        older_than_days: Only delete files older than this many days
    
    Returns:
        dict with purge statistics: {
            'files_deleted': int,
            'dirs_deleted': int,
            'success': bool,
            'error': str | None
        }
    """
    print("=" * 70)
    print("🗑️  PURGING LOG FILES")
    print("=" * 70)
    print(f"Logs Directory: {logs_dir}")
    if file_patterns:
        print(f"Patterns: {', '.join(file_patterns)}")
    if specific_files:
        print(f"Specific Files: {', '.join(specific_files)}")
    if older_than_days is not None:
        print(f"Age Filter: Older than {older_than_days} days")
    print()
    
    stats = {
        'files_deleted': 0,
        'dirs_deleted': 0,
        'success': False,
        'error': None,
    }
    
    logs_path = Path(logs_dir)
    
    # Validate logs directory
    if not logs_path.exists():
        msg = f"Logs directory '{logs_dir}' does not exist. Nothing to delete."
        print(f"ℹ️  {msg}")
        print()
        stats['success'] = True
        return stats
    
    if not logs_path.is_dir():
        msg = f"'{logs_dir}' is not a directory!"
        print(f"❌ Error: {msg}")
        print()
        stats['error'] = msg
        return stats
    
    # Collect items to delete
    items_to_delete = []
    
    if specific_files:
        # Delete specific files
        for filename in specific_files:
            file_path = logs_path / filename
            if file_path.exists():
                items_to_delete.append(file_path)
    elif file_patterns:
        # Delete by pattern
        for pattern in file_patterns:
            matched = list(logs_path.glob(pattern))
            items_to_delete.extend(matched)
    else:
        # Delete all contents
        items_to_delete = list(logs_path.glob("*"))
    
    # Apply age filter if specified
    if older_than_days is not None:
        cutoff_time = datetime.now() - timedelta(days=older_than_days)
        filtered_items = []
        for item in items_to_delete:
            try:
                if item.is_file():
                    mod_time = datetime.fromtimestamp(item.stat().st_mtime)
                    if mod_time < cutoff_time:
                        filtered_items.append(item)
            except Exception:
                pass  # Skip files that can't be stat'd
        items_to_delete = filtered_items
    
    # Count items
    files_to_delete = [f for f in items_to_delete if f.is_file()]
    dirs_to_delete = [d for d in items_to_delete if d.is_dir()]
    
    print(f"📊 Found {len(files_to_delete)} log files and {len(dirs_to_delete)} subdirectories to delete")
    print()
    
    if len(items_to_delete) == 0:
        print("ℹ️  No matching log files found. Nothing to delete.")
        print()
        stats['success'] = True
        return stats
    
    # Delete items
    print(f"🗑️  Deleting {len(items_to_delete)} items...")
    deleted_files = 0
    deleted_dirs = 0
    failed = []
    
    for item in items_to_delete:
        try:
            if item.is_file():
                item.unlink()
                deleted_files += 1
                print(f"   ✓ Deleted file: {item.name}")
            elif item.is_dir():
                shutil.rmtree(item)
                deleted_dirs += 1
                print(f"   ✓ Deleted directory: {item.name}")
        except Exception as exc:
            failed.append((item, exc))
            print(f"   ⚠️  Warning: Could not delete {item.name}: {exc}")
    
    print()
    print(f"✅ Deleted {deleted_files} files and {deleted_dirs} directories")
    if failed:
        print(f"⚠️  Failed to delete {len(failed)} items")
    print()
    
    stats['files_deleted'] = deleted_files
    stats['dirs_deleted'] = deleted_dirs
    stats['success'] = len(failed) == 0
    if failed:
        stats['error'] = f"Failed to delete {len(failed)} items"
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Purge log files from logs directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Purge all log files:
  python -m src.utils.purge_logs
  
  # Purge CLMS logs only:
  python -m src.utils.purge_logs --pattern "CLMS*.log" "CLMS*.txt"
  
  # Purge specific files:
  python -m src.utils.purge_logs --files CLMS_agent_log.txt advanced_graph_builder_20260511.log
  
  # Purge logs older than 7 days:
  python -m src.utils.purge_logs --older-than 7
  
  # Purge from custom directory:
  python -m src.utils.purge_logs --logs-dir custom_logs
        """
    )
    parser.add_argument(
        "--logs-dir",
        default="logs",
        metavar="DIR",
        help="Path to logs directory (default: logs)",
    )
    parser.add_argument(
        "--pattern",
        nargs="+",
        default=None,
        metavar="PATTERN",
        help="Glob patterns to match log files (e.g., 'CLMS*.log' '*.txt')",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Specific file names to delete",
    )
    parser.add_argument(
        "--older-than",
        type=int,
        default=None,
        metavar="DAYS",
        help="Only delete files older than this many days",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt and proceed with purge immediately",
    )
    
    args = parser.parse_args()
    
    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + "  LOG FILES PURGE UTILITY".center(68) + "║")
    print("╚" + "═" * 68 + "╝")
    print()
    
    # Show what will be purged
    print("📋 Purge Summary:")
    print(f"   Logs Directory: {args.logs_dir}")
    if args.pattern:
        print(f"   Patterns: {', '.join(args.pattern)}")
    if args.files:
        print(f"   Specific Files: {', '.join(args.files)}")
    if args.older_than:
        print(f"   Age Filter: Older than {args.older_than} days")
    if not args.pattern and not args.files:
        print("   ⚠️  Filter: NONE (will delete ALL log files)")
    print()
    
    # Confirmation prompt
    if not args.yes:
        if args.pattern or args.files or args.older_than:
            prompt = "Are you sure you want to purge matching log files? (yes/no): "
        else:
            prompt = "⚠️  WARNING: This will delete ALL log files! Type 'yes' to confirm: "
        
        response = input(prompt).strip().lower()
        if response not in ['yes', 'y']:
            print("❌ Purge cancelled.")
            sys.exit(0)
        print()
    
    # Purge logs
    try:
        stats = purge_logs(
            logs_dir=args.logs_dir,
            file_patterns=args.pattern,
            specific_files=args.files,
            older_than_days=args.older_than,
        )
        
        # Final summary
        print("=" * 70)
        print("📊 PURGE SUMMARY")
        print("=" * 70)
        print(f"   Files deleted: {stats['files_deleted']}")
        print(f"   Directories deleted: {stats['dirs_deleted']}")
        if stats['error']:
            print(f"   ⚠️  Error: {stats['error']}")
        print("=" * 70)
        
        if stats['success']:
            print("✅ Log purge completed successfully!")
        else:
            print("⚠️  Log purge completed with warnings.")
            sys.exit(1)
        print()
        
    except Exception as exc:
        print(f"❌ Fatal error during log purge: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
