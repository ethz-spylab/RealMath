"""
Theorem Extractor: Extract High-Quality Theorems from Mathematics Papers

This script processes arXiv mathematics papers to extract theorems.
It works by:
1. Extracting theorems from LaTeX source using regex pattern matching
2. Filtering for high-quality, well-formatted theorems
3. Verifying LaTeX compilation to ensure theorem quality

Key features:
- Customized LaTeX parsing to extract theorem content with context
- Outputs a structured dataset of theorems with links to source papers
- Advanced filtering to ensure theorem quality

Usage:
  python extract_theorems.py --input <dataset_path> [--output <path>] [--sample_papers <num>]
  
  For dataset processing:
    python extract_theorems.py --input arxiv_math_papers_full_text --output theorem_dataset

Dependencies:
  - OpenAI API access (for theorem quality verification)
  - tqdm, datasets, requests (for processing and output)
"""

import re
import os
import argparse
import json

from datasets import Dataset, load_from_disk
import openai
from tqdm import tqdm
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
import shutil
import tempfile
import subprocess
from prompts import SYSTEM_PROMPT_STANDARDIZE_LATEX, SYSTEM_PROMPT_THEOREM_QUALITY

load_dotenv()
console = Console()

# Default API key - replace with your own or provide via argument
DEFAULT_API_KEY = os.getenv("OPENAI_API_KEY")




def setup_random_seed(seed=42):
    """
    Set the random seed for reproducibility.
    """
    import random
    import numpy as np
    random.seed(seed)
    np.random.seed(seed)




