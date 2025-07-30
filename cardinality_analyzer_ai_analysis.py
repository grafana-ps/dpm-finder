#!/usr/bin/env python3

"""
AI Analysis module for Cardinality Analyzer
Provides LLM-based insights and recommendations for cardinality analysis results
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

def get_ai_analysis(analyses: List[Dict], comparisons: Optional[List[Dict]], 
                   window: str, start_time: Optional[str] = None) -> str:
    """
    Send cardinality analysis data to OpenAI and get insights
    
    Args:
        analyses: List of metric analysis results
        comparisons: Optional list of comparison results
        window: Time window analyzed
        start_time: Optional start time for the analysis
        
    Returns:
        AI-generated analysis and recommendations
    """
    try:
        # Import OpenAI only when needed
        from openai import OpenAI
    except ImportError:
        logger.error("OpenAI SDK not installed. Please install with: pip install -r requirements-cardinalityanalysis.txt")
        return "Error: OpenAI SDK not available"
    
    # Check for API key
    api_key = os.getenv('OPENAI_KEY')
    if not api_key:
        logger.error("OPENAI_KEY environment variable not set")
        return "Error: OPENAI_KEY not configured"
    
    # Get model from env or use default
    model = os.getenv('OPENAI_MODEL', 'gpt-4o')
    
    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)
    
    # Prepare the data summary for the AI
    data_summary = prepare_data_summary(analyses, comparisons, window, start_time)
    
    # System prompt explaining cardinality analysis
    system_prompt = """You are an expert in Prometheus/Mimir metrics and cardinality analysis. Your role is to analyze metric cardinality data and explain what the data shows, focusing on describing the current state and any changes between time windows.

Key concepts:
- Cardinality: The number of unique time series for a metric (unique combinations of label values)
- Labels: Key-value pairs that create dimensions for metrics (e.g., instance="server1", job="api")
- High cardinality labels: Those with many unique values that consume more resources

Your task is to:
1. Describe the current cardinality state for each metric and label
2. For comparisons: Explain exactly what changed between the two time windows
3. Highlight which labels have the highest cardinality and their specific values
4. Provide detailed breakdowns of the differences, not recommendations
5. Focus on facts and observations, not solutions

IMPORTANT: Do NOT provide recommendations, solutions, or next steps. The differences you observe are expected and intentional. Your role is purely to explain what the data shows in detail.

Format your response with:
- **Key Findings** (detailed bullet points of observations)
- **Detailed Analysis** (comprehensive breakdown by metric and label)
- For comparisons: **Changes Between Windows** (specific differences with numbers)

Use markdown formatting with **bold** for emphasis and proper bullet points."""
    
    # User prompt with the actual data
    user_prompt = f"""Please analyze this Prometheus/Mimir cardinality data and explain what it shows:

Analysis Window: {window} {f'(starting {start_time})' if start_time else '(recent)'}
Analysis Type: {'Comparison between two time windows' if comparisons else 'Single time window analysis'}

{data_summary}

Please provide a detailed explanation of:
1. The current cardinality levels for each metric and label
2. Which labels have the highest number of unique values and what those values are
3. For comparisons: Exactly what changed between the windows (with specific numbers and percentages)
4. The distribution of cardinality across different labels
5. Any patterns or notable observations in the data

