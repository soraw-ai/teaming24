"""Data-layer constants and schema metadata."""

from __future__ import annotations

from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".teaming24" / "teaming24.db"

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connection_history (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        alias TEXT,
        ip TEXT NOT NULL,
        port INTEGER NOT NULL,
        wallet_address TEXT,
        agent_id TEXT,
        capability TEXT,
        description TEXT,
        capabilities TEXT,
        last_connected REAL,
        connect_count INTEGER DEFAULT 1,
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connection_sessions (
        session_id TEXT PRIMARY KEY,
        node_id TEXT,
        name TEXT,
        alias TEXT,
        ip TEXT,
        port INTEGER,
        direction TEXT,
        started_at REAL,
        ended_at REAL,
        duration_seconds REAL,
        reason TEXT,
        metadata TEXT,
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS known_nodes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        ip TEXT NOT NULL,
        port INTEGER NOT NULL,
        type TEXT DEFAULT 'wan',
        wallet_address TEXT,
        agent_id TEXT,
        capability TEXT,
        description TEXT,
        capabilities TEXT,
        price TEXT,
        region TEXT,
        status TEXT DEFAULT 'offline',
        last_seen REAL,
        created_at REAL,
        metadata TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS marketplace_cache (
        id TEXT PRIMARY KEY,
        data TEXT,
        fetched_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        name TEXT,
        description TEXT,
        status TEXT DEFAULT 'pending',
        task_type TEXT DEFAULT 'local',
        assigned_to TEXT,
        delegated_agents TEXT,
        steps TEXT,
        result TEXT,
        error TEXT,
        cost TEXT,
        output_dir TEXT,
        created_at REAL,
        started_at REAL,
        completed_at REAL,
        metadata TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_steps (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        agent_id TEXT,
        agent_name TEXT,
        action TEXT,
        content TEXT,
        thought TEXT,
        observation TEXT,
        status TEXT DEFAULT 'pending',
        started_at REAL,
        completed_at REAL,
        tokens_used INTEGER DEFAULT 0,
        FOREIGN KEY (task_id) REFERENCES tasks(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        title TEXT,
        mode TEXT DEFAULT 'chat',
        created_at REAL,
        updated_at REAL,
        metadata TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT,
        task_id TEXT,
        steps TEXT,
        cost TEXT,
        is_task INTEGER DEFAULT 0,
        created_at REAL,
        FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT DEFAULT 'worker',
        status TEXT DEFAULT 'offline',
        capabilities TEXT,
        endpoint TEXT,
        model TEXT,
        goal TEXT,
        backstory TEXT,
        tools TEXT,
        system_prompt TEXT,
        allow_delegation INTEGER DEFAULT 1,
        metadata TEXT,
        created_at REAL,
        updated_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS skills (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        instructions TEXT,
        category TEXT DEFAULT 'general',
        tags TEXT,
        author TEXT,
        version TEXT DEFAULT '1.0.0',
        license TEXT DEFAULT '',
        compatibility TEXT DEFAULT '',
        requires TEXT,
        enabled INTEGER DEFAULT 1,
        source TEXT,
        file_path TEXT,
        created_at REAL,
        updated_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_skills (
        agent_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        assigned_at REAL,
        PRIMARY KEY (agent_id, skill_id),
        FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
        FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS custom_tools (
        name TEXT PRIMARY KEY,
        description TEXT,
        category TEXT DEFAULT 'custom',
        enabled INTEGER DEFAULT 1,
        created_at REAL,
        updated_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wallet_transactions (
        id TEXT PRIMARY KEY,
        timestamp REAL,
        type TEXT,
        amount REAL,
        task_id TEXT,
        task_name TEXT,
        description TEXT,
        tx_hash TEXT,
        payer TEXT,
        payee TEXT,
        mode TEXT,
        network TEXT,
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sandbox_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sandbox_id TEXT NOT NULL,
        event_type TEXT,
        event_data TEXT,
        timestamp REAL,
        created_at REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payment_records (
        parent_task_id TEXT NOT NULL,
        requester_id TEXT NOT NULL,
        paid_at REAL NOT NULL,
        PRIMARY KEY (parent_task_id, requester_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wallet_expense_records (
        task_id TEXT NOT NULL,
        target_an TEXT NOT NULL,
        amount REAL NOT NULL,
        recorded_at REAL NOT NULL,
        PRIMARY KEY (task_id, target_an)
    )
    """,
)

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_task_steps_task ON task_steps(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_agents_type ON agents(type)",
    "CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category)",
    "CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_wallet_tx_timestamp ON wallet_transactions(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_sandbox_events_sandbox ON sandbox_events(sandbox_id)",
    "CREATE INDEX IF NOT EXISTS idx_payment_records_parent ON payment_records(parent_task_id)",
    "CREATE INDEX IF NOT EXISTS idx_wallet_expense_task ON wallet_expense_records(task_id)",
)

AGENT_SCHEMA_MIGRATIONS = (
    ("tools", "TEXT"),
    ("system_prompt", "TEXT"),
    ("allow_delegation", "INTEGER DEFAULT 1"),
)

SKILL_SCHEMA_MIGRATIONS = (
    ("license", "TEXT DEFAULT ''"),
    ("compatibility", "TEXT DEFAULT ''"),
)
