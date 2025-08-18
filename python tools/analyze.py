#!/usr/bin/env python3
"""
Folder Size Analyzer
Analyzes folder sizes and file counts for two archive directories:
- jillem_zips\\jillem-full-archive
- jillem_full_zips2_finaldestination\\jillem-full-archive_2

Outputs results to CSV with columns: folder_name, size_bytes, size_mb, file_count
"""

import os
import csv
from pathlib import Path
from typing import List, Tuple, Dict


def get_folder_size_and_count(folder_path: Path) -> Tuple[int, int]:
    """
    Calculate total size in bytes and file count for a folder.
    
    Args:
        folder_path: Path to the folder to analyze
        
    Returns:
        Tuple of (total_size_bytes, file_count)
    """
    total_size = 0
    file_count = 0
    
    try:
        for dirpath, dirnames, filenames in os.walk(folder_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                try:
                    if os.path.exists(file_path):
                        file_size = os.path.getsize(file_path)
                        total_size += file_size
                        file_count += 1
                except (OSError, FileNotFoundError):
                    # Skip files that can't be accessed
                    continue
    except (OSError, PermissionError):
        # Skip folders that can't be accessed
        pass
    
    return total_size, file_count


def analyze_directory(base_path: Path, directory_name: str) -> List[Dict]:
    """
    Analyze all subdirectories in the given base path.
    
    Args:
        base_path: Base directory to analyze
        directory_name: Name identifier for the archive
        
    Returns:
        List of dictionaries with folder analysis data
    """
    results = []
    
    if not base_path.exists():
        print(f"Warning: Directory '{base_path}' does not exist")
        return results
    
    print(f"Analyzing {directory_name}: {base_path}")
    
    # Get all subdirectories
    try:
        subdirs = [d for d in base_path.iterdir() if d.is_dir()]
        subdirs.sort()  # Sort for consistent output
        
        for subdir in subdirs:
            print(f"  Processing: {subdir.name}")
            size_bytes, file_count = get_folder_size_and_count(subdir)
            size_mb = size_bytes / (1024 * 1024)  # Convert to MB
            
            results.append({
                'archive': directory_name,
                'folder_name': subdir.name,
                'size_bytes': size_bytes,
                'size_mb': round(size_mb, 2),
                'file_count': file_count
            })
            
    except PermissionError:
        print(f"Error: Permission denied accessing {base_path}")
    except Exception as e:
        print(f"Error analyzing {base_path}: {e}")
    
    return results


def format_bytes(size_bytes: int) -> str:
    """Format bytes into human readable format."""
    size_float = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_float < 1024.0:
            return f"{size_float:.2f} {unit}"
        size_float /= 1024.0
    return f"{size_float:.2f} PB"


def main():
    """Main execution function."""
    # Define the paths to analyze
    current_dir = Path(__file__).parent
    
    archive_paths = [
        (current_dir / "jillem_zips" / "jillem-full-archive", "jillem-full-archive"),
        (current_dir / "jillem_zips" / "jillem-full-archive_2", "jillem-full-archive"),
    ]
    
    all_results = []
    
    # Analyze each archive directory
    for archive_path, archive_name in archive_paths:
        results = analyze_directory(archive_path, archive_name)
        all_results.extend(results)
    
    # Output to CSV
    output_file = current_dir / "folder_analysis.csv"
    
    if all_results:
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['archive', 'folder_name', 'size_bytes', 'size_mb', 'file_count']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            writer.writerows(all_results)
        
        print(f"\nResults written to: {output_file}")
        
        # Print summary statistics
        print("\n" + "="*80)
        print("SUMMARY STATISTICS")
        print("="*80)
        
        for archive_name in set(result['archive'] for result in all_results):
            archive_results = [r for r in all_results if r['archive'] == archive_name]
            
            if archive_results:
                total_folders = len(archive_results)
                total_size_bytes = sum(r['size_bytes'] for r in archive_results)
                total_files = sum(r['file_count'] for r in archive_results)
                
                print(f"\n{archive_name}:")
                print(f"  Total folders: {total_folders}")
                print(f"  Total size: {format_bytes(total_size_bytes)}")
                print(f"  Total files: {total_files:,}")
                
                if archive_results:
                    largest_folder = max(archive_results, key=lambda x: x['size_bytes'])
                    print(f"  Largest folder: {largest_folder['folder_name']} ({format_bytes(largest_folder['size_bytes'])})")
    
    else:
        print("No data found to analyze. Check that the directories exist and are accessible.")


if __name__ == "__main__":
    main()