Remember: Focus only on describing and explaining the data. Do not provide recommendations or solutions."""
    
    try:
        # Make the API call
        logger.info(f"Sending analysis to OpenAI using model: {model}")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,  # Lower temperature for more consistent analysis
            max_tokens=2000
        )
        
        # Extract the response
        ai_analysis = response.choices[0].message.content
        
        # Log token usage for monitoring
        if hasattr(response, 'usage'):
            logger.info(f"OpenAI token usage - Prompt: {response.usage.prompt_tokens}, "
                       f"Completion: {response.usage.completion_tokens}, "
                       f"Total: {response.usage.total_tokens}")
        
        return ai_analysis
        
    except Exception as e:
        logger.error(f"Error calling OpenAI API: {e}")
        return f"Error generating AI analysis: {str(e)}"

def prepare_data_summary(analyses: List[Dict], comparisons: Optional[List[Dict]], 
                        window: str, start_time: Optional[str]) -> str:
    """
    Prepare a structured summary of the cardinality data for AI analysis
    """
    summary_parts = []
    
    # Summary of analyzed metrics
    summary_parts.append(f"=== METRICS ANALYZED: {len(analyses)} ===\n")
    
    # For each metric, provide key cardinality information
    for analysis in analyses:
        metric_name = analysis['metric']
        data = analysis['data']
        total_info = data.get('__total__', {})
        
        # Get label cardinalities sorted by value
        label_cardinalities = []
        for label, info in data.items():
            if label == '__total__':
                continue
            if 'total_cardinality' in info:
                label_cardinalities.append((label, info['total_cardinality'], info.get('top_values', [])))
        
        label_cardinalities.sort(key=lambda x: x[1], reverse=True)
        
        summary_parts.append(f"\nMetric: {metric_name}")
        if total_info:
            summary_parts.append(f"  Total Cardinality: max={total_info.get('max_cardinality', 'N/A')}, "
                               f"avg={total_info.get('avg_cardinality', 'N/A'):.0f}")
        
        summary_parts.append("  Top Labels by Cardinality:")
        for label, cardinality, top_values in label_cardinalities[:5]:
            summary_parts.append(f"    - {label}: {cardinality} unique values")
            if top_values:
                top_3 = ', '.join([f"{val[0]} ({val[1]:.0f} series)" for val in top_values[:3]])
                summary_parts.append(f"      Top values: {top_3}")
    
    # Add comparison data if available
    if comparisons:
        summary_parts.append("\n\n=== COMPARISON ANALYSIS ===")
        summary_parts.append("Changes between time windows:\n")
        
        for comp in comparisons:
            metric_name = comp['metric']
            summary_parts.append(f"\nMetric: {metric_name}")
            summary_parts.append("  Significant changes:")
            
            # Get top 5 changes by absolute value
            changes = comp.get('sorted_changes', [])[:5]
            for label, change_data in changes:
                change = change_data['change']
                change_pct = change_data['change_pct']
                if abs(change) > 0:
                    direction = "increased" if change > 0 else "decreased"
                    summary_parts.append(f"    - {label}: {direction} by {abs(change)} "
                                       f"({abs(change_pct):.1f}%) "
                                       f"[{change_data['before']} → {change_data['after']}]")
    
    # Add observation notes
    summary_parts.append("\n\n=== NOTABLE OBSERVATIONS ===")
    summary_parts.append("Key patterns in the data:")
    summary_parts.append("- Labels with extremely high unique value counts (>1000)")
    summary_parts.append("- Significant changes in cardinality between time windows")
    summary_parts.append("- Distribution of cardinality across different label types")
    summary_parts.append("- Metrics with the most diverse label combinations")
    
    return '\n'.join(summary_parts)

def convert_markdown_to_html(text: str) -> str:
    """
    Convert basic markdown to HTML for better formatting
    """
    import html
    import re
    
    # First escape HTML to prevent injection
    text = html.escape(text)
    
    # Convert markdown formatting
    # Headers
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    
    # Bold text
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    
    # Italic text
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    
    # Bullet points
    lines = text.split('\n')
    in_list = False
    formatted_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- '):
            if not in_list:
                formatted_lines.append('<ul>')
                in_list = True
            formatted_lines.append(f'<li>{stripped[2:]}</li>')
        else:
            if in_list and not stripped.startswith('  '):
                formatted_lines.append('</ul>')
                in_list = False
            if stripped:
                formatted_lines.append(f'<p>{line}</p>')
            else:
                formatted_lines.append('<br>')
    
    if in_list:
        formatted_lines.append('</ul>')
    
    return '\n'.join(formatted_lines)

def generate_ai_report_section(ai_analysis: str) -> str:
    """
    Generate an HTML section for the AI analysis to be inserted into the main report
    """
    # Convert markdown to HTML
    formatted_analysis = convert_markdown_to_html(ai_analysis)
    
    html_section = f"""
    <div class="ai-analysis-section">
        <h2>AI Analysis</h2>
        <div class="ai-disclaimer">
            <strong>Note:</strong> This analysis was generated by {os.getenv('OPENAI_MODEL', 'gpt-4o')} 
            based on the cardinality data.
        </div>
        <div class="ai-content">
            {formatted_analysis}
        </div>
    </div>
    
    <style>
        .ai-analysis-section {{
            margin: 30px 0;
            padding: 25px;
            background: #f0f7ff;
            border: 1px solid #2196f3;
            border-radius: 8px;
        }}
        .ai-analysis-section h2 {{
            color: #1976d2;
            margin-bottom: 15px;
        }}
        .ai-disclaimer {{
            background: #fff3cd;
            border: 1px solid #ffeaa7;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .ai-content {{
            background: white;
            padding: 20px;
            border-radius: 4px;
            line-height: 1.8;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }}
        .ai-content h1, .ai-content h2, .ai-content h3 {{
            color: #333;
            margin-top: 20px;
            margin-bottom: 10px;
        }}
        .ai-content h1 {{ font-size: 1.8em; }}
        .ai-content h2 {{ font-size: 1.5em; }}
        .ai-content h3 {{ font-size: 1.2em; }}
        .ai-content p {{
            margin: 10px 0;
        }}
        .ai-content ul {{
            margin: 10px 0;
            padding-left: 30px;
        }}
        .ai-content li {{
            margin: 5px 0;
        }}
        .ai-content strong {{
            color: #1976d2;
            font-weight: 600;
        }}
        .ai-content em {{
            font-style: italic;
            color: #666;
        }}
    </style>
    """
    
    return html_section