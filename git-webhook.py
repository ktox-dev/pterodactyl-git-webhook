from flask import Flask, request, jsonify
import subprocess
import logging
import requests
import ipaddress
from dotenv import load_dotenv
import os
from collections import defaultdict
from typing import Dict, Tuple, Optional, Any
from dataclasses import dataclass

# Load environment variables
load_dotenv()

@dataclass
class Config:
    """Configuration class to hold all application settings."""
    current_dir: str
    repos_dir: str
    containers: Dict[str, str]
    submodules: Dict[str, Dict[str, str]]
    flask_host: str = "0.0.0.0"
    flask_port: int = 5000
    flask_debug: bool = False
    log_level: str = "INFO"
    log_file: str = "webhook.log"
    github_api_timeout: int = 10
    git_user_name: str = "Git Webhook Bot"
    git_user_email: str = "webhook@example.com"
    health_check_enabled: bool = True
    
    @classmethod
    def from_environment(cls) -> 'Config':
        current_dir = os.path.dirname(os.path.abspath(__file__))
        repos_dir = os.environ.get('REPOS_DIR', '/home/container/server-data')
        
        # Parse containers from environment variables
        containers = {}
        for key, value in os.environ.items():
            if key.startswith('CONTAINER_'):
                container_id = key.replace('CONTAINER_', '')
                containers[container_id] = value
        
        # Parse submodules from environment variables (per container)
        submodules = defaultdict(dict)
        for key, value in os.environ.items():
            if key.startswith('SUBMODULE_'):
                try:
                    _, container_id, name = key.split('_', 2)
                    path, branch = value.split(':')
                    submodules[container_id][path] = branch
                except ValueError:
                    logging.warning(f"Invalid submodule configuration: {key}={value}")
        
        # Optional configuration with defaults
        flask_host = os.environ.get('FLASK_HOST', '0.0.0.0')
        flask_port = int(os.environ.get('FLASK_PORT', '5000'))
        flask_debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
        log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
        log_file = os.environ.get('LOG_FILE', 'webhook.log')
        github_api_timeout = int(os.environ.get('GITHUB_API_TIMEOUT', '10'))
        git_user_name = os.environ.get('GIT_USER_NAME', 'Git Webhook Bot')
        git_user_email = os.environ.get('GIT_USER_EMAIL', 'webhook@example.com')
        health_check_enabled = os.environ.get('HEALTH_CHECK_ENABLED', 'true').lower() == 'true'
        
        return cls(
            current_dir=current_dir,
            repos_dir=repos_dir,
            containers=containers,
            submodules=dict(submodules),
            flask_host=flask_host,
            flask_port=flask_port,
            flask_debug=flask_debug,
            log_level=log_level,
            log_file=log_file,
            github_api_timeout=github_api_timeout,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            health_check_enabled=health_check_enabled
        )

# Initialize configuration
config = Config.from_environment()

# Set up logging with configurable level
log_file = os.path.join(config.current_dir, config.log_file)
log_level = getattr(logging, config.log_level, logging.INFO)
logging.basicConfig(
    filename=log_file, 
    level=log_level, 
    format='%(asctime)s %(levelname)s: %(message)s'
)

app = Flask(__name__)