class TheoremExtractor:
    """
    A class for extracting high-quality theorems from mathematical papers.
    
    This class provides functionality to:
    1. Process LaTeX files to extract theorems
    2. Filter for high-quality, well-formatted theorems
    3. Process datasets of mathematics papers
    """
    
    def __init__(self):
        """
        Initialize the TheoremExtractor.
        """
        self.api_key = DEFAULT_API_KEY
        self.client = openai.OpenAI(api_key=self.api_key)

    def extract_theorems(self, latex_text):
        """
        Extract theorems from LaTeX text.
        
        Args:
            latex_text (str): LaTeX source code
            
        Returns:
            list: List of dictionaries with label, content, and position of theorems
        """
        def _detect_section_numbering(latex_text):
            """Helper method to detect if the document uses section-based theorem numbering."""
            numbering_patterns = [
                r'\\numberwithin{theorem}{section}',
                r'\\numberwithin{thm}{section}',
                r'\\renewcommand{\\thethm}{\\thesection\\.\\arabic{thm}}',
                r'\\renewcommand{\\thetheorem}{\\thesection\\.\\arabic{theorem}}',
                r'\\newtheorem{theorem}{Theorem}[section]'
            ]
            
            for pattern in numbering_patterns:
                if re.search(pattern, latex_text):
                    return True
            return False
        
        def _extract_section_data(latex_text):
            """Helper method to extract section numbers and positions."""
            section_data = []
            section_pattern = r'\\section\s*(?:\[.*?\])?\s*{([^}]*)}'
            section_matches = re.finditer(section_pattern, latex_text)
            
            current_section_num = 0
            for match in section_matches:
                current_section_num += 1
                # Some papers might explicitly number sections like \section{2. Main Results}
                section_title = match.group(1)
                explicit_num_match = re.match(r'^(\d+)[.\s]+', section_title)
                if explicit_num_match:
                    explicit_num = int(explicit_num_match.group(1))
                    if explicit_num > 0:  # Only use valid section numbers
                        current_section_num = explicit_num
                
                section_data.append({
                    'number': current_section_num,
                    'position': match.start(),
                    'title': section_title
                })
            
            return section_data
        
        def _get_theorem_patterns(latex_text):
            """Helper method to define theorem patterns and find custom environments."""
            # Base patterns for standard theorem environments
            patterns = {'theorem': r'\\begin{theorem}(.*?)\\end{theorem}'}
            numbered_patterns = {'theorem': r'\\begin{theorem}\[([^]]+)\](.*?)\\end{theorem}'}
            
            # Find custom theorem environments
            custom_theorem_envs = []
            custom_pattern = r'\\newtheorem{([^}]+)}{([^}]+)}'
            
            for match in re.finditer(custom_pattern, latex_text):
                env_name = match.group(1)
                display_name = match.group(2)
                
                if 'theorem' in env_name.lower() or 'theorem' in display_name.lower():
                    custom_theorem_envs.append({
                        'env_name': env_name,
                        'display_name': display_name
                    })
                    
                    # Add custom environment patterns
                    patterns[env_name] = r'\\begin{' + env_name + r'}(.*?)\\end{' + env_name + r'}'
                    numbered_patterns[env_name] = r'\\begin{' + env_name + r'}\[([^]]+)\](.*?)\\end{' + env_name + r'}'
            
            return patterns, numbered_patterns, custom_theorem_envs
        
        def _extract_numbered_theorems(latex_text, numbered_patterns, custom_theorem_envs):
            """
            Extract explicitly numbered theorems from LaTeX text.
            
            Args:
                latex_text (str): LaTeX text to process
                numbered_patterns (dict): Patterns for numbered theorems
                custom_theorem_envs (list): Custom theorem environments
                
            Returns:
                list: List of extracted theorems
            """
            results = []
            
            for env_type, pattern in numbered_patterns.items():
                matches = re.finditer(pattern, latex_text, re.DOTALL)
                for match in matches:
                    number = match.group(1).strip()
                    content = match.group(2).strip()
                    start_pos = match.start()
                    end_pos = match.end()
                    
                    # Try to extract label if present
                    label_match = re.search(r'\\label{(.*?)}', content)
                    label = label_match.group(1) if label_match else None
                    
                    # Remove label from content if found
                    if label_match:
                        content = content.replace(label_match.group(0), '').strip()
                    
                    # Create display name based on type of environment
                    display_name = "Theorem"  # Default
                    if env_type != 'theorem':
                        for env in custom_theorem_envs:
                            if env['env_name'] == env_type:
                                display_name = env['display_name']
                                break
                    
                    # Create display label with the explicitly provided number
                    display_label = f"{display_name} {number}"
                    
                    results.append({
                        'type': env_type,
                        'label': label,
                        'display_label': display_label,
                        'content': content,
                        'start_pos': start_pos,
                        'end_pos': end_pos
                    })
                    
            return results
        
        def _extract_regular_theorems(latex_text, patterns, custom_theorem_envs, 
                                   section_data, section_numbering, theorem_counters):
            """
            Extract regular (unnumbered) theorems from LaTeX text.
            
            Args:
                latex_text (str): LaTeX text to process
                patterns (dict): Patterns for regular theorems
                custom_theorem_envs (list): Custom theorem environments
                section_data (list): Section information
                section_numbering (bool): Whether section-based numbering is used
                theorem_counters (dict): Counters for each theorem environment
                
            Returns:
                list: List of extracted theorems
            """
            results = []
            
            for env_type, pattern in patterns.items():
                matches = re.finditer(pattern, latex_text, re.DOTALL)
                for match in matches:
                    content = match.group(1).strip()
                    start_pos = match.start()
                    end_pos = match.end()
                    
                    # Try to extract label if present
                    label_match = re.search(r'\\label{(.*?)}', content)
                    label = label_match.group(1) if label_match else None
                    
                    # Remove label from content if found
                    if label_match:
                        content = content.replace(label_match.group(0), '').strip()
                    
                    # Create display name based on type of environment
                    display_name = "Theorem"  # Default
                    if env_type != 'theorem':
                        for env in custom_theorem_envs:
                            if env['env_name'] == env_type:
                                display_name = env['display_name']
                                break
                    
                    # Determine the current section for this theorem
                    current_section = None
                    if section_numbering and section_data:
                        for i, section in enumerate(section_data):
                            if section['position'] < start_pos:
                                current_section = section
                            else:
                                # We've gone past the current section
                                break
                    
                    # Increment the counter for this type of theorem
                    theorem_counters[env_type] += 1
                    
                    # Look for explicit theorem number in a larger surrounding context
                    surrounding_text = latex_text[max(0, start_pos-1000):min(len(latex_text), end_pos+1000)]
                    
                    # Set of patterns to find theorem numbers
                    num_patterns = [
                        # Check for theorem reference with label
                        (r'\\ref{' + re.escape(label) + r'}[\s\n]*([0-9.]+)', lambda m: m.group(1)) if label else (None, None),
                        # Check for \label with numbering
                        (r'\\label{(?:theorem|thm)(?::|_|\-)([0-9.]+)', lambda m: m.group(1)),
                        (r'\\label{(?:th|theorem|Theorem):?([0-9.]+(?:\.[0-9]+)?)', lambda m: m.group(1)),
                        # Check for theorem tag
                        (r'\\tag{\(?([^}]+)\)?}', lambda m: m.group(1)),
                        # Check for explicit reference in text
                        (r'(?:Theorem|theorem)[\s~]*(?:\\ref{[^}]*}|([0-9.]+))', lambda m: m.group(1) if m.group(1) else None),
                        # Check for theorem numbering in text
                        (r'(?:Theorem|theorem)[\s~]*([0-9]+\.[0-9]+)', lambda m: m.group(1)),
                        (r'(?:Theorem|theorem)[\s~]*([0-9]+)', lambda m: m.group(1))
                    ]
                    
                    # Try all patterns to find a theorem number
                    theorem_number = None
                    for pattern, extract in num_patterns:
                        if pattern is None:
                            continue
                        number_match = re.search(pattern, surrounding_text, re.IGNORECASE)
                        if number_match and extract(number_match):
                            theorem_number = extract(number_match)
                            break
                    
                    # If we have a label but couldn't find a number, try to extract it from the label
                    if not theorem_number and label:
                        label_number_match = re.search(r'([0-9]+(?:\.[0-9]+)?)', label)
                        if label_number_match:
                            theorem_number = label_number_match.group(1)
                    
                    # Default to section based numbering if enabled
                    if not theorem_number and section_numbering and current_section:
                        theorem_number = f"{current_section['number']}.{theorem_counters[env_type]}"
                    elif not theorem_number:
                        # Use simple counter if nothing else worked
                        theorem_number = str(theorem_counters[env_type])
                    
                    # Create the final display label
                    display_label = f"{display_name} {theorem_number}"
                    
                    results.append({
                        'type': env_type,
                        'label': label,
                        'display_label': display_label,
                        'content': content,
                        'start_pos': start_pos,
                        'end_pos': end_pos
                    })
                    
            return results
            
        # Analyze document for section numbering
        section_numbering = _detect_section_numbering(latex_text)
        
        # Extract sections and their positions
        section_data = _extract_section_data(latex_text)
        
        # Define patterns for theorems and find custom theorem environments
        patterns, numbered_patterns, custom_theorem_envs = _get_theorem_patterns(latex_text)
        
        # Initialize theorem counters
        theorem_counters = {env_name: 0 for env_name in patterns.keys()}
        
        results = []
        
        # Process explicitly numbered theorems first
        numbered_theorems = _extract_numbered_theorems(
            latex_text, numbered_patterns, custom_theorem_envs)
        
        results.extend(numbered_theorems)
        # process regular theorems, which are not numbered
        regular_theorems = _extract_regular_theorems(
            latex_text, patterns, custom_theorem_envs, section_data, 
            section_numbering, theorem_counters)
        results.extend(regular_theorems)
        
        #! check if the end_pos is the same for some duplicates, filter them out
        num_removed = 0
        end_pos_cache = set()
        for result in results:
            if result['end_pos'] in end_pos_cache:
                #! remove the duplicate
                results.remove(result)
                num_removed += 1
            else:
                end_pos_cache.add(result['end_pos'])
        if num_removed > 0:
            console.print(f"[bold green]Removed {num_removed} duplicates[/bold green]")
        # Sort by position in the document
        results.sort(key=lambda x: x['start_pos'])


        return results
    
    def get_context_before(self, latex_text, position):
        """
        Get the context before a given position in the LaTeX text, including all content.
        
        Args:
            latex_text (str): LaTeX text to process
            position (int): Position to get context before
            
        Returns:
            str: Context text
        """
        # Start from the beginning of the file
        start_pos = 0
        
        # Get the text between start_pos and position without filtering
        context = latex_text[start_pos:position]
        
        # Clean up whitespace and normalize spacing
        context = re.sub(r'\s+', ' ', context)
        context = context.strip()

        return context

    def remove_latex_comments(self, latex_text):
        """
        Remove LaTeX comments from the text.
        
        This function removes:
        1. Line comments (starting with % and continuing to the end of the line)
        2. Respects LaTeX's escaped % character (i.e., \\% is not treated as a comment marker)
        
        Returns the LaTeX text with all comments removed.
        """
        # Use regex to remove comments but preserve escaped % characters
        # First, temporarily replace escaped % with a unique marker
        text = re.sub(r'\\%', 'ESCAPED_PERCENT_PLACEHOLDER', latex_text)
        
        # Remove comments (% to end of line)
        text = re.sub(r'%.*?(?:\n|$)', '\n', text)
        
        # Restore escaped % characters
        text = re.sub(r'ESCAPED_PERCENT_PLACEHOLDER', '\\%', text)
        
        # Clean up excessive newlines that might have been created
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        
        return text

    def extract_custom_commands(self, latex_text):
        """
        Extract custom LaTeX command definitions from the text.
        
        Args:
            latex_text (str): LaTeX text to process
            
        Returns:
            str: Extracted custom command definitions
        """
        # Common patterns for custom command definitions
        command_patterns = [
            r'\\newcommand{\\[^}]+}(\[[\d]+\])?{[^}]+}',
            r'\\DeclareMathOperator{\\[^}]+}{[^}]+}',
            r'\\def\\[A-Za-z0-9]+(\[[^\]]*\])?{[^}]+}',
            r'\\renewcommand{\\[^}]+}(\[[\d]+\])?{[^}]+}'
        ]
        
        # Extract all matching custom commands
        custom_commands = []
        for pattern in command_patterns:
            matches = re.findall(pattern, latex_text)
            if matches:
                for match in re.finditer(pattern, latex_text):
                    custom_commands.append(match.group(0))
        
        # Return as a string with one command per line
        return '\n'.join(custom_commands)

    def evaluate_theorem_uniqueness(self, theorem_content):
        """
        Use o3-mini-2025-01-31 to evaluate if a theorem has a single, definitive answer.
        
        Args:
            theorem_content (str): The content of the theorem
            
        Returns:
            tuple: (single_unique_answer, theorem, explanation)
        """
        default_result = {
            "explanation": "",
            "single_unique_answer": "false"
        }
        try:
            # call the model untill we are not missing any keys
            user_prompt = f"""Please evaluate this mathematical theorem and determine if it has a single, definitive answer:

                {theorem_content}

                Please explain if it has a single, definitive answer. please be very strict about the theorem, if there is any ambiguity, you should deem it as 'non-unique'.
                Return in this exact JSON format:
                {{
                    "single_unique_answer": "true" if the theorem has a single, definitive answer, otherwise "false"
                    "explanation": "explanation of if this theorem has a single, definitive answer, otherwise an empty string",
                }}
                """
            # make sure that the result has all the keys
            iteration = 0
            while True:
                iteration += 1
                response = self.client.chat.completions.create(
                    model="o3-mini-2025-01-31",
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT_THEOREM_QUALITY},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    # max_tokens=1000
                )
                response_content = response.choices[0].message.content
                result = json.loads(response_content)
                # check if all the keys are present
                if all(key in result for key in default_result.keys()):
                    break
                if iteration > 5:
                    console.print(f"[bold red]Stupid model, still not returning the correct keys when evaluating theorem uniqueness[/bold red]")
                    return default_result
            # once we get the result from the model, just return it   
            return result
        except Exception as e:
            console.print(f"[bold red]Calling GPT models failed: {e}[/bold red]")
            return default_result
        
      
    def process_paper(self, latex_text, skip_appendix=True, paper_link=""):
        """
        Process a LaTeX paper to extract high-quality theorems.
        
        Args:
            latex_text (str): The LaTeX text to process
            skip_appendix (bool): Whether to skip theorems from appendices
            paper_link (str): Link to the original paper
            
        Returns:
            list: List of single, definitive theorems extracted from the paper
        """
        # Remove comments from LaTeX text
        custom_commands = self.extract_custom_commands(latex_text)
        latex_text = self.remove_latex_comments(latex_text)
        
        def _skip_appendix(latex_text):
            # If skip_appendix is True, extract only the main text by finding the appendix start position
            if skip_appendix:
                # Detect appendix sections in the document
                appendix_positions = []
                appendix_patterns = [
                    r'\\appendix',
                    r'\\section{Appendix}',
                    r'\\section{Appendices}',
                    r'\\section{\s*A\s+.*?}',  # Section A or Appendix A
                    r'\\section{.*?Appendix.*?}',
                    r'\\begin{appendix}',
                    r'\\part{Appendix}'
                ]
                
                for pattern in appendix_patterns:
                    for match in re.finditer(pattern, latex_text, re.IGNORECASE):
                        appendix_positions.append(match.start())
                
                # If we found appendix markers, truncate the latex_text to only include content before the appendix
                if appendix_positions:
                    appendix_start = min(appendix_positions)
                    latex_text = latex_text[:appendix_start]
            return latex_text
        
        latex_text = _skip_appendix(latex_text)
        
        # Extract theorems from the (possibly truncated) latex text
        theorems = self.extract_theorems(latex_text)
        num_theorems = len(theorems)
        
        if num_theorems == 0:
            console.print(f"[yellow]No theorems found in the paper, skipping[/yellow]")
            return [], 0
            
        high_quality_theorems = []
        
        # Function to create a test LaTeX document with a theorem
        def _create_test_latex(theorem_content):
            return r"""\documentclass{article}
                \usepackage{amsmath, amssymb, enumerate, amsfonts, mathrsfs, mathtools, logicproof}
                \usepackage{geometry}
                \usepackage{hyperref}
                \usepackage{xcolor}
                \usepackage{fancyhdr}
                \usepackage{tcolorbox}
                \newtheorem{theorem}{Theorem}
                \begin{document}
                \section{Theorem Test}
                
                """ + theorem_content + r"""
                \end{document}"""
        
        for i, theorem in enumerate(theorems):
            console.print(f"[bold]Processing theorem {i+1}/{num_theorems}[/bold]")
            
            # Get context before the theorem
            context = self.get_context_before(latex_text, theorem['start_pos'])
            # Evaluate theorem quality - now returns is_high_quality, theorem text, and explanation
            result_unique = self.evaluate_theorem_uniqueness(theorem['content'])
            if result_unique['single_unique_answer'] == "false":
                console.print(f"[yellow]Theorem {i+1} does not have a single, definitive answer, skipping[/yellow]")
                continue
            
            formatted_theorem = theorem['content']

            # finally, we filter out samples that don't have a single, definitive answer, and cannot be compiled
            high_quality_theorems.append({
                "paper_link": paper_link,
                "theorem": formatted_theorem,
                "context": context,
                "unique_answer_explanation": result_unique['explanation'],
            })

        return high_quality_theorems, num_theorems

    def process_dataset(self, input_path, output_path, sample_papers=None, skip_appendix=True):
        """
        Process a dataset of LaTeX papers.
        
        Args:
            input_path (str): Path to the input dataset
            output_path (str): Path to save the output dataset
            sample_papers (int, optional): Number of papers to process
            skip_appendix (bool): Whether to skip theorems from appendices
            
        Returns:
            Dataset: Dataset of high-quality theorems extracted from the papers
        """
        # Initialize empty result containers
        all_ids = []
        all_paper_links = []
        all_contexts = []
        all_theorems = []
        all_unique_answer_explanations = []
        input_dataset = load_from_disk(input_path)
        # Shuffle the dataset
        input_dataset = input_dataset.shuffle(seed=42)
        
        console.print(f"[green]Loaded {len(input_dataset)} papers from {input_path}[/green]")
        
        # Remove duplicates in the dataset
        original_size = len(input_dataset)
        # First try to remove duplicates based on paper_link if available
        if 'paper_link' in input_dataset.column_names:
            # Get unique papers based on paper_link
            unique_links = set()
            unique_indices = []
            
            for i, paper in enumerate(input_dataset):
                link = paper['paper_link']
                if link not in unique_links:
                    unique_links.add(link)
                    unique_indices.append(i)

            
            input_dataset = input_dataset.select(unique_indices)
            console.print(f"[yellow]Removed {original_size - len(input_dataset)} duplicate papers based on paper_link[/yellow]")
                
        console.print(f"[green]After removing duplicates, {len(input_dataset)} papers remain[/green]")
        if sample_papers:
            # Select papers with indices
            start_index = 0
            end_index = sample_papers
            # Make sure indices are within dataset bounds
            end_index = min(end_index, len(input_dataset))

            indices = range(start_index, end_index)
            input_dataset = input_dataset.select(indices)
            output_path = f"{output_path}_{start_index}_{end_index}"
            console.print(f"[yellow]Selected papers from index {start_index} to {end_index-1}, saving to {output_path}[/yellow]")
        
        total_theorems = 0
        total_unique_theorems = 0
        
        for i, paper in enumerate(input_dataset):
            console.print(
                Panel(f"Processing paper {i+1} / {len(input_dataset)}", title="Processing Paper", border_style="green")
            )
            
            latex_text = paper['full_text']
            paper_link = paper.get('paper_link', f"paper_{i}")
            unique_theorems, num_theorems = self.process_paper(latex_text, skip_appendix, paper_link)
            total_theorems += num_theorems
            total_unique_theorems += len(unique_theorems)
            
            console.print(f"[green]Found {len(unique_theorems)} high-quality theorems out of {num_theorems} total[/green]")
            
            # Add theorems to our collections
            for theorem in unique_theorems:
                all_ids.append(len(all_ids))
                all_paper_links.append(paper_link)
                all_contexts.append(theorem['context'])
                all_theorems.append(theorem['theorem'])
                all_unique_answer_explanations.append(theorem['unique_answer_explanation'])
            
            # Print running totals after each paper
            console.print(f"[cyan]Running totals - Total theorems found: {total_theorems}, High-quality theorems: {total_unique_theorems}, Dataset size: {len(all_ids)}[/cyan]")
            

        
        console.print(
            Panel(
                f"[bold green]Processing complete![/bold green]\n\n"
                f"Total papers processed: {len(input_dataset)}\n"
                f"Total theorems found: {total_theorems}\n"
                f"Total unique theorems found: {total_unique_theorems}\n",
                title="Extraction Results",
                border_style="green"
            )
        )
        # Create the dataset with updated fields
        dataset = Dataset.from_dict({
            'id': all_ids,
            'paper_link': all_paper_links,
            'context': all_contexts,
            'theorem': all_theorems,
            'unique_answer_explanation': all_unique_answer_explanations,
        })
        
        
        
        return dataset


