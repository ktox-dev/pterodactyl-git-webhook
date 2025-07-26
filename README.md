# Git Webhook Server for Pterodactyl

A robust Flask-based webhook server that automatically synchronizes Git repositories within Docker containers when GitHub webhooks are received. Designed specifically for Pterodactyl game server management with support for both development and production workflows.

## 🚀 Features

- **Multi-Container Support**: Manage multiple Docker containers with different Git workflows
- **Branch-Specific Workflows**: Different handling for `main` (production) and `dev` (development) branches
- **Submodule Management**: Automatic submodule synchronization with configurable branches
- **Security**: GitHub IP validation and auto-commit loop prevention
- **Flexible Configuration**: Environment-based configuration with sensible defaults
- **Health Monitoring**: Optional health check endpoint for monitoring
- **Git User Management**: Automatic Git user configuration to prevent commit errors
- **Comprehensive Logging**: Detailed logging with configurable levels

## 📋 Prerequisites

- Python 3.8+
- Docker (for container operations)
- Git repositories with webhook access
- Traefik (for reverse proxy, optional)

## 🛠️ Installation

### 1. Clone and Setup

```bash
# Clone the repository
git clone <your-repo-url> /home/docker/pterodactyl-git-webhook
cd /home/docker/pterodactyl-git-webhook

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install flask python-dotenv requests
```

### 2. Configuration

Copy and configure the environment file:

```bash
cp .env_example .env
```

Edit `.env` with your container and repository settings:

```bash
# Repository directory inside containers
REPOS_DIR=/home/container/server-data

# Container configuration (format: CONTAINER_<id>=<branch>)
CONTAINER_e4c77c95-3ebe-4d03-8e8f-18c63a662d8f=main
CONTAINER_4906c456-74fd-4d02-b458-e17ae8026ba4=dev

# Submodule configuration (format: SUBMODULE_<container-id>_<name>=<path>:<branch>)
SUBMODULE_4906c456-74fd-4d02-b458-e17ae8026ba4_CARS=resources/[VL_Scripts]/[Cars]:dev

# Git user configuration
GIT_USER_NAME=Pterodactyl Webhook Bot
GIT_USER_EMAIL=webhook@yourdomain.com

# Optional: Server configuration
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
LOG_LEVEL=INFO
HEALTH_CHECK_ENABLED=true
```

### 3. Systemd Service Setup

Create the systemd service file:

```bash
sudo nano /etc/systemd/system/git-webhook.service
```

Add the following content:

```ini
[Unit]
Description=Git Webhook Server for Pterodactyl
After=network.target
Wants=network.target

[Service]
Type=simple
User=docker
Group=docker
WorkingDirectory=/home/docker/pterodactyl-git-webhook
ExecStart=/home/docker/pterodactyl-git-webhook/venv/bin/python3 /home/docker/pterodactyl-git-webhook/git-webhook.py
Restart=always
RestartSec=5
Environment=PATH=/home/docker/pterodactyl-git-webhook/venv/bin
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable git-webhook.service
sudo systemctl start git-webhook.service

# Check status
sudo systemctl status git-webhook.service
```

## 🌐 Traefik Configuration

Add to your Traefik dynamic configuration (`config.yaml`):

```yaml
http:
  routers:
    git-webhook:
      entryPoints:
        - "websecure"
      tls:
        certResolver: "cloudflare"
      rule: "Host(`git-webhook.yourdomain.com`)"
      service: "git-webhook"
      # Optional: Add middleware for security
      middlewares:
        - "real-ip"
  
  services:
    git-webhook:
      loadBalancer:
        servers:
          - url: "http://172.18.0.1:5000"  # Adjust to your host IP

  middlewares:
    real-ip:
      headers:
        customRequestHeaders:
          X-Real-IP: "{CF-Connecting-IP}"
          X-Forwarded-For: "{CF-Connecting-IP}"
```

## 📊 API Endpoints

### Webhook Endpoint
- **URL**: `POST /webhook`
- **Purpose**: Receives GitHub webhook payloads
- **Authentication**: GitHub IP validation
- **Response**: JSON with operation status

