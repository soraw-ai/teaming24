#!/usr/bin/env python3
"""
CrewAI + OpenHands Integration Demo

Demonstrates how to use CrewAI agents with OpenHands sandbox tools
for code execution, file operations, and more.

This example shows:
1. Creating agents with OpenHands tools
2. Executing code in a sandboxed environment
3. File operations through the agent
4. Multi-agent collaboration with sandbox access

Usage:
    # With mock mode (no OpenHands required)
    uv run python examples/crewai_openhands_demo.py --mock
    
    # With real OpenHands runtime (requires Docker)
    uv run python examples/crewai_openhands_demo.py
    
    # With OpenAI API
    export OPENAI_API_KEY=your-key
    uv run python examples/crewai_openhands_demo.py
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def check_dependencies():
    """Check available dependencies."""
    deps = {
        "crewai": False,
        "openhands": False,
    }
    
    try:
        import crewai
        deps["crewai"] = True
    except ImportError:
        pass
    
    try:
        from teaming24.runtime.openhands import check_openhands_available
        deps["openhands"] = check_openhands_available()
    except ImportError:
        pass
    
    return deps


async def demo_mock_execution():
    """Demo with mock execution (no external dependencies)."""
    print_header("Demo: Mock Execution Mode")
    
    print("This demo shows the structure without requiring CrewAI or OpenHands.\n")
    
    # Simulate agent workflow
    steps = [
        ("Code Developer", "Analyzing task requirements..."),
        ("Code Developer", "Creating Python script using shell_command tool..."),
        ("Code Developer", "Result: def fibonacci(n): ..."),
        ("Code Reviewer", "Reviewing code for best practices..."),
        ("Code Reviewer", "Using python_interpreter to test the code..."),
        ("Code Reviewer", "All tests passed!"),
    ]
    
    for agent, action in steps:
        print(f"  [{agent}] {action}")
        await asyncio.sleep(0.3)
    
    print("\n✅ Mock execution completed successfully!")
    return True


async def demo_with_crewai():
    """Demo using actual CrewAI with OpenHands tools."""
    print_header("Demo: CrewAI + OpenHands Integration")
    
    try:
        from crewai import Agent, Crew, Task, Process
        from teaming24.agent.tools import (
            create_openhands_tools,
            check_openhands_tools_available,
        )
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("   Install with: uv pip install crewai")
        return False
    
    # Check OpenHands availability
    openhands_available = check_openhands_tools_available()
    
    if openhands_available:
        print("✓ OpenHands tools available")
        tools = create_openhands_tools()
    else:
        print("⚠ OpenHands not available - using limited tool set")
        tools = []
    
    # Create agents
    print("\nCreating agents...")
    
    developer = Agent(
        role="Python Developer",
        goal="Write clean, efficient Python code",
        backstory="""You are an expert Python developer with years of experience.
        You write well-documented, tested code following best practices.""",
        tools=tools,
        verbose=True,
    )
    
    reviewer = Agent(
        role="Code Reviewer",
        goal="Review code for quality, security, and best practices",
        backstory="""You are a senior engineer who reviews code meticulously.
        You ensure code is readable, maintainable, and secure.""",
        tools=tools,
        verbose=True,
    )
    
    print(f"  - {developer.role}")
    print(f"  - {reviewer.role}")
    
    # Create tasks
    print("\nCreating tasks...")
    
    coding_task = Task(
        description="""
        Create a Python function that calculates the nth Fibonacci number
        using dynamic programming. The function should:
        1. Be named 'fibonacci'
        2. Handle edge cases (n <= 0)
        3. Be efficient (O(n) time complexity)
        4. Include a docstring
        """,
        expected_output="A Python function implementing fibonacci calculation",
        agent=developer,
    )
    
    review_task = Task(
        description="""
        Review the fibonacci function created by the developer:
        1. Check for correctness
        2. Verify edge case handling
        3. Assess code quality and documentation
        4. Suggest improvements if needed
        """,
        expected_output="Code review with approval or suggestions",
        agent=reviewer,
    )
    
    print(f"  - Coding task assigned to {developer.role}")
    print(f"  - Review task assigned to {reviewer.role}")
    
    # Create crew
    print("\nCreating crew...")
    
    crew = Crew(
        agents=[developer, reviewer],
        tasks=[coding_task, review_task],
        process=Process.sequential,
        verbose=True,
    )
    
    # Execute
    print("\nExecuting crew...\n")
    print("-" * 50)
    
    try:
        result = crew.kickoff()
        print("-" * 50)
        print(f"\n✅ Crew execution completed!")
        print(f"\nResult:\n{result}")
        return True
    except Exception as e:
        print(f"\n❌ Execution error: {e}")
        return False


async def demo_openhands_standalone():
    """Demo OpenHands runtime without CrewAI."""
    print_header("Demo: OpenHands Runtime Direct Usage")
    
    try:
        from teaming24.runtime.openhands import (
            OpenHandsAdapter, 
            OpenHandsConfig,
            create_openhands_runtime,
        )
    except ImportError as e:
        print(f"❌ OpenHands not available: {e}")
        print("   Install with: pip install openhands-ai")
        return False
    
    print("Creating OpenHands runtime adapter...")
    
    try:
        async with create_openhands_runtime(runtime_type="local") as runtime:
            print("✓ Runtime connected\n")
            
            # Test shell command
            print("Testing shell command...")
            result = await runtime.run_command("echo 'Hello from OpenHands!'")
            print(f"  Output: {result['output']}")
            
            # Test Python execution
            print("\nTesting Python execution...")
            code = """
import sys
print(f"Python version: {sys.version}")
print("Fibonacci sequence:", [0, 1, 1, 2, 3, 5, 8, 13])
"""
            result = await runtime.run_python(code)
            print(f"  Output: {result['output']}")
            
            print("\n✅ OpenHands runtime demo completed!")
            return True
            
    except Exception as e:
        print(f"❌ Runtime error: {e}")
        print("\nNote: Docker may be required for OpenHands runtime.")
        return False


async def main():
    """Run the integration demos."""
    parser = argparse.ArgumentParser(description="CrewAI + OpenHands Demo")
    parser.add_argument("--mock", action="store_true", 
                       help="Use mock execution (no dependencies)")
    parser.add_argument("--crewai-only", action="store_true",
                       help="Run CrewAI demo only")
    parser.add_argument("--openhands-only", action="store_true",
                       help="Run OpenHands demo only")
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("  TEAMING24 - CrewAI + OpenHands Integration Demo")
    print("=" * 60)
    
    # Check dependencies
    deps = check_dependencies()
    print("\nDependency Status:")
    print(f"  - CrewAI: {'✓ Available' if deps['crewai'] else '✗ Not installed'}")
    print(f"  - OpenHands: {'✓ Available' if deps['openhands'] else '✗ Not installed'}")
    
    if args.mock or (not deps['crewai'] and not deps['openhands']):
        print("\nRunning in mock mode...")
        await demo_mock_execution()
        return
    
    # Run demos based on available dependencies and flags
    if args.openhands_only:
        if deps['openhands']:
            await demo_openhands_standalone()
        else:
            print("\n❌ OpenHands not available for standalone demo")
    elif args.crewai_only:
        if deps['crewai']:
            await demo_with_crewai()
        else:
            print("\n❌ CrewAI not available")
    else:
        # Run all available demos
        if deps['crewai']:
            await demo_with_crewai()
        
        if deps['openhands']:
            await demo_openhands_standalone()
    
    print("\n" + "=" * 60)
    print("  Demo completed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