def remove_duplicates(dataset):
    seen_contexts = set()

    def is_first_occurrence(example):
        if example['context'] in seen_contexts:
            return False
        seen_contexts.add(example['context'])
        return True
    print(f"length of dataset before removing duplicates: {len(dataset)}")
    dataset = dataset.filter(is_first_occurrence)
    print(f"length of dataset after removing duplicates: {len(dataset)}")
    return dataset
    

def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(description="Extract high-quality theorems from LaTeX papers")
    parser.add_argument("--input", type=str, default="arxiv_math_papers_full_text", help="Path to the input dataset of LaTeX papers")
    parser.add_argument("--output", type=str, default="theorem_dataset", help="Path to save the output dataset")
    parser.add_argument("--sample_papers", type=int, help="Number of papers to process")
    parser.add_argument("--include_appendix", action="store_true", help="Include theorems from appendices (default: skip appendix theorems)")
    args = parser.parse_args()
    setup_random_seed(seed=42)

    console.print(
        Panel(
            "This tool extracts high-quality theorems from LaTeX papers.\n"
            "The theorems are filtered for quality, mathematical significance, and proper formatting.",
            title="Theorem Extractor",
            border_style="blue"
        )
    )
    
    # Print appendix status
    if not args.include_appendix:
        console.print("[yellow]Appendix theorems: EXCLUDED[/yellow]")
        console.print("[yellow]Theorems from appendices will be skipped. Use --include_appendix to include them.[/yellow]")
    else:
        console.print("[green]Appendix theorems: INCLUDED[/green]")
        console.print("[green]Theorems from appendices will be included in the output.[/green]")
    
    # Create an instance of TheoremExtractor
    extractor = TheoremExtractor()
    
    console.print(f"[bold]Processing dataset of LaTeX papers: {args.input}[/bold]")
    dataset = extractor.process_dataset(
        input_path=args.input,
        output_path=args.output,
        sample_papers=args.sample_papers,
        skip_appendix=not args.include_appendix,
    )
    dataset = remove_duplicates(dataset)
    dataset.save_to_disk(args.output)
    console.print(f"[bold] Processed dataset saved to {args.output}[/bold]")



if __name__ == "__main__":
    main() 