### Health Check Endpoint
- **URL**: `GET /health`
- **Purpose**: Service health and configuration status
- **Response**: JSON with system information
- **Configurable**: Can be disabled via `HEALTH_CHECK_ENABLED=false`

Example health response:
```json
{
    "status": "healthy",
    "version": "2.0.0",
    "containers": 2,
    "submodules": 1,
    "config": {
        "repos_dir": "/home/container/server-data",
        "log_level": "INFO",
        "github_api_timeout": 10,
        "git_user": "Pterodactyl Webhook Bot <webhook@yourdomain.com>"
    }
}
```

## ⚙️ Workflow Types

### Main Branch (Production)
- **Reset**: Discards local changes
- **Pull**: Updates to latest remote changes
- **No Commits**: Read-only workflow for production stability

### Dev Branch (Development)
- **Submodule Processing**: Commits, pulls, and pushes submodule changes
- **Submodule Updates**: Syncs to latest remote commits
- **Main Repo**: Commits, pulls, and pushes main repository changes
- **Full Workflow**: Complete development cycle automation

## 🔧 Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REPOS_DIR` | `/home/container/server-data` | Repository directory in containers |
| `FLASK_HOST` | `0.0.0.0` | Flask server bind address |
| `FLASK_PORT` | `5000` | Flask server port |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | `webhook.log` | Log file name |
| `GITHUB_API_TIMEOUT` | `10` | GitHub API request timeout (seconds) |
| `GIT_USER_NAME` | `Git Webhook Bot` | Git commit author name |
| `GIT_USER_EMAIL` | `webhook@example.com` | Git commit author email |
| `HEALTH_CHECK_ENABLED` | `true` | Enable/disable health endpoint |

### Container Configuration
Format: `CONTAINER_<container-id>=<branch>`
- `container-id`: Docker container name or ID
- `branch`: Either `main` or `dev`

### Submodule Configuration
Format: `SUBMODULE_<container-id>_<name>=<path>:<branch>`
- `container-id`: Must match a configured container
- `name`: Unique identifier for the submodule
- `path`: Relative path from REPOS_DIR
- `branch`: Git branch for the submodule

## 📝 Logging

Logs are written to `webhook.log` in the application directory. Log levels:
- **INFO**: Normal operations, container processing
- **WARNING**: Non-critical issues (e.g., submodule configuration problems)
- **ERROR**: Critical failures, container processing errors

View logs:
```bash
# Real-time log monitoring
tail -f /home/docker/pterodactyl-git-webhook/webhook.log

# Service logs
sudo journalctl -u git-webhook.service -f
```

## 🔒 Security Features

- **GitHub IP Validation**: Only accepts webhooks from GitHub's IP ranges
- **Auto-commit Detection**: Prevents infinite loops from webhook-generated commits
- **Error Handling**: Comprehensive error handling with appropriate HTTP status codes
- **Health Check Control**: Optional health endpoint can be disabled for security

## 🐛 Troubleshooting

### Common Issues

1. **"Author identity unknown" errors**
   - Ensure `GIT_USER_NAME` and `GIT_USER_EMAIL` are set in `.env`

2. **Container not found**
   - Verify container IDs/names in configuration
   - Check if containers are running: `docker ps`

3. **Webhook not received**
   - Check GitHub webhook settings
   - Verify Traefik routing configuration
   - Check firewall settings

4. **Submodule errors**
   - Review submodule configuration in `.gitmodules`
   - Ensure submodule repositories are accessible
   - Check authentication for private repositories

### Debug Mode

Enable debug logging:
```bash
# In .env file
LOG_LEVEL=DEBUG
FLASK_DEBUG=true
```

## 📚 Development

### Architecture
- **Config Class**: Environment-based configuration management
- **GitOperations Class**: Git command execution within containers
- **GitHubValidator Class**: Webhook validation and security
- **WebhookProcessor Class**: Main workflow orchestration

### Testing

Test the health endpoint:
```bash
curl https://git-webhook.yourdomain.com/health
```

Test webhook locally:
```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"head_commit": {"message": "test commit"}}'
```

## 📄 License

[Add your license information here]

## 🤝 Contributing

[Add contribution guidelines here]

## 📞 Support

For issues and questions:
- Check the logs first
- Review configuration settings
- Verify container and repository access
- [Add your support contact information]

