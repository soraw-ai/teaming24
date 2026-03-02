# CLI Reference

Command line interface for Teaming24.

## Usage

```bash
python main.py [OPTIONS]
```

## Options

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--config` | `-c` | Path to config file | `teaming24/config/teaming24.yaml` |
| `--host` | | Server host | `0.0.0.0` |
| `--port` | `-p` | Server port | `8000` |
| `--reload` | `-r` | Enable auto-reload | `false` |
| `--workers` | `-w` | Number of worker processes | `1` |
| `--help` | | Show help message | |

## Examples

### Basic startup

```bash
python main.py
```

### Development mode

```bash
python main.py --reload
```

### Custom port

```bash
python main.py --port 9000
```

### Custom config file

```bash
python main.py --config /path/to/config.yaml
```

### Production mode

```bash
python main.py --workers 4 --host 0.0.0.0 --port 8000
```

## Environment Variables

The CLI respects these environment variables:

| Variable | Description |
|----------|-------------|
| `TEAMING24_CONFIG` | Path to config file |
| `TEAMING24_HOST` | Server host override |
| `TEAMING24_PORT` | Server port override |
| `TEAMING24_LOG_LEVEL` | Logging level override |
| `TEAMING24_DB_PATH` | Database path override |

## Dev Script

For development, use the provided script:

```bash
./scripts/start_dev.sh
```

This starts both backend and frontend with auto-reload enabled.