class GitOperations:
    """Handles all Git operations within Docker containers."""
    
    def __init__(self, repos_dir: str, git_user_name: str, git_user_email: str):
        self.repos_dir = repos_dir
        self.git_user_name = git_user_name
        self.git_user_email = git_user_email
    
    def run_docker_command(self, container: str, *args) -> subprocess.CompletedProcess:
        """Execute a command inside a Docker container."""
        # Try to run as the container's default user first
        cmd = ["docker", "exec", container] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # If there's an error and it contains ownership issues, 
        if (result.returncode != 0 and result.stderr and "dubious ownership" in result.stderr):
            
            logging.warning(f"ownership error detected, declaring as safe directory {container}")
            
            # Declare repository as safe first
            safe_cmd = ["docker", "exec", container, "git", "config", "--global", "--add", "safe.directory", self.repos_dir]
            safe_result = subprocess.run(safe_cmd, capture_output=True, text=True)
            if safe_result.returncode != 0:
                logging.warning(f"Could not declare safe repository: {safe_result.stderr}")
            
            # Retry the original command
            cmd = ["docker", "exec", container] + list(args)
            result = subprocess.run(cmd, capture_output=True, text=True)
        elif (result.returncode != 0 and result.stderr and "permission denied" in result.stderr):

            logging.warning(f"permissions error detected, applying default user and group 988 to {self.repos_dir} in container {container}")

            # Apply default user and group 988 to the repository directory
            fix_cmd = ["docker", "exec", "--user", "root", container, "chown", "-R", "988:988", self.repos_dir]
            fix_result = subprocess.run(fix_cmd, capture_output=True, text=True)
            if fix_result.returncode != 0:
                logging.warning(f"Could not apply ownership fix: {fix_result.stderr}")
        
        return result
    
    def setup_git_user(self, container: str, path: str) -> Tuple[bool, str]:
        """Set up Git user configuration in the container."""
        # Set user name
        result_name = self.run_docker_command(
            container, "git", "-C", path, "config", "user.name", self.git_user_name
        )
        if result_name.returncode != 0:
            return False, f"Failed to set git user.name: {result_name.stderr}"
        
        # Set user email
        result_email = self.run_docker_command(
            container, "git", "-C", path, "config", "user.email", self.git_user_email
        )
        if result_email.returncode != 0:
            return False, f"Failed to set git user.email: {result_email.stderr}"
        
        return True, f"Git user configured as {self.git_user_name} <{self.git_user_email}>"
    
    def has_changes(self, container: str, path: str) -> bool:
        """Check if there are uncommitted changes in the repository."""
        result = self.run_docker_command(container, "git", "-C", path, "status", "--porcelain", "-uno")
        return bool(result.stdout.strip()) and result.returncode == 0
    
    def add_all(self, container: str, path: str) -> Tuple[bool, str]:
        """Add all changes in the repository."""
        result = self.run_docker_command(container, "git", "-C", path, "add", "--all")
        if result.returncode != 0:
            return False, f"Add failed: {result.stderr}"
        return True, "All changes added successfully"
    
    def commit(self, container: str, path: str, message: str = "Auto-commit by webhook") -> Tuple[bool, str]:
        """Commit changes in the repository."""
        # First check if there are any changes to commit
        if not self.has_changes(container, path):
            return True, "No changes to commit"
        
        # Ensure Git user is configured before committing
        success, msg = self.setup_git_user(container, path)
        if not success:
            return False, f"Git user setup failed: {msg}"
        
        # Add all changes before committing
        success, msg = self.add_all(container, path)
        if not success:
            return False, f"Add failed: {msg}"

        result = self.run_docker_command(container, "git", "-C", path, "commit", "-am", message)
        if result.returncode != 0:
            return False, f"Commit failed: {result.stderr}"
        return True, "Commit successful"
    
    def pull(self, container: str, path: str, branch: str) -> Tuple[bool, str]:
        """Pull changes from remote repository."""
        result = self.run_docker_command(container, "git", "-C", path, "pull", "origin", branch)
        if result.returncode != 0:
            error_msg = f"Pull failed in container {container}: {result.stderr}"
            logging.error(error_msg)
            return False, error_msg
        return True, "Pull successful"
    
    def push(self, container: str, path: str, branch: str) -> Tuple[bool, str]:
        """Push changes to remote repository."""
        result = self.run_docker_command(container, "git", "-C", path, "push", "origin", branch)
        if result.returncode != 0:
            error_msg = f"Push failed in container {container}: {result.stderr}"
            logging.error(error_msg)
            return False, error_msg
        return True, "Push successful"

    def reset_hard(self, container: str, path: str, branch: str) -> Tuple[bool, str]:
        """Reset repository to HEAD, discarding local changes and untracked files."""
        # First reset to HEAD (discards staged and unstaged changes)
        result_reset = self.run_docker_command(container, "git", "-C", path, "reset", "--hard", "origin/" + branch)
        if result_reset.returncode != 0:
            error_msg = f"Reset failed in container {container}: {result_reset.stderr}"
            logging.error(error_msg)
            return False, error_msg
        
        # Then clean untracked files and directories
        result_clean = self.run_docker_command(container, "git", "-C", path, "clean", "-fd")
        if result_clean.returncode != 0:
            error_msg = f"Clean failed in container {container}: {result_clean.stderr}"
            logging.error(error_msg)
            return False, error_msg
        
        return True, "Reset and clean successful"
    
    def submodule_update(self, container: str, path: str, use_remote: bool = True) -> Tuple[bool, str]:
        """Update submodules in the repository."""
        cmd_args = ["git", "-C", path, "submodule", "update", "--init", "--recursive"]
        if use_remote:
            cmd_args.extend(["--remote", "--force"])
        
        result = self.run_docker_command(container, *cmd_args)
        if result.returncode != 0:
            # Check if it's the "No url found" error - this is not critical
            if "No url found for submodule path" in result.stderr:
                warning_msg = f"Submodule configuration issue in container {container}: {result.stderr}"
                logging.warning(warning_msg)
                return True, warning_msg  # Return success but with warning
            
            error_msg = f"Submodule update failed in container {container}: {result.stderr}"
            logging.error(error_msg)
            return False, error_msg
        return True, "Submodule update successful"
    
    def checkout_and_merge(self, container: str, path: str, branch: str) -> Tuple[bool, str]:
        """Checkout branch and merge previous state (used for submodule push)."""
        # Checkout branch
        checkout_result = self.run_docker_command(container, "git", "-C", path, "checkout", branch)
        if checkout_result.returncode != 0:
            return False, f"Checkout failed: {checkout_result.stderr}"
        
        # Merge previous state
        merge_result = self.run_docker_command(container, "git", "-C", path, "merge", "HEAD@{1}", branch)
        if merge_result.returncode != 0:
            return False, f"Merge failed: {merge_result.stderr}"
        
        return True, "Checkout and merge successful"

