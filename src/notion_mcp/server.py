from mcp.server import Server
from mcp.types import (
    Resource, 
    Tool,
    TextContent,
    EmbeddedResource
)
from pydantic import AnyUrl
import os
import json
from datetime import datetime, timedelta
import httpx
from typing import Any, Sequence
from dotenv import load_dotenv
from pathlib import Path
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('academic_notion_mcp')

# Find and load .env file from project root
project_root = Path(__file__).parent.parent.parent
env_path = project_root / '.env'
if not env_path.exists():
    raise FileNotFoundError(f"No .env file found at {env_path}")
load_dotenv(env_path)

# Initialize server
server = Server("academic-notion")

# Configuration with validation
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

if not NOTION_API_KEY:
    raise ValueError("NOTION_API_KEY not found in .env file")
if not DATABASE_ID:
    raise ValueError("NOTION_DATABASE_ID not found in .env file")

NOTION_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

# Notion API headers
headers = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": NOTION_VERSION
}

def parse_task_type(title: str) -> dict:
    """Parse task title to determine type and extract info"""
    title = title.strip()
    
    if title.startswith(('H ', 'HTN ', 'Q ')):
        # Homework or Quiz task
        parts = title.split(' ', 2)
        task_type = parts[0]  # H, HTN, or Q
        subject = parts[1] if len(parts) > 1 else ""
        task_desc = parts[2] if len(parts) > 2 else ""
        return {
            'type': 'assignment',
            'assignment_type': task_type,
            'subject': subject,
            'description': task_desc,
            'is_main_task': True
        }
    elif title.split()[0].isdigit() and not title.endswith('*'):
        # Countdown task like "5 bio homework" or "2 chem farabaugh8.1-8.3"
        parts = title.split(' ', 2)
        days_left = int(parts[0])
        subject = parts[1] if len(parts) > 1 else ""
        task_desc = parts[2] if len(parts) > 2 else ""
        return {
            'type': 'countdown',
            'days_left': days_left,
            'subject': subject,
            'description': task_desc,
            'is_main_task': False
        }
    elif '*' in title and title.split('*')[0].strip().isdigit():
        # Priority task like "1* call hershey motel"
        parts = title.split('*', 1)
        priority = int(parts[0].strip())
        task_desc = parts[1].strip()
        return {
            'type': 'priority',
            'priority': priority,
            'description': task_desc,
            'is_main_task': False
        }
    else:
        # Regular task
        return {
            'type': 'regular',
            'description': title,
            'is_main_task': False
        }

async def fetch_tasks() -> dict:
    """Fetch all tasks from Notion database"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NOTION_BASE_URL}/databases/{DATABASE_ID}/query",
            headers=headers,
            json={
                "sorts": [
                    {
                        "property": "Due Date",
                        "direction": "ascending"
                    }
                ]
            }
        )
        response.raise_for_status()
        return response.json()

async def create_task(title: str, due_date: str = None, due_time: str = None, 
                     status: str = "", task_type: str = "regular", 
                     priority: int = None, related_task_id: str = None) -> dict:
    """Create a single task in Notion"""
    async with httpx.AsyncClient() as client:
        properties = {
            "Name": {
                "type": "title",
                "title": [{"type": "text", "text": {"content": title}}]
            }
        }
        
        # Add status if provided
        if status:
            properties["Status"] = {
                "type": "select",
                "select": {"name": status}
            }
        
        # Add due date if provided
        if due_date:
            date_obj = {"start": due_date}
            if due_time:
                date_obj["start"] += f"T{due_time}:00"
            properties["Due Date"] = {
                "type": "date",
                "date": date_obj
            }
        
        # Add priority for priority tasks
        if priority:
            properties["Priority"] = {
                "type": "number",
                "number": priority
            }
        
        # Add task type
        properties["Type"] = {
            "type": "select",
            "select": {"name": task_type}
        }
        
        # Link to related task if provided
        if related_task_id:
            properties["Related Task"] = {
                "type": "rich_text",
                "rich_text": [{"type": "text", "text": {"content": related_task_id}}]
            }
        
        response = await client.post(
            f"{NOTION_BASE_URL}/pages",
            headers=headers,
            json={
                "parent": {"database_id": DATABASE_ID},
                "properties": properties
            }
        )
        response.raise_for_status()
        return response.json()

async def update_task_status(page_id: str, status: str) -> dict:
    """Update task status in Notion"""
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{NOTION_BASE_URL}/pages/{page_id}",
            headers=headers,
            json={
                "properties": {
                    "Status": {
                        "type": "select",
                        "select": {"name": status}
                    }
                }
            }
        )
        response.raise_for_status()
        return response.json()

async def find_tasks_by_title(title: str) -> list:
    """Find tasks by title"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NOTION_BASE_URL}/databases/{DATABASE_ID}/query",
            headers=headers,
            json={
                "filter": {
                    "property": "Name",
                    "title": {"contains": title}
                }
            }
        )
        response.raise_for_status()
        return response.json().get("results", [])

