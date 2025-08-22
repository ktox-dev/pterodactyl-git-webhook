# Git Webhook Server for Pterodactyl

Automatically sync Git repositories in Docker containers when GitHub webhooks are received. Built for Pterodactyl game servers with support for multiple environments and submodules.

## Features

- Multi-container support with YAML or environment configuration
- Different workflows for production/development branches
- Automatic submodule management
- GitHub webhook validation and security
- Health monitoring endpoint
- File-based logging

## Quick Start

1. **Install dependencies**
```bash
pip install -r requirements.txt
```

2. **Configure**
```bash
# Copy and edit configuration files
cp config.yaml.example config.yaml
cp .env.example .env
```

3. **Run**
```bash
python git-webhook.py
```

The server will start on port 5000 and begin listening for GitHub webhooks.

## Configuration

### YAML Configuration (Recommended)

First define your workflows, then assign containers to them:

```yaml
# Define workflows with their behavior
workflows:
  production:
    description: "Production workflow - pull only, reset local changes"
    reset_on_changes: true
    pull: true
    commit: false
    push: false
    submodule_update: true
    submodule_remote: false
    submodule_commit_push: false

  development:
    description: "Development workflow - full sync with commit and push"
    reset_on_changes: false
    pull: true
    commit: true
    push: true
    submodule_update: true
    submodule_remote: true
    submodule_commit_push: true

# Configure your containers
containers:
  - id: "your-container-id"
    name: "prod-server"
    branch: "main"
    workflow: "production"
    repos_dir: "/home/container/server-data"
    submodules:
      - path: "resources/[VL_Scripts]/[Cars]"
        branch: "main"
      - path: "resources/[VL_Scripts]/[Core]"
        branch: "main"

  - id: "another-container-id"
    name: "dev-server"
    branch: "dev"
    workflow: "development"
    repos_dir: "/home/container/server-data"
    submodules:
      - path: "resources/[VL_Scripts]/[Cars]"
        branch: "dev"
```

**How it works:**
- Define workflows with specific Git operation behaviors
- Assign containers to workflows based on their purpose
- Each container can have its own branch and submodule configuration

### Environment Variables

Global settings in `.env`:

```bash
# Repository path inside Docker containers (fallback for YAML configs)
REPOS_DIR=/home/container/server-data

# Git configuration
GIT_USER_NAME=Git Webhook Bot
GIT_USER_EMAIL=webhook@example.com

# Flask server settings
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=false

# Logging
LOG_LEVEL=INFO
LOG_FILE=webhook.log

# Advanced settings
GITHUB_API_TIMEOUT=10
MAX_CONCURRENT_CONTAINERS=5
CONTAINER_TIMEOUT=300
HEALTH_CHECK_ENABLED=true
COMMIT_MESSAGE_TEMPLATE=Auto-commit by webhook: {timestamp}
```

### Legacy Environment Config

Still supported for simple setups (deprecated - use YAML instead):
```bash
# Container format: CONTAINER_<container-id>=<branch>
CONTAINER_34bee3f5-fb2b-4bab-b45e-c303b1d15137=main

# Submodule format: SUBMODULE_<container-id>_<name>=<path>:<branch>
SUBMODULE_34bee3f5-fb2b-4bab-b45e-c303b1d15137_CARS=resources/[VL_Scripts]/[Cars]:main
```

## Systemd Service

```bash
sudo nano /etc/systemd/system/git-webhook.service
```

```ini
[Unit]
Description=Git Webhook Server
After=network.target

[Service]
Type=simple
User=docker
WorkingDirectory=/path/to/pterodactyl-git-webhook
ExecStart=/path/to/venv/bin/python git-webhook.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now git-webhook.service
```

## API Endpoints

- `POST /webhook` - Receives GitHub webhooks (validates GitHub IPs)
- `GET /health` - Health check and configuration status

## How it Works

**Main branch**: Resets local changes and pulls latest (production workflow)
**Dev branch**: Commits changes, pulls, pushes (full development workflow)

Submodules are handled automatically based on your configuration. The server validates that webhooks come from GitHub's official IP ranges for security.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_FILE` | `config.yaml` | Path to YAML configuration |
| `REPOS_DIR` | `/home/container/server-data` | Default repository path (fallback for YAML containers) |
| `FLASK_HOST` | `0.0.0.0` | Server bind address |
| `FLASK_PORT` | `5000` | Server port |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | `webhook.log` | Log file path |
| `GIT_USER_NAME` | `Git Webhook Bot` | Git commit author |
| `GIT_USER_EMAIL` | `webhook@example.com` | Git commit email |
| `GITHUB_API_TIMEOUT` | `10` | GitHub API timeout in seconds |
| `MAX_CONCURRENT_CONTAINERS` | `5` | Max concurrent container operations |
| `CONTAINER_TIMEOUT` | `300` | Container operation timeout in seconds |
| `HEALTH_CHECK_ENABLED` | `true` | Enable health endpoint |
| `COMMIT_MESSAGE_TEMPLATE` | `Auto-commit by webhook: {timestamp}` | Git commit message template |

## Troubleshooting

**Container not found**: Check `docker ps` and verify container IDs in config
**Git errors**: Set `GIT_USER_NAME` and `GIT_USER_EMAIL` in `.env`
**Webhook not received**: Check GitHub webhook settings, firewall, and Traefik config
**YAML errors**: Validate syntax with `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`

Enable debug logging: `LOG_LEVEL=DEBUG`

View logs: `tail -f webhook.log`

## License

MIT License - see [LICENSE](LICENSE) file.

