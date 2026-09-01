#!/usr/bin/env python3
"""
Advanced course content extraction with intelligent boilerplate filtering.
Extracts only course-specific content from OCW HTML files.
"""

import os
import json
import re
from pathlib import Path
from html.parser import HTMLParser
from html import unescape

# Boilerplate patterns to filter
BOILERPLATE_PATTERNS = [
    r"Browse Course Material",
    r"Give Now|About OCW|Help & FAQs|Contact Us",
    r"Over 2,500 courses",
    r"Freely sharing knowledge",
    r"©.*Massachusetts Institute",
    r"Accessibility|Creative Commons|Terms and Conditions",
    r"You are leaving MIT",
    r"Please be advised that external sites",
    r"Stay Here|Continue",
    r"Proud member of",
    r"More Info|menu",
]

# Navigation menu items to filter
NAV_ITEMS = [
    "Syllabus", "Calendar", "Readings", "Assignments", "Pages",
    "Course Info", "Departments", "Learning Resource Types",
    "Browse Resources", "Cities of Commerce", "Moral Economy",
    "Shaping Medieval Markets", "Energy and the English",
    "Medieval Technology", "The Great Divergence", "Why the West"
]

class CleanTextExtractor(HTMLParser):
    """Extract clean text from HTML, filtering out boilerplate."""
    
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_content = False
        self.in_nav = False
        self.in_footer = False
        self.heading_stack = []
    
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip_content = True
        elif tag in ('nav', 'footer', 'noscript'):
            self.skip_content = True
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.heading_stack.append(tag)
    
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer', 'noscript'):
            self.skip_content = False
        elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            if self.heading_stack:
                self.heading_stack.pop()
            if self.text_parts:
                self.text_parts.append('\n')
        elif tag in ('p', 'div', 'section', 'article', 'li', 'td', 'tr', 'table'):
            if self.text_parts and self.text_parts[-1].strip():
                self.text_parts.append('\n')
    
    def handle_data(self, data):
        if not self.skip_content:
            text = data.strip()
            if text:
                self.text_parts.append(text + ' ')
    
    def get_text(self):
        """Return cleaned text with boilerplate removed."""
        text = ''.join(self.text_parts)
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        
        # Remove boilerplate lines
        lines = text.split('\n')
        filtered_lines = []
        
        for line in lines:
            line_stripped = line.strip()
            
            # Skip empty lines
            if not line_stripped:
                continue
            
            # Skip boilerplate patterns
            is_boilerplate = any(
                re.search(pattern, line_stripped, re.IGNORECASE) 
                for pattern in BOILERPLATE_PATTERNS
            )
            
            if is_boilerplate:
                continue
            
            # Skip pure navigation menus (lines with only nav items)
            if all(item in line_stripped for item in line_stripped.split() if item in NAV_ITEMS):
                continue
            
            filtered_lines.append(line_stripped)
        
        return '\n'.join(filtered_lines).strip()


def extract_html_text(html_path):
    """Extract clean text from HTML file."""
    try:
        with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
            html_content = f.read()
        
        extractor = CleanTextExtractor()
        extractor.feed(html_content)
        return extractor.get_text()
    except Exception as e:
        return f""


def extract_syllabus_json(json_path):
    """Extract structured data from syllabus JSON."""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract key fields
        extracted = {}
        key_map = {
            'title': ['title', 'course_title', 'name'],
            'description': ['description', 'course_description', 'overview'],
            'prerequisites': ['prerequisites', 'requirements'],
            'learning_outcomes': ['learning_outcomes', 'objectives', 'outcomes'],
            'grading': ['grading', 'grading_policy', 'grades'],
            'schedule': ['schedule', 'sessions', 'calendar'],
        }
        
        for target_key, source_keys in key_map.items():
            for source_key in source_keys:
                if source_key in data and data[source_key]:
                    extracted[target_key] = data[source_key]
                    break
        
        return extracted
    except:
        return {}


def process_course(course_path, course_name):
    """Process a single course directory, extracting key pages."""
    pages_dir = os.path.join(course_path, 'pages')
    
    if not os.path.exists(pages_dir):
        return None
    
    content_parts = [f"COURSE: {course_name}\n{'='*70}\n"]
    
    # Priority order: syllabus, then index, then supplementary pages
    page_order = [
        ('pages/syllabus/index.html', 'SYLLABUS'),
        ('pages/index.html', 'COURSE OVERVIEW'),
        ('pages/syllabus/data.json', 'COURSE METADATA'),
        ('pages/calendar/index.html', 'CALENDAR'),
        ('pages/assignments/index.html', 'ASSIGNMENTS'),
        ('pages/readings/index.html', 'READINGS'),
    ]
    
    extracted_count = 0
    
    for rel_path, label in page_order:
        full_path = os.path.join(course_path, rel_path)
        
        if rel_path.endswith('.json'):
            if os.path.exists(full_path):
                data = extract_syllabus_json(full_path)
                if data:
                    content_parts.append(f"\n{label}:")
                    for key, value in data.items():
                        content_parts.append(f"  {key.upper()}: {value}")
                    extracted_count += 1
        else:
            if os.path.exists(full_path):
                text = extract_html_text(full_path)
                if text and len(text) > 50:  # Only include if substantial
                    content_parts.append(f"\n{label}:")
                    content_parts.append(text)
                    extracted_count += 1
    
    if extracted_count > 0:
        return '\n'.join(content_parts)
    
    return None


def main():
    # --- FSTEM patch: accept a path instead of hardcoding one ---------------
    import argparse as _fstem_argparse
    _fstem_ap = _fstem_argparse.ArgumentParser(add_help=True)
    _fstem_ap.add_argument("root", nargs="?", default=None,
                           help="Directory of course folders. Default ~/Projects/OCW1")
    _fstem_ap.add_argument("--root", dest="root_flag", default=None,
                           help="Same as the positional argument.")
    _fstem_args, _ = _fstem_ap.parse_known_args()
    ocw1_path = os.path.expanduser(
        _fstem_args.root_flag or _fstem_args.root or "~/Projects/OCW1")
    print(f"[fstem] root: {ocw1_path}")
    # --- end FSTEM patch -----------------------------------------------------
    if not os.path.exists(ocw1_path):
        print(f"ERROR: {ocw1_path} does not exist")
        return
    
    courses = sorted([d for d in os.listdir(ocw1_path) 
                     if os.path.isdir(os.path.join(ocw1_path, d)) and not d.startswith('.')])
    
    print(f"Processing {len(courses)} courses with CLEAN extraction\n")
    
    success_count = 0
    
    for i, course in enumerate(courses, 1):
        course_path = os.path.join(ocw1_path, course)
        summary_file = os.path.join(course_path, f"{course}CLEAN.txt")
        
        content = process_course(course_path, course)
        
        if content:
            try:
                with open(summary_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"[{i:3d}] ✓ {course} → {course}CLEAN.txt")
                success_count += 1
            except Exception as e:
                print(f"[{i:3d}] ✗ {course} - Error: {e}")
        else:
            print(f"[{i:3d}] - {course} (no extractable content)")
    
    print(f"\n{'='*70}")
    print(f"COMPLETE: {success_count} clean summaries created")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()