class GitHubValidator:
    """Handles GitHub webhook validation."""
    
    @staticmethod
    def get_real_ip(request) -> str:
        """Extract the real IP address from request headers."""
        forwarded_for = request.headers.get('X-Forwarded-For')
        if forwarded_for:
            # Take the second IP if multiple IPs are present (first is usually proxy)
            ips = forwarded_for.split(', ')
            return ips[1] if len(ips) > 1 else ips[0]
        return request.headers.get('X-Real-IP', request.remote_addr)
    
    @staticmethod
    def is_github_ip(ip: str, timeout: int = 10) -> bool:
        """Validate if the IP address is from GitHub."""
        try:
            meta = requests.get("https://api.github.com/meta", timeout=timeout).json()
            valid_ips = meta.get("hooks", [])
            remote_ip = ipaddress.ip_address(ip)
            return any(remote_ip in ipaddress.ip_network(ip_range) for ip_range in valid_ips)
        except Exception as e:
            logging.error("Failed to retrieve GitHub hooks IPs: %s", e)
            return False
    
    @staticmethod
    def is_push_event(request) -> bool:
        """Check if the request is a GitHub push event."""
        return request.headers.get("X-GitHub-Event") == "push"
    
    @staticmethod
    def is_auto_commit(payload: Dict[str, Any]) -> bool:
        """Check if the commit is an auto-commit to avoid loops."""
        if not payload or "head_commit" not in payload:
            return False
        
        head_commit = payload.get("head_commit")
        if not head_commit or "message" not in head_commit:
            return False
        
        return "Auto-commit by webhook" in head_commit["message"]


class WebhookProcessor:
    """Processes webhook requests and manages Git operations."""
    
    def __init__(self, config: Config):
        self.config = config
        self.git_ops = GitOperations(config.repos_dir, config.git_user_name, config.git_user_email)
    
    def process_dev_container(self, container: str, branch: str) -> Tuple[bool, str]:
        """Process a development container with submodule handling."""
        try:
            # Handle submodules first
            container_submodules = self.config.submodules.get(container, {})
            for path, branch_sub in container_submodules.items():
                full_path = os.path.join(self.config.repos_dir, path)
                
                # Check for changes in submodule
                if not self.git_ops.has_changes(container, full_path):
                    logging.info(f"{container}: No changes in {path}, only pulling")
                    success, msg = self.git_ops.pull(container, full_path, branch_sub)
                    if not success:
                        return False, f"Submodule pull failed: {msg}"
                    logging.info(f"{container}: Auto pull {path} successful")
                    continue
                
                # Commit, pull, and push submodule changes
                success, msg = self.git_ops.commit(container, full_path)
                if not success:
                    return False, f"Submodule commit failed: {msg}"
                
                # Log the commit result appropriately
                if "No changes to commit" in msg:
                    logging.info(f"{container}: {path} - {msg}")
                else:
                    logging.info(f"{container}: Auto commit {path} successful")
                
                success, msg = self.git_ops.pull(container, full_path, branch_sub)
                if not success:
                    return False, f"Submodule pull failed: {msg}"
                logging.info(f"{container}: Auto pull {path} successful")
                
                # Checkout and merge for submodule
                success, msg = self.git_ops.checkout_and_merge(container, full_path, branch_sub)
                if success:
                    success, msg = self.git_ops.push(container, full_path, branch_sub)
                    if not success:
                        return False, f"Submodule push failed: {msg}"
                    logging.info(f"{container}: Auto push {path} successful")
            
            # Update submodules to use newest commits
            success, msg = self.git_ops.submodule_update(container, self.config.repos_dir, use_remote=True)
            if not success:
                return False, f"Submodule update failed: {msg}"
            
            # Commit, pull, and push main repository
            success, msg = self.git_ops.commit(container, self.config.repos_dir)
            if not success:
                return False, f"Main repo commit failed: {msg}"
            
            # Log the commit result appropriately
            if "No changes to commit" in msg:
                logging.info(f"{container}: {msg}")
            else:
                logging.info(f"{container}: Auto commit successful")
            
            success, msg = self.git_ops.pull(container, self.config.repos_dir, branch)
            if not success:
                return False, f"Main repo pull failed: {msg}"
            logging.info(f"{container}: Auto pull successful")
            
            success, msg = self.git_ops.push(container, self.config.repos_dir, branch)
            if not success:
                return False, f"Main repo push failed: {msg}"
            logging.info(f"{container}: Auto push successful")
            
            return True, "Dev container processed successfully"
            
        except Exception as e:
            error_msg = f"Error processing dev container {container}: {str(e)}"
            logging.error(error_msg)
            return False, error_msg
    
    def process_main_container(self, container: str, branch: str) -> Tuple[bool, str]:
        """Process a main/production container."""
        try:
            # Check for local changes and reset if necessary
            if self.git_ops.has_changes(container, self.config.repos_dir):
                success, msg = self.git_ops.reset_hard(container, self.config.repos_dir, branch)
                if not success:
                    return False, f"Reset failed: {msg}"
                logging.info(f"{container}: Reset successful")
            
            # Pull latest changes
            success, msg = self.git_ops.pull(container, self.config.repos_dir, branch)
            if not success:
                return False, f"Pull failed: {msg}"
            logging.info(f"{container}: Git pull successful")
            
            # Note: Submodule update is commented out in original code
            # Uncomment if needed:
            # success, msg = self.git_ops.submodule_update(container, self.config.repos_dir, use_remote=False)
            # if not success:
            #     return False, f"Submodule update failed: {msg}"
            
            logging.info(f"{container}: Main container processed successfully")
            return True, "Main container processed successfully"
            
        except Exception as e:
            error_msg = f"Error processing main container {container}: {str(e)}"
            logging.error(error_msg)
            return False, error_msg
    
    def process_all_containers(self) -> Tuple[bool, str]:
        """Process all configured containers."""
        for container, branch in self.config.containers.items():
            if branch == "dev":
                success, msg = self.process_dev_container(container, branch)
                if not success:
                    return False, f"Container {container}: {msg}"
            elif branch == "main":
                success, msg = self.process_main_container(container, branch)
                if not success:
                    return False, f"Container {container}: {msg}"
            else:
                logging.warning(f"Unknown branch '{branch}' for container {container}")
        
        return True, "All containers processed successfully"


