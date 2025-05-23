#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import arxiv
import datetime
import os
import logging
from datasets import Dataset
from typing import List, Dict, Any, Optional
import argparse


class ArxivMathPaperRetriever:
    """
    A class to retrieve papers from arXiv starting from a specified time
    until reaching a maximum number of papers.
    """
    
    def __init__(self, start_time: datetime.datetime, category: str = 'math', log_path: str = None):
        """
        Initialize the retriever with a start time and category.
        
        Args:
            start_time: The start time for paper retrieval
            category: The arXiv category to search (default: 'math')
                      Can be a main category (e.g., 'math', 'cs') or specific subcategory (e.g., 'cs.IT', 'math.AG')
            log_path: Path to save logs (default: None)
        """
        self.start_time = start_time
        self.category = category
        self.current_end_time = None
        
        # Setup logging
        self.logger = logging.getLogger('arxiv_retriever')
        self.logger.setLevel(logging.INFO)
        
        # Create a formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Create console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # Create file handler if log_path is provided
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            file_handler = logging.FileHandler(log_path)
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
    
    def retrieve_papers(self, max_results: int = 100, time_window_days: int = 30) -> List[Dict[str, Any]]:
        """
        Retrieve papers from arXiv within the specified category starting from start_time,
        incrementally increasing the time window until reaching max_results.
        
        Args:
            max_results: Maximum number of results to retrieve
            time_window_days: Number of days to extend the search window each iteration
            
        Returns:
            A list of dictionaries containing paper information
        """
        papers = []
        # Set to keep track of paper IDs we've already seen to avoid duplicates
        seen_paper_ids = set()
        current_end_time = self.start_time + datetime.timedelta(days=time_window_days)
        
        self.logger.info(f"Starting search for {self.category} papers from {self.start_time}...")
        
        while len(papers) < max_results:
            # Define the search query for the current time window
            # Check if the category has a subcategory format (containing a dot)
            if '.' in self.category:
                search_query = f'cat:{self.category}'  # Exact category match for subcategories
            else:
                search_query = f'cat:{self.category}.*'  # Wildcard for all subcategories
                
            search_query += ' AND submittedDate:[{} TO {}]'.format(
                self.start_time.strftime('%Y%m%d%H%M%S'),
                current_end_time.strftime('%Y%m%d%H%M%S')
            )
            
            # Set up the arXiv client
            client = arxiv.Client(
                page_size=100,  # Number of results per query
                delay_seconds=3,  # Be nice to the API
                num_retries=5    # Retry on failure
            )
            
            # Create the search
            search = arxiv.Search(
                query=search_query,
                max_results=max_results - len(papers),  # Only retrieve what we still need
                sort_by=arxiv.SortCriterion.SubmittedDate
            )
            # Track papers retrieved in this iteration
            papers_before = len(papers)
            papers_after = papers_before  # Initialize papers_after
            
            # Execute the search and process results
            try:
                for result in client.results(search):
                    paper_id = result.get_short_id()
                    
                    # Skip this paper if we've already seen it
                    if paper_id in seen_paper_ids:
                        continue
                        
                    # Add to seen set
                    seen_paper_ids.add(paper_id)
                    
                    title = result.title
                    paper_link = result.entry_id
                    
                    # Construct the LaTeX source link
                    latex_link = f"https://arxiv.org/e-print/{paper_id}"
                    
                    papers.append({
                        'id': paper_id,
                        'paper_link': paper_link,
                        'latex_link': latex_link,
                        'title': title
                    })
                    
                    # Print progress and check if we have enough papers
                    if len(papers) % 100 == 0:
                        self.logger.info(f"Retrieved {len(papers)} papers so far...")
                    
                    if len(papers) >= max_results:
                        break
                
                # Update papers_after if no exception occurred
                papers_after = len(papers)
            except arxiv.UnexpectedEmptyPageError as e:
                self.logger.warning(f"Encountered empty page error: {e}. Extending time window and continuing...")
                # papers_after remains the same as initialized
            except Exception as e:
                self.logger.error(f"Error during paper retrieval: {str(e)}. Extending time window and continuing...")
                # papers_after remains the same as initialized
            
            # If we didn't get any new papers in this iteration, extend the time window
            if papers_after == papers_before:
                self.logger.info(f"No new papers found in window {self.start_time} to {current_end_time}. Extending time window...")
                current_end_time += datetime.timedelta(days=time_window_days)
            else:
                self.logger.info(f"Retrieved {papers_after - papers_before} papers from {self.start_time} to {current_end_time}")
                
                # If we've reached max_results, break out of the loop
                if len(papers) >= max_results:
                    self.logger.info(f"Reached target of {max_results} papers.")
                    break
                    
                # Otherwise, extend the time window for the next iteration
                current_end_time += datetime.timedelta(days=time_window_days)
        
        self.current_end_time = current_end_time
        self.logger.info(f"Retrieved a total of {len(papers)} papers from {self.start_time} to {current_end_time}.")
        return papers
    
    def build_dataset(self, papers: Optional[List[Dict[str, Any]]] = None, max_results: int = 100) -> Dataset:
        """
        Build a Hugging Face dataset from the retrieved papers.
        
        Args:
            papers: List of paper dictionaries. If None, papers will be retrieved.
            max_results: Maximum number of results to retrieve if papers is None
            
        Returns:
            A Hugging Face Dataset object
        """
        if papers is None:
            papers = self.retrieve_papers(max_results=max_results)
        
        # Create a Hugging Face dataset
        dataset = Dataset.from_list(papers)
        
        self.logger.info(f"Created dataset with {len(dataset)} papers and columns: {dataset.column_names}")
        return dataset
    
    def save_dataset(self, output_path: str = None, max_results: int = 100) -> None:
        """
        Retrieve papers, build a dataset, and save it to disk.
        
        Args:
            output_path: Path where the dataset will be saved
            max_results: Maximum number of results to retrieve
        """
        if output_path is None:
            output_path = f"arxiv_{self.category}_papers"
            
        dataset = self.build_dataset(max_results=max_results)
        dataset.save_to_disk(output_path)
        self.logger.info(f"Dataset saved to {output_path}")
        self.logger.info(f"Final time window: {self.start_time} to {self.current_end_time}")


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Retrieve arXiv papers starting from a specific month until reaching max results.')
    parser.add_argument('--year', type=int, default=2023, help='Starting year to retrieve papers (default: 2023)')
    parser.add_argument('--month', type=int, default=3, help='Starting month to retrieve papers (default: 3 for March)')
    parser.add_argument('--category', type=str, default='math', 
                        help='arXiv category to search (default: math). Can be a main category or specific subcategory (e.g., cs.IT)')
    parser.add_argument('--output', type=str, default=None, help='Output directory for the dataset')
    parser.add_argument('--max-results', type=int, default=100, help='Maximum number of results to retrieve')
    parser.add_argument('--time-window-days', type=int, default=30, help='Days to extend search window in each iteration')
    
    args = parser.parse_args()
    
    # Validate month
    if args.month < 1 or args.month > 12:
        raise ValueError("Month must be between 1 and 12")
    
    # Calculate the start time
    start_time = datetime.datetime(args.year, args.month, 1)
    
    # Determine output path
    output_path = args.output if args.output else f"arxiv_{args.category}_papers"
    
    # Ensure the output directory exists
    os.makedirs(output_path, exist_ok=True)
    
    # Create log file path
    log_file = os.path.join(output_path, 'retrieval_log.txt')
    
    print(f"Retrieving {args.category} papers starting from {datetime.datetime.strftime(start_time, '%B %Y')}")
    print(f"Logs will be saved to {log_file}")
    
    # Create the retriever with log path
    retriever = ArxivMathPaperRetriever(start_time, category=args.category, log_path=log_file)
    
    # Retrieve papers and build dataset
    retriever.save_dataset(output_path=output_path, max_results=args.max_results)
    
    # Show a sample of the dataset
    dataset = Dataset.load_from_disk(output_path)
    print("\nSample of the dataset:")
    print(dataset[:5])

"""
Examples:
python arxiv_retriever.py --year 2022 --month 6 --category cs --output cs_june_2022_papers --max-results 200
python arxiv_retriever.py --year 2022 --month 6 --category cs.IT --output cs_it_june_2022_papers --max-results 200
"""

if __name__ == "__main__":
    main() 