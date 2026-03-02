#!/usr/bin/env python3
"""
Multi-Agent Framework Demo

Demonstrates the CrewAI integration with Teaming24:
1. Local crew execution
2. Remote delegation via AgentaNet
3. Task tracking and cost display

Usage:
    # Run with mock LLM (no API key required)
    uv run python examples/multi_agent_demo.py --mock
    
    # Run with real LLM (requires OPENAI_API_KEY)
    uv run python examples/multi_agent_demo.py
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from teaming24.config import get_config
from teaming24.task import get_task_manager
from teaming24.agent import (
    check_crewai_available,
    create_local_crew,
    CostTracker,
    format_cost_display,
)
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def print_step(step_data: dict):
    """Print a step event."""
    agent = step_data.get("agent", "Unknown")
    action = step_data.get("action", "step")
    content = step_data.get("content", "")[:200]
    
    icon = {
        "think": "🤔",
        "delegate": "📤",
        "finish": "✅",
        "step": "⚙️",
    }.get(action, "➡️")
    
    print(f"  {icon} [{agent}] {action}: {content}")


async def demo_local_execution(task_manager, use_mock: bool = True):
    """
    Demo: Execute a task with local crew.
    
    Scenario: Frontend Team handles a React component task.
    """
    print_header("Demo 1: Local Crew Execution")
    
    prompt = "Create a simple React button component with hover effects"
    print(f"Task: {prompt}\n")
    
    # Create crew with step callback
    def on_step(step_data):
        print_step(step_data)
    
    crew = create_local_crew(task_manager, on_step=on_step)
    
    # Check capabilities
    caps = crew.get_capabilities()
    print(f"Local capabilities: {', '.join(caps)}\n")
    
    # Check if can handle
    required = ["react", "typescript"]
    can_handle = crew.can_handle(required)
    print(f"Can handle {required}? {can_handle}\n")
    
    if use_mock:
        # Mock execution for demo
        print("(Mock execution - install crewai for real execution)\n")
        
        # Simulate steps
        steps = [
            {"agent": "Product Manager", "action": "think", "content": "Analyzing button requirements..."},
            {"agent": "React Developer", "action": "step", "content": "Creating Button.tsx component..."},
            {"agent": "React Developer", "action": "finish", "content": "Button component with hover effects created"},
        ]
        
        for step in steps:
            print_step(step)
            await asyncio.sleep(0.5)
        
        # Mock result
        result = """
```tsx
// Button.tsx
import React from 'react';

interface ButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
}

export const Button: React.FC<ButtonProps> = ({ children, onClick }) => {
  return (
    <button
      onClick={onClick}
      className="px-4 py-2 bg-blue-500 text-white rounded 
                 hover:bg-blue-600 transition-colors duration-200"
    >
      {children}
    </button>
  );
};
```
"""
        return {"status": "success", "result": result}
    else:
        # Real execution
        result = await crew.execute(prompt)
        return result


async def demo_delegation_scenario(task_manager, use_mock: bool = True):
    """
    Demo: Task delegation to remote node.
    
    Scenario: Frontend Team needs Python/SQLite work,
    delegates to Backend Team on AgentaNet.
    """
    print_header("Demo 2: Remote Delegation Scenario")
    
    prompt = "Build a stock analysis script with Python and SQLite"
    print(f"Task: {prompt}\n")
    
    # Create crew
    def on_step(step_data):
        print_step(step_data)
    
    crew = create_local_crew(task_manager, on_step=on_step)
    
    # Check local capabilities
    caps = crew.get_capabilities()
    print(f"Local capabilities: {', '.join(caps)}")
    
    # Check if can handle
    required = ["python", "sqlite"]
    can_handle = crew.can_handle(required)
    print(f"Can handle {required}? {can_handle}\n")
    
    if use_mock:
        print("(Mock delegation - no actual network communication)\n")
        
        # Simulate local attempt and delegation
        steps = [
            {"agent": "Local Coordinator", "action": "think", 
             "content": "Analyzing requirements: python, sqlite"},
            {"agent": "Local Coordinator", "action": "step", 
             "content": "Local workers lack python/sqlite capabilities"},
            {"agent": "Organizer", "action": "delegate", 
             "content": "Searching AgentaNet for backend_dev capability..."},
            {"agent": "Organizer", "action": "step", 
             "content": "Found: Backend Team (192.168.1.100:8001)"},
            {"agent": "Organizer", "action": "step", 
             "content": "Sending x402 payment (0.5 units)..."},
            {"agent": "Remote: Senior Python Dev", "action": "step", 
             "content": "Creating stock analysis script..."},
            {"agent": "Remote: DB Architect", "action": "step", 
             "content": "Setting up SQLite schema..."},
            {"agent": "Remote: Senior Python Dev", "action": "finish", 
             "content": "Stock analysis script completed"},
            {"agent": "Organizer", "action": "finish", 
             "content": "Task completed via Backend Team"},
        ]
        
        for step in steps:
            print_step(step)
            await asyncio.sleep(0.5)
        
        # Mock result with cost
        result = """
