from flask import Flask, request, jsonify # type: ignore
import subprocess
import logging
import requests
import ipaddress
from dotenv import load_dotenv # type: ignore
import os
import yaml # type: ignore
from collections import defaultdict
from typing import Dict, Tuple, Optional, Any, List
from dataclasses import dataclass, field
import threading
from queue import Queue
from datetime import datetime

# Load environment variables
load_dotenv()

@dataclass
class Submodule:
    """Represents a Git submodule configuration."""
    path: str
    branch: str

@dataclass
class Workflow:
    """Represents a workflow configuration."""
    description: str
    reset_on_changes: bool = False
    pull: bool = True
    commit: bool = False
    push: bool = False
    submodule_update: bool = True
    submodule_remote: bool = False
    submodule_commit_push: bool = False

@dataclass
class Container:
    """Represents a container configuration."""
    id: str
    name: str
    branch: str
    workflow: str
    repos_dir: str
    submodules: List[Submodule] = field(default_factory=list)

@dataclass
class Config:
    """Configuration class to hold all application settings."""
    current_dir: str
    repos_dir: str
    containers: List[Container]
    workflows: Dict[str, Workflow]
    flask_host: str = "0.0.0.0"
    flask_port: int = 5000
    flask_debug: bool = False
    log_level: str = "INFO"
    log_file: str = "webhook.log"
    github_api_timeout: int = 10
    git_user_name: str = "Git Webhook Bot"
    git_user_email: str = "webhook@example.com"
    health_check_enabled: bool = True
    config_file: str = "config.yaml"
    commit_message_template: str = "Auto-commit by webhook"
    max_concurrent_containers: int = 5
    container_timeout: int = 300
    
    @classmethod
    def from_environment_and_file(cls) -> 'Config':
        """Create configuration from environment variables and YAML file."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Load basic settings from environment
        config_file = os.environ.get('CONFIG_FILE', 'config.yaml')
        config_path = os.path.join(current_dir, config_file)
        
        # Default repositories directory
        default_repos_dir = os.environ.get('REPOS_DIR', '/home/container/server-data')
        
        # Try to load YAML configuration
        containers = []
        workflows = {}
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    content = f.read().strip()
                    if not content:
                        logging.warning(f"YAML config file {config_path} is empty, using environment variables")
                        containers, workflows = cls._load_legacy_env_config()
                    else:
                        yaml_config = yaml.safe_load(content)
                        
                        # Handle empty or None YAML file
                        if yaml_config is None:
                            logging.warning(f"YAML config file {config_path} contains no valid YAML, using environment variables")
                            containers, workflows = cls._load_legacy_env_config()
                        else:
                            # Load workflows
                            yaml_workflows = yaml_config.get('workflows', {})
                            if yaml_workflows is None:
                                yaml_workflows = {}
                            
                            for name, workflow_data in yaml_workflows.items():
                                if workflow_data is None:
                                    workflow_data = {}
                                workflows[name] = Workflow(
                                    description=workflow_data.get('description', ''),
                                    reset_on_changes=workflow_data.get('reset_on_changes', False),
                                    pull=workflow_data.get('pull', True),
                                    commit=workflow_data.get('commit', False),
                                    push=workflow_data.get('push', False),
                                    submodule_update=workflow_data.get('submodule_update', True),
                                    submodule_remote=workflow_data.get('submodule_remote', False),
                                    submodule_commit_push=workflow_data.get('submodule_commit_push', False)
                                )
                            
                            # Load containers
                            yaml_containers = yaml_config.get('containers', [])
                            if yaml_containers is None:
                                yaml_containers = []
                            
                            for container_data in yaml_containers:
                                if container_data is None:
                                    continue
                                
                                submodules = []
                                submodule_list = container_data.get('submodules', [])
                                if submodule_list is None:
                                    submodule_list = []
                                
                                for sub_data in submodule_list:
                                    if sub_data is None:
                                        continue
                                    submodules.append(Submodule(
                                        path=sub_data.get('path', ''),
                                        branch=sub_data.get('branch', '')
                                    ))
                                
                                container = Container(
                                    id=container_data.get('id', ''),
                                    name=container_data.get('name', container_data.get('id', '')),
                                    branch=container_data.get('branch', ''),
                                    workflow=container_data.get('workflow', ''),
                                    repos_dir=container_data.get('repos_dir', default_repos_dir),
                                    submodules=submodules
                                )
                                containers.append(container)
                            
                            # Load global settings from YAML
                            yaml_settings = yaml_config.get('settings', {})
                            if yaml_settings is None:
                                yaml_settings = {}
                                
                            if 'default_repos_dir' in yaml_settings:
                                default_repos_dir = yaml_settings['default_repos_dir']
                            
                            logging.info(f"Loaded configuration from {config_path}")
                            logging.info(f"Found {len(containers)} containers and {len(workflows)} workflows")
                
            except yaml.YAMLError as e:
                logging.error(f"YAML parsing error in {config_path}: {e}")
                logging.info("Falling back to environment variable configuration")
                containers, workflows = cls._load_legacy_env_config()
            except Exception as e:
                logging.warning(f"Failed to load YAML config from {config_path}: {e}")
                logging.info("Falling back to environment variable configuration")
                containers, workflows = cls._load_legacy_env_config()
        else:
            logging.info(f"No config file found at {config_path}, using environment variables")
            containers, workflows = cls._load_legacy_env_config()
        
        # Load Flask and other settings from environment (these override YAML)
        flask_host = os.environ.get('FLASK_HOST', '0.0.0.0')
        flask_port = int(os.environ.get('FLASK_PORT', '5000'))
        flask_debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
        log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
        log_file = os.environ.get('LOG_FILE', 'webhook.log')
        github_api_timeout = int(os.environ.get('GITHUB_API_TIMEOUT', '10'))
        git_user_name = os.environ.get('GIT_USER_NAME', 'Git Webhook Bot')
        git_user_email = os.environ.get('GIT_USER_EMAIL', 'webhook@example.com')
        health_check_enabled = os.environ.get('HEALTH_CHECK_ENABLED', 'true').lower() == 'true'
        commit_message_template = os.environ.get('COMMIT_MESSAGE_TEMPLATE', 'Auto-commit by webhook')
        max_concurrent_containers = int(os.environ.get('MAX_CONCURRENT_CONTAINERS', '5'))
        container_timeout = int(os.environ.get('CONTAINER_TIMEOUT', '300'))
        
        return cls(
            current_dir=current_dir,
            repos_dir=default_repos_dir,
            containers=containers,
            workflows=workflows,
            flask_host=flask_host,
            flask_port=flask_port,
            flask_debug=flask_debug,
            log_level=log_level,
            log_file=log_file,
            github_api_timeout=github_api_timeout,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            health_check_enabled=health_check_enabled,
            config_file=config_file,
            commit_message_template=commit_message_template,
            max_concurrent_containers=max_concurrent_containers,
            container_timeout=container_timeout
        )
    
    @classmethod
    def _load_legacy_env_config(cls) -> Tuple[List[Container], Dict[str, Workflow]]:
        """Load configuration from legacy environment variables."""
        containers = []
        
        # Default workflows for backward compatibility
        workflows = {
            'main': Workflow(
                description="Legacy main branch workflow",
                reset_on_changes=True,
                pull=True,
                commit=False,
                push=False,
                submodule_update=True,
                submodule_remote=False
            ),
            'dev': Workflow(
                description="Legacy dev branch workflow",
                reset_on_changes=False,
                pull=True,
                commit=True,
                push=True,
                submodule_update=True,
                submodule_remote=True,
                submodule_commit_push=True
            )
        }
        
        # Parse legacy containers from environment variables
        container_configs = {}
        for key, value in os.environ.items():
            if key.startswith('CONTAINER_'):
                container_id = key.replace('CONTAINER_', '')
                container_configs[container_id] = value
        
        # Parse legacy submodules
        submodules_by_container = defaultdict(list)
        for key, value in os.environ.items():
            if key.startswith('SUBMODULE_'):
                try:
                    _, container_id, name = key.split('_', 2)
                    path, branch = value.split(':')
                    submodules_by_container[container_id].append(Submodule(path=path, branch=branch))
                except ValueError:
                    logging.warning(f"Invalid legacy submodule configuration: {key}={value}")
        
        # Create container objects
        default_repos_dir = os.environ.get('REPOS_DIR', '/home/container/server-data')
        for container_id, branch in container_configs.items():
            workflow = 'main' if branch == 'main' else 'dev'
            container = Container(
                id=container_id,
                name=container_id,  # Use ID as name for legacy configs
                branch=branch,
                workflow=workflow,
                repos_dir=default_repos_dir,
                submodules=submodules_by_container.get(container_id, [])
            )
            containers.append(container)
        
        return containers, workflows
    
    def get_workflow(self, workflow_name: str) -> Optional[Workflow]:
        """Get workflow by name."""
        return self.workflows.get(workflow_name)
    
    def get_container_by_id(self, container_id: str) -> Optional[Container]:
        """Get container configuration by ID."""
        for container in self.containers:
            if container.id == container_id:
                return container
        return None
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if not self.containers:
            errors.append("No containers configured")
        
        for container in self.containers:
            # Validate workflow exists
            if container.workflow not in self.workflows:
                errors.append(f"Container {container.id}: workflow '{container.workflow}' not found")
            
            # Validate container ID format (basic check)
            if not container.id.strip():
                errors.append(f"Container has empty ID")
            
            # Validate branch name
            if not container.branch.strip():
                errors.append(f"Container {container.id}: empty branch name")
            
            # Validate submodules
            for submodule in container.submodules:
                if not submodule.path.strip():
                    errors.append(f"Container {container.id}: submodule has empty path")
                if not submodule.branch.strip():
                    errors.append(f"Container {container.id}: submodule {submodule.path} has empty branch")
        
        return errors

# Initialize configuration
config = Config.from_environment_and_file()

# Validate configuration
config_errors = config.validate()
if config_errors:
    print("Configuration errors found:")
    for error in config_errors:
        print(f"  - {error}")
    exit(1)

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
        cmd = ["docker", "exec", container] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # Handle ownership issues
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
        elif (result.returncode != 0 and result.stderr and "Need to specify how to reconcile divergent branches" in result.stderr):
            
            logging.warning(f"Divergent branches detected, configuring pull strategy for {container}")
            
            # Set pull strategy to rebase
            pull_config_cmd = ["docker", "exec", container, "git", "config", "--global", "pull.rebase", "true"]
            pull_result = subprocess.run(pull_config_cmd, capture_output=True, text=True)
            if pull_result.returncode != 0:
                logging.warning(f"Could not set pull strategy: {pull_result.stderr}")
            
            # Retry the original command
            cmd = ["docker", "exec", container] + list(args)
            result = subprocess.run(cmd, capture_output=True, text=True)
        
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
        # Check if there are changes to commit
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
        # Reset to HEAD (discards staged and unstaged changes)
        result_reset = self.run_docker_command(container, "git", "-C", path, "reset", "--hard", "origin/" + branch)
        if result_reset.returncode != 0:
            error_msg = f"Reset failed in container {container}: {result_reset.stderr}"
            logging.error(error_msg)
            return False, error_msg
        
        # Clean untracked files and directories
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
            # Use second IP if multiple present (first is usually proxy)
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
    
    def process_container(self, container: Container) -> Tuple[bool, str]:
        """Process a single container based on its workflow."""
        try:
            workflow = self.config.get_workflow(container.workflow)
            if not workflow:
                return False, f"Workflow '{container.workflow}' not found"
            
            logging.info(f"Processing container {container.name} ({container.id}) with workflow '{container.workflow}'")
            
            # Handle submodules first if workflow supports it
            if workflow.submodule_update and container.submodules:
                for submodule in container.submodules:
                    success, msg = self._process_submodule(container, submodule, workflow)
                    if not success:
                        return False, f"Submodule {submodule.path} failed: {msg}"
            
            # Handle main repository workflow
            success, msg = self._process_main_repo(container, workflow)
            if not success:
                return False, f"Main repo failed: {msg}"
            
            logging.info(f"Container {container.name} processed successfully")
            return True, "Container processed successfully"
            
        except Exception as e:
            error_msg = f"Error processing container {container.name}: {str(e)}"
            logging.error(error_msg)
            return False, error_msg
    
    def _process_submodule(self, container: Container, submodule: Submodule, workflow: Workflow) -> Tuple[bool, str]:
        """Process a single submodule according to workflow."""
        full_path = os.path.join(container.repos_dir, submodule.path)
        
        # Check if submodule has changes
        has_changes = self.git_ops.has_changes(container.id, full_path)
        
        if not has_changes and not workflow.submodule_commit_push:
            # No changes and no commit/push required, just pull
            logging.info(f"{container.name}: No changes in {submodule.path}, only pulling")
            success, msg = self.git_ops.pull(container.id, full_path, submodule.branch)
            if not success:
                return False, msg
            logging.info(f"{container.name}: Pull successful for {submodule.path}")
            return True, "Submodule pulled successfully"
        
        # Handle submodule workflow
        if workflow.commit and has_changes:
            success, msg = self.git_ops.commit(container.id, full_path, self._get_commit_message())
            if not success:
                return False, msg
            
            if "No changes to commit" in msg:
                logging.info(f"{container.name}: {submodule.path} - {msg}")
            else:
                logging.info(f"{container.name}: Committed {submodule.path}")
        
        if workflow.pull:
            success, msg = self.git_ops.pull(container.id, full_path, submodule.branch)
            if not success:
                return False, msg
            logging.info(f"{container.name}: Pulled {submodule.path}")
        
        if workflow.push and workflow.submodule_commit_push:
            # Checkout and merge for submodule push
            success, msg = self.git_ops.checkout_and_merge(container.id, full_path, submodule.branch)
            if success:
                success, msg = self.git_ops.push(container.id, full_path, submodule.branch)
                if not success:
                    return False, msg
                logging.info(f"{container.name}: Pushed {submodule.path}")
        
        return True, "Submodule processed successfully"
    
    def _process_main_repo(self, container: Container, workflow: Workflow) -> Tuple[bool, str]:
        """Process the main repository according to workflow."""
        # Check for local changes and reset if workflow requires it
        if workflow.reset_on_changes and self.git_ops.has_changes(container.id, container.repos_dir):
            success, msg = self.git_ops.reset_hard(container.id, container.repos_dir, container.branch)
            if not success:
                return False, msg
            logging.info(f"{container.name}: Reset successful")
        
        # Update submodules to use newest commits if enabled
        if workflow.submodule_update and container.submodules:
            success, msg = self.git_ops.submodule_update(container.id, container.repos_dir, workflow.submodule_remote)
            if not success:
                return False, msg
        
        # Commit changes if workflow allows
        if workflow.commit:
            success, msg = self.git_ops.commit(container.id, container.repos_dir, self._get_commit_message())
            if not success:
                return False, msg
            
            if "No changes to commit" in msg:
                logging.info(f"{container.name}: {msg}")
            else:
                logging.info(f"{container.name}: Main repo committed")
        
        # Pull latest changes if workflow allows
        if workflow.pull:
            success, msg = self.git_ops.pull(container.id, container.repos_dir, container.branch)
            if not success:
                return False, msg
            logging.info(f"{container.name}: Main repo pulled")
        
        # Push changes if workflow allows
        if workflow.push:
            success, msg = self.git_ops.push(container.id, container.repos_dir, container.branch)
            if not success:
                return False, msg
            logging.info(f"{container.name}: Main repo pushed")
        
        return True, "Main repository processed successfully"
    
    def _get_commit_message(self) -> str:
        """Generate commit message from template."""
        template = self.config.commit_message_template
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return template.replace("{timestamp}", timestamp)
    
    def process_all_containers(self) -> Tuple[bool, str]:
        """Process all configured containers."""
        if not self.config.containers:
            return False, "No containers configured"
        
        for container in self.config.containers:
            success, msg = self.process_container(container)
            if not success:
                return False, f"Container {container.name}: {msg}"
        
        return True, "All containers processed successfully"


# Initialize the webhook processor
webhook_processor = WebhookProcessor(config)

# Initialize a queue for processing requests
request_queue = Queue()

def process_requests():
    while True:
        # Get the next request from the queue
        request_data = request_queue.get()
        try:
            real_ip, payload = request_data
            logging.info(f"Processing webhook request from {real_ip}")

            # Validate GitHub IP
            if not GitHubValidator.is_github_ip(real_ip, timeout=config.github_api_timeout):
                logging.error(f"Unauthorized request from IP: {real_ip}")
                continue

            # Check for auto-commit to avoid loops
            if GitHubValidator.is_auto_commit(payload):
                logging.info("Auto-commit by webhook detected, skipping processing")
                continue

            # Process all containers
            success, message = webhook_processor.process_all_containers()

            if success:
                logging.info("Operations successful for all containers")
            else:
                logging.error(f"Container processing failed: {message}")
        except Exception as e:
            logging.error(f"Unexpected error in request processing: {str(e)}")
        finally:
            # Mark the task as done
            request_queue.task_done()

# Start the worker thread
worker_thread = threading.Thread(target=process_requests, daemon=True)
worker_thread.start()

@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle GitHub webhook requests."""
    try:
        # Extract and log the real IP
        real_ip = GitHubValidator.get_real_ip(request)
        logging.info(f"Received webhook request from {real_ip}")

        # Check if it's a push event
        if not GitHubValidator.is_push_event(request):
            logging.info("Received non-push event")
            return jsonify({"message": "Not a push event"}), 200
        
        # Parse and validate payload
        payload = request.json
        if not payload:
            logging.error("Invalid or missing payload")
            return jsonify({"error": "Invalid payload"}), 400

        # Add the request to the queue
        request_queue.put((real_ip, payload))
        logging.info("Request added to the queue")
        return jsonify({"message": "Request is being processed"}), 202

    except Exception as e:
        error_msg = f"Unexpected error in webhook handler: {str(e)}"
        logging.error(error_msg)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    if not config.health_check_enabled:
        return jsonify({"error": "Health check endpoint is disabled"}), 404
    
    total_submodules = sum(len(container.submodules) for container in config.containers)
    container_summary = {}
    for container in config.containers:
        workflow = config.get_workflow(container.workflow)
        container_summary[container.id] = {
            "name": container.name,
            "branch": container.branch,
            "workflow": container.workflow,
            "workflow_description": workflow.description if workflow else "Unknown workflow",
            "submodules": len(container.submodules)
        }
    
    return jsonify({
        "status": "healthy",
        "version": "3.0.0",
        "containers": len(config.containers),
        "submodules": total_submodules,
        "workflows": len(config.workflows),
        "container_details": container_summary,
        "config": {
            "repos_dir": config.repos_dir,
            "log_level": config.log_level,
            "github_api_timeout": config.github_api_timeout,
            "git_user": f"{config.git_user_name} <{config.git_user_email}>",
            "config_file": config.config_file
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
    container_names = [f"{c.name} ({c.id})" for c in config.containers]
    logging.info(f"Configured containers: {container_names}")
    logging.info(f"Repository directory: {config.repos_dir}")
    logging.info(f"Git user: {config.git_user_name} <{config.git_user_email}>")
    logging.info(f"Health check endpoint: {'enabled' if config.health_check_enabled else 'disabled'}")
    logging.info(f"Configuration file: {config.config_file}")
    app.run(host=config.flask_host, port=config.flask_port, debug=config.flask_debug)
