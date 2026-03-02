---
name: web-scraping
description: "Extract data from web pages: navigate sites, parse HTML, handle pagination, and structure output. Use when user needs to scrape a website, extract data from web pages, or automate browser-based data collection."
license: Apache-2.0
metadata:
  author: Teaming24
  version: "1.0.0"
  category: automation
  tags: "web, scraping, data-extraction, browser"
allowed-tools: browser_navigate browser_action python_exec
---

# Web Scraping Skill

## When to Use

- User asks to scrape or extract data from a website
- User needs structured data from web pages (tables, lists, etc.)
- User mentions "scrape", "crawl", "extract from website"

## Workflow

1. **Plan**: Identify target URLs, data fields, and output format.
2. **Navigate**: Use `browser_navigate` to load pages.
3. **Extract**: Use `browser_action` (get_text, evaluate) to pull data.
4. **Transform**: Use `python_exec` to parse and structure the data.
5. **Paginate**: Handle next-page links or infinite scroll if needed.
6. **Output**: Save structured data (JSON, CSV) via `file_write`.

## Examples

- "Scrape product prices from this e-commerce page" — navigate, extract product names + prices, output as CSV
- "Get all article titles from this blog" — navigate, paginate, extract titles + URLs

## Guidelines

- Respect robots.txt and rate limits
- Add delays between requests to avoid being blocked
- Handle missing data gracefully (default values, skip)
- Validate extracted data before saving
- Use CSS selectors or XPath for reliable element targeting

## Output Formats

### JSON
```json
[
  {"field1": "value1", "field2": "value2"},
  {"field1": "value3", "field2": "value4"}
]
```

### CSV
```
field1,field2
value1,value2
value3,value4
```