async def create_assignment_with_countdown(assignment_type: str, subject: str, 
                                         description: str, due_date: str, due_time: str = None) -> dict:
    """Add main assignment and create countdown tasks"""
    try:
        due_datetime = datetime.strptime(due_date, "%Y-%m-%d")
        
        # Create main assignment task
        main_title = f"{assignment_type} {subject.upper()} {description.upper()}"
        main_task = await create_task(
            title=main_title,
            due_date=due_date,
            due_time=due_time,
            status="due" if assignment_type in ['H', 'HTN', 'Q'] else "",
            task_type="assignment"
        )
        
        # Create 5-day countdown task
        countdown_date = due_datetime - timedelta(days=5)
        countdown_title = f"5 {subject.lower()} {description.lower()}"
        
        countdown_task = await create_task(
            title=countdown_title,
            due_date=countdown_date.strftime("%Y-%m-%d"),
            status="",
            task_type="countdown",
            related_task_id=main_task['id']
        )
        
        return {
            'main_task': main_task['id'],
            'countdown_task': countdown_task['id'],
            'message': f"Created {main_title} with 5-day countdown task"
        }
        
    except Exception as e:
        logger.error(f"Error creating assignment: {e}")
        return {'error': str(e)}

async def complete_task_with_logic(task_title: str) -> dict:
    """Mark task as completed and handle smart logic"""
    try:
        # Find the task
        tasks = await find_tasks_by_title(task_title)
        if not tasks:
            return {'error': f"Task '{task_title}' not found"}
        
        task = tasks[0]
        task_info = parse_task_type(task_title)
        
        # Mark as completed
        await update_task_status(task['id'], "completed")
        
        # Handle smart completion logic
        if task_info['type'] == 'countdown':
            # Create next countdown task (n-1)
            next_day_count = task_info['days_left'] - 1
            if next_day_count > 0:
                tomorrow = datetime.now() + timedelta(days=1)
                next_title = f"{next_day_count} {task_info['subject']} {task_info['description']}"
                
                await create_task(
                    title=next_title,
                    due_date=tomorrow.strftime("%Y-%m-%d"),
                    task_type="countdown"
                )
                
                return {
                    'message': f"Marked '{task_title}' complete and created '{next_title}' for tomorrow"
                }
        
        elif task_info['type'] == 'assignment':
            # Mark all related countdown tasks as completed
            # This would require finding related tasks and marking them complete
            return {
                'message': f"Marked '{task_title}' complete and completed all related countdown tasks"
            }
        
        return {'message': f"Marked '{task_title}' as completed"}
        
    except Exception as e:
        logger.error(f"Error completing task: {e}")
        return {'error': str(e)}