```python
# stock_analysis.py
import sqlite3
from datetime import datetime

def create_database():
    conn = sqlite3.connect('stocks.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()
    return conn

def analyze_stocks(symbol: str):
    conn = create_database()
    # Analysis logic...
    return {"symbol": symbol, "trend": "bullish"}

if __name__ == "__main__":
    result = analyze_stocks("AAPL")
    print(result)
```
"""
        return {
            "status": "success", 
            "result": result,
            "cost": {
                "input_tokens": 500,
                "output_tokens": 800,
                "total_tokens": 1300,
                "llm_cost_usd": 0.044,
                "x402_payment": 0.5,
                "total_cost_usd": 0.544,
            }
        }
    else:
        # Real execution (would use network)
        result = await crew.execute(prompt)
        return result


async def demo_task_tracking(task_manager):
    """
    Demo: Task management and tracking.
    """
    print_header("Demo 3: Task Management")
    
    # List recent tasks
    tasks = task_manager.list_tasks(limit=5)
    print(f"Recent tasks: {len(tasks)}\n")
    
    for task in tasks:
        print(f"  - [{task.status.value}] {task.id}")
        print(f"    Prompt: {task.prompt[:50]}...")
        if task.duration:
            print(f"    Duration: {task.duration}s")
        if task.cost.total_tokens > 0:
            print(f"    Cost: {format_cost_display(task.cost.to_dict())}")
        print()


async def main():
    """Run the multi-agent demo."""
    parser = argparse.ArgumentParser(description="Multi-Agent Framework Demo")
    parser.add_argument("--mock", action="store_true", 
                       help="Use mock execution (no API key required)")
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("  TEAMING24 Multi-Agent Framework Demo")
    print("=" * 60)
    
    # Check CrewAI availability
    if check_crewai_available():
        print("\n✓ CrewAI is installed")
    else:
        print("\n⚠ CrewAI not installed - using mock mode")
        print("  Install with: uv pip install crewai")
        args.mock = True
    
    # Load config
    config = get_config()
    active_scenario = config.agents.get("active_scenario", "frontend_team")
    print(f"✓ Active scenario: {active_scenario}")
    
    # Initialize task manager
    task_manager = get_task_manager("demo-node")
    print(f"✓ Task manager initialized\n")
    
    # Run demos
    try:
        # Demo 1: Local execution
        result = await demo_local_execution(task_manager, args.mock)
        if result.get("status") == "success":
            print(f"\n✅ Result:\n{result['result'][:500]}...")
        
        # Demo 2: Delegation scenario
        result = await demo_delegation_scenario(task_manager, args.mock)
        if result.get("status") == "success":
            print(f"\n✅ Result:\n{result['result'][:500]}...")
            if "cost" in result:
                print(f"\n💰 Cost: {format_cost_display(result['cost'])}")
        
        # Demo 3: Task tracking
        await demo_task_tracking(task_manager)
        
    except KeyboardInterrupt:
        print("\n\nDemo interrupted.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise
    
    print("\n" + "=" * 60)
    print("  Demo completed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