# Initialize the webhook processor
webhook_processor = WebhookProcessor(config)
@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle GitHub webhook requests."""
    try:
        # Extract and log the real IP
        real_ip = GitHubValidator.get_real_ip(request)
        logging.info(f"Received webhook request from {real_ip}")
        
        # Validate GitHub IP
        if not GitHubValidator.is_github_ip(real_ip, timeout=config.github_api_timeout):
            logging.error(f"Unauthorized request from IP: {real_ip}")
            return jsonify({"error": "Unauthorized IP"}), 403
        
        # Check if it's a push event
        if not GitHubValidator.is_push_event(request):
            logging.info("Received non-push event")
            return jsonify({"message": "Not a push event"}), 200
        
        # Parse and validate payload
        payload = request.json
        if not payload:
            logging.error("Invalid or missing payload")
            return jsonify({"error": "Invalid payload"}), 400
        
        # Check for auto-commit to avoid loops
        if GitHubValidator.is_auto_commit(payload):
            logging.info("Auto-commit by webhook detected, skipping processing")
            return jsonify({"message": "Auto-commit detected, skipping"}), 202
        
        # Process all containers
        success, message = webhook_processor.process_all_containers()
        
        if success:
            logging.info("Operations successful for all containers")
            return jsonify({"message": "Success"}), 200
        else:
            logging.error(f"Container processing failed: {message}")
            return jsonify({"error": message}), 500
            
    except Exception as e:
        error_msg = f"Unexpected error in webhook handler: {str(e)}"
        logging.error(error_msg)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    if not config.health_check_enabled:
        return jsonify({"error": "Health check endpoint is disabled"}), 404
    
    return jsonify({
        "status": "healthy",
        "version": "2.0.0",
        "containers": len(config.containers),
        "submodules": sum(len(subs) for subs in config.submodules.values()),
        "config": {
            "repos_dir": config.repos_dir,
            "log_level": config.log_level,
            "github_api_timeout": config.github_api_timeout,
            "git_user": f"{config.git_user_name} <{config.git_user_email}>"
        }
    }), 200


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logging.error(f"Internal server error: {str(error)}")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    logging.info("Starting Git Webhook Server")
    logging.info(f"Configured containers: {list(config.containers.keys())}")
    logging.info(f"Repository directory: {config.repos_dir}")
    logging.info(f"Git user: {config.git_user_name} <{config.git_user_email}>")
    logging.info(f"Health check endpoint: {'enabled' if config.health_check_enabled else 'disabled'}")
    app.run(host=config.flask_host, port=config.flask_port, debug=config.flask_debug)