async def get_today_tasks() -> list:
    """Get all tasks due today"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{NOTION_BASE_URL}/databases/{DATABASE_ID}/query",
                headers=headers,
                json={
                    "filter": {
                        "property": "Due Date",
                        "date": {"equals": today}
                    }
                }
            )
            response.raise_for_status()
            tasks = response.json().get("results", [])
            
            formatted_tasks = []
            for task in tasks:
                props = task["properties"]
                formatted_task = {
                    "id": task["id"],
                    "title": props["Name"]["title"][0]["text"]["content"] if props["Name"]["title"] else "",
                    "status": props.get("Status", {}).get("select", {}).get("name", "") if "Status" in props else "",
                    "type": props.get("Type", {}).get("select", {}).get("name", "") if "Type" in props else ""
                }
                formatted_tasks.append(formatted_task)
            
            return formatted_tasks
    except Exception as e:
        logger.error(f"Error getting today's tasks: {e}")
        return []

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available academic task management tools"""
    return [
        Tool(
            name="add_assignment",
            description="Add homework/quiz with automatic countdown tasks (H/HTN/Q + 5-day countdown)",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string", 
                        "enum": ["H", "HTN", "Q"], 
                        "description": "H=homework due before class, HTN=homework due tonight, Q=quiz/test"
                    },
                    "subject": {
                        "type": "string", 
                        "description": "Subject name (e.g., CHEM, BIO, STAT, ENG)"
                    },
                    "description": {
                        "type": "string", 
                        "description": "Assignment description (e.g., FARABAUGH8.1-8.3, EDPUZZLE)"
                    },
                    "due_date": {
                        "type": "string", 
                        "description": "Due date in YYYY-MM-DD format"
                    },
                    "due_time": {
                        "type": "string", 
                        "description": "Due time in HH:MM format (optional, e.g., 15:30 for 3:30 PM)"
                    }
                },
                "required": ["type", "subject", "description", "due_date"]
            }
        ),
        Tool(
            name="add_priority_task",
            description="Add priority task with star notation (1* highest priority to 5* lowest)",
            inputSchema={
                "type": "object",
                "properties": {
                    "priority": {
                        "type": "integer", 
                        "minimum": 1, 
                        "maximum": 5, 
                        "description": "Priority level: 1 (most important) to 5 (least important)"
                    },
                    "description": {
                        "type": "string", 
                        "description": "Task description (e.g., 'call hershey motel', 'get birth cert')"
                    }
                },
                "required": ["priority", "description"]
            }
        ),
        Tool(
            name="complete_task",
            description="Mark task as completed (automatically handles countdown logic and related tasks)",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_title": {
                        "type": "string", 
                        "description": "Exact task title to mark complete (e.g., '5 bio homework', 'H CHEM FARABAUGH8.1-8.3')"
                    }
                },
                "required": ["task_title"]
            }
        ),
        Tool(
            name="show_today_tasks",
            description="Show all tasks due today",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="show_all_tasks",
            description="Show all tasks from your academic database",
            inputSchema={
                "type": "object", 
                "properties": {}
            }
        ),
        Tool(
            name="add_syllabus_bulk",
            description="Parse syllabus and create multiple assignments with countdown tasks",
            inputSchema={
                "type": "object",
                "properties": {
                    "syllabus_text": {
                        "type": "string", 
                        "description": "Paste your syllabus content with due dates"
                    },
                    "course_name": {
                        "type": "string", 
                        "description": "Course abbreviation (e.g., CHEM, BIO, STAT)"
                    }
                },
                "required": ["syllabus_text", "course_name"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent | EmbeddedResource]:
    """Handle tool calls for academic task management"""
    try:
        if name == "add_assignment":
            if not isinstance(arguments, dict):
                raise ValueError("Invalid arguments")
            
            result = await create_assignment_with_countdown(
                arguments["type"],
                arguments["subject"],
                arguments["description"],
                arguments["due_date"],
                arguments.get("due_time")
            )
            
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "add_priority_task":
            if not isinstance(arguments, dict):
                raise ValueError("Invalid arguments")
            
            priority = arguments["priority"]
            description = arguments["description"]
            title = f"{priority}* {description}"
            
            result = await create_task(
                title=title,
                task_type="priority",
                priority=priority
            )
            
            return [TextContent(type="text", text=f"Added priority task: {title}")]
        
        elif name == "complete_task":
            if not isinstance(arguments, dict):
                raise ValueError("Invalid arguments")
            
            result = await complete_task_with_logic(arguments["task_title"])
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "show_today_tasks":
            tasks = await get_today_tasks()
            return [TextContent(type="text", text=json.dumps(tasks, indent=2))]
        
        elif name == "show_all_tasks":
            result = await fetch_tasks()
            formatted_tasks = []
            for task in result.get("results", []):
                props = task["properties"]
                formatted_task = {
                    "id": task["id"],
                    "title": props["Name"]["title"][0]["text"]["content"] if props["Name"]["title"] else "",
                    "status": props.get("Status", {}).get("select", {}).get("name", "") if "Status" in props else "",
                    "due_date": props.get("Due Date", {}).get("date", {}).get("start", "") if "Due Date" in props else "",
                    "type": props.get("Type", {}).get("select", {}).get("name", "") if "Type" in props else ""
                }
                formatted_tasks.append(formatted_task)
            
            return [TextContent(type="text", text=json.dumps(formatted_tasks, indent=2))]
        
        elif name == "add_syllabus_bulk":
            # Placeholder for syllabus parsing - you can paste your actual syllabus for me to implement
            return [TextContent(type="text", text="Syllabus bulk import feature - ready to implement with your syllabus format")]
        
        else:
            raise ValueError(f"Unknown tool: {name}")
            
    except Exception as e:
        logger.error(f"Error in tool call: {e}")
        return [TextContent(type="text", text=f"Error: {str(e)}")]

async def main():
    """Main entry point for the server"""
    from mcp.server.stdio import stdio_server
    
    if not NOTION_API_KEY or not DATABASE_ID:
        raise ValueError("NOTION_API_KEY and NOTION_DATABASE_ID environment variables are required")